# 由处方生成 Cycles OSL 自定义相机（纯 Python 生成源码，不依赖 bpy）。
# mode 0 = 解析：在 OSL 里按处方逐面真实追迹（快、无几何噪点、可做三波长色散）
# mode 1 = 几何：只从传感器向出瞳发射光线，由 Cycles 穿过场景里的镜片网格（鬼影/炫光）
# 相机坐标：+Z 视线、+Y 上；镜头坐标经绕 Y 轴 180° 映射：cam = (−x, y, z)。
import math, os
try:
    from . import optics as O
except ImportError:
    import optics as O

def _exit_pupil_axis(S, z_stop, r_stop, axis):
    k = min(i for i, sf in enumerate(S) if sf["z"] < z_stop)          # 光阑后第一面
    sub = S[k:]
    u0 = 1e-3
    y = u0 * (z_stop - sub[0]["z"])
    y, nu, n = O._paraxial_core(sub, axis, y, u0)
    u = nu / n
    t = -y / u                                                        # 从最后一面沿 −Z 到出瞳（负 = 虚出瞳在其前方）
    return sub[-1]["z"] - t, r_stop * abs(u0 / u)

def exit_pupil(rx, S=None):
    """近轴出瞳：返回 (z_exp, r_exp, z_stop, r_stop)。光阑后有柱面时两截面出瞳不同 → 采样盘放在垂直截面出瞳平面，
    半径取能同时罩住水平截面出瞳（按传感器中心视锥换算到该平面）的较大值。"""
    S = S or O.layout(rx)
    z_stop, r_stop = O.stop_geometry(rx, S)
    zy, ry = _exit_pupil_axis(S, z_stop, r_stop, "Ry")
    zx, rx_ = _exit_pupil_axis(S, z_stop, r_stop, "Rx")
    return zy, max(ry, rx_ * zy / zx), z_stop, r_stop

def _arr(name, vals, fmt="{:.9g}", typ="float"):
    return f"  {typ} {name}[{len(vals)}] = {{ " + ", ".join(fmt.format(v) for v in vals) + " };\n"

# 光谱：与 Cycles 5.3 的单波长路径同一套约定（见 spectral.py）。0.8.1 及以前是三条高斯「传感器响应」曲线。
try:
    from . import spectral as SP
except ImportError:
    import spectral as SP


def spectral_table(l0=0.400, l1=0.700, dl=0.005):
    return SP.sensor_table(l0, l1, dl)


def spectral_arrays():
    d = SP.data()
    cmf = d["cmf_xyz"]
    return dict(CMX=[c[0] for c in cmf], CMY=[c[1] for c in cmf], CMZ=[c[2] for c in cmf], D65=d["d65_spd"][:len(cmf)],
                L0=d["lambda_min_um"], L1=d["lambda_max_um"], DL=d["dlambda_um"], RES=d["resolution"],
                NORM=d["d65_normalization"], **{"S_" + k: v for k, v in d["sampler"].items() if k != "note"})

_PUPIL_CACHE = {}
_T0_CACHE = {}
# 出瞳表的磁盘位置：随包分发的（预设，tools/build_presets.py 生成）+ 用户缓存（rig 在 register 时设置；None = 不写盘）
SHIPPED_PUPIL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prescriptions", "pupil_tables")
PUPIL_CACHE_DIR = None

def rx_key(rx):
    """处方内容的稳定指纹（跨进程不变；Python 的 hash() 每次启动都不同，不能用来做磁盘缓存）。"""
    import hashlib, json
    return hashlib.sha1(json.dumps(rx, sort_keys=True, default=str).encode()).hexdigest()[:20]

def _pupil_key(rx):
    try:
        from . import pupil as PU
    except ImportError:
        import pupil as PU
    import copy
    optical = {k: v for k, v in rx.items() if k not in ("meta", "look")}   # 观感参数 / 说明文字不影响出瞳表
    if optical.get("focus", {}).get("compensator"):                        # 表已覆盖补偿强度 0–1
        optical = copy.deepcopy(optical); optical["focus"]["compensator"].pop("level", None)
    return f"{rx_key(optical)}_v{PU.TABLE_VERSION}"

def save_pupil_table(T, path):
    import numpy as np
    np.savez_compressed(path, **{k: np.asarray(T[k]) for k in ("NX", "NY", "X0", "X1", "Y0", "Y1", "r_samp", "eff_disc", "eff_box")})

def _load_pupil_table(path):
    import numpy as np
    with np.load(path) as z:
        T = {k: z[k] for k in z.files}
    T["NX"], T["NY"] = int(T["NX"]), int(T["NY"])
    return T

def pupil_table(rx):
    """出瞳重要性采样表（见 pupil.py）：内存缓存 → 随包分发 → 用户磁盘缓存 → 现算（约 2–10 秒）并写盘。"""
    try:
        from . import pupil as PU
    except ImportError:
        import pupil as PU
    key = _pupil_key(rx)
    if key in _PUPIL_CACHE:
        return _PUPIL_CACHE[key]
    T = None
    for d in (SHIPPED_PUPIL_DIR, PUPIL_CACHE_DIR):
        f = os.path.join(d, key + ".npz") if d else None
        if f and os.path.exists(f):
            try:
                T = _load_pupil_table(f)
                break
            except (OSError, ValueError, KeyError):
                T = None
    if T is None:
        T = PU.table(rx)
        if PUPIL_CACHE_DIR:
            try:
                os.makedirs(PUPIL_CACHE_DIR, exist_ok=True)
                save_pupil_table(T, os.path.join(PUPIL_CACHE_DIR, key + ".npz"))
            except OSError:
                pass
    if len(_PUPIL_CACHE) > 8:
        _PUPIL_CACHE.clear()
    _PUPIL_CACHE[key] = T
    return T

_DATA_KEYS = ("S", "z_exp", "r_exp", "z_stop", "r_stop", "NS", "Z", "CX", "CY", "KX", "KY", "A_", "B_", "CLR", "MSX", "MSY",
              "AS", "G1", "G2", "CO", "AG", "NPAIRS", "SG1", "SG2", "W", "H", "N0", "sources", "SPEC", "T0", "PNX", "PNY", "PB",
              "AT", "AC", "AMX", "AMY", "C_", "E_", "ND", "KCX", "KCY")

def lens_data(rx, importance=True):
    """OSL 相机与 GPU 内核（gpugen）共用的处方数据：同一份数组，两边结果才能逐像素对得上。"""
    S = O.layout(rx)
    for sf in S:
        if sf["ap"] is None:
            raise ValueError(f"{sf['id']} 没有口径：先运行 tools/rebuild_apertures.py")
    z_exp, r_exp, z_stop, r_stop = exit_pupil(rx, S)
    NS = len(S)
    Z = [sf["z"] for sf in S]
    CX = [O.curv(sf["Rx"]) for sf in S]
    CY = [O.curv(sf["Ry"]) for sf in S]
    KX = [sf["kx"] for sf in S]
    KY = [sf["ky"] for sf in S]
    A_, B_, C_, E_ = zip(*[O.dispersion_coeffs(sf["glass"]) for sf in S])   # 该面之后的介质：n = A + B/λ² + C/λ⁴ + E/λ⁶
    ND = [O.index_at(sf["glass"]) for sf in S]
    lim = [O.ap_limits(sf["ap"]) for sf in S]
    CLR = [l[0] for l in lim]; MSX = [l[1] for l in lim]; MSY = [l[2] for l in lim]
    AS = [[(sf.get("asph") or []) [m] if m < len(sf.get("asph") or []) else 0.0 for sf in S] for m in range(5)]
    G1 = [float(sf["move"][0]) for sf in S]; G2 = [float(sf["move"][1]) for sf in S]
    # 解析求交：平面 / 球面 / 纯柱面（无圆锥、无非球面）走顶点平面上的二次方程；其余走牛顿
    AT, AC, AMX, AMY = [], [], [], []
    for sf in S:
        cx, cy = O.curv(sf["Rx"]), O.curv(sf["Ry"])
        general = any(sf.get("asph") or []) or sf["kx"] or sf["ky"] or (cx and cy and cx != cy) or any(sf["kc"])   # 补偿板（曲率随对焦）走牛顿
        AT.append(0 if general else 1)
        AC.append(cx or cy)
        AMX.append(1.0 if cx else 0.0); AMY.append(1.0 if cy else 0.0)
    KCX = [sf["kc"][0] for sf in S]; KCY = [sf["kc"][1] for sf in S]      # 发腮补偿：曲率 = C + KC·φ（φ 每帧由插件写入）
    before = [1.0] + [sf["glass"]["nd"] for sf in S[:-1]]
    CO = [1 if (before[k] == 1.0) != (sf["glass"]["nd"] == 1.0) else 0 for k, sf in enumerate(S)]   # 空气-玻璃面（镀膜）
    AG = [k for k in range(NS) if CO[k]]                                                             # 鬼影反射候选面
    NPAIRS = len(AG) * (len(AG) - 1) // 2
    st_id = O.stop_group(rx)["stop"]["after_surface"]
    SG1, SG2 = next(sf["move"] for sf in S if sf["id"] == st_id)            # 光阑随它前面那一面所在的组移动
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    N0 = O.nominal_f_number(rx)
    sources = ", ".join(g["source"]["patent"].split(" — ")[0] for g in rx["groups"] if "source" in g)
    SPEC = spectral_arrays()
    SPEC["table"] = spectral_table()
    try:
        from . import pupil as PU
    except ImportError:
        import pupil as PU
    _k = rx_key(rx)
    if _k not in _T0_CACHE:
        _T0_CACHE[_k] = PU.axial_transmission(rx)
    T0 = _T0_CACHE[_k]
    if importance:
        T = pupil_table(rx)
        PNX, PNY = T["NX"], T["NY"]
        PB = [list(T[k].ravel()) for k in ("X0", "X1", "Y0", "Y1")]     # 下标 ix * NY + iy
    else:
        PNX, PNY = 2, 2
        PB = [[-1e3] * 4, [1e3] * 4, [-1e3] * 4, [1e3] * 4]
    loc = locals()
    return {k: loc[k] for k in _DATA_KEYS}

def generate(rx, name="blenderlens_camera", importance=True):
    D = lens_data(rx, importance)
    (S, z_exp, r_exp, z_stop, r_stop, NS, Z, CX, CY, KX, KY, A_, B_, CLR, MSX, MSY, AS, G1, G2, CO, AG, NPAIRS,
     SG1, SG2, W, H, N0, sources, SPEC, T0, PNX, PNY, PB, AT, AC, AMX, AMY, C_, E_, ND, KCX, KCY) = (D[k] for k in _DATA_KEYS)

    src = f"""// 自动生成：{rx['meta']['name']}
// 来源处方：{sources}
// 不要手改本文件——改 prescription JSON 后重新生成。

float sag_grad(float x, float y, float cx, float cy, float kx, float ky,
               float a4, float a6, float a8, float a10, float a12,
               output float gx, output float gy, output int ok)
{{
  float N = cx * x * x + cy * y * y;
  float Q = 1.0 - (1.0 + kx) * cx * cx * x * x - (1.0 + ky) * cy * cy * y * y;
  if (Q <= 0.0) {{ ok = 0; gx = 0; gy = 0; return 0.0; }}
  ok = 1;
  float Sq = sqrt(Q);
  float den = 1.0 + Sq;
  float dSx = -(1.0 + kx) * cx * cx * x / Sq;
  float dSy = -(1.0 + ky) * cy * cy * y / Sq;
  gx = (2.0 * cx * x * den - N * dSx) / (den * den);
  gy = (2.0 * cy * y * den - N * dSy) / (den * den);
  float s = N / den;
  float r2 = x * x + y * y;
  // 转动对称高次项：Σ A_{{2m}} r^{{2m}}，梯度 Σ 2m A_{{2m}} r^{{2m−2}} (x, y)
  float r4 = r2 * r2, r6 = r4 * r2, r8 = r6 * r2, r10 = r8 * r2;
  s += a4 * r4 + a6 * r6 + a8 * r8 + a10 * r10 + a12 * r10 * r2;
  float g = 4.0 * a4 * r2 + 6.0 * a6 * r4 + 8.0 * a8 * r6 + 10.0 * a10 * r8 + 12.0 * a12 * r10;
  gx += g * x; gy += g * y;
  return s;
}}

float fresnel_plain(float n1, float n2, float ci)
{{
  float st = n1 / n2 * sqrt(max(0.0, 1.0 - ci * ci));
  if (st >= 1.0) return 1.0;
  float ct = sqrt(1.0 - st * st);
  float rs = (n1 * ci - n2 * ct) / (n1 * ci + n2 * ct);
  float rp = (n2 * ci - n1 * ct) / (n2 * ci + n1 * ct);
  return 0.5 * (rs * rs + rp * rp);
}}

// 镀膜反射率（非偏振）：特征矩阵法，最多 3 层（与 optics.stack_reflectance 相同）。
// kind 0 = 单层（厚 d1，折射率 nf1）；1 = 三层 1/4–1/2–1/4 宽带膜（外层同上，λ0 = 4·nf1·d1，
// 中层 2.05 半波，贴玻璃层 clamp(nf1·√n_glass, 1.55, 2.0) 四分之一波）。从玻璃一侧入射时层序反过来。
float coat_R(float n_in, float n_out, float c_in, float lam_um, int kind, float d1, float nf1, float n_glass)
{{
  float s0 = sqrt(max(0.0, 1.0 - c_in * c_in));
  float k = n_in * s0;
  if (k >= n_out) return 1.0;
  float ln[3], ld[3];
  int nl = 1;
  ln[0] = nf1; ld[0] = d1;
  if (kind == 1) {{
    float l0 = 4.0 * nf1 * d1;
    float n3 = clamp(nf1 * sqrt(n_glass), 1.55, 2.0);
    ln[1] = {O.COAT_HIGH_N}; ld[1] = l0 / (2.0 * {O.COAT_HIGH_N});
    ln[2] = n3; ld[2] = l0 / (4.0 * n3);
    nl = 3;
  }}
  int from_air = n_in < n_out;
  float cs_out = sqrt(max(1e-12, 1.0 - (k / n_out) * (k / n_out)));
  float R = 0.0;
  for (int pol = 0; pol < 2; ++pol) {{
    float A = 1.0, B = 0.0, C = 0.0, D = 1.0;
    for (int j = 0; j < nl; ++j) {{
      int jj = from_air ? j : nl - 1 - j;
      float nj = ln[jj], dj = ld[jj];
      float q = 1.0 - (k / nj) * (k / nj);
      if (q <= 0.0) return fresnel_plain(n_in, n_out, c_in);   // 层内倏逝：退回无膜
      float cj = sqrt(q);
      float ej = pol == 0 ? nj * cj : nj / cj;
      float dl = 2.0 * M_PI * nj * dj * cj / (lam_um * 1000.0);
      float cd = cos(dl), sd = sin(dl);
      float A2 = A * cd - B * ej * sd, B2 = A * sd / ej + B * cd, C2 = C * cd + D * ej * sd, D2 = -C * sd / ej + D * cd;
      A = A2; B = B2; C = C2; D = D2;
    }}
    float e0 = pol == 0 ? n_in * c_in : n_in / max(c_in, 1e-12);
    float es = pol == 0 ? n_out * cs_out : n_out / cs_out;
    float nr = e0 * A - D * es, ni = e0 * B * es - C;
    float dr = e0 * A + D * es, di = e0 * B * es + C;
    R += 0.5 * (nr * nr + ni * ni) / (dr * dr + di * di);
  }}
  return R;
}}

shader {name}(
  int mode = 0 [[ string widget = "mapper", string options = "Analytic:0|Geometry:1",
                  string help = "Analytic：OSL 内逐面追迹；Geometry：交给 Cycles 穿过场景里的镜片网格（鬼影/炫光）" ]],
  float f_number = {N0:.3g} [[ float min = {N0:.3g}, float max = 22.0, float sensitivity = 0.1,
                         string help = "光圈 f 值（以垂直截面的主镜计）" ]],
  int blades = 0 [[ int min = 0, int max = 16, string help = "光圈叶片数；0 = 圆形" ]],
  float blade_rotation = 0.0 [[ string help = "叶片旋转（度）" ]],
  float delta_unit = 0.0 [[ string help = "对焦：整体单元前移量 Δ1（mm），由插件按对焦距离写入" ]],
  float delta_front = 0.0 [[ string help = "对焦：变形器前组附加位移 Δ2（mm），由插件按对焦距离写入" ]],
  float comp_phi = 0.0 [[ string help = "发腮补偿板的光焦度 φ（1/mm，水平 −φ、垂直 +φ），由插件按对焦距离写入" ]],
  int dispersion = 1 [[ string widget = "checkBox", string help = "三波长色散（仅 Analytic）" ]],
  int exposure_compensate = 1 [[ string widget = "checkBox", string help = "收光圈时补偿曝光（关闭 = 物理 T 值变暗）" ]],
  float pupil_margin = 1.4 [[ float min = 1.0, float max = 3.0, string help = "出瞳采样盘相对近轴出瞳的放大倍数" ]],
  int ghosts = 0 [[ string widget = "checkBox", string help = "序列鬼影追迹（两次反射，炫光/一字拉丝；仅 Analytic）" ]],
  float ghost_fraction = 0.5 [[ float min = 0.05, float max = 0.95, string help = "走鬼影路径的采样比例" ]],
  float ghost_gain = 1.0 [[ float min = 0.0, float max = 1000.0, string help = "鬼影增益（1 = 物理正确）" ]],
  int coated = 1 [[ string widget = "checkBox", string help = "空气-玻璃面镀膜" ]],
  int coating_type = 0 [[ string widget = "mapper", string options = "Single MgF2:0|Broadband 3-layer:1",
                  string help = "单层 MgF₂ / 三层宽带膜（1/4–1/2–1/4）" ]],
  float coating_nm = 100.0 [[ float min = 0.0, float max = 400.0, string help = "外层 MgF₂ 膜厚（nm）；~100 = 550 nm 四分之一波；宽带膜整套按它缩放" ]],
  float coating_ior = 1.38 [[ float min = 1.0, float max = 2.5 ]],
  int pupil_importance = {1 if importance else 0} [[ string widget = "checkBox",
                  string help = "出瞳重要性采样：只在各视场真正通光的区域里采样（无偏；鬼影 / Geometry 模式自动关闭）" ]],
  float dispersion_strength = 1.0 [[ float min = 0.0, float max = 1.0,
                  string help = "色散强度：n(λ) 在 n_d 与真实值之间插值（0 = 无色散，1 = 物理）" ]],
  float rec_r0 = 1.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_r1 = 0.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_r2 = 0.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_g0 = 0.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_g1 = 1.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_g2 = 0.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_b0 = 0.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_b1 = 0.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float rec_b2 = 1.0 [[ string help = "Rec.709 → 工作色彩空间矩阵（插件按场景写入）" ]],
  float unit_scale = 1.0 [[ string help = "场景单位 / mm（米制场景 0.001）：出射光线起点按此缩放、方向不变，镜头内部仍按毫米追迹" ]],
  output point position = 0.0,
  output vector direction = vector(0.0, 0.0, 1.0),
  output color throughput = 1.0)
{{
{_arr("Z", Z)}{_arr("CX", CX)}{_arr("CY", CY)}{_arr("KX", KX)}{_arr("KY", KY)}{_arr("NA", A_)}{_arr("NB", B_)}{_arr("NC", C_)}{_arr("NE", E_)}{_arr("ND", ND)}{_arr("CLR", CLR)}{_arr("MSX", MSX)}{_arr("MSY", MSY)}{_arr("AA4", AS[0])}{_arr("AA6", AS[1])}{_arr("AA8", AS[2])}{_arr("AA10", AS[3])}{_arr("AA12", AS[4])}{_arr("G1", G1)}{_arr("G2", G2)}{_arr("CO", CO, "{:d}", "int")}{_arr("AG", AG, "{:d}", "int")}{_arr("PX0", PB[0])}{_arr("PX1", PB[1])}{_arr("PY0", PB[2])}{_arr("PY1", PB[3])}{_arr("AT", AT, "{:d}", "int")}{_arr("AC", AC)}{_arr("AMX", AMX)}{_arr("AMY", AMY)}{_arr("KCX", KCX)}{_arr("KCY", KCY)}{_arr("SPC", SPEC["table"]["cdf"])}{_arr("SPR", SPEC["table"]["w"][0])}{_arr("SPG", SPEC["table"]["w"][1])}{_arr("SPB", SPEC["table"]["w"][2])}
  float SENSOR_W = {W:.6g}, SENSOR_H = {H:.6g};
  float Z_STOP = {z_stop:.9g}, R_STOP = {r_stop:.9g}, Z_EXP = {z_exp:.9g}, R_EXP = {r_exp:.9g};
  float N0 = {N0:.6g};
  float EXPOSURE_NORM = {1.0 / T0:.6g};   // 1 / 轴上满光瞳透过率（{T0:.4f}）：轴上满光瞳 = 1
  int NS = {NS}, NAG = {len(AG)}, NPAIRS = {NPAIRS};
  int PNX = {PNX}, PNY = {PNY};
  int SPN = {len(SPEC["table"]["cdf"])};
  float SP_L0 = {SPEC["table"]["l0"]:.6g}, SP_DL = {SPEC["table"]["dl"]:.6g};

  // 传感器点：镜头成倒像，再经镜头→相机坐标的 x 取反（绕 Y 转 180°），
  // 所以画面向右 = 传感器 +x、画面向上 = 传感器 −y，输出正立且不镜像。
  point ras = camera_shader_raster_position();
  float sx = (ras[0] - 0.5) * SENSOR_W;
  float sy = -(ras[1] - 0.5) * SENSOR_H;

  vector rnd = camera_shader_random_sample();
  float k_stop = N0 / f_number;
  float r_samp = R_EXP * k_stop * pupil_margin;
  float zexp = Z_EXP + {SG1} * delta_unit + {SG2} * delta_front;
  point p = point(sx, sy, 0.0);
  float ex = 0.0, ey = 0.0, area_w = 1.0;
  int use_box = 0;
  float bx0 = 0.0, bx1 = 0.0, by0 = 0.0, by1 = 0.0;
  if (pupil_importance && mode == 0 && !ghosts && fabs(pupil_margin - 1.4) < 1e-4) {{
    // 出瞳重要性采样：按 |sx|、|sy| 在象限表里双线性插值得到通光区的包围盒，镜像到本象限，
    // 再夹到当前光圈的采样盘的外接正方形里；盒内均匀采样，盘外照旧算挡光。权重 × 盒面积 / 盘面积 → 无偏。
    float fx = fabs(sx) / (0.5 * SENSOR_W) * (PNX - 1), fy = fabs(sy) / (0.5 * SENSOR_H) * (PNY - 1);
    int ix = min((int)floor(fx), PNX - 2), iy = min((int)floor(fy), PNY - 2);
    float tx = clamp(fx - ix, 0.0, 1.0), ty = clamp(fy - iy, 0.0, 1.0);
    int k00 = ix * PNY + iy, k10 = (ix + 1) * PNY + iy, k01 = ix * PNY + iy + 1, k11 = (ix + 1) * PNY + iy + 1;
    bx0 = mix(mix(PX0[k00], PX0[k10], tx), mix(PX0[k01], PX0[k11], tx), ty);
    bx1 = mix(mix(PX1[k00], PX1[k10], tx), mix(PX1[k01], PX1[k11], tx), ty);
    by0 = mix(mix(PY0[k00], PY0[k10], tx), mix(PY0[k01], PY0[k11], tx), ty);
    by1 = mix(mix(PY1[k00], PY1[k10], tx), mix(PY1[k01], PY1[k11], tx), ty);
    if (sx < 0.0) {{ float t0 = bx0; bx0 = -bx1; bx1 = -t0; }}
    if (sy < 0.0) {{ float t0 = by0; by0 = -by1; by1 = -t0; }}
    bx0 = max(bx0, -r_samp); bx1 = min(bx1, r_samp); by0 = max(by0, -r_samp); by1 = min(by1, r_samp);
    if (bx1 <= bx0 || by1 <= by0) {{ throughput = color(0.0); return; }}
    // 盒面积不小于采样盘（收光圈后通光区接近整盘）→ 整盘采样更省（盒的四角落在盘外）
    use_box = (bx1 - bx0) * (by1 - by0) < M_PI * r_samp * r_samp;
  }}
  if (use_box) {{
    ex = bx0 + rnd[0] * (bx1 - bx0); ey = by0 + rnd[1] * (by1 - by0);
    if (ex * ex + ey * ey > r_samp * r_samp) {{ throughput = color(0.0); return; }}
    area_w = (bx1 - bx0) * (by1 - by0) / (M_PI * r_samp * r_samp);
  }} else {{
    // 出瞳盘上均匀采样（同心映射）
    float a = 2.0 * rnd[0] - 1.0, b = 2.0 * rnd[1] - 1.0, rr = 0.0, th = 0.0;
    if (a == 0.0 && b == 0.0) {{ rr = 0.0; th = 0.0; }}
    else if (fabs(a) > fabs(b)) {{ rr = a; th = M_PI / 4.0 * (b / a); }}
    else {{ rr = b; th = M_PI / 2.0 - M_PI / 4.0 * (a / b); }}
    ex = rr * cos(th) * r_samp; ey = rr * sin(th) * r_samp;
  }}
  vector d = normalize(point(ex, ey, zexp) - p);

  float c2 = d[2] * d[2];
  float w = c2 * c2 * pupil_margin * pupil_margin * area_w * EXPOSURE_NORM;   // cos⁴θ × 采样域/出瞳面积比 ÷ 轴上透过率 → 轴上满光瞳 = 1
  if (!exposure_compensate) w *= k_stop * k_stop;         // 采样盘随光圈缩放，物理变暗需显式乘面积比

  if (mode == 1) {{
    position = point(-p[0], p[1], p[2]) * unit_scale;
    direction = vector(-d[0], d[1], d[2]);
    throughput = color(w);
    return;
  }}

  // 连续波长色散：λ 按三条传感器响应之和做重要性采样（5 nm 分段的逆 CDF 表），每条光线同时贡献给 R/G/B
  // （权重 = 响应 / 采样概率，等能白光仍是白色）；0.8.2 起再按插件写入的矩阵从 Rec.709 换到场景工作色彩空间。
  // OSL 相机只有两维随机数（都给了出瞳），第三维用随机数与光栅位置（含亚像素抖动）的哈希；
  // 也拿不到 Cycles 的路径波长 —— 场景里有 5.3 色散材质时 CPU 路径会重复加权（插件 diagnose 会警告）。
  color chan = color(1.0);
  float lam = {O.LAMBDA_D:.7f};
  if (dispersion && dispersion_strength > 0.0) {{
    float h = noise("hash", ras * 1000.0 + vector(rnd[0], rnd[1], 0.37) * 17.0);
    int j = 0;
    while (j < SPN - 1 && h > SPC[j]) j += 1;
    float c0 = j > 0 ? SPC[j - 1] : 0.0;
    float fr = clamp((h - c0) / max(SPC[j] - c0, 1e-9), 0.0, 1.0);
    lam = SP_L0 + (j + fr) * SP_DL;
    float gr = SPR[j], gg = SPG[j], gb = SPB[j];
    chan = color(rec_r0 * gr + rec_r1 * gg + rec_r2 * gb, rec_g0 * gr + rec_g1 * gg + rec_g2 * gb, rec_b0 * gr + rec_b1 * gg + rec_b2 * gb);
  }}
  float il2 = 1.0 / (lam * lam);              // n(λ) = A + B/λ² + C/λ⁴ + E/λ⁶（目录色散拟合），按强度在 n_d 与之间插值

  float r_stop = R_STOP * k_stop;
  float z_stop = Z_STOP + {SG1} * delta_unit + {SG2} * delta_front;
  float ca = cos(radians(blade_rotation)), sa = sin(radians(blade_rotation));

  // 鬼影：以 ghost_fraction 的概率在空气-玻璃面里均匀选一对 (ga < gb)，权重做无偏补偿
  int ga = -1, gb = -1;
  float wsel = 1.0;
  if (ghosts && NPAIRS > 0) {{
    float h1 = noise("hash", ras * 911.0 + vector(rnd[1], rnd[0], 0.71) * 23.0);
    if (h1 < ghost_fraction) {{
      float h2 = noise("hash", ras * 577.0 + vector(rnd[0], rnd[1], 0.13) * 41.0);
      int k = min((int)floor(h2 * NPAIRS), NPAIRS - 1);
      int u = 0;
      while (k >= NAG - 1 - u) {{ k -= NAG - 1 - u; u += 1; }}
      ga = AG[u]; gb = AG[u + 1 + k];
      wsel = NPAIRS / ghost_fraction * ghost_gain;
    }} else {{
      wsel = 1.0 / (1.0 - ghost_fraction);
    }}
  }}
  color T = color(1.0);
  float LR = 0.610, LG = 0.550, LB = 0.465;

  int i = NS - 1, dirn = 1, state = 0;      // dirn = 1：沿 +Z（面序号递减）；−1：沿 −Z
  for (int step = 0; step < 3 * NS + 4; ++step) {{
    if (i < 0 || i >= NS) break;
    float zi = Z[i] + G1[i] * delta_unit + G2[i] * delta_front;

    // 光阑：本段若穿过光阑平面则检查
    float ts = (z_stop - p[2]) / d[2], tz = (zi - p[2]) / d[2];
    if (ts > 0.0 && ts < tz) {{
      float qx = p[0] + ts * d[0], qy = p[1] + ts * d[1];
      if (blades >= 3) {{
        float bx = ca * qx + sa * qy, by = -sa * qx + ca * qy;
        float seg = 2.0 * M_PI / blades;
        float ang = atan2(by, bx);
        float m = ang - seg * floor(ang / seg) - 0.5 * seg;
        if (hypot(bx, by) * cos(m) > r_stop * cos(0.5 * seg)) {{ throughput = color(0.0); return; }}
      }} else if (qx * qx + qy * qy > r_stop * r_stop) {{ throughput = color(0.0); return; }}
    }}

    // 求交：平面 / 球面 / 纯柱面用解析解（顶点平面上的二次方程 c(x²+y²+w²) + 2w = 0，取靠近顶点的根，数值稳定）；
    // 带圆锥或非球面的面用牛顿
    float t = tz;
    float gx = 0, gy = 0;
    int ok = 1;
    if (AT[i]) {{
      float x0 = p[0] + tz * d[0], y0 = p[1] + tz * d[1];
      float c_ = AC[i], mx = AMX[i], my = AMY[i];
      float qa = c_ * (mx * d[0] * d[0] + my * d[1] * d[1] + d[2] * d[2]);
      float qb = 2.0 * (c_ * (mx * x0 * d[0] + my * y0 * d[1]) + d[2]);
      float qc = c_ * (mx * x0 * x0 + my * y0 * y0);
      float disc = qb * qb - 4.0 * qa * qc;
      if (disc < 0.0) {{ throughput = color(0.0); return; }}
      float den = qb + (qb >= 0.0 ? 1.0 : -1.0) * sqrt(disc);
      t = tz + (den != 0.0 ? -2.0 * qc / den : 0.0);
    }}
    float cxi = CX[i] + KCX[i] * comp_phi, cyi = CY[i] + KCY[i] * comp_phi;
    for (int it = 0; it < (AT[i] ? 0 : 12); ++it) {{
      float x = p[0] + t * d[0], y = p[1] + t * d[1];
      float sg = sag_grad(x, y, cxi, cyi, KX[i], KY[i], AA4[i], AA6[i], AA8[i], AA10[i], AA12[i], gx, gy, ok);
      if (!ok) {{ throughput = color(0.0); return; }}
      float g = p[2] + t * d[2] - zi + sg;
      float dg = d[2] + gx * d[0] + gy * d[1];
      float dt = g / dg;
      t -= dt;
      if (fabs(dt) < 1e-7) break;
    }}
    if (t <= 1e-6) {{ throughput = color(0.0); return; }}
    p = p + t * d;
    sag_grad(p[0], p[1], cxi, cyi, KX[i], KY[i], AA4[i], AA6[i], AA8[i], AA10[i], AA12[i], gx, gy, ok);
    if (!ok) {{ throughput = color(0.0); return; }}

    // 口径：圆形通光区 + x/y 遮罩（矩形镜片、切边、矩形视场光阑）；平台与镜筒都按挡光处理
    if (p[0] * p[0] + p[1] * p[1] > CLR[i] * CLR[i] || fabs(p[0]) > MSX[i] || fabs(p[1]) > MSY[i]) {{
      throughput = color(0.0); return;
    }}

    // 两侧介质：沿 +Z 从「面后」到「面前」，沿 −Z 反之
    float na_ = ND[i] + dispersion_strength * (NA[i] + il2 * (NB[i] + il2 * (NC[i] + il2 * NE[i])) - ND[i]);
    float nb_ = (i > 0) ? ND[i - 1] + dispersion_strength * (NA[i - 1] + il2 * (NB[i - 1] + il2 * (NC[i - 1] + il2 * NE[i - 1])) - ND[i - 1]) : 1.0;
    float n1 = dirn == 1 ? na_ : nb_;
    float n2 = dirn == 1 ? nb_ : na_;
    vector nrm = normalize(vector(gx, gy, 1.0));
    float c = -dot(nrm, d);
    if (c < 0.0) {{ nrm = -nrm; c = -c; }}

    color R = color(0.0);
    if (ghosts) {{
      if (CO[i] && coated && coating_nm > 0.0) {{
        float ng = max(ND[i], i > 0 ? ND[i - 1] : 1.0);
        if (dispersion && dispersion_strength > 0.0) {{
          R = color(coat_R(n1, n2, c, lam, coating_type, coating_nm, coating_ior, ng));   // 光谱采样：反射率按本条光线的波长
        }} else {{
          float iR = 1.0 / (LR * LR), iB = 1.0 / (LB * LB);
          float naR = NA[i] + iR * (NB[i] + iR * (NC[i] + iR * NE[i])), naB = NA[i] + iB * (NB[i] + iB * (NC[i] + iB * NE[i]));
          float nbR = (i > 0) ? NA[i - 1] + iR * (NB[i - 1] + iR * (NC[i - 1] + iR * NE[i - 1])) : 1.0;
          float nbB = (i > 0) ? NA[i - 1] + iB * (NB[i - 1] + iB * (NC[i - 1] + iB * NE[i - 1])) : 1.0;
          float n1R = dirn == 1 ? naR : nbR, n2R = dirn == 1 ? nbR : naR;
          float n1B = dirn == 1 ? naB : nbB, n2B = dirn == 1 ? nbB : naB;
          R = color(coat_R(n1R, n2R, c, LR, coating_type, coating_nm, coating_ior, ng),
                    coat_R(n1, n2, c, LG, coating_type, coating_nm, coating_ior, ng),
                    coat_R(n1B, n2B, c, LB, coating_type, coating_nm, coating_ior, ng));
        }}
      }} else {{
        R = color(fresnel_plain(n1, n2, c));
      }}
    }}

    int refl = (state == 0 && i == ga) || (state == 1 && i == gb);
    if (refl) {{
      d = normalize(d + 2.0 * c * nrm);
      T *= R;
      state += 1;
      dirn = -dirn;
    }} else {{
      float eta = n1 / n2;
      float kk = 1.0 - eta * eta * (1.0 - c * c);
      if (kk < 0.0) {{ throughput = color(0.0); return; }}
      d = normalize(eta * d + (eta * c - sqrt(kk)) * nrm);
      if (ghosts) T *= color(1.0) - R;
    }}
    i -= dirn;
  }}
  if (i != -1 || dirn != 1 || state != (ga >= 0 ? 2 : 0)) {{ throughput = color(0.0); return; }}

  position = point(-p[0], p[1], p[2]) * unit_scale;
  direction = vector(-d[0], d[1], d[2]);
  throughput = color(w * wsel) * T * chan;
}}
"""
    return src
