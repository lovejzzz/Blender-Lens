# 炫光通道：从光源正向追迹光线网格穿过镜头，按「两次反射」的所有面对累加到传感器（Hullin 式序列鬼影）。
# 确定性、无噪点；面形 / 口径 / 光阑 / 薄膜公式与 OSL 相机一致。只依赖 numpy（Blender 自带）。
# 坐标：镜头坐标（原点像面，物方 +Z）；输出图像与 OSL 相机同一映射（画面向右 = 传感器 +x，向上 = 传感器 −y）。
import math, os
import numpy as np

try:
    from . import optics as O
except ImportError:
    import optics as O

LAMBDAS = (0.610, 0.550, 0.465)          # R / G / B（μm），与 OSL 相机一致


def _sag_grad(x, y, cx, cy, kx, ky, asph=()):
    N = cx * x * x + cy * y * y
    Q = 1.0 - (1 + kx) * cx * cx * x * x - (1 + ky) * cy * cy * y * y
    ok = Q > 0.0
    Sq = np.sqrt(np.where(ok, Q, 1.0))
    den = 1.0 + Sq
    dSx = -(1 + kx) * cx * cx * x / Sq
    dSy = -(1 + ky) * cy * cy * y / Sq
    gx = (2 * cx * x * den - N * dSx) / (den * den)
    gy = (2 * cy * y * den - N * dSy) / (den * den)
    s = N / den
    if asph:
        r2 = x * x + y * y
        p = r2
        for m, a in enumerate(asph, start=2):
            s = s + a * p * r2
            g = a * 2 * m * p
            gx = gx + g * x; gy = gy + g * y
            p = p * r2
    return s, gx, gy, ok

def _fresnel_plain(n1, n2, ci):
    st = n1 / n2 * np.sqrt(np.clip(1.0 - ci * ci, 0, None))
    tir = st >= 1.0
    ct = np.sqrt(np.clip(1.0 - st * st, 0, None))
    rs = (n1 * ci - n2 * ct) / (n1 * ci + n2 * ct)
    rp = (n2 * ci - n1 * ct) / (n2 * ci + n1 * ct)
    return np.where(tir, 1.0, 0.5 * (rs * rs + rp * rp))

def _airy(a, b, cd):
    return (a * a + b * b + 2 * a * b * cd) / (1 + a * a * b * b + 2 * a * b * cd)

def _fresnel_film(n0, nf, n2, c0, d_nm, lam_um):
    s0 = np.sqrt(np.clip(1.0 - c0 * c0, 0, None))
    s1 = n0 * s0 / nf
    s2 = n0 * s0 / n2
    film_tir = s1 >= 1.0
    tir = s2 >= 1.0
    c1 = np.sqrt(np.clip(1.0 - s1 * s1, 1e-12, None))
    c2 = np.sqrt(np.clip(1.0 - s2 * s2, 1e-12, None))
    cd = np.cos(4.0 * math.pi * nf * d_nm * c1 / (lam_um * 1000.0))
    rs01 = (n0 * c0 - nf * c1) / (n0 * c0 + nf * c1); rs12 = (nf * c1 - n2 * c2) / (nf * c1 + n2 * c2)
    rp01 = (nf * c0 - n0 * c1) / (nf * c0 + n0 * c1); rp12 = (n2 * c1 - nf * c2) / (n2 * c1 + nf * c2)
    R = 0.5 * (_airy(rs01, rs12, cd) + _airy(rp01, rp12, cd))
    return np.where(tir, 1.0, np.where(film_tir, _fresnel_plain(n0, n2, c0), R))


class Lens:
    """一次对焦 / 光圈状态下的镜头（数组化，供正向追迹）。"""

    def __init__(self, rx, focus_dist=math.inf, f_number=None):
        d1, d2 = O.solve_focus(rx, focus_dist)
        self.S = O.layout(rx, d1, d2)
        z_stop, r_stop = O.stop_geometry(rx, self.S)
        N0 = O.nominal_f_number(rx)
        self.z_stop = z_stop
        self.r_stop = r_stop * (N0 / (f_number or N0))
        self.sensor = (rx["sensor"]["width"], rx["sensor"]["height"])
        nb = [1.0] + [sf["glass"]["nd"] for sf in self.S[:-1]]
        self.coatable = [(nb[k] == 1.0) != (sf["glass"]["nd"] == 1.0) for k, sf in enumerate(self.S)]
        self.AG = [k for k, c in enumerate(self.coatable) if c]
        self.n_glass = [max(nb[k], sf["glass"]["nd"]) for k, sf in enumerate(self.S)]   # 镀膜面玻璃一侧的 n_d（宽带膜贴玻璃层按它配）
        self.cauchy = [O.dispersion_coeffs(sf["glass"]) for sf in self.S]

    def n_after(self, k, lam):
        A, B, C, E = self.cauchy[k]
        il2 = 1.0 / (lam * lam)
        return A + il2 * (B + il2 * (C + il2 * E))

    def n_before(self, k, lam):
        return 1.0 if k == 0 else self.n_after(k - 1, lam)

    def pairs(self):
        return [(a, b) for i, a in enumerate(self.AG) for b in self.AG[i + 1:]]

    # ── 单条路径（向量化）：seq = [(面序号, 'T'|'R'), …]，光沿 −Z 从物方出发 ──
    def trace(self, P, D, seq, lam, coated, coating_nm, coating_ior, coating_type="SINGLE", state=None, record=None):
        """返回 (idx, X, Y, w, Dout)：存活光线在输入中的下标、像面落点、权重、最终方向。
        每过一面就剔除被挡光线，ghost 路径大多很快被挡掉。
        0.8.4：state = 从某个中间状态 (idx, P, D, w, dirn) 接着追（P、D 不用给）；record = dict，记下每一面之前的状态
        （直射路径记下来，鬼影路径在第一次反射之前与它完全相同，直接接着追 → 省掉共同的前半段）。"""
        if state is not None:
            idx, P, D, w, dirn = state
        else:
            idx = np.arange(P.shape[0])
            w = np.ones(P.shape[0])
            dirn = -1                                      # −1：沿 −Z（面序号递增）
        for k, act in seq:
            if record is not None:
                record[k] = (idx, P, D, w, dirn)
            sf = self.S[k]
            cx, cy = O.curv(sf["Rx"]), O.curv(sf["Ry"])
            t = (sf["z"] - P[:, 2]) / D[:, 2]
            with np.errstate(all="ignore"):
                for it in range(8):
                    x = P[:, 0] + t * D[:, 0]; y = P[:, 1] + t * D[:, 1]
                    s, gx, gy, ok = _sag_grad(x, y, cx, cy, sf["kx"], sf["ky"], sf.get("asph") or ())
                    g = P[:, 2] + t * D[:, 2] - sf["z"] + s
                    dg = D[:, 2] + gx * D[:, 0] + gy * D[:, 1]
                    step = g / np.where(np.abs(dg) < 1e-12, 1e-12, dg)
                    t = t - step
                    if it >= 1 and not (np.abs(step) > 1e-10).any():    # 收敛就停（NaN 不算未收敛：那些光线会被剔除）
                        x = P[:, 0] + t * D[:, 0]; y = P[:, 1] + t * D[:, 1]
                        s, gx, gy, ok = _sag_grad(x, y, cx, cy, sf["kx"], sf["ky"], sf.get("asph") or ())
                        break
                Pn = P + t[:, None] * D
                keep = ok & (t > 1e-6) & np.isfinite(t)
                cross = (P[:, 2] - self.z_stop) * (Pn[:, 2] - self.z_stop) < 0
                ts = (self.z_stop - P[:, 2]) / D[:, 2]
                qx = P[:, 0] + ts * D[:, 0]; qy = P[:, 1] + ts * D[:, 1]
                keep &= ~cross | (qx * qx + qy * qy <= self.r_stop ** 2)
                a = sf["ap"]
                clr, msx, msy, _ = O.ap_limits(a)
                keep &= (Pn[:, 0] ** 2 + Pn[:, 1] ** 2 <= clr * clr) & (np.abs(Pn[:, 0]) <= msx) & (np.abs(Pn[:, 1]) <= msy)
            P, D, w, idx = Pn[keep], D[keep], w[keep], idx[keep]
            if P.shape[0] == 0:
                break
            s, gx, gy, ok = _sag_grad(P[:, 0], P[:, 1], cx, cy, sf["kx"], sf["ky"], sf.get("asph") or ())
            L = np.sqrt(gx * gx + gy * gy + 1.0)
            Nrm = np.stack([gx / L, gy / L, 1.0 / L], axis=1)
            c = -(Nrm * D).sum(axis=1)
            flip = c < 0
            Nrm[flip] *= -1; c = np.abs(c)
            na_, nb_ = self.n_after(k, lam), self.n_before(k, lam)
            n1, n2 = (nb_, na_) if dirn == -1 else (na_, nb_)
            with np.errstate(all="ignore"):
                if self.coatable[k] and coated and coating_nm > 0:
                    R = O.stack_reflectance(n1, n2, c, lam, O.coating_layers(coating_type, coating_nm, coating_ior, self.n_glass[k]))
                else:
                    R = _fresnel_plain(n1, n2, c)
            if act == 'R':
                D = D + 2.0 * c[:, None] * Nrm
                w = w * R
                dirn = -dirn
            else:
                eta = n1 / n2
                kk = 1.0 - eta * eta * (1.0 - c * c)
                ok = kk >= 0
                D = eta * D + (eta * c - np.sqrt(np.clip(kk, 0, None)))[:, None] * Nrm
                w = w * (1.0 - R)
                P, D, w, idx = P[ok], D[ok], w[ok], idx[ok]
            D = D / np.linalg.norm(D, axis=1)[:, None]
        ok = D[:, 2] < 0
        P, D, w, idx = P[ok], D[ok], w[ok], idx[ok]
        t = -P[:, 2] / D[:, 2]
        return idx, P[:, 0] + t * D[:, 0], P[:, 1] + t * D[:, 1], w, D

    @staticmethod
    def seq_direct(NS):
        return [(k, 'T') for k in range(NS)]

    @staticmethod
    def seq_ghost(NS, a, b):
        """物方 → … → b 反射 → 回到 a 反射 → … → 像面（a < b）。"""
        return ([(k, 'T') for k in range(b)] + [(b, 'R')] + [(k, 'T') for k in range(b - 1, a, -1)]
                + [(a, 'R')] + [(k, 'T') for k in range(a + 1, NS)])


def _source_rays(lens, src, n_grid):
    """从光源点（镜头坐标）射向前片矩形口径的均匀网格。返回 P, D, 每条光线的相对权重。"""
    a = lens.S[0]["ap"]
    sx, sy = O._outer_extent(lens.S[0])
    u = (np.arange(n_grid) + 0.5) / n_grid * 2 - 1
    gx, gy = np.meshgrid(u * sx * 1.02, u * sy * 1.02)
    z0 = lens.S[0]["z"] + 5.0
    T = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z0)], axis=1)
    S = np.asarray(src, float)
    D = T - S
    dist = np.linalg.norm(D, axis=1)
    D = D / dist[:, None]
    P = np.repeat(S[None, :], len(T), axis=0)
    wt = (-D[:, 2]) / (dist * dist)                     # 平面网格单元的立体角 ∝ cosθ / r²
    return P, D, wt / wt.mean()


def _splat(img, X, Y, w, sensor, ch):
    H, W = img.shape[:2]
    SW, SH = sensor
    u = (X / SW + 0.5) * W - 0.5
    v = (0.5 + Y / SH) * H - 0.5
    m = (w > 0) & (u > -1) & (u < W) & (v > -1) & (v < H)
    u, v, w = u[m], v[m], w[m]
    i0 = np.floor(u).astype(int); j0 = np.floor(v).astype(int)
    fu, fv = u - i0, v - j0
    for di, dj, ww in ((0, 0, (1 - fu) * (1 - fv)), (1, 0, fu * (1 - fv)), (0, 1, (1 - fu) * fv), (1, 1, fu * fv)):
        ii, jj = i0 + di, j0 + dj
        ok = (ii >= 0) & (ii < W) & (jj >= 0) & (jj < H)
        np.add.at(img[:, :, ch], (jj[ok], ii[ok]), w[ok] * ww[ok])


def _blur_fft(img, sx, sy=None):
    """高斯模糊（FFT，可各向异性：x / y 两个 σ，单位像素）。"""
    sy = sx if sy is None else sy
    if sx <= 0 and sy <= 0:
        return img
    H, W = img.shape[:2]
    fy = np.fft.fftfreq(H)[:, None]; fx = np.fft.rfftfreq(W)[None, :]
    G = np.exp(-2.0 * math.pi ** 2 * ((sx * fx) ** 2 + (sy * fy) ** 2))
    out = np.empty_like(img)
    for c in range(img.shape[2]):
        out[:, :, c] = np.fft.irfft2(np.fft.rfft2(img[:, :, c]) * G, s=(H, W))
    return np.clip(out, 0, None)


def _splat_adaptive(buckets, X, Y, w, idx, n_grid, sensor, ch, shape):
    """按光线网格在像面上的局部间距自适应铺开（x、y 分开）：鬼影常被压成一条线（y 极薄、x 很宽），
    各向同性的足迹会在线上留下串珠。按 (2^kx, 2^ky) 分桶，之后整桶做各向异性高斯模糊。"""
    H, W = shape
    SW, SH = sensor
    u = np.full(n_grid * n_grid, np.nan); v = np.full(n_grid * n_grid, np.nan)
    u[idx] = (X / SW + 0.5) * W - 0.5
    v[idx] = (0.5 + Y / SH) * H - 0.5
    U = u.reshape(n_grid, n_grid); V = v.reshape(n_grid, n_grid)
    def spacing(A):
        a = np.abs(np.diff(A, axis=1, append=np.nan)); a = np.where(np.isnan(a), np.abs(np.diff(A, axis=1, prepend=np.nan)), a)
        b = np.abs(np.diff(A, axis=0, append=np.nan)); b = np.where(np.isnan(b), np.abs(np.diff(A, axis=0, prepend=np.nan)), b)
        return np.nan_to_num(np.fmax(a, b), nan=1.0).ravel()[idx]
    su, sv = spacing(U), spacing(V)
    kx = np.clip(np.round(np.log2(np.maximum(su, 1e-6))), 0, 7).astype(int); kx[su <= 1.5] = 0
    ky = np.clip(np.round(np.log2(np.maximum(sv, 1e-6))), 0, 7).astype(int); ky[sv <= 1.5] = 0
    key = kx * 8 + ky
    for kk in np.unique(key):
        m = key == kk
        if kk not in buckets:
            buckets[kk] = np.zeros((H, W, 3))
        _splat(buckets[kk], X[m], Y[m], w[m], sensor, ch)


def _blur(img, sigma):
    if sigma <= 0:
        return img
    r = int(math.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    out = img.copy()
    for ax in (0, 1):
        pad = [(0, 0)] * out.ndim; pad[ax] = (r, r)
        q = np.pad(out, pad)
        acc = np.zeros_like(out)
        n = out.shape[ax]
        for i, kv in enumerate(k):
            acc += kv * np.take(q, np.arange(i, i + n), axis=ax)
        out = acc
    return out


def flare_layer(rx, src_lens, radiance, radius, W, H, focus_dist=math.inf, f_number=None,
                coated=True, coating_nm=100.0, coating_ior=1.38, n_grid=320, blur_px=0.8, exit_filter=None, coating_type="SINGLE"):
    """一个球形光源（镜头坐标 src_lens，半径 radius，自发光强度 radiance）的鬼影层（线性，H×W×3）。
    以同一套光线的直射像总能量归一化，再乘以 Cycles 中该光源直射像的总能量（像素值 × 像素数）。"""
    lens = Lens(rx, focus_dist, f_number)
    NS = len(lens.S)
    P, D, wt = _source_rays(lens, src_lens, n_grid)
    ghost = np.zeros((H, W, 3))
    buckets = {}
    direct_E = np.zeros(3)
    def through_exit_disc(X, Y, Dout):
        """（仅用于验证）像面点沿 −Dout 反推到出瞳平面，是否落在 OSL 相机的采样盘内。"""
        if exit_filter is None:
            return np.ones(len(X), bool)
        z_exp, r_samp = exit_filter
        t = z_exp / -Dout[:, 2]
        return (X - t * Dout[:, 0]) ** 2 + (Y - t * Dout[:, 1]) ** 2 <= r_samp * r_samp
    for ch, lam in enumerate(LAMBDAS):
        states = {}
        idx, X, Y, w, Dout = lens.trace(P, D, Lens.seq_direct(NS), lam, coated, coating_nm, coating_ior, coating_type,
                                        record=states)
        direct_E[ch] = wt[idx].sum()            # 不计透射损失：与 Analytic 主画面（无菲涅耳损失）同一基准
        back = {}                               # b → 在 b 面反射后往回走、到达各面之前的状态
        for a, b in lens.pairs():               # 前 b 步（0…b−1 透射）与直射路径相同；b 反射后往回到 a 之前也与同一 b 的
            if b not in states:                 # 其他鬼影相同 → 两段都只追一次（直射在 b 之前全被挡 → 没有光线）
                continue
            if b not in back:
                back[b] = {}
                lens.trace(None, None, [(b, 'R')] + [(k, 'T') for k in range(b - 1, -1, -1)], lam, coated, coating_nm,
                           coating_ior, coating_type, state=states[b], record=back[b])
            if a not in back[b]:                # 往回走时在 a 之前就全被挡了
                continue
            seq = [(a, 'R')] + [(k, 'T') for k in range(a + 1, NS)]
            idx, X, Y, w, Dout = lens.trace(None, None, seq, lam, coated, coating_nm, coating_ior, coating_type,
                                            state=back[b][a])
            if len(idx):
                m = through_exit_disc(X, Y, Dout)
                _splat_adaptive(buckets, X[m], Y[m], (w * wt[idx])[m], idx[m], n_grid, lens.sensor, ch, (H, W))
        del states, back
    for kk, buf in buckets.items():
        bx, by = divmod(kk, 8)
        ghost += buf if kk == 0 else _blur_fft(buf, 0.5 * 2.0 ** bx if bx else 0.0, 0.5 * 2.0 ** by if by else 0.0)
    # Cycles 直射像总能量：亮度 × 光源立体角 ÷ 单像素物方立体角（水平/垂直焦距不同）
    Sf = O.layout(rx, *O.solve_focus(rx, focus_dist))
    fx = O.paraxial_infinity(Sf, "Rx")[0]; fy = O.paraxial_infinity(Sf, "Ry")[0]
    dist = float(np.linalg.norm(src_lens))
    omega = math.pi * radius * radius / (dist * dist)
    pix = (lens.sensor[0] / W / fx) * (lens.sensor[1] / H / fy)
    E_cycles = radiance * omega / pix
    scale = np.where(direct_E > 0, E_cycles / np.maximum(direct_E, 1e-30), 0.0)
    ghost *= scale[None, None, :]
    return _blur(ghost, blur_px), dict(direct_energy=direct_E.tolist(), E_cycles=E_cycles,
                                       ghost_energy=ghost.sum(axis=(0, 1)).tolist())


# ───────────────────────── 衍射星芒（0.6）─────────────────────────
# 光圈多边形的夫琅禾费衍射：PSF = |FFT(光瞳)|²，按 R/G/B 波长缩放；变形镜头出瞳是光阑的仿射像，
# 所以像方两向的数值孔径不同（由追迹的光束张角量出），星芒在 X/Y 上按各自的 λ/NA 拉伸。
# 能量：PSF 归一化为 1，乘该光源在 Cycles 里的直射像总能量（与鬼影同一基准）；去掉 Airy 主斑（渲染里已有），
# 只加主斑以外的衍射能量。强离焦光源（焦外光斑大）以焦外为主，星芒按光斑半径淡出。

def _aperture_mask(n, L, blades, rot_rad):
    """[-L, L]² 网格上的单位光瞳（半径 1）：圆，或 blades 边形（外接圆半径 1，与 OSL 相机同一判据）。
    边缘按到边界的有符号距离做抗锯齿（覆盖率），否则阶梯状边缘会衍射出平行的假星芒。"""
    u = (np.arange(n) + 0.5) / n * 2 * L - L
    X, Y = np.meshgrid(u, u)
    du = 2 * L / n
    if blades < 3:
        dist = np.hypot(X, Y) - 1.0
    else:
        ca, sa = math.cos(rot_rad), math.sin(rot_rad)
        bx, by = ca * X + sa * Y, -sa * X + ca * Y
        seg = 2 * math.pi / blades
        ang = np.arctan2(by, bx)
        m = ang - seg * np.floor(ang / seg) - 0.5 * seg
        dist = np.hypot(bx, by) * np.cos(m) - math.cos(0.5 * seg)     # 到最近一条边的有符号距离
    return np.clip(0.5 - dist / du, 0.0, 1.0)

_PSF_CACHE = {}

def _psf(blades, rot_rad, n=2048, L=2.0):
    """归一化的衍射 PSF（总和 1），频率步长 1/(2L)。按光圈形状缓存（与波长、f 值无关，像面上再按 λ/NA 缩放）。"""
    key = (int(blades), round(rot_rad, 6), n, L)
    if key not in _PSF_CACHE:
        if len(_PSF_CACHE) > 6:
            _PSF_CACHE.clear()
        A = _aperture_mask(n, L, blades, rot_rad)
        I = np.abs(np.fft.fftshift(np.fft.fft2(A))) ** 2
        _PSF_CACHE[key] = I / I.sum()
    return _PSF_CACHE[key]

_SAT_CACHE = {}

def _psf_sat(blades, rot_rad, n=2048, L=2.0, rx_cells=None, ry_cells=None):
    """星芒用的 PSF（去掉 Airy 主斑、外圈平滑衰减）的积分表（(n+1)×(n+1)，S[j, i] = 前 j 行 i 列之和）：
    任意矩形里的能量 = 四个角相减。主斑与衰减都在 PSF 单元坐标里做：主斑半径 1.22 λ/(2NA)·1.5 在单元里恒为 1.83·L 个单元
    （单元尺寸 λ/(2L·NA)），与波长、f 值无关 —— 在像素上做的话主斑只有零点几个像素，挖不挖掉取决于亚像素位置（R6 闪烁）。
    rx_cells / ry_cells：挖掉的椭圆半径（单元），不小于主斑；星芒用它挖掉光源自己那一个像素左右（直射像 Cycles 已经渲了）。"""
    rcx = max(1.83 * L, rx_cells or 0.0); rcy = max(1.83 * L, ry_cells or 0.0)
    key = (int(blades), round(rot_rad, 6), n, L, round(rcx, 4), round(rcy, 4))
    if key not in _SAT_CACHE:
        if len(_SAT_CACHE) > 12:
            _SAT_CACHE.clear()
        c = (np.arange(n) - n / 2)
        rr = np.hypot(c[None, :], c[:, None])                       # 单元
        e = np.hypot(c[None, :] / rcx, c[:, None] / rcy)
        keep = np.clip((e - 1.0) * min(rcx, rcy) + 0.5, 0.0, 1.0)   # 挖掉的椭圆：约 1 个单元宽的平滑边
        q = rr / (n / 2)
        keep *= np.where(q < 0.55, 1.0, np.cos(np.clip((q - 0.55) / 0.4, 0.0, 1.0) * math.pi / 2) ** 2)   # 径向平滑衰减
        S = np.zeros((n + 1, n + 1))
        S[1:, 1:] = np.cumsum(np.cumsum(_psf(blades, rot_rad, n, L) * keep, axis=0), axis=1)
        _SAT_CACHE[key] = S
    return _SAT_CACHE[key]


def _sat_at(S, x, y):
    """积分表在连续坐标 (x, y)（单元边界 = 整数）处的双线性值。"""
    n = S.shape[0] - 1
    x = np.clip(x, 0.0, n); y = np.clip(y, 0.0, n)
    i = np.minimum(np.floor(x).astype(int), n - 1); j = np.minimum(np.floor(y).astype(int), n - 1)
    tx, ty = x - i, y - j
    return (S[j, i] * (1 - tx) + S[j, i + 1] * tx) * (1 - ty) + (S[j + 1, i] * (1 - tx) + S[j + 1, i + 1] * tx) * ty


def starburst_layer(rx, src_lens, radiance, radius, W, H, focus_dist=math.inf, f_number=None, blades=0,
                    blade_rotation_deg=0.0, n_rays=120, gain=1.0, fade_px=12.0):
    """一个光源的衍射层（H×W×3，行 0 = 顶部，与 flare_layer 同一坐标）。返回 (layer, info)。"""
    lens = Lens(rx, focus_dist, f_number)
    P, D, wt = _source_rays(lens, src_lens, n_rays)
    idx, X, Y, w, Dout = lens.trace(P, D, Lens.seq_direct(len(lens.S)), O.LAMBDA_D, True, 0.0, 1.38)
    if len(idx) < 8:
        return np.zeros((H, W, 3)), dict(skipped="no direct image")
    ww = wt[idx]
    cx, cy = float((X * ww).sum() / ww.sum()), float((Y * ww).sum() / ww.sum())
    SW, SH = lens.sensor
    if abs(cx) > SW * 0.75 or abs(cy) > SH * 0.75:
        return np.zeros((H, W, 3)), dict(skipped="off frame")
    # 像方数值孔径（两向）：光束方向相对平均方向的张角。0.8.4：用加权标准差 × 2（均匀圆盘的投影：σ = R/2），
    # 不再取 99% 分位数 —— 分位数只看几条边缘光线，光源一动就跳（星芒随之缩放、能量跳），视频里闪（R6）
    tx, ty = Dout[:, 0] / -Dout[:, 2], Dout[:, 1] / -Dout[:, 2]
    NAx = 2.0 * float(np.sqrt(np.average((tx - np.average(tx, weights=ww)) ** 2, weights=ww)))
    NAy = 2.0 * float(np.sqrt(np.average((ty - np.average(ty, weights=ww)) ** 2, weights=ww)))
    if NAx <= 0 or NAy <= 0:
        return np.zeros((H, W, 3)), dict(skipped="degenerate pupil")
    # 离焦：几何光斑半径（像素），大于 fade_px 时星芒淡出
    px_w, px_h = SW / W, SH / H
    sdx = float(np.sqrt(np.average((X - cx) ** 2, weights=ww))); sdy = float(np.sqrt(np.average((Y - cy) ** 2, weights=ww)))
    blur_px = 2.0 * max(sdx / px_w, sdy / px_h)                     # 光斑半径（加权，0.8.4 前用 ptp：受个别光线影响会跳）
    fade = 1.0 / (1.0 + (blur_px / fade_px) ** 2)
    # 直射像总能量（与 flare_layer 相同的 Cycles 基准）
    Sf = O.layout(rx, *O.solve_focus(rx, focus_dist))
    fx = O.paraxial_infinity(Sf, "Rx")[0]; fy = O.paraxial_infinity(Sf, "Ry")[0]
    dist = float(np.linalg.norm(src_lens))
    E = radiance * (math.pi * radius * radius / (dist * dist)) / ((SW / W / fx) * (SH / H / fy))
    n, L = 2048, 2.0
    out = np.zeros((H, W, 3))
    # 图像像素中心 → 传感器 mm（画面向右 = 传感器 +x，向上 = 传感器 −y，与 OSL 一致）
    u0 = (cx / SW + 0.5) * W - 0.5
    v0 = (0.5 + cy / SH) * H - 0.5
    for ch, lam in enumerate(LAMBDAS):
        lam_mm = lam * 1e-3
        dxm, dym = lam_mm / (2 * L * NAx), lam_mm / (2 * L * NAy)     # FFT 单元在像面上的尺寸（mm）
        sat = _psf_sat(blades, math.radians(blade_rotation_deg), n, L, px_w / dxm, px_h / dym)   # 挖掉半径 1 像素
        hw = int(min(W, n / 2 * dxm / px_w)); hh = int(min(H, n / 2 * dym / px_h))
        i0, i1 = max(0, int(u0) - hw), min(W, int(u0) + hw + 1)
        j0, j1 = max(0, int(v0) - hh), min(H, int(v0) + hh + 1)
        if i1 <= i0 or j1 <= j0:
            continue
        uu, vv = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1))
        sx_mm, sy_mm = (uu - u0) * px_w, (vv - v0) * px_h
        fi, fj = sx_mm / dxm + n / 2, sy_mm / dym + n / 2              # PSF 下标（浮点，单元中心 = 整数）
        # 0.8.4：按像素面积积分 PSF（积分表），不再取像素中心一个点 —— 一个像素盖住 ~25×25 个 PSF 单元，
        # 点采样会混叠：光源挪半个像素，星芒总能量就跳 ±25%，视频里闪（MessyRoom R6）
        hx, hy = 0.5 * px_w / dxm, 0.5 * px_h / dym
        x0, x1 = fi + 0.5 - hx, fi + 0.5 + hx; y0, y1 = fj + 0.5 - hy, fj + 0.5 + hy
        val = _sat_at(sat, x1, y1) - _sat_at(sat, x0, y1) - _sat_at(sat, x1, y0) + _sat_at(sat, x0, y0)   # 主斑已在 PSF 里去掉
        out[j0:j1, i0:i1, ch] += val * E * gain * fade
    return out, dict(centre_px=(u0, v0), NA=(NAx, NAy), blur_px=blur_px, fade=fade, energy=E)


# ───────────────────────── 杂光 / 雾化（veiling glare，0.6；0.8 按 ISO 9358 标定）─────────────────────────
# 镜片表面散射、镜筒内壁和多次反射造成的大范围低对比度光晕。经验模型：
#   out = (1 − g)·img + g·(img ⊗ k)，k = (1 − c)·幂律核 + c·均匀核
#   幂律核 ∝ 1 / (1 + (r / r0)²)^(p/2)（近处的光晕），均匀项：任一点的光均匀铺满整个画面（镜筒内壁的漫反射），
#   即 img ⊗ k = (1 − c)·(img ⊗ 幂律核) + c·画面平均。
# 线性卷积（补零，不环绕；落到画面外的光就丢掉，与真实传感器一样）。
# 参数 vgi = 预设的观感参数 look.veiling_glare，含义就是 ISO 9358 的杂光指数：整幅均匀白场中央放一个黑斑
# （直径 = 画面高度的 SPOT_FRAC），黑斑中心的亮度 / 旁边白场的亮度。g 由数值标定反解，使这个合成测试正好测出 vgi。
# 取值参考 VGI 的典型范围：现代多层膜 0.5–1.5%，单层 MgF₂ 2–3%，无镀膜 5–10%。公开的电影变形镜头 VGI 实测不存在，
# 各预设的值是按镀膜年代的估计。c、r0、p、SPOT_FRAC 也是假设（ISO 9358 未规定黑斑的确切大小），改动需同时改测试。
# 0.7 及以前：同一个幂律核、FFT 环绕卷积、g 直接当能量比例 → 实测 VGI 只有设定值的约 1/4，且右边的亮光会漏到左边。
VG_SPOT_FRAC = 0.10
VG_UNIFORM = 0.5
VG_MAX_DIM = 768            # 卷积在降采样图上做（光晕是低频的），4K 也只要零点几秒


def _vg_kernel(h, w, r0_px, p):
    """尺寸 (2h, 2w) 的幂律线性卷积核（中心在 [0, 0]，环绕下标），积分为 1。"""
    fy = np.fft.fftfreq(2 * h)[:, None] * 2 * h; fx = np.fft.fftfreq(2 * w)[None, :] * 2 * w
    k = (1.0 + (fx * fx + fy * fy) / (r0_px * r0_px)) ** (-p / 2.0)
    return k / k.sum()


def _vg_halo(img, K, h, w, c):
    mean = img.reshape(h * w, *img.shape[2:]).mean(axis=0)
    return (1.0 - c) * _vg_convolve(img, K, h, w) + c * mean


def _vg_convolve(img, K, h, w):
    pad = np.zeros((2 * h, 2 * w) + img.shape[2:])
    pad[:h, :w] = img
    F = np.fft.rfft2(pad, axes=(0, 1))
    Kf = np.fft.rfft2(K)
    if img.ndim == 3:
        Kf = Kf[..., None]
    return np.fft.irfft2(F * Kf, s=(2 * h, 2 * w), axes=(0, 1))[:h, :w]


def _vg_setup(h, w, r0_frac, p, c, spot_frac):
    """降采样分辨率 (h, w) 上的核与「单位 g」时的黑斑中心 / 白场亮度（合成 ISO 9358 测试）。"""
    K = _vg_kernel(h, w, max(r0_frac * math.hypot(w, h), 0.5), p)
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(yy - (h - 1) / 2, xx - (w - 1) / 2)
    R = 0.5 * spot_frac * h
    white = (rr > R).astype(float)
    conv = _vg_halo(white, K, h, w, c)
    v_spot = conv[(h - 1) // 2, (w - 1) // 2]
    ring = (rr > 1.5 * R) & (rr < 2.0 * R)
    v_field = conv[ring].mean()
    return K, v_spot, v_field


_VG_CACHE = {}

def veiling_glare_strength(vgi, h, w, r0_frac=0.004, p=2.6, c=VG_UNIFORM, spot_frac=VG_SPOT_FRAC):
    """目标 VGI → 散射能量比例 g：VGI = g·v_s / (1 − g + g·v_f)。"""
    key = (h, w, r0_frac, p, c, spot_frac)
    if key not in _VG_CACHE:
        _VG_CACHE.clear()
        _VG_CACHE[key] = _vg_setup(h, w, r0_frac, p, c, spot_frac)
    K, vs, vf = _VG_CACHE[key]
    return K, vgi / (vs + vgi * (1.0 - vf))


def veiling_glare(img, vgi, r0_frac=0.004, p=2.6, c=VG_UNIFORM, spot_frac=VG_SPOT_FRAC):
    """img：H×W×3 线性图像；vgi：目标杂光指数（0–0.2）。光晕在降采样图上卷积后双线性放大，原图按 (1 − g) 保留。"""
    if vgi <= 0:
        return img
    H, W = img.shape[:2]
    s = max(1, int(math.ceil(max(H, W) / VG_MAX_DIM)))
    h, w = -(-H // s), -(-W // s)
    small = np.zeros((h * s, w * s) + img.shape[2:])
    small[:H, :W] = img
    small = small.reshape(h, s, w, s, *img.shape[2:]).mean(axis=(1, 3))     # 面积平均（保能量）
    if H % s or W % s:                                                         # 边缘不满的块按实际面积补偿
        cover = np.zeros((h * s, w * s)); cover[:H, :W] = 1.0
        cover = cover.reshape(h, s, w, s).mean(axis=(1, 3))
        small = small / np.maximum(cover, 1e-9)[..., None] if img.ndim == 3 else small / np.maximum(cover, 1e-9)
    K, g = veiling_glare_strength(vgi, h, w, r0_frac, p, c, spot_frac)
    halo = np.clip(_vg_halo(small, K, h, w, c), 0, None)
    if s > 1:                                                                   # 双线性放大回原分辨率（像素中心对齐）
        halo = halo.astype(np.float32)                                          # 4K：放大用 float32（float64 临时数组要 1 GB 以上）
        yi = (np.arange(H) + 0.5) / s - 0.5; xi = (np.arange(W) + 0.5) / s - 0.5
        y0 = np.clip(np.floor(yi).astype(int), 0, h - 1); x0 = np.clip(np.floor(xi).astype(int), 0, w - 1)
        y1 = np.minimum(y0 + 1, h - 1); x1 = np.minimum(x0 + 1, w - 1)
        ty = np.clip(yi - y0, 0, 1).astype(np.float32)[:, None]; tx = np.clip(xi - x0, 0, 1).astype(np.float32)[None, :]
        if img.ndim == 3:
            ty = ty[..., None]; tx = tx[..., None]
        hy0, hy1 = halo[y0], halo[y1]
        top = hy0[:, x0] * (1 - tx); top += hy0[:, x1] * tx
        bot = hy1[:, x0] * (1 - tx); bot += hy1[:, x1] * tx
        del hy0, hy1
        top *= (1 - ty); bot *= ty; top += bot
        halo = top; del bot
    return (1.0 - g) * img + g * halo
