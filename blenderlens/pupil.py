# 出瞳重要性采样表（纯 numpy，不依赖 bpy）。
# OSL 相机原本在出瞳平面的圆盘（半径 = 近轴出瞳 × pupil_margin）上均匀采样；变形镜头离轴时真正通光的区域
# 只是盘里的一小块（猫眼、两截面出瞳不同）。这里离线追迹：对传感器一个象限的网格点，求「能穿过整支镜头的
# 采样点」在出瞳平面上的包围盒（多个波长 × 多个对焦状态取并集，再向相邻网格点膨胀并加余量）。
# OSL 在盒内均匀采样、盒外的盘内区域照旧视为挡光，权重乘「盒面积 / 盘面积」→ 与原估计器同一个积分，无偏。
# 镜头对 x、y 两个主截面对称，所以只需一个象限。只用于 Analytic 直射光路：鬼影（两次反射）的光线会穿过
# 盒外区域，所以开鬼影或 Geometry 模式时 OSL 退回整盘采样。
import math
import numpy as np

try:
    from . import optics as O
    from .flare import _sag_grad
except ImportError:
    import optics as O
    from flare import _sag_grad

LAMBDAS = (0.40, 0.55, 0.70)          # 覆盖连续波长色散的范围


def _passes(S, z_stop, r_stop, P, D, lam):
    """从传感器出发（沿 +Z）逐面追迹，返回能到达物方的布尔掩码。与 OSL 相机的 Analytic 直射光路同一判据
    （平面 / 球面 / 纯柱面用同一个稳定二次式解析求交，其余牛顿）。被挡的光线随时剔除，只追活着的。"""
    n = P.shape[0]
    alive = np.zeros(n, bool)
    co = [O.dispersion_coeffs(sf["glass"]) for sf in S]
    il2 = 1.0 / (lam * lam)
    nidx = lambda k: 1.0 if k < 0 else co[k][0] + il2 * (co[k][1] + il2 * (co[k][2] + il2 * co[k][3]))
    idx = np.arange(n)
    P = P.copy(); D = D.copy()
    for i in range(len(S) - 1, -1, -1):
        sf = S[i]
        cx, cy = O.curv(sf["Rx"]), O.curv(sf["Ry"])
        asph = sf.get("asph") or ()
        zi = sf["z"]
        with np.errstate(all="ignore"):
            ts = (z_stop - P[:, 2]) / D[:, 2]
            tz = (zi - P[:, 2]) / D[:, 2]
            hit_stop = (ts > 0) & (ts < tz)
            qx = P[:, 0] + ts * D[:, 0]; qy = P[:, 1] + ts * D[:, 1]
            keep = ~hit_stop | (qx * qx + qy * qy <= r_stop * r_stop)
            general = any(asph) or sf["kx"] or sf["ky"] or (cx and cy and cx != cy)
            if not general:
                c_ = cx or cy
                mx, my = (1.0 if cx else 0.0), (1.0 if cy else 0.0)
                x0 = P[:, 0] + tz * D[:, 0]; y0 = P[:, 1] + tz * D[:, 1]
                qa = c_ * (mx * D[:, 0] ** 2 + my * D[:, 1] ** 2 + D[:, 2] ** 2)
                qb = 2.0 * (c_ * (mx * x0 * D[:, 0] + my * y0 * D[:, 1]) + D[:, 2])
                qc = c_ * (mx * x0 * x0 + my * y0 * y0)
                disc = qb * qb - 4.0 * qa * qc
                den = qb + np.where(qb >= 0, 1.0, -1.0) * np.sqrt(np.clip(disc, 0, None))
                t = tz + np.where(den != 0, -2.0 * qc / np.where(den != 0, den, 1.0), 0.0)
                keep &= disc >= 0
            else:
                t = tz.copy()
                for _ in range(8):
                    x = P[:, 0] + t * D[:, 0]; y = P[:, 1] + t * D[:, 1]
                    s, gx, gy, ok = _sag_grad(x, y, cx, cy, sf["kx"], sf["ky"], asph)
                    g = P[:, 2] + t * D[:, 2] - zi + s
                    dg = D[:, 2] + gx * D[:, 0] + gy * D[:, 1]
                    t = t - g / np.where(np.abs(dg) < 1e-12, 1e-12, dg)
            keep &= (t > 1e-6) & np.isfinite(t)
            P = P + t[:, None] * D
            clr, msx, msy, _ = O.ap_limits(sf["ap"])
            keep &= (P[:, 0] ** 2 + P[:, 1] ** 2 <= clr * clr) & (np.abs(P[:, 0]) <= msx) & (np.abs(P[:, 1]) <= msy)
            s, gx, gy, ok = _sag_grad(P[:, 0], P[:, 1], cx, cy, sf["kx"], sf["ky"], asph)
            keep &= ok
            L = np.sqrt(gx * gx + gy * gy + 1.0)
            Nrm = np.stack([gx / L, gy / L, 1.0 / L], axis=1)
            c = -(Nrm * D).sum(axis=1)
            flip = c < 0
            Nrm[flip] *= -1; c = np.abs(c)
            eta = nidx(i) / nidx(i - 1)                       # 沿 +Z：从面后（像方）介质进入面前介质
            kk = 1.0 - eta * eta * (1.0 - c * c)
            keep &= kk >= 0
            D = eta * D + (eta * c - np.sqrt(np.clip(kk, 0, None)))[:, None] * Nrm
            D = D / np.linalg.norm(D, axis=1)[:, None]
            keep &= np.isfinite(P).all(axis=1) & np.isfinite(D).all(axis=1)
        P, D, idx = P[keep], D[keep], idx[keep]
        if not len(idx):
            break
    alive[idx] = True
    return alive


TABLE_VERSION = 3       # 改了表的算法 / 默认参数就加 1（随包分发与磁盘缓存按 处方内容 + 版本 取用）


def table(rx, nx=11, ny=9, m=72, focus_states=None, margin_frac=0.01):
    """返回 dict(NX, NY, X0, X1, Y0, Y1, eff_disc, eff_box, stats)：象限网格（ax = |sx| 从 0 到 W/2，ay 同理）上
    每个节点的采样盒（出瞳平面坐标，mm，最大光圈时；OSL 里再按当前光圈的盘夹紧）。"""
    try:
        from . import oslgen as OG
    except ImportError:
        import oslgen as OG
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    z_exp0, r_exp, z_stop0, r_stop = OG.exit_pupil(rx)
    st_id = O.stop_group(rx)["stop"]["after_surface"]
    margin = 1.4                                             # 与 OSL pupil_margin 默认值一致；表按它的盘算
    r_samp = r_exp * margin
    if focus_states is None:
        mfd = rx.get("focus", {}).get("min_distance_mm") or rx.get("look", {}).get("min_focus_mm") or 0.0
        focus_states = [math.inf] + ([mfd, 2.5 * mfd] if mfd else [])
    u = (np.arange(m) + 0.5) / m * 2 - 1
    gx, gy = np.meshgrid(u * r_samp, u * r_samp)
    inside = gx ** 2 + gy ** 2 <= r_samp ** 2
    Qx, Qy = gx[inside], gy[inside]
    AX = np.linspace(0.0, W / 2, nx); AY = np.linspace(0.0, H / 2, ny)
    NA, NB = np.meshgrid(AX, AY, indexing="ij")              # [ix, iy]
    nodes = np.stack([NA.ravel(), NB.ravel()], axis=1)
    K = len(Qx)
    hit = np.zeros((len(nodes), K), bool)
    import copy
    variants = [rx]
    if O.compensator(rx):                                    # 发腮补偿：强度 0 与 1 都要覆盖（中间值介于两者之间）
        variants = []
        for lev in (0.0, 1.0):
            r = copy.deepcopy(rx); r["focus"]["compensator"]["level"] = lev
            variants.append(r)
    for fd, rxv in [(fd, r) for fd in focus_states for r in variants]:
        try:
            d1, d2 = O.solve_focus(rxv, fd)
        except (ZeroDivisionError, ValueError, ArithmeticError):
            continue                                         # 求不出对焦的状态（物在镜头内）不参与
        S = O.layout(rxv, d1, d2)
        mv = next(sf["move"] for sf in S if sf["id"] == st_id)
        shift = mv[0] * d1 + mv[1] * d2
        z_exp, z_stop = z_exp0 + shift, z_stop0 + shift
        Psens = np.repeat(np.column_stack([nodes, np.zeros(len(nodes))]), K, axis=0)
        Q = np.column_stack([np.tile(Qx, len(nodes)), np.tile(Qy, len(nodes)), np.full(len(nodes) * K, z_exp)])
        Dd = Q - Psens
        Dd /= np.linalg.norm(Dd, axis=1)[:, None]
        for lam in LAMBDAS:
            hit |= _passes(S, z_stop, r_stop, Psens, Dd, lam).reshape(len(nodes), K)
    big = 1e9
    x0 = np.where(hit, Qx[None, :], big).min(axis=1); x1 = np.where(hit, Qx[None, :], -big).max(axis=1)
    y0 = np.where(hit, Qy[None, :], big).min(axis=1); y1 = np.where(hit, Qy[None, :], -big).max(axis=1)
    x0, x1, y0, y1 = (a.reshape(nx, ny) for a in (x0, x1, y0, y1))
    # 向 3×3 邻域膨胀（取并集）：任一网格单元的四个角点盒都 ⊇ 单元四角的原始盒，双线性插值后仍覆盖
    def dil(a, fn, fill):
        p = np.pad(a, 1, constant_values=fill)
        st = [p[1 + di: 1 + di + nx, 1 + dj: 1 + dj + ny] for di in (-1, 0, 1) for dj in (-1, 0, 1)]
        return fn(np.stack(st), axis=0)
    X0, X1 = dil(x0, np.min, big), dil(x1, np.max, -big)
    Y0, Y1 = dil(y0, np.min, big), dil(y1, np.max, -big)
    # 采样网格间距 + 余量。0.8：网格从 36 加密到 72、余量 3% → 1%，带光采样占比平均 ≈ 0.5 → 0.65
    # （实测：损失几乎全来自这一项，对焦状态 / 波长取并集几乎不损失）
    pad = 1.5 * (2 * r_samp / m) + margin_frac * r_samp
    empty = X0 > X1
    X0 = np.where(empty, -r_samp, np.clip(X0 - pad, -r_samp, r_samp))
    X1 = np.where(empty, r_samp, np.clip(X1 + pad, -r_samp, r_samp))
    Y0 = np.where(empty, -r_samp, np.clip(Y0 - pad, -r_samp, r_samp))
    Y1 = np.where(empty, r_samp, np.clip(Y1 + pad, -r_samp, r_samp))
    # 采样效率（无穷远、d 线、最大光圈；只作诊断）：带光采样占比 = 通过数 / 盘内采样数（整盘） vs / 盒内采样数（盒）
    eff_disc, eff_box = [], []
    for j in range(len(nodes)):
        ix, iy = divmod(j, ny)
        inbox = (Qx >= X0[ix, iy]) & (Qx <= X1[ix, iy]) & (Qy >= Y0[ix, iy]) & (Qy <= Y1[ix, iy])
        eff_disc.append(hit[j].mean()); eff_box.append(hit[j].sum() / max(inbox.sum(), 1))
    eff_disc = np.array(eff_disc).reshape(nx, ny); eff_box = np.array(eff_box).reshape(nx, ny)
    return dict(NX=nx, NY=ny, X0=X0, X1=X1, Y0=Y0, Y1=Y1, r_samp=r_samp, eff_disc=eff_disc, eff_box=eff_box,
                focus_states=focus_states)


def axial_transmission(rx, m=160, k=0.5):
    """轴上点、无穷远、d 线、光阑缩到 k 倍（默认半径一半 → 只有光阑限光，不含其他镜片的切光）：
    采样盘（近轴出瞳 × 1.4 × k）里能穿过镜头的面积 × cos⁴，相对近轴出瞳面积（× k²）。
    即「真实出瞳面积 / 近轴出瞳估计面积」：球面主镜 ≈ 1；光阑后有柱面时出瞳是椭圆而采样盘按两截面取大，
    比值 ≈ 1 / 压缩比（Panavision 后置约 0.77）。OSL 用它的倒数归一化曝光：轴上、只受光阑限光时 = 1，
    与 T 值一致；最大光圈下其他镜片的切光（物理的）照常保留。"""
    try:
        from . import oslgen as OG
    except ImportError:
        import oslgen as OG
    z_exp, r_exp, z_stop, r_stop = OG.exit_pupil(rx)
    S = O.layout(rx)
    r_samp = 1.4 * r_exp * k
    r_stop = r_stop * k
    u = (np.arange(m) + 0.5) / m * 2 - 1
    gx, gy = np.meshgrid(u * r_samp, u * r_samp)
    inside = gx ** 2 + gy ** 2 <= r_samp ** 2
    Qx, Qy = gx[inside], gy[inside]
    n = len(Qx)
    P = np.zeros((n, 3))
    D = np.column_stack([Qx, Qy, np.full(n, z_exp)])
    D /= np.linalg.norm(D, axis=1)[:, None]
    ok = _passes(S, z_stop, r_stop, P, D, O.LAMBDA_D)
    return float((ok * D[:, 2] ** 4).mean() * 1.96)
