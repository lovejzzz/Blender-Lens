# 光谱采样与颜色（0.8.2，纯 Python；OSL 与 GPU 内核用同一套）。
#   · 波长：默认按三条传感器响应之和的逆 CDF 表抽样（sensor_table，色噪最低）；场景里有 5.3 色散材质时（GPU「共用模式」）
#     改按 Cycles 的 sample_wavelength() 映射（与色散材质同一个映射、同一个随机数 → 同一个 λ），权重 = 响应 / pdf；
#   · 颜色：三条高斯「传感器响应」（Rec.709 原色下定义，每通道 ∫ = 1），权重 = g_c(λ) / pdf(λ)，再从 Rec.709
#     换到场景工作色彩空间（GPU 用 Cycles 的 rec709_to_rgb，OSL 用插件写入的矩阵）。等能光谱 → 白。
#   · 为什么不用 CIE 色匹配：CIE → RGB 有负瓣，同样采样数色噪约高 1.7 倍（实测，RESEARCH.md §6.12）；
#     传感器响应本来就是相机的做法。CIE 表仍保留在 spectral/cie_d65.json（采样器常数也在那里）。
import json, math, os

_DATA = None
SENSOR_RGB = ((0.610, 0.032), (0.550, 0.032), (0.465, 0.026))   # (中心 μm, σ μm)，R / G / B
# CIE XYZ (D65) → 线性 Rec.709（sRGB 原色，D65 白点）：没有 Blender / OCIO 时的默认值
XYZ_TO_REC709 = ((3.2409699, -1.5373832, -0.4986108),
                 (-0.9692436, 1.8759675, 0.0415551),
                 (0.0556301, -0.2039770, 1.0569715))


def data():
    global _DATA
    if _DATA is None:
        _DATA = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "spectral", "cie_d65.json")))
    return _DATA


def sample_wavelength(u):
    """u ∈ [0, 1) → (λ μm, pdf 每 μm)。与 Cycles util/colorspace.h 的 sample_wavelength 相同。"""
    d = data(); s = d["sampler"]
    r = s["N"] * u + s["y0"]
    prob = s["a"] * r * (1.0 - r) * (d["lambda_max_um"] - d["lambda_min_um"]) / s["N"]
    lam = -math.log(1.0 / r - 1.0) / s["a"] + s["x0"]
    return min(max(lam, d["lambda_min_um"]), d["lambda_max_um"]), prob


def xyz_d65(lam):
    """λ（μm）→ CIE XYZ × D65 光谱 × 归一化常数（线性插值，同 Cycles）。"""
    d = data()
    x = (lam - d["lambda_min_um"]) / d["dlambda_um"]
    i = int(math.floor(x)); f = x - i
    if i < 0 or i >= d["resolution"]:
        return (0.0, 0.0, 0.0)
    c0, c1 = d["cmf_xyz"][i], d["cmf_xyz"][i + 1]
    spd = d["d65_spd"][i] + f * (d["d65_spd"][i + 1] - d["d65_spd"][i])
    k = spd * d["d65_normalization"]
    return tuple((c0[j] + f * (c1[j] - c0[j])) * k for j in range(3))


def pdf_per_um(prob):
    """Cycles 的 prob 是相对 [λmin, λmax] 归一化坐标的密度；换成每 μm。"""
    d = data()
    return prob / (d["lambda_max_um"] - d["lambda_min_um"])


def sensor_rgb(lam):
    """三条高斯传感器响应（每通道对 λ 积分为 1，单位 1/μm）。"""
    return tuple(math.exp(-0.5 * ((lam - c) / s) ** 2) / (s * math.sqrt(2.0 * math.pi)) for c, s in SENSOR_RGB)


def rgb_weight(lam, prob, M=None):
    """一条波长为 λ 的光线对 R/G/B 的权重（Rec.709；给 M 时换到该色彩空间）。"""
    q = pdf_per_um(prob)
    g = [v / q for v in sensor_rgb(lam)]
    if M is None:
        return tuple(g)
    return tuple(sum(M[r][c] * g[c] for c in range(3)) for r in range(3))


def sensor_table(l0=0.400, l1=0.700, dl=0.005):
    """默认模式的波长采样表（0.4–0.8 的做法）：按三条传感器响应之和做重要性采样（5 nm 分段的逆 CDF），
    权重 w_c = 归一化响应 / 采样概率；等能光谱 → 白。场景里没有 5.3 色散材质时用它（比 Cycles 的映射色噪低约 1.15–1.6 倍）。"""
    n = int(round((l1 - l0) / dl))
    lam = [l0 + (j + 0.5) * dl for j in range(n)]
    S = [[math.exp(-0.5 * ((x - c) / s) ** 2) for x in lam] for c, s in SENSOR_RGB]
    Sn = [[v / sum(row) for v in row] for row in S]
    p = [sum(Sn[c][j] for c in range(3)) / 3.0 for j in range(n)]
    cdf, acc = [], 0.0
    for v in p:
        acc += v; cdf.append(acc)
    cdf[-1] = 1.0
    w = [[Sn[c][j] / p[j] if p[j] > 0 else 0.0 for j in range(n)] for c in range(3)]
    return dict(l0=l0, dl=dl, cdf=cdf, w=w)


IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def working_space_matrix(source="Linear Rec.709"):
    """Blender 里：source（默认线性 Rec.709）→ 当前工作色彩空间（bpy.data.colorspace.working_space）的 3×3 矩阵；
    用 Blender 自带的 PyOpenColorIO 和当前 OCIO 配置算（与 Cycles 的 rec709_to_rgb 一致）。取不到时返回单位阵。"""
    try:
        import bpy, PyOpenColorIO as OCIO
        cs = bpy.data.colorspace
        path = cs.ocio_config_path
        cfg = OCIO.Config.CreateFromFile(path) if path else OCIO.GetCurrentConfig()
        if cs.working_space == source:
            return IDENTITY
        cpu = cfg.getProcessor(source, cs.working_space).getDefaultCPUProcessor()
        cols = [cpu.applyRGB([1.0 if k == j else 0.0 for k in range(3)]) for j in range(3)]
        return tuple(tuple(float(cols[c][r]) for c in range(3)) for r in range(3))
    except Exception:                                   # noqa: BLE001 — 没有 bpy / OCIO：默认
        return IDENTITY


def working_space_name():
    try:
        import bpy
        return bpy.data.colorspace.working_space
    except Exception:                                   # noqa: BLE001
        return "Linear Rec.709"
