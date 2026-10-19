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

def _upsample(field, nx, ny, W_px, H_px, cols=None, rows=None):
    """粗网格 → 像素中心（双线性）。网格覆盖传感器边到边。cols / rows：只算这些列 / 行（像素下标数组）。"""
    gx = ((np.arange(W_px) if cols is None else cols) + 0.5) / W_px * (nx - 1)
    gy = ((np.arange(H_px) if rows is None else rows) + 0.5) / H_px * (ny - 1)
    i0 = np.clip(np.floor(gx).astype(int), 0, nx - 2); tx = gx - i0
    j0 = np.clip(np.floor(gy).astype(int), 0, ny - 2); ty = gy - j0
    f = field
    a = f[j0][:, i0] * (1 - tx)[None, :] + f[j0][:, i0 + 1] * tx[None, :]
    b = f[j0 + 1][:, i0] * (1 - tx)[None, :] + f[j0 + 1][:, i0 + 1] * tx[None, :]
    return a * (1 - ty)[:, None] + b * ty[:, None]

def _bilinear(img, u, v):
    """img[H, W, C]，u/v 为像素坐标（列 / 行，像素中心 = 整数），边界夹紧。"""
    H, W = img.shape[:2]
    u = np.clip(u, 0, W - 1); v = np.clip(v, 0, H - 1)
    i0 = np.minimum(np.floor(u).astype(int), W - 2); j0 = np.minimum(np.floor(v).astype(int), H - 2)   # float32 时 W − 1.000001 会舍入成 W − 1
    fu = (u - i0)[..., None]; fv = (v - j0)[..., None]
    return ((img[j0, i0] * (1 - fu) + img[j0, i0 + 1] * fu) * (1 - fv) +
            (img[j0 + 1, i0] * (1 - fu) + img[j0 + 1, i0 + 1] * fu) * fv)

class LazyMaps(dict):
    """与 pixel_maps 同样的映射，但只存粗网格位移，取某个波长时才放大到像素（float32）。
    4K 下完整的 11 个波长 × (u, v) 映射在 float64 时约 1.5 GB；这样常驻内存只有几十 KB。"""

    def __init__(self, rx, W_px, H_px, focus_dist=math.inf, lambdas_nm=LAMBDAS_NM, nx=33, ny=19, crop=None):
        super().__init__()
        self.L = lateral_ca_offsets(rx, focus_dist, nx, ny, lambdas_nm)
        self.W_px, self.H_px, self.nx, self.ny = W_px, H_px, nx, ny
        self.crop = crop                                   # (x0, y0_top, w, h)：只取整幅里的这一块
        self._keys = list(lambdas_nm)

    def keys(self):
        return list(self._keys)

    def __contains__(self, k):
        return k in self._keys

    def __getitem__(self, nm):
        W, H = self.L["sensor"]
        dx, dy = self.L["offsets"][nm]
        u = (np.arange(self.W_px, dtype=np.float32)[None, :] + 0.0 * np.arange(self.H_px, dtype=np.float32)[:, None]
             + (_upsample(dx[::-1], self.nx, self.ny, self.W_px, self.H_px) / W * self.W_px).astype(np.float32))
        v = (np.arange(self.H_px, dtype=np.float32)[:, None] + 0.0 * np.arange(self.W_px, dtype=np.float32)[None, :]
             - (_upsample(dy[::-1], self.nx, self.ny, self.W_px, self.H_px) / H * self.H_px).astype(np.float32))
        if self.crop:
            x0, y0, w, h = self.crop
            return u[y0:y0 + h, x0:x0 + w] - x0, v[y0:y0 + h, x0:x0 + w] - y0
        return u, v

    def rows(self, nm, r0, r1):
        """只取输出的第 r0…r1−1 行（有裁切时是裁切块里的行）→ (u, v)，各 (r1−r0)×w，float32。多线程分块用。"""
        W, H = self.L["sensor"]
        dx, dy = self.L["offsets"][nm]
        x0, y0, w, _ = self.crop or (0, 0, self.W_px, self.H_px)
        cols = np.arange(x0, x0 + w); rws = np.arange(y0 + r0, y0 + r1)
        u = (cols.astype(np.float32)[None, :] - x0
             + (_upsample(dx[::-1], self.nx, self.ny, self.W_px, self.H_px, cols, rws) / W * self.W_px).astype(np.float32))
        v = (rws.astype(np.float32)[:, None] - y0
             - (_upsample(dy[::-1], self.nx, self.ny, self.W_px, self.H_px, cols, rws) / H * self.H_px).astype(np.float32))
        return u, v

    def items(self):
        return ((k, self[k]) for k in self._keys)

    def __bool__(self):                                  # dict 子类：没存条目也不能算「空」
        return True

    def __len__(self):
        return len(self._keys)


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

def apply_lateral_ca_planes(planes, maps, lambdas_nm=LAMBDAS_NM, w_min=2e-3):
    """多张同尺寸图像一起加横向色差：每个波长的采样位置与双线性权重只算一次，用于所有图像；
    每个通道只取权重 ≥ w_min 的波长（重新归一化，亮度守恒）。0.8.3：4K 七个颜色层从 97 s 降到十几秒。
    planes：每张图一个 [R, G, B]（各 H×W，行 0 = 顶部，float32 或 float16，不复制）。
    maps：pixel_maps / LazyMaps（键 = 波长 nm，值 = (u, v)）。返回同结构的新平面（float32）。
    0.8.4：直接按平面处理，内存峰值 = 输入 + 输出（以前另有拼 / 拆 RGB 的两份拷贝）。"""
    if not planes:
        return []
    H, W = planes[0][0].shape
    wts = _channel_weights(lambdas_nm)
    keep = wts >= w_min
    wts = np.where(keep, wts, 0.0)
    wts = wts / wts.sum(axis=1, keepdims=True)
    from concurrent.futures import ThreadPoolExecutor
    flat = [[pl.reshape(-1) for pl in img] for img in planes]
    outs = [[np.zeros(H * W, np.float32) for _ in range(3)] for _ in planes]
    used = [(k, nm, [c for c in range(3) if keep[c, k]]) for k, nm in enumerate(lambdas_nm)]
    used = [x for x in used if x[2]]

    def rows(r0, r1):                                    # 一段行：所有波长、所有图像（numpy 取数 / 运算会释放 GIL → 多线程）
        a0, a1 = r0 * W, r1 * W
        for k, nm, chans in used:
            if hasattr(maps, "rows"):
                u, v = maps.rows(nm, r0, r1)
            else:
                u, v = maps[nm]; u, v = u[r0:r1], v[r0:r1]
            uu = np.clip(np.asarray(u, np.float32).reshape(-1), 0, W - 1); vv = np.clip(np.asarray(v, np.float32).reshape(-1), 0, H - 1)
            i0 = np.minimum(uu.astype(np.int32), W - 2); j0 = np.minimum(vv.astype(np.int32), H - 2)
            fu = uu - i0; fv = vv - j0
            idx = j0 * W + i0
            w00 = (1 - fu) * (1 - fv); w01 = fu * (1 - fv); w10 = (1 - fu) * fv; w11 = fu * fv
            for pl, out in zip(flat, outs):
                for c in chans:
                    s = pl[c]
                    out[c][a0:a1] += np.float32(wts[c, k]) * (s[idx] * w00 + s[idx + 1] * w01 + s[idx + W] * w10 + s[idx + W + 1] * w11)

    step = max(16, (1 << 17) // W)
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        list(ex.map(lambda r: rows(r, min(r + step, H)), range(0, H, step)))
    return [[o.reshape(H, W) for o in out] for out in outs]


def apply_lateral_ca_many(imgs, maps, lambdas_nm=LAMBDAS_NM, w_min=2e-3):
    """同 apply_lateral_ca_planes，输入输出是 H×W×3 图像（float32）。"""
    planes = [[np.ascontiguousarray(im[..., c], dtype=np.float32) for c in range(3)] for im in imgs]
    return [np.stack(o, axis=-1) for o in apply_lateral_ca_planes(planes, maps, lambdas_nm, w_min)]


def apply_lateral_ca(img, rx, focus_dist=math.inf, lambdas_nm=LAMBDAS_NM, strength=1.0, maps=None):
    """对关色散渲染的线性图像（H×W×3，行 0 = 顶部）加上横向色差。每个通道 = Σ_λ w_c(λ) · d 线图像在 map_λ 处的值。
    白色（中性）且无色差的区域保持不变；亮度守恒（每通道权重和为 1）。"""
    H_px, W_px = img.shape[:2]
    maps = maps if maps is not None else pixel_maps(rx, W_px, H_px, focus_dist, lambdas_nm)
    if strength != 1.0:
        base_u = np.arange(W_px)[None, :] + 0.0 * np.arange(H_px)[:, None]
        base_v = np.arange(H_px)[:, None] + 0.0 * np.arange(W_px)[None, :]
        maps = {nm: (base_u + strength * (maps[nm][0] - base_u), base_v + strength * (maps[nm][1] - base_v)) for nm in lambdas_nm}
    return apply_lateral_ca_many([img], maps, lambdas_nm)[0].astype(img.dtype, copy=False)

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


class LensProjector:
    """3D 点（镜头坐标 mm，物方 +Z）→ 传感器点（mm）：求主光线（过出瞳中心、d 线）穿过该点的传感器位置。
    主光线在深度 z 处的横向位置 = A(s) + z·B(s)（A、B 在传感器上平滑），先在网格上追迹 A、B，
    之后每个点只做向量化的二维牛顿（双线性插值 + 解析雅可比），不再逐像素追迹。0.8.3：BL_Vector（运动矢量）用。"""

    def __init__(self, rx, focus_dist=math.inf, nx=161, ny=121, margin=1.25):
        M = LensMap(rx, focus_dist)
        self.W, self.H = M.W, M.H
        self.gx = np.linspace(-margin * M.W / 2, margin * M.W / 2, nx)
        self.gy = np.linspace(-margin * M.H / 2, margin * M.H / 2, ny)
        SX, SY = np.meshgrid(self.gx, self.gy)                       # [iy, ix]
        n = SX.size
        P = np.column_stack([SX.ravel(), SY.ravel(), np.zeros(n)])
        D = np.column_stack([-SX.ravel(), -SY.ravel(), np.full(n, M.z_exp)])
        D /= np.linalg.norm(D, axis=1)[:, None]
        Po, Do = _trace_out(M.S, M.z_stop, M.r_stop, P, D, O.LAMBDA_D)
        tx, ty = Do[:, 0] / Do[:, 2], Do[:, 1] / Do[:, 2]
        self.Ax = (Po[:, 0] - Po[:, 2] * tx).reshape(SX.shape); self.Bx = tx.reshape(SX.shape)
        self.Ay = (Po[:, 1] - Po[:, 2] * ty).reshape(SX.shape); self.By = ty.reshape(SX.shape)
        self.ok = np.isfinite(self.Ax).all() and np.isfinite(self.Ay).all()

    def _interp(self, F, sx, sy):
        fx = (sx - self.gx[0]) / (self.gx[1] - self.gx[0]); fy = (sy - self.gy[0]) / (self.gy[1] - self.gy[0])
        ix = np.clip(np.floor(fx).astype(int), 0, len(self.gx) - 2); iy = np.clip(np.floor(fy).astype(int), 0, len(self.gy) - 2)
        tx, ty = fx - ix, fy - iy
        f00, f10, f01, f11 = F[iy, ix], F[iy, ix + 1], F[iy + 1, ix], F[iy + 1, ix + 1]
        v = (f00 * (1 - tx) + f10 * tx) * (1 - ty) + (f01 * (1 - tx) + f11 * tx) * ty
        dx = ((f10 - f00) * (1 - ty) + (f11 - f01) * ty) / (self.gx[1] - self.gx[0])
        dy = ((f01 - f00) * (1 - tx) + (f11 - f10) * tx) / (self.gy[1] - self.gy[0])
        return v, dx, dy

    def project(self, Q, s0=None, iters=6):
        """Q：N×3（镜头坐标 mm）。返回 (sx, sy, valid)。s0：初值（如当前像素的传感器坐标），缺省用小孔近似。"""
        Q = np.asarray(Q, np.float64)
        z = Q[:, 2]
        if s0 is None:
            bx = self._interp(self.Bx, np.zeros(1), np.zeros(1))[1][0]
            sx = np.zeros(len(Q)); sy = np.zeros(len(Q))
        else:
            sx, sy = (np.array(s0[0], np.float64), np.array(s0[1], np.float64))
        for _ in range(iters):
            ax, axx, axy = self._interp(self.Ax, sx, sy); bx_, bxx, bxy = self._interp(self.Bx, sx, sy)
            ay, ayx, ayy = self._interp(self.Ay, sx, sy); by_, byx, byy = self._interp(self.By, sx, sy)
            fx = ax + z * bx_ - Q[:, 0]; fy = ay + z * by_ - Q[:, 1]
            j11, j12 = axx + z * bxx, axy + z * bxy
            j21, j22 = ayx + z * byx, ayy + z * byy
            det = j11 * j22 - j12 * j21
            det = np.where(np.abs(det) < 1e-12, 1e-12, det)
            sx = sx - (j22 * fx - j12 * fy) / det
            sy = sy - (-j21 * fx + j11 * fy) / det
            sx = np.clip(sx, self.gx[0], self.gx[-1]); sy = np.clip(sy, self.gy[0], self.gy[-1])
        ax = self._interp(self.Ax, sx, sy)[0] + z * self._interp(self.Bx, sx, sy)[0]
        ay = self._interp(self.Ay, sx, sy)[0] + z * self._interp(self.By, sx, sy)[0]
        resid = np.hypot(ax - Q[:, 0], ay - Q[:, 1])
        valid = (z > 0) & (resid < 1e-3 * np.maximum(1.0, np.abs(z)) )
        return sx, sy, valid
