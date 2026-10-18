# 炫光通道：从光源正向追迹光线网格穿过镜头，按「两次反射」的所有面对累加到传感器（Hullin 式序列鬼影）。
# 确定性、无噪点；面形 / 口径 / 光阑 / 薄膜公式与 OSL 相机一致。只依赖 numpy（Blender 自带）。
# 坐标：镜头坐标（原点像面，物方 +Z）；输出图像与 OSL 相机同一映射（画面向右 = 传感器 +x，向上 = 传感器 −y）。
import math
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
        self.cauchy = [O.cauchy(sf["glass"]["nd"], sf["glass"].get("vd")) for sf in self.S]

    def n_after(self, k, lam):
        A, B = self.cauchy[k]
        return A + B / (lam * lam)

    def n_before(self, k, lam):
        return 1.0 if k == 0 else self.n_after(k - 1, lam)

    def pairs(self):
        return [(a, b) for i, a in enumerate(self.AG) for b in self.AG[i + 1:]]

    # ── 单条路径（向量化）：seq = [(面序号, 'T'|'R'), …]，光沿 −Z 从物方出发 ──
    def trace(self, P, D, seq, lam, coated, coating_nm, coating_ior):
        """返回 (idx, X, Y, w, Dout)：存活光线在输入中的下标、像面落点、权重、最终方向。
        每过一面就剔除被挡光线，ghost 路径大多很快被挡掉。"""
        idx = np.arange(P.shape[0])
        w = np.ones(P.shape[0])
        dirn = -1                                          # −1：沿 −Z（面序号递增）
        for k, act in seq:
            sf = self.S[k]
            cx, cy = O.curv(sf["Rx"]), O.curv(sf["Ry"])
            t = (sf["z"] - P[:, 2]) / D[:, 2]
            with np.errstate(all="ignore"):
                for _ in range(8):
                    x = P[:, 0] + t * D[:, 0]; y = P[:, 1] + t * D[:, 1]
                    s, gx, gy, ok = _sag_grad(x, y, cx, cy, sf["kx"], sf["ky"], sf.get("asph") or ())
                    g = P[:, 2] + t * D[:, 2] - sf["z"] + s
                    dg = D[:, 2] + gx * D[:, 0] + gy * D[:, 1]
                    t = t - g / np.where(np.abs(dg) < 1e-12, 1e-12, dg)
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
                    R = _fresnel_film(n1, coating_ior, n2, c, coating_nm, lam)
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
                coated=True, coating_nm=100.0, coating_ior=1.38, n_grid=320, blur_px=0.8, exit_filter=None):
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
        idx, X, Y, w, Dout = lens.trace(P, D, Lens.seq_direct(NS), lam, coated, coating_nm, coating_ior)
        direct_E[ch] = wt[idx].sum()            # 不计透射损失：与 Analytic 主画面（无菲涅耳损失）同一基准
        for a, b in lens.pairs():
            idx, X, Y, w, Dout = lens.trace(P, D, Lens.seq_ghost(NS, a, b), lam, coated, coating_nm, coating_ior)
            if len(idx):
                m = through_exit_disc(X, Y, Dout)
                _splat_adaptive(buckets, X[m], Y[m], (w * wt[idx])[m], idx[m], n_grid, lens.sensor, ch, (H, W))
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
