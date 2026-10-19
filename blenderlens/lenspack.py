# 镜头包（纯 numpy）：横向色差的每波长 ST map、渐晕图、压缩比 / 呼吸信息。
# 用途：
#   · 色散模式 LATERAL_POST：渲染时关掉色散（d 线，单波长，没有色噪），横向色差（边缘彩边）由这里的映射在后期精确加上；
#     纵向色差（焦外紫 / 绿边）不在其中 —— 那需要按波长采样（FULL 模式）。
#   · 导出给 Nuke / Resolve：stmap_<λ>.exr、stmap_R/G/B.exr（通道等效）、vignette.exr、lens.json。
# 横向色差的定义：传感器点 s 上、波长 λ 的光，来自物面上的哪一点 —— 取从 s 射向出瞳中心的那条光线
# （近似整束光的质心）。d 线渲染里同一物点的位置 = O_d⁻¹(O_λ(s))，即 λ 通道在 s 处应取 d 线图像的那一点。
import json, math, os
import numpy as np

try:
    from . import optics as O
    from . import oslgen as G
    from . import spectral as SP
    from .pupil import _passes
    from .flare import _sag_grad
except ImportError:
    import optics as O
    import oslgen as G
    import spectral as SP
    from pupil import _passes
    from flare import _sag_grad

LAMBDAS_NM = (400, 430, 460, 490, 520, 550, 580, 610, 640, 670, 700)


def _trace_out(S, z_stop, r_stop, P, D, lam):
    """与 pupil._passes 相同的逐面追迹，但返回物方的点和方向（不检查口径 / 光阑：主光线附近的细光束）。"""
    P = P.copy(); D = D.copy()
    co = [O.dispersion_coeffs(sf["glass"]) for sf in S]
    il2 = 1.0 / (lam * lam)
    nidx = lambda k: 1.0 if k < 0 else co[k][0] + il2 * (co[k][1] + il2 * (co[k][2] + il2 * co[k][3]))
    for i in range(len(S) - 1, -1, -1):
        sf = S[i]
        cx, cy = O.curv(sf["Rx"]), O.curv(sf["Ry"])
        asph = sf.get("asph") or ()
        with np.errstate(all="ignore"):
            t = (sf["z"] - P[:, 2]) / D[:, 2]
            for _ in range(10):
                x = P[:, 0] + t * D[:, 0]; y = P[:, 1] + t * D[:, 1]
                s, gx, gy, ok = _sag_grad(x, y, cx, cy, sf["kx"], sf["ky"], asph)
                g = P[:, 2] + t * D[:, 2] - sf["z"] + s
                dg = D[:, 2] + gx * D[:, 0] + gy * D[:, 1]
                t = t - g / np.where(np.abs(dg) < 1e-12, 1e-12, dg)
            P = P + t[:, None] * D
            s, gx, gy, ok = _sag_grad(P[:, 0], P[:, 1], cx, cy, sf["kx"], sf["ky"], asph)
            L = np.sqrt(gx * gx + gy * gy + 1.0)
            N = np.stack([gx / L, gy / L, 1.0 / L], axis=1)
            c = -(N * D).sum(axis=1)
            N[c < 0] *= -1; c = np.abs(c)
            eta = nidx(i) / nidx(i - 1)
            kk = 1.0 - eta * eta * (1.0 - c * c)
            D = eta * D + (eta * c - np.sqrt(np.clip(kk, 0, None)))[:, None] * N
            D = D / np.linalg.norm(D, axis=1)[:, None]
    return P, D


class LensMap:
    """一次对焦状态下：传感器点 → 物面点（对焦面；无穷远时用方向），按波长。"""

    def __init__(self, rx, focus_dist=math.inf):
        self.rx = rx
        d1, d2 = O.solve_focus(rx, focus_dist)
        self.S = O.layout(rx, d1, d2)
        z_exp, r_exp, z_stop, r_stop = G.exit_pupil(rx)
        st_id = O.stop_group(rx)["stop"]["after_surface"]
        mv = next(sf["move"] for sf in self.S if sf["id"] == st_id)
        sh = mv[0] * d1 + mv[1] * d2
        self.z_exp, self.z_stop, self.r_stop = z_exp + sh, z_stop + sh, r_stop
        self.L = focus_dist if math.isfinite(focus_dist) else None
        self.W, self.H = rx["sensor"]["width"], rx["sensor"]["height"]

    def obj(self, sx, sy, lam_um):
        """sx, sy：传感器坐标（mm，镜头坐标）。返回物面坐标（有限对焦）或方向切线（无穷远）。"""
        n = sx.size
        P = np.column_stack([sx.ravel(), sy.ravel(), np.zeros(n)])
        D = np.column_stack([-sx.ravel(), -sy.ravel(), np.full(n, self.z_exp)])
        D /= np.linalg.norm(D, axis=1)[:, None]
        Po, Do = _trace_out(self.S, self.z_stop, self.r_stop, P, D, lam_um)
        if self.L is None:
            return (Do[:, 0] / Do[:, 2]).reshape(sx.shape), (Do[:, 1] / Do[:, 2]).reshape(sx.shape)
        t = (self.L - Po[:, 2]) / Do[:, 2]
        return (Po[:, 0] + t * Do[:, 0]).reshape(sx.shape), (Po[:, 1] + t * Do[:, 1]).reshape(sx.shape)

    def inverse_d(self, X, Y, s0x, s0y, iters=6):
        """求 d 线下成像到物面点 (X, Y) 的传感器点（牛顿，有限差分雅可比，向量化）。"""
        sx, sy = s0x.copy(), s0y.copy()
        h = 1e-3 * max(self.W, self.H)
        for _ in range(iters):
            fx, fy = self.obj(sx, sy, O.LAMBDA_D)
            ax, ay = self.obj(sx + h, sy, O.LAMBDA_D)
            bx, by = self.obj(sx, sy + h, O.LAMBDA_D)
            j11, j21 = (ax - fx) / h, (ay - fy) / h
            j12, j22 = (bx - fx) / h, (by - fy) / h
            det = j11 * j22 - j12 * j21
            rx_, ry_ = X - fx, Y - fy
            sx = sx + (j22 * rx_ - j12 * ry_) / det
            sy = sy + (-j21 * rx_ + j11 * ry_) / det
        return sx, sy


def lateral_ca_offsets(rx, focus_dist=math.inf, nx=33, ny=19, lambdas_nm=LAMBDAS_NM):
    """粗网格上的横向色差位移：dict λ(nm) → (dsx, dsy)，单位 mm（传感器、镜头坐标），
    含义：λ 通道在传感器点 s 处应取 d 线图像在 s + (dsx, dsy) 处的值。"""
    M = LensMap(rx, focus_dist)
    sx, sy = np.meshgrid(np.linspace(-M.W / 2, M.W / 2, nx), np.linspace(-M.H / 2, M.H / 2, ny))
    out = {}
    for nm in lambdas_nm:
        X, Y = M.obj(sx, sy, nm / 1000.0)
        ux, uy = M.inverse_d(X, Y, sx, sy)
        out[nm] = (ux - sx, uy - sy)
    return dict(grid=(sx, sy), offsets=out, sensor=(M.W, M.H))

def _channel_weights(lambdas_nm, M=None):
    """各采样波长对 R/G/B 的权重（与 OSL / GPU 同一传感器响应；每通道归一化）。"""
    w = np.array([[SP.sensor_rgb(nm / 1000.0)[r] for nm in lambdas_nm] for r in range(3)])
    return w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)

def _upsample(field, nx, ny, W_px, H_px):
    """粗网格 → 像素中心（双线性）。网格覆盖传感器边到边。"""
    gx = (np.arange(W_px) + 0.5) / W_px * (nx - 1)
    gy = (np.arange(H_px) + 0.5) / H_px * (ny - 1)
    i0 = np.clip(np.floor(gx).astype(int), 0, nx - 2); tx = gx - i0
    j0 = np.clip(np.floor(gy).astype(int), 0, ny - 2); ty = gy - j0
    f = field
    a = f[j0][:, i0] * (1 - tx)[None, :] + f[j0][:, i0 + 1] * tx[None, :]
    b = f[j0 + 1][:, i0] * (1 - tx)[None, :] + f[j0 + 1][:, i0 + 1] * tx[None, :]
    return a * (1 - ty)[:, None] + b * ty[:, None]

def _bilinear(img, u, v):
    """img[H, W, C]，u/v 为像素坐标（列 / 行，像素中心 = 整数），边界夹紧。"""
    H, W = img.shape[:2]
    u = np.clip(u, 0, W - 1.000001); v = np.clip(v, 0, H - 1.000001)
    i0 = np.floor(u).astype(int); j0 = np.floor(v).astype(int)
    fu = (u - i0)[..., None]; fv = (v - j0)[..., None]
    return ((img[j0, i0] * (1 - fu) + img[j0, i0 + 1] * fu) * (1 - fv) +
            (img[j0 + 1, i0] * (1 - fu) + img[j0 + 1, i0 + 1] * fu) * fv)

def pixel_maps(rx, W_px, H_px, focus_dist=math.inf, lambdas_nm=LAMBDAS_NM, nx=33, ny=19):
    """每个波长的源像素坐标（u, v）：输出像素 → 在 d 线渲染图里取值的位置。图像行 0 = 顶部。
    传感器 → 画面：画面向右 = 传感器 +x，向上 = 传感器 −y（与 OSL 相机一致）。"""
    L = lateral_ca_offsets(rx, focus_dist, nx, ny, lambdas_nm)
    W, H = L["sensor"]
    maps = {}
    base_u = np.arange(W_px)[None, :] + 0.0 * np.arange(H_px)[:, None]
    base_v = np.arange(H_px)[:, None] + 0.0 * np.arange(W_px)[None, :]
    for nm, (dx, dy) in L["offsets"].items():
        # 粗网格的行 0 是 sy = −H/2（画面底部）→ 翻成画面行 0 = 顶部
        dxp = _upsample(dx[::-1], nx, ny, W_px, H_px) / W * W_px
        dyp = -_upsample(dy[::-1], nx, ny, W_px, H_px) / H * H_px
        maps[nm] = (base_u + dxp, base_v + dyp)
    return maps

def apply_lateral_ca(img, rx, focus_dist=math.inf, lambdas_nm=LAMBDAS_NM, strength=1.0, maps=None):
    """对关色散渲染的线性图像（H×W×3，行 0 = 顶部）加上横向色差。每个通道 = Σ_λ w_c(λ) · d 线图像在 map_λ 处的值。
    白色（中性）且无色差的区域保持不变；亮度守恒（每通道权重和为 1）。"""
    H_px, W_px = img.shape[:2]
    maps = maps or pixel_maps(rx, W_px, H_px, focus_dist, lambdas_nm)
    wts = _channel_weights(lambdas_nm)
    out = np.zeros_like(img)
    base_u = np.arange(W_px)[None, :] + 0.0 * np.arange(H_px)[:, None]
    base_v = np.arange(H_px)[:, None] + 0.0 * np.arange(W_px)[None, :]
    for k, nm in enumerate(lambdas_nm):
        u, v = maps[nm]
        if strength != 1.0:
            u = base_u + strength * (u - base_u); v = base_v + strength * (v - base_v)
        s = _bilinear(img, u, v)
        for c in range(3):
            out[..., c] += wts[c, k] * s[..., c]
    return out

def vignette_map(rx, W_px, H_px, f_number=None, focus_dist=math.inf, nx=17, ny=11, n=21):
    """相对照度（光瞳通过率 × cos⁴，中心归一化），H×W，行 0 = 顶部。"""
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    S = O.layout(rx, *O.solve_focus(rx, focus_dist))
    z_exp = G.exit_pupil(rx)[0]
    vals = np.zeros((ny, nx))
    for j, sy in enumerate(np.linspace(-H / 2, H / 2, ny)):
        for i, sx in enumerate(np.linspace(-W / 2, W / 2, nx)):
            if i > nx // 2 or j > ny // 2:
                continue
            t = O.pupil_transmission(rx, (sx, sy), f_number=f_number, focus_dist=focus_dist, n=n)
            c = z_exp / math.sqrt(z_exp * z_exp + sx * sx + sy * sy)
            vals[j, i] = t * c ** 4
    vals[:, nx // 2 + 1:] = vals[:, :nx // 2][:, ::-1]
    vals[ny // 2 + 1:, :] = vals[:ny // 2, :][::-1]
    vals /= vals[ny // 2, nx // 2]
    return _upsample(vals[::-1], nx, ny, W_px, H_px)

def stmaps(rx, W_px, H_px, focus_dist=math.inf, lambdas_nm=LAMBDAS_NM):
    """Nuke 习惯的 ST map（0–1，原点左下）：每波长一张，外加 R/G/B 通道等效（按光谱权重平均位置）。"""
    maps = pixel_maps(rx, W_px, H_px, focus_dist, lambdas_nm)
    wts = _channel_weights(lambdas_nm)
    st = {}
    for nm, (u, v) in maps.items():
        st[nm] = np.stack([(u + 0.5) / W_px, 1.0 - (v + 0.5) / H_px], axis=-1)
    for c, name in enumerate("RGB"):
        u = sum(wts[c, k] * maps[nm][0] for k, nm in enumerate(lambdas_nm))
        v = sum(wts[c, k] * maps[nm][1] for k, nm in enumerate(lambdas_nm))
        st[name] = np.stack([(u + 0.5) / W_px, 1.0 - (v + 0.5) / H_px], axis=-1)
    return st

def lens_info(rx, focus_dists=(math.inf,)):
    """压缩比、两向焦距（呼吸）随对焦距离的变化。"""
    rows = []
    for fd in focus_dists:
        S = O.layout(rx, *O.solve_focus(rx, fd))
        rows.append(dict(focus_mm=None if not math.isfinite(fd) else fd, squeeze=O.squeeze_at(rx, fd),
                         efl_x=O.paraxial_infinity(S, "Rx")[0], efl_y=O.paraxial_infinity(S, "Ry")[0]))
    return dict(name=rx["meta"]["name"], sensor_mm=[rx["sensor"]["width"], rx["sensor"]["height"]],
                desqueeze=O.desqueeze(rx), by_focus=rows)
