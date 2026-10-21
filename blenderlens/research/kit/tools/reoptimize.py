# 「同焦段全画幅」重新优化（0.9.x 试点，用户需求：保留镜头风格、同焦段覆盖全画幅）
#   思路：变形镜头的「风格」主要来自前变形部分（柱面组、压缩比、发腮 / 对焦组、炫光结构）——Neil / Sirui 自己就是用同一个
#   前组配不同后组做出 50 / 65 / 85 的。所以：前组的镜片形状、间隔、玻璃一律不动（只把口径放大到能通过全画幅视场），
#   只重新优化后组（球面）的曲率与间隔，玻璃不换（色彩特性不变），垂直焦距与 f 值不变。
#   评价函数（scipy least_squares）：
#     · 全画幅光窗上 9 个视场点的复色（d / F / C）RMS 光斑（同时管住 X / Y 合焦与色差）；
#     · 垂直焦距 = 原值、f 值不变（光阑按近轴 f/N 定孔径）；
#     · 风格锁：原 S35 视场以内，横 / 竖畸变与原设计相同；
#     · 物理：玻璃边厚 ≥ 0.8 mm、空气间隙 ≥ 0.3 mm（按实际光线高度）、后组总长不超过原来的 1.3 倍。
#   这是 BlenderLens 自己的设计（「按 XX 专利重新优化」），不是任何真实镜头——生成的预设 meta 里写明。
# 用法（研究用）：python3 tools/reoptimize.py baseline | optimize [--iters N]
import copy, json, math, os, sys, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blenderlens"))
import optics as O
import flare as FL

# 每支镜头一份配置：可变曲率的面（REAR）、可变间隔的面（GAPS，含前后组间隔）、后截距面（BFL；None = 由近轴求）
CONFIGS = {
    "neil_2x_50": dict(preset="neil_2x_50.json", rear=["S23", "S24", "S25", "S26", "S27", "S28", "S30", "S31", "S32", "S33", "S34", "S35"],
                       gaps=["S22", "S23", "S24", "S25", "S26", "S27", "S28", "S30", "S31", "S32", "S33", "S34"], bfl="S35"),
    "c_series_style": dict(preset="c_series_style.json", rear=["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11"],
                           gaps=["A8", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"], bfl=None),
    # 0.9.x：自己设计的 IMAX 15/70 2× 镜头——起点 = Neil 2× 50 整支放大到 IMAX，全部面（含前变形组的柱面）都参与优化，焦距逐步压短
    "neil_imax": dict(preset="neil_2x_50.json", rear="ALL", gaps="ALL", bfl="S35", fmt="IMAX"),
    # 「C 系列全画幅版」：想象 Panavision 自己出全画幅版（参照 Ultra Vista 1.65×）——起点 = C 整支放大到全画幅（75 mm、2.01×），
    # 全部面重新设计，焦距与压缩比一起走到 50 mm、1.65×；风格按「相对画面同一位置」对齐原版（清晰度 / 畸变 % / 暗角）
    "c_ff": dict(preset="c_series_style.json", rear="ALL", gaps="ALL", bfl=None, fmt="FF", style="relative"),
    # 第二种路线（更像厂商的做法）：焦距 50 不变，变形器按解析关系重配成 1.65×（负组焦距 = 正组 / 1.65，再求间隔使 X 向无焦），
    # 主镜（50 mm f/2 双高斯，本来就覆盖全画幅）不动作起点，光窗 = 全画幅 36 × 24，然后整支精修
    "c_ff2": dict(preset="c_series_style.json", rear="ALL", gaps="ALL", bfl=None, fmt="FF", style="relative",
                  repower=dict(neg=["A1", "A2", "A3", "A4", "A5"], pos=["A6", "A7", "A8"], gap="A5", squeeze=1.65),
                  fy=50.0, sq=1.65, spot_w=1 / 0.15,
                  # 真实厂商的取舍：保住原版「中心 → 四角」的软硬比例（边角加权），畸变允许与原版差 1–2 个百分点
                  field_w={(1, 0): 2.0, (1, 1): 3.0, (0.7, 0.7): 1.5}, dist_tol=0.015, out="c_series_style_ff.json", order_add=20,
                  qpts=((0, 0), (0.5, 0), (0.7, 0), (1, 0), (0, 0.5), (0, 0.7), (0, 1), (0.5, 0.5), (0.7, 0.7), (1, 1)), split_color=True),
    # 0.9.1：65 mm 5 齿孔版（真实世界的 65 mm 变形镜头：Ultra Panavision 70 1.25×、Hawk65 1.3×）——同一路线，压缩比 1.3×，片门 52.48 × 23.01
    "c_65": dict(preset="c_series_style.json", rear="ALL", gaps="ALL", bfl=None, fmt="65MM", style="relative",
                 repower=dict(neg=["A1", "A2", "A3", "A4", "A5"], pos=["A6", "A7", "A8"], gap="A5", squeeze=1.3),
                 fy=50.0, sq=1.3, spot_w=1 / 0.15,
                 qpts=((0, 0), (0.5, 0), (0.7, 0), (0.85, 0), (1, 0), (0, 0.5), (0, 0.7), (0, 1), (0.5, 0.5), (0.7, 0.7), (1, 1)),
                 field_w={(1, 0): 2.0, (1, 1): 3.0, (0.7, 0.7): 1.5}, dist_tol=0.015, out="c_series_style_65.json", order_add=21, split_color=True),
}
CFG = dict(CONFIGS["neil_2x_50"])
PRESET = CFG["preset"]
REAR = CFG["rear"]


def use(name):
    """切换要优化的镜头（模块级的 PRESET / REAR / GAPS / BFL / FMT）。rear / gaps = "ALL"：所有有光焦度的面 / 所有间隔。"""
    global CFG, PRESET, REAR, GAPS, BFL, FMT
    CFG = dict(CONFIGS[name]); CFG["name"] = name; PRESET = CFG["preset"]; BFL = CFG["bfl"]; FMT = CFG.get("fmt", "FF")
    rx = O.load(os.path.join(O.PRESCRIPTION_DIR, PRESET))
    sfs = [sf for g in rx["groups"] for sf in g["surfaces"]]
    REAR = [sf["id"] for sf in sfs if sf.get("R_x") or sf.get("R_y")] if CFG["rear"] == "ALL" else CFG["rear"]
    GAPS = [sf["id"] for sf in sfs[:-1] if sf.get("d") is not None] if CFG["gaps"] == "ALL" else CFG["gaps"]
LAM = (O.LAMBDA_D, 0.4861, 0.6563)           # d、F、C（µm）
BIG = 200.0


def load():
    return O.load(os.path.join(O.PRESCRIPTION_DIR, PRESET))


def open_apertures(rx):
    """优化时不让口径挡光（只由光阑限光）；最后再按新视场估口径。"""
    rx = copy.deepcopy(rx)
    for g in rx["groups"]:
        g.pop("aperture_source", None)
        for sf in g["surfaces"]:
            sf["aperture"] = {"shape": "circle", "clear": BIG, "outer": BIG}
            sf.pop("patent_heights", None)
    return rx                                   # 光阑孔径用专利值（7.85 mm @ f/3）；f 值的漂移由评价函数管（入瞳）


FMT = "FF"


def ff_gate(rx):
    return O.FORMAT_SIZES[FMT]                 # 0.9.1：用满整个画幅（解挤压后的比例由压缩比决定）


def fields(rx, W, H):
    """全画幅光窗上的视场点 → 物方方向（按近轴焦距换算，够用：只用来取样）。"""
    S = O.layout(rx)
    fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
    pts = [(0, 0), (0.5, 0), (0.85, 0), (1, 0), (0, 0.6), (0, 1), (0.6, 0.6), (0.85, 0.85), (1, 1)]
    return [(u, v, math.atan(u * W / 2 / fx), math.atan(v * H / 2 / fy)) for u, v in pts]


def _grid_rays(lens, d, x0, x1, y0, y1, n):
    u = (np.arange(n) + 0.5) / n
    gx, gy = np.meshgrid(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)
    z0 = lens.S[0]["z"] + 5.0
    P = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z0)], axis=1)
    D = np.repeat(d[None, :], len(P), axis=0)
    return P, D


def bundle(lens, ax, ay, lam=O.LAMBDA_D, n=16):
    """平行光（物方方向 ax, ay）里能穿过整支镜头的那一束在前片前平面上的包围盒：先粗网格找，再细网格铺满。
    → (P, D)：细网格光线（各波长共用同一组入射光线）。"""
    d = np.array([math.tan(ax), math.tan(ay), -1.0]); d /= np.linalg.norm(d)
    P, D = _grid_rays(lens, d, -BIG / 3, BIG / 3, -BIG / 3, BIG / 3, 90)
    idx, X, Y, w, Do = lens.trace(P, D, FL.Lens.seq_direct(len(lens.S)), lam, False, 0.0, 1.38)
    if len(idx) == 0:
        return None, None
    q = P[np.asarray(idx)]
    pad = 2 * BIG / 3 / 90
    return _grid_rays(lens, d, q[:, 0].min() - pad, q[:, 0].max() + pad, q[:, 1].min() - pad, q[:, 1].max() + pad, n)


def trace_field(lens, P, D, lam):
    idx, X, Y, w, Dout = lens.trace(P, D, FL.Lens.seq_direct(len(lens.S)), lam, False, 0.0, 1.38)
    return np.asarray(idx), np.asarray(X), np.asarray(Y)


def spots(rx, W, H, n=16, flds=None):
    lens = FL.Lens(rx, math.inf, None)
    out = []
    for u, v, ax, ay in (flds or fields(rx, W, H)):
        P, D = bundle(lens, ax, ay, n=n)
        if P is None:
            out.append(dict(u=u, v=v, n=0, rms_x=np.nan, rms_y=np.nan, cx=np.nan, cy=np.nan, ax=ax, ay=ay)); continue
        Xs, Ys, cen, nd = [], [], None, 0
        for lam in LAM:
            idx, X, Y = trace_field(lens, P, D, lam)
            if lam == LAM[0]:
                nd = len(X)
                cen = (X.mean(), Y.mean()) if len(X) else (np.nan, np.nan)
            Xs.append(X); Ys.append(Y)
        X = np.concatenate(Xs); Y = np.concatenate(Ys)
        out.append(dict(u=u, v=v, n=nd, rms_x=float(np.sqrt(((X - cen[0]) ** 2).mean())) if len(X) else np.nan,
                        rms_y=float(np.sqrt(((Y - cen[1]) ** 2).mean())) if len(X) else np.nan, cx=float(cen[0]), cy=float(cen[1]),
                        ax=ax, ay=ay))
    return out


def baseline():
    rx0 = load()
    rx = open_apertures(rx0)
    W, H = ff_gate(rx)
    S = O.layout(rx)
    print(f"{PRESET}: fy {O.paraxial_infinity(S, 'Ry')[0]:.3f}  fx {O.paraxial_infinity(S, 'Rx')[0]:.3f}  FF gate {W} × {H}")
    t = time.time()
    for s in spots(rx, W, H):
        print(f"  field ({s['u']:.2f},{s['v']:.2f})  rays {s['n']:4d}  RMS x {1000 * s['rms_x']:7.1f} µm  y {1000 * s['rms_y']:7.1f} µm")
    print(f"  {time.time() - t:.2f} s")


# ───────────────────────── 瞄准光阑的光束（评价函数要连续）─────────────────────────
def pupil_grid(rings=5, spokes=12):
    """光阑上的固定取样点（单位圆）：中心 + 等面积环。"""
    pts = [(0.0, 0.0)]
    for i in range(1, rings + 1):
        r = math.sqrt(i / rings) * 0.98
        n = spokes * i // 2 + 4
        pts += [(r * math.cos(2 * math.pi * (j + 0.5 * (i % 2)) / n), r * math.sin(2 * math.pi * (j + 0.5 * (i % 2)) / n)) for j in range(n)]
    return np.array(pts)


GRID = pupil_grid()


_CHIEF = {}                                          # 方向 → 上次的主光线入射点（优化时参数连续变化，作初值）


def aim(lens, d, targets, lam, z0, iters=14):
    """平行光方向 d：解出入射平面 z0 上的点，使光线在光阑平面上落到 targets（mm）。向量化牛顿 + 有限差分雅可比。"""
    pre = [(k, 'T') for k, sf in enumerate(lens.S) if sf["z"] > lens.z_stop]
    def stop_xy(Q):
        P = np.column_stack([Q[:, 0], Q[:, 1], np.full(len(Q), z0)])
        D = np.repeat(d[None, :], len(Q), axis=0)
        keep_r = lens.r_stop; lens.r_stop = 1e9
        try:
            rec = {}
            idx, X, Y, w, Do = lens.trace(P, D, pre, lam, False, 0.0, 1.38, record=rec)
        finally:
            lens.r_stop = keep_r
        out = np.full((len(Q), 2), np.nan)
        idx = np.asarray(idx)
        if len(idx) == 0:
            return out
        # 最后一面之后：X, Y 是到「像面 z=0」的落点；这里要光阑平面 → 用最后出射方向反推
        # trace 返回的 X, Y 是在 z=0 平面；方向 Do。光阑平面 z_stop：x = X + Do_x/Do_z·(z_stop − 0)
        Do = np.asarray(Do)
        out[idx, 0] = X + Do[:, 0] / Do[:, 2] * lens.z_stop
        out[idx, 1] = Y + Do[:, 1] / Do[:, 2] * lens.z_stop
        return out
    # 初值：主光线（光阑中心）——先用上次的解，失败再粗网格找；在它附近用局部雅可比把目标点映回入射平面
    key = (round(float(d[0]), 9), round(float(d[1]), 9), lam, round(z0, 0), round(lens.r_stop, 1))   # 区分不同尺度的镜头
    def refine(c):
        Jc = None
        for _ in range(8):
            f0 = stop_xy(c); fx_ = stop_xy(c + [1e-3, 0.0]); fy_ = stop_xy(c + [0.0, 1e-3])
            Jc = np.column_stack([(fx_ - f0)[0] / 1e-3, (fy_ - f0)[0] / 1e-3])
            if not (np.isfinite(Jc).all() and np.isfinite(f0).all()):
                return None, None
            step = np.linalg.solve(Jc, f0[0])
            c = c - step[None, :]
            if np.abs(step).max() < 1e-11:
                break
        return c, Jc
    c, Jc = refine(_CHIEF[key]) if key in _CHIEF else (None, None)
    if c is None:
        tan_ = math.hypot(d[0], d[1]) / abs(d[2])
        Rg = max(BIG / 3, 1.3 * tan_ * (z0 - lens.z_stop) + 3 * lens.r_stop)   # 网格范围随镜头尺寸与视场角
        u = np.linspace(-Rg, Rg, 121)
        gx, gy = np.meshgrid(u, u)
        G = np.column_stack([gx.ravel(), gy.ravel()])
        S0 = stop_xy(G)
        r = np.hypot(S0[:, 0], S0[:, 1]); r[~np.isfinite(r)] = np.inf
        if not np.isfinite(r.min()):
            return np.full_like(targets, np.nan)
        c, Jc = refine(G[np.argmin(r)][None, :])
        if c is None:
            return np.full_like(targets, np.nan)
    _CHIEF[key] = c
    Q = c + targets @ np.linalg.inv(Jc).T
    h = 1e-3
    for _ in range(iters):
        F0 = stop_xy(Q) - targets
        Fx = stop_xy(Q + [h, 0.0]) - targets; Fy = stop_xy(Q + [0.0, h]) - targets
        J = np.stack([(Fx - F0) / h, (Fy - F0) / h], axis=2)          # (n, 2, 2)：列 = ∂/∂Qx, ∂/∂Qy
        det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
        ok = np.isfinite(det) & (np.abs(det) > 1e-12)
        dq = np.zeros_like(Q)
        dq[ok, 0] = (J[ok, 1, 1] * F0[ok, 0] - J[ok, 0, 1] * F0[ok, 1]) / det[ok]
        dq[ok, 1] = (-J[ok, 1, 0] * F0[ok, 0] + J[ok, 0, 0] * F0[ok, 1]) / det[ok]
        Q = Q - dq
        if np.nanmax(np.abs(F0)) < 1e-10:          # 收敛得足够紧：有限差分雅可比不被瞄准噪声淹没
            break
    return Q


def aimed_bundle(lens, ax, ay, lam=O.LAMBDA_D):
    d = np.array([math.tan(ax), math.tan(ay), -1.0]); d /= np.linalg.norm(d)
    z0 = lens.S[0]["z"] + 5.0
    Q = aim(lens, d, GRID * lens.r_stop, lam, z0)
    P = np.column_stack([Q[:, 0], Q[:, 1], np.full(len(Q), z0)])
    D = np.repeat(d[None, :], len(Q), axis=0)
    return P, D


def quality(rx, W, H, pts=((0, 0), (0.5, 0), (1, 0), (0, 0.5), (0, 1), (0.5, 0.5), (0.7, 0.7), (1, 1))):
    """同一套瞄准光束：光窗上相对位置 (u, v) 的复色 RMS 光斑（µm）与「光斑 / 光窗高」。"""
    S = O.layout(rx)
    fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
    lens = FL.Lens(rx, math.inf, None)
    out = []
    for u, v in pts:
        ax, ay = math.atan(u * W / 2 / fx), math.atan(v * H / 2 / fy)
        P, D = aimed_bundle(lens, ax, ay)
        good = np.isfinite(P).all(axis=1); P, D = P[good], D[good]
        Xs, Ys, cen = [], [], None
        for lam in LAM:
            idx, X, Y, w, Do = lens.trace(P, D, FL.Lens.seq_direct(len(lens.S)), lam, False, 0.0, 1.38)
            X, Y = np.asarray(X), np.asarray(Y)
            if lam == LAM[0]:
                cen = (X.mean(), Y.mean())
            Xs.append(X); Ys.append(Y)
        mx, my = 1000 * Xs[0].std(), 1000 * Ys[0].std()                       # 单色（d）光斑
        lx, ly = 1000 * (Xs[1].mean() - Xs[2].mean()), 1000 * (Ys[1].mean() - Ys[2].mean())   # 横向色差（F − C 重心）
        X = np.concatenate(Xs); Y = np.concatenate(Ys)
        rx_, ry_ = 1000 * math.sqrt(((X - cen[0]) ** 2).mean()), 1000 * math.sqrt(((Y - cen[1]) ** 2).mean())
        rms = math.hypot(rx_, ry_)
        out.append(dict(u=u, v=v, rms_um=round(rms, 1), per_mille_of_height=round(rms / (H * 1000) * 1000, 2),
                        x_per_mille=rx_ / H, y_per_mille=ry_ / H, mono_x=mx / H, mono_y=my / H, lat_x=lx / H, lat_y=ly / H))
    return out


QPTS = ((0, 0), (0.5, 0), (1, 0), (0, 0.5), (0, 1), (0.5, 0.5), (0.7, 0.7), (1, 1))


# ───────────────────────── 优化 ─────────────────────────
SIG_SPOT = 0.010          # mm：光斑 10 µm 记 1
SIG_POS = 0.010           # mm：风格锁（S35 视场内像点位置）10 µm 记 1（IMAX 设计按像高放大，见 Merit.sig_pos）
GAPS = CFG["gaps"]                               # 前后组间隔 + 后组各厚度 / 间隔
BFL = CFG["bfl"]
MIN_GLASS, MIN_AIR = 0.8, 0.3


def _sf(rx, sid):
    return next(sf for g in rx["groups"] for sf in g["surfaces"] if sf["id"] == sid)


def _scale(rx, sid):
    return next(g.get("scale", 1.0) for g in rx["groups"] for sf in g["surfaces"] if sf["id"] == sid)


def _R(sf):
    return sf["R_y"] if sf.get("R_y") else sf.get("R_x")


def pack(rx):
    c = [1.0 / _R(_sf(rx, i)) if _R(_sf(rx, i)) else 0.0 for i in REAR]
    d = [_sf(rx, i)["d"] for i in GAPS]
    return np.array(c + d + ([_sf(rx, BFL)["d"]] if BFL else []), float)


def unpack(rx0, x):
    rx = copy.deepcopy(rx0)
    n = len(REAR)
    for i, sid in enumerate(REAR):
        sf = _sf(rx, sid)
        R = 1.0 / x[i] if abs(x[i]) > 1e-9 else None
        cx, cy = sf.get("R_x") is not None, sf.get("R_y") is not None
        if cx and cy:
            sf["R_x"] = sf["R_y"] = R
        elif cy:
            sf["R_y"] = R
        else:
            sf["R_x"] = R
    for j, sid in enumerate(GAPS):
        _sf(rx, sid)["d"] = float(x[n + j])
    if BFL:
        _sf(rx, BFL)["d"] = float(x[-1])
        rx["image_distance_mm"] = float(x[-1]) * _scale(rx, BFL)          # 物理 mm（JSON 的 d × group scale）
    return rx


def bounds(rx0, x0):
    n = len(REAR)
    lo, hi = [], []
    for sid in REAR:
        k = _scale(rx0, sid)
        lo.append(-k / 9.0); hi.append(k / 9.0)              # 物理 |R| ≥ 9 mm（JSON 里的 R × group scale）
    for sid in GAPS:
        k = _scale(rx0, sid)
        glass = _sf(rx0, sid)["medium"] != "air"
        lo.append((MIN_GLASS if glass else 0.1) / k); hi.append((16.0 if glass else 30.0) / k)
    if BFL:
        k = _scale(rx0, BFL)
        lo.append(0.8 * x0[-1]); hi.append(1.3 * x0[-1])      # 后截距：起点 ±（放大后的镜头按比例）
    lo, hi = np.array(lo), np.array(hi)
    return lo, hi


def edge_thickness(rx, heights):
    """相邻面的间距。玻璃（同一片镜片，一个外径）按两面光线高度的较大者 × 1.08 量边厚；空气间隔按两面都存在的范围
    （较小者 × 1.08）量间隙。每个面另外检查：它自己的光线高度 × 1.08 不能超出曲面有定义的范围（95%）。
    → [(id, kind, gap)]；超出曲面范围时 gap = −1 − 10 × 超出量（平滑，优化器看得见方向）。"""
    S = O.layout(rx)
    out = []
    for i in range(len(S) - 1):
        a, b = S[i], S[i + 1]
        if a["id"] not in REAR and a["id"] not in GAPS:
            continue
        kind = "air" if a["n"] == 1.0 else "glass"
        ha, hb = heights.get(a["id"], 0.0), heights.get(b["id"], 0.0)
        over = 0.0
        for sf_, h_ in ((a, ha), (b, hb)):
            for R_, k_ in ((sf_["Rx"], sf_["kx"]), (sf_["Ry"], sf_["ky"])):
                lim_ = O._sag_limit(R_, k_, 0.95)
                if 1.08 * h_ > lim_:
                    over = max(over, 1.08 * h_ - lim_)
        if over > 0:
            out.append((a["id"], kind, -1.0 - 10.0 * over))
            continue
        h = 1.08 * (max(ha, hb) if kind == "glass" else min(ha, hb))
        worst = math.inf
        for (x, y) in ((h, 0.0), (0.0, h), (h / 1.4142, h / 1.4142)):
            sa = O.surf_sag(a, x, y); sb = O.surf_sag(b, x, y)
            if sa is None or sb is None:
                worst = -1.0; break
            worst = min(worst, a["d"] - sa[0] + sb[0])
        out.append((a["id"], kind, worst))
    return out


class Merit:
    def __init__(self, rx0, fy_target=None, sq_target=None):
        """rx0：优化起点（口径已放开）；fy_target：目标垂直焦距（None = 起点的）。
        清晰度目标与风格锁都以**原设计**（load()，在它自己的原光窗上）为参照：
          · 清晰度：原设计在原光窗上同一相对位置的复色 RMS 光斑 / 画高，换算到当前画幅高度；
          · 风格锁：原设计在它自己视场内若干物方角度的主光线像高，按焦距比例缩放后，新镜头同样角度的像高要一致（畸变分布）。"""
        self.rx0 = rx0
        self.W, self.H = ff_gate(rx0)
        orig = open_apertures(load())
        So = O.layout(orig)
        self.fyo = O.paraxial_infinity(So, "Ry")[0]
        self.sq0 = self.fyo / O.paraxial_infinity(So, "Rx")[0]
        sq_orig = self.sq0                                  # 原版自己的压缩比：原版的畸变 / 风格锁按它算（不能用目标压缩比）
        S = O.layout(rx0)
        self.fy0 = fy_target or O.paraxial_infinity(S, "Ry")[0]
        if sq_target:
            self.sq0 = sq_target
        self.relative = CFG.get("style") == "relative"
        g0 = load()["sensor"]
        self.qpts = tuple(CFG.get("qpts", QPTS))           # 评价的视场点（宽视场的设计要更密：c_65 加了水平 / 竖直 0.7、0.85）
        q0 = quality(orig, g0["width"], g0["height"], pts=self.qpts)
        self.targets = [max(q["per_mille_of_height"] / 1000 * self.H, 0.004) for q in q0]
        # 两向分开的目标（变形镜头横 / 竖的软硬比例也是风格）：原版 X、Y 光斑 / 画高 × 当前画幅高
        self.targets_xy = [(max(q["x_per_mille"] / 1000 * self.H, 0.003), max(q["y_per_mille"] / 1000 * self.H, 0.003)) for q in q0]
        # 0.9.1：分开对齐「单色清晰度」与「横向色差」（只对齐复色光斑时，优化器会拿色边换单色模糊——软的总量对了，软的样子变了）
        self.split = bool(CFG.get("split_color"))
        self.npf = 5 if self.split else 3
        self.targets_mono = [(max(q["mono_x"] / 1000 * self.H, 0.002), max(q["mono_y"] / 1000 * self.H, 0.002)) for q in q0]
        self.targets_lat = [(q["lat_x"] / 1000 * self.H, q["lat_y"] / 1000 * self.H) for q in q0]
        fx0 = self.fy0 / self.sq0
        self.flds = [(u, v, math.atan(u * self.W / 2 / fx0), math.atan(v * self.H / 2 / self.fy0)) for u, v in self.qpts]
        lens_o = FL.Lens(orig, math.inf, None)
        fxo = self.fyo / sq_orig
        self.lock_flds, self.lock_ref = [], []
        for u, v in ((1.0, 0), (0, 1.0), (0.7, 0.7), (1.0, 1.0)):
            ax, ay = math.atan(u * g0["width"] / 2 / fxo), math.atan(v * g0["height"] / 2 / self.fyo)
            P, D = aimed_bundle(lens_o, ax, ay)
            idx, X, Y, w, Do = lens_o.trace(P[:1], D[:1], FL.Lens.seq_direct(len(lens_o.S)), O.LAMBDA_D, False, 0.0, 1.38)
            r = self.fy0 / self.fyo
            self.lock_flds.append((u, v, ax, ay)); self.lock_ref.append((float(X[0]) * r, float(Y[0]) * r))
        self.L0 = sum(_sf(rx0, i)["d"] * _scale(rx0, i) for i in GAPS)
        self.N0 = self.fnum(lens_o, self.fyo)
        self.bfl_o = O.paraxial_infinity(So, "Ry")[1] * (self.fy0 / self.fyo)
        if self.relative:
            # 相对风格：原版在它自己原光窗上、同一相对位置的横 / 竖畸变 %（主光线 vs 近轴）与 f/4 相对照度
            import coverage as CV
            self.dist_t = [self.distortion(lens_o, u, v, g0["width"], g0["height"], fxo, self.fyo) for u, v in self.qpts]
            self.ri_t = CV.relative_illumination(load(), list(self.qpts), math.inf, 4.0, m=28, margin=3.0)   # 与评价时同一估算方式

    @staticmethod
    def distortion(lens, u, v, W, H, fx, fy):
        """光窗相对位置 (u, v) 的横 / 竖畸变（主光线像点 / 近轴像点 − 1）；u 或 v 为 0 时该向记 0。只瞄准主光线（光阑中心）。"""
        ax, ay = math.atan(u * W / 2 / fx), math.atan(v * H / 2 / fy)
        d = np.array([math.tan(ax), math.tan(ay), -1.0]); d /= np.linalg.norm(d)
        z0 = lens.S[0]["z"] + 5.0
        Q = aim(lens, d, np.zeros((1, 2)), O.LAMBDA_D, z0)
        if not np.isfinite(Q).all():
            return (np.nan, np.nan)
        P = np.array([[Q[0, 0], Q[0, 1], z0]]); D = d[None, :]
        idx, X, Y, w, Do = lens.trace(P, D, FL.Lens.seq_direct(len(lens.S)), O.LAMBDA_D, False, 0.0, 1.38)
        if len(X) == 0:
            return (np.nan, np.nan)
        dx = X[0] / (fx * math.tan(ax)) - 1.0 if u else 0.0
        dy = Y[0] / (fy * math.tan(ay)) - 1.0 if v else 0.0
        return (float(dx), float(dy))

    @staticmethod
    def fnum(lens, fy):
        d = np.array([0.0, 0.0, -1.0])
        Q = aim(lens, d, np.array([[0.0, lens.r_stop]]), O.LAMBDA_D, lens.S[0]["z"] + 5.0)
        return fy / (2.0 * abs(Q[0, 1]))
        self.cache = None
        self.last = None

    def bundles(self, rx):
        self.cache = None                                   # 每次评价都重新瞄准（连续）

    def normalize(self, x):
        """整支镜头按比例缩放到恰好等于目标焦距（曲率 ÷ s、间隔 × s）：焦距约束由此精确满足，不再是很「硬」的残差。"""
        try:
            fy = O.paraxial_infinity(O.layout(unpack(self.rx0, x)), "Ry")[0]
        except Exception:
            return x
        s_ = self.fy0 / fy
        if not np.isfinite(s_) or s_ <= 0:
            return x
        x = np.array(x, float).copy(); n = len(REAR)
        x[:n] /= s_; x[n:] *= s_
        return x

    def __call__(self, x):
        x = self.normalize(x)
        rx = unpack(self.rx0, x)
        try:
            S = O.layout(rx)
            fy = O.paraxial_infinity(S, "Ry")[0]; fx = O.paraxial_infinity(S, "Rx")[0]
            lens = FL.Lens(rx, math.inf, None)
        except Exception:
            return np.full(self.nres, 1e3)
        res, heights = [], {}
        nf = len(self.flds)
        rec_all = []
        for k, fld in enumerate(self.flds + self.lock_flds):
            P, D = aimed_bundle(lens, fld[2], fld[3])
            good = np.isfinite(P).all(axis=1)
            n_all = len(P)
            if good.sum() < 8 or (k >= nf and not good[0]):
                res += [50.0, 50.0] + ([20.0] * (self.npf - 2) if k < nf else []); continue
            P, D = P[good], D[good]
            if k >= nf and self.relative:                 # 相对风格：绝对像高的风格锁不适用（压缩比变了），由畸变 % 取代
                res += [0.0, 0.0]; continue
            if k >= nf:                                   # 风格锁只要主光线（光阑中心 = 第 0 个取样点）
                P, D = P[:1], D[:1]
            Xs, Ys, cen = [], [], None
            for lam in (LAM if k < nf else LAM[:1]):
                rec = {} if lam == LAM[0] else None
                idx, X, Y, w, Do = lens.trace(P, D, FL.Lens.seq_direct(len(lens.S)), lam, False, 0.0, 1.38, record=rec)
                X, Y = np.asarray(X), np.asarray(Y)
                if lam == LAM[0]:
                    if (k < nf and len(X) < 8) or (k >= nf and len(X) < 1):
                        cen = None; break
                    lost = 1.0 - len(X) / n_all               # 0.9.x：部分光线过不去时按损失比例计（连续，优化器看得见覆盖）
                    cen = (X.mean(), Y.mean()) if k < nf else (X[0], Y[0])
                    fin = set(np.asarray(idx).tolist())
                    for kk, (ik, Pk, _D, _w, _dd) in rec.items():
                        if kk >= 1 and len(Pk):
                            m = np.isin(np.asarray(ik), list(fin))
                            if m.any():
                                sid = lens.S[kk - 1]["id"]
                                heights[sid] = max(heights.get(sid, 0.0), float(np.sqrt(Pk[m, 0] ** 2 + Pk[m, 1] ** 2).max()))
                Xs.append(X); Ys.append(Y)
            if cen is None:
                res += [50.0, 50.0] + ([20.0] * (self.npf - 2) if k < nf else []); continue
            if k < nf:
                X = np.concatenate(Xs); Y = np.concatenate(Ys)
                tx, ty = self.targets_xy[k]
                # 双向匹配、两向分开：既不比原版糊，也不比原版锐（老镜头全开时的柔光 / 球差是风格的一部分）
                sw = CFG.get("spot_w", 1.0) * CFG.get("field_w", {}).get(tuple(fld[:2]), 1.0)              # 光斑比例偏差的权重（1 = 偏 100% 记 1；c_ff2 用 1/0.15）
                if self.split:
                    (tmx, tmy), (tlx, tly) = self.targets_mono[k], self.targets_lat[k]
                    lx_, ly_ = Xs[1].mean() - Xs[2].mean(), Ys[1].mean() - Ys[2].mean()
                    sl = lambda t: max(0.25 * abs(t), 0.002)          # 色差容差：原版的 25%，至少 2 µm
                    res += [sw * (Xs[0].std() / tmx - 1.0), sw * (Ys[0].std() / tmy - 1.0),
                            (lx_ - tlx) / sl(tlx), (ly_ - tly) / sl(tly), 20.0 * lost]
                else:
                    res += [sw * (math.sqrt(((X - cen[0]) ** 2).mean()) / tx - 1.0), sw * (math.sqrt(((Y - cen[1]) ** 2).mean()) / ty - 1.0), 20.0 * lost]
            else:
                r = self.lock_ref[k - nf]
                sp = SIG_POS * max(1.0, self.fy0 / 50.0)          # 风格锁容差随焦距（像高）放大：约像高的 0.03%
                res += [(cen[0] - r[0]) / sp, (cen[1] - r[1]) / sp]
        if self.relative:
            import coverage as CV
            for (u, v), (tdx, tdy) in zip(self.qpts, self.dist_t):
                dx, dy = self.distortion(lens, u, v, self.W, self.H, self.fy0 / self.sq0, self.fy0)   # 固定视场角（归一化后 fy = fy0）：主光线缓存命中
                dt = CFG.get("dist_tol", 0.005)
                res += [((dx - tdx) / dt) if np.isfinite(dx) else 20.0, ((dy - tdy) / dt) if np.isfinite(dy) else 20.0]
            try:
                ri = CV.relative_illumination(rx, list(self.qpts), math.inf, 4.0, m=28, margin=3.0)   # 不建出瞳表（快）
            except Exception:
                ri = [0.0] * len(self.qpts)
            res += [max(0.0, t - r_) / 0.05 for t, r_ in zip(self.ri_t, ri)]   # 只罚「比原版暗」：最后用口径把猫眼调到一致
        res += [(fy - self.fy0) / 0.02, (fy / fx - self.sq0) / 0.005, (self.fnum(lens, fy) - self.N0) / 0.03]
        if self.relative:                                 # X / Y 近轴像面重合（变形器在 X 向保持无焦）：直接引导，否则只靠光斑找得很慢
            res.append((O.paraxial_infinity(S, "Rx")[1] - O.paraxial_infinity(S, "Ry")[1]) / 0.02)
            res.append(max(0.0, 0.9 * self.bfl_o - O.paraxial_infinity(S, "Ry")[1]) / 0.5)   # 后截距不短于原设计的 90%（卡口）
        for sid, kind, gap in edge_thickness(rx, heights):
            lim = MIN_GLASS if kind == "glass" else MIN_AIR
            res.append(max(0.0, lim - gap) / 0.2)
        L = sum(_sf(rx, i)["d"] * _scale(rx, i) for i in GAPS)
        res.append(max(0.0, L - 1.3 * self.L0) / 0.5)
        r = np.array(res, float)
        if not hasattr(self, "nres"):
            self.nres = len(r)
        r = np.pad(r, (0, max(0, self.nres - len(r))))[: self.nres]
        self.last = r
        return r


def heights_of(rx, flds):
    """各面实际通过光线的最大高度（瞄准光束，d 线）。"""
    lens = FL.Lens(rx, math.inf, None); h = {}
    for fld in flds:
        P, D = aimed_bundle(lens, fld[2], fld[3]); good = np.isfinite(P).all(axis=1); P, D = P[good], D[good]
        rec = {}; idx, X, Y, w, Do = lens.trace(P, D, FL.Lens.seq_direct(len(lens.S)), O.LAMBDA_D, False, 0.0, 1.38, record=rec)
        fin = np.asarray(idx)
        for kk, (ik, Pk, *_r) in rec.items():
            m = np.isin(np.asarray(ik), fin)
            if kk >= 1 and m.any():
                sid = lens.S[kk - 1]["id"]; h[sid] = max(h.get(sid, 0.0), float(np.hypot(Pk[m, 0], Pk[m, 1]).max()))
    return h


def make_feasible(rx0, M, x, rounds=8):
    """起点先修成可行：边厚 / 间隙不够的间隔按差额加厚（JSON 单位 = 物理 / group scale）。"""
    n = len(REAR)
    for _ in range(rounds):
        rx = unpack(rx0, x)
        bad = [(sid, kind, gap) for sid, kind, gap in edge_thickness(rx, heights_of(rx, M.flds))
               if -1.0 < gap < (MIN_GLASS if kind == "glass" else MIN_AIR)]        # 曲面范围不够（≤ −1）加厚没用，交给优化器
        if not bad:
            break
        for sid, kind, gap in bad:
            if sid in GAPS:
                j = n + GAPS.index(sid)
                x[j] += ((MIN_GLASS if kind == "glass" else MIN_AIR) - gap + 0.1) / _scale(rx0, sid)
        print("make_feasible:", [(sid, kind, round(gap, 3)) for sid, kind, gap in bad])
    return x


_M = None


def _eval(x):
    return _M(x)


def parallel_jac(M, xs, workers=10):
    """有限差分雅可比，多进程并行（fork：子进程继承已建好的 Merit）。→ (jac(x), pool)"""
    import multiprocessing as mp
    global _M
    _M = M
    pool = mp.get_context("fork").Pool(workers)
    def jac(x, *a):
        f0 = M(x)
        h = 1e-4 * np.asarray(xs)
        X = [x + np.eye(len(x))[j] * h[j] for j in range(len(x))]
        F = pool.map(_eval, X)
        return np.column_stack([(np.asarray(F[j]) - f0) / h[j] for j in range(len(x))])
    return jac, pool


def _sys_x(rx, a, b):
    """面 a..b 在 X 截面的 ABCD（空气中）。"""
    S = O.layout(rx); idx = {sf["id"]: i for i, sf in enumerate(S)}
    M = np.eye(2); n_prev = 1.0
    for i in range(idx[a], idx[b] + 1):
        sf = S[i]; Rr = sf["Rx"]; n_next = sf["n"] if i < idx[b] else 1.0
        P = 0.0 if math.isinf(Rr) else (n_next - n_prev) / Rr
        M = np.array([[1, 0], [-P, 1]]) @ M
        if i < idx[b]:
            M = np.array([[1, sf["d"] / n_next], [0, 1]]) @ M
        n_prev = n_next
    return M


def repower(rx, neg, pos, gap, squeeze):
    """无焦柱面变形器换压缩比：负组曲率按比例缩放使 |f_neg| = f_pos / squeeze，再一维求解负 / 正组间隔使 X 向无焦。"""
    rx = copy.deepcopy(rx)
    f_neg = -1.0 / _sys_x(rx, neg[0], neg[-1])[1, 0] * -1.0
    f_pos = -1.0 / _sys_x(rx, pos[0], pos[-1])[1, 0] * -1.0
    s_ = abs(f_neg) / (abs(f_pos) / squeeze)                 # 曲率乘 s_（光焦度近似线性）
    for sid in neg:
        sf = _sf(rx, sid)
        if sf.get("R_x"):
            sf["R_x"] = sf["R_x"] / s_
    sg = _sf(rx, gap); k = _scale(rx, gap)
    def power(dd):
        sg["d"] = dd; return _sys_x(rx, neg[0], pos[-1])[1, 0]
    a_, b_ = 0.2 * sg["d"], 3.0 * sg["d"]
    fa, fb = power(a_), power(b_)
    for _ in range(80):                                      # 二分 / 割线求 X 向光焦度 = 0
        m_ = 0.5 * (a_ + b_); fm = power(m_)
        if fa * fm <= 0:
            b_, fb = m_, fm
        else:
            a_, fa = m_, fm
    sg["d"] = 0.5 * (a_ + b_)
    return rx


def start_rx():
    """优化起点：IMAX / 全部面重新设计 = 原设计整支放大到该画幅（覆盖与风格逐点相同）；repower = 原焦距、变形器换压缩比、
    光窗换成该画幅；其余 = 原设计。"""
    rx = load()
    if CFG.get("repower"):
        rp = CFG["repower"]
        rx = repower(rx, rp["neg"], rp["pos"], rp["gap"], rp["squeeze"])
        W, H = O.FORMAT_SIZES[FMT]
        rx["sensor"] = {"width": W, "height": H, "max_gate": [W, H], "format": f"{FMT} {W:g} × {H:g}"}
    elif FMT == "IMAX" or CFG.get("style") == "relative":
        rx = O.to_format(rx, FMT)
    return open_apertures(rx)


def optimize(iters=6, nfev=400, out=None, start=None, fy_target=None, sq_target=None):
    out = out or PRESET.replace(".json", "_ff_opt.json")
    from scipy.optimize import least_squares
    rx0 = start_rx()
    M = Merit(rx0, fy_target, sq_target)
    x = pack(rx0) if not start else np.array(json.load(open(start))["x"])
    if not start:                                   # 从已有的解接着优化时不做「推开边厚」（加厚会毁掉设计；边厚由评价函数的平滑惩罚管）
        x = make_feasible(rx0, M, x)
    lo, hi = bounds(rx0, x)
    x = np.clip(x, lo + 1e-9, hi - 1e-9)
    M.bundles(rx0)
    r0 = M(x)
    print(f"start: cost {0.5 * (r0 @ r0):.1f}  ({len(r0)} residuals, {len(x)} variables)")
    best_ = (float(0.5 * (r0 @ r0)), x.copy())
    for it in range(iters):
        M.bundles(unpack(rx0, x))
        xs = np.concatenate([[0.004 * _scale(rx0, i) for i in REAR], [0.5 / _scale(rx0, i) for i in GAPS], [0.5] if BFL else []])
        jac, pool = parallel_jac(M, xs)
        try:
            sol = least_squares(M, x, jac=jac, bounds=(lo, hi), method="trf", x_scale=xs, max_nfev=nfev,
                                xtol=1e-12, ftol=1e-10, gtol=1e-10, verbose=0)
        finally:
            pool.close(); pool.join()
        x = np.clip(M.normalize(sol.x), lo + 1e-9, hi - 1e-9)       # 归一化（整体缩放）后可能略出边界
        r = M(x)
        print(f"round {it + 1}: cost {0.5 * (r @ r):.2f}  nfev {sol.nfev}  status {sol.status}")
        sys.stdout.flush()
        path_ = os.path.join(ROOT, "renders", "reopt", out)
        os.makedirs(os.path.dirname(path_), exist_ok=True)
        json.dump(dict(x=x.tolist(), rx=unpack(rx0, x)), open(path_, "w"), ensure_ascii=False, indent=1)   # 每轮存档
        c_ = float(0.5 * (r @ r))
        if c_ < best_[0]:                                    # 另存代价最低的一轮（换光束后代价可能回升）
            best_ = (c_, x.copy())
            json.dump(dict(x=x.tolist(), rx=unpack(rx0, x), cost=c_), open(path_.replace(".json", "_best.json"), "w"), ensure_ascii=False, indent=1)
    rx = unpack(rx0, x)
    r = M(x); nf = M.npf * len(M.flds); nl = 2 * len(M.lock_flds)
    k = nf + nl
    nd = 2 * len(M.qpts) if M.relative else 0; nri = len(M.qpts) if M.relative else 0
    part = lambda a, b: round(0.5 * float(r[a:b] @ r[a:b]), 3)
    print("cost parts: spots", part(0, nf), " style-lock", part(nf, k), " distortion%", part(k, k + nd), " vignetting", part(k + nd, k + nd + nri),
          " focal/squeeze/fnum", part(k + nd + nri, k + nd + nri + 3), " edges/length", part(k + nd + nri + 3, len(r)))
    path = os.path.join(ROOT, "renders", "reopt", out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(dict(x=x.tolist(), rx=rx), open(path, "w"), ensure_ascii=False, indent=1)
    report(rx, M)
    return rx


def report(rx, M):
    S = O.layout(rx)
    print(f"fy {O.paraxial_infinity(S, 'Ry')[0]:.3f}  squeeze {O.paraxial_infinity(S, 'Ry')[0] / O.paraxial_infinity(S, 'Rx')[0]:.4f}"
          f"  BFL_y {O.paraxial_infinity(S, 'Ry')[1]:.3f}  BFL_x {O.paraxial_infinity(S, 'Rx')[1]:.3f}")
    for s in spots(rx, M.W, M.H):
        print(f"  field ({s['u']:.2f},{s['v']:.2f})  RMS x {1000 * s['rms_x']:6.1f}  y {1000 * s['rms_y']:6.1f} µm")



def continuation(name="neil_imax", steps=tuple(round(158.26 * 0.97 ** i, 1) for i in range(1, 16)) + (100.0,), iters=2, prev=None):
    """延拓：焦距一步步压短，每一步从上一步的解出发、整支镜头重新优化。每步存 renders/reopt/<name>_<f>.json。"""
    use(name)
    rx0 = start_rx()
    n = len(REAR)
    for f in steps:
        out = f"{name}_{f:g}.json"
        x = pack(rx0) if prev is None else np.array(json.load(open(prev))["x"])
        fy_now = O.paraxial_infinity(O.layout(unpack(rx0, x)), "Ry")[0]
        r = f / fy_now                                            # 整支按焦距比例缩到目标焦距作初值（覆盖会变小，交给优化器找回来）
        x[:n] /= r; x[n:] *= r
        seed = os.path.join(ROOT, "renders", "reopt", f"{name}_{f:g}_seed.json")
        json.dump(dict(x=x.tolist()), open(seed, "w"))
        print(f"==== step: fy {fy_now:.1f} → {f} mm ====", flush=True)
        optimize(iters=iters, nfev=80, out=out, start=seed, fy_target=f)
        prev = os.path.join(ROOT, "renders", "reopt", out)


# ───────────────────────── 做成预设 + 风格指纹 ─────────────────────────
NAMES = {
    "c_series_style": ("C 系列风格（1968）· 全画幅同焦段 50 mm（BlenderLens 重新优化）", "C 风格 50 mm 全画幅（重新优化）",
                       "设想厂商出全画幅版：50 mm 不变，变形器重配成 1.65×（大画幅变形镜头的常见做法），整支重新优化，用满全画幅 36 × 24（2.48:1）"),
    "c_65": ("C 系列风格（1968）· 65 mm 5 齿孔 50 mm 1.3×（BlenderLens 重新设计）", "C 风格 50 mm 65 mm 5 齿孔（1.3×）",
             "设想厂商出 65 mm 版（参照 Ultra Panavision 70 1.25× / Hawk65 1.3×）：50 mm 不变，变形器重配成 1.3×，整支重新优化，用满 52.48 × 23.01 片门（2.97:1）"),
    "neil_2x_50": ("Neil 2× 50 mm · 全画幅同焦段（BlenderLens 重新优化）", "Neil 2× 50 mm 全画幅（重新优化）",
                   "同一个前变形部分，后组重新优化"),
}


def build_preset(path, out_name=None):
    """优化结果 → 预设：光窗 = 整个全画幅 36 × 24（解挤压后的比例由压缩比决定），口径按新视场估算，角落放行比例选成
    「全画幅四角照度 = 原版在它自己原光窗四角的照度」（暗角 / 猫眼风格一致），再按真实边厚收回。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build_presets as BP
    import coverage as CV
    key = PRESET.replace(".json", "")
    out_name = out_name or CFG.get("out") or f"{key}_ff.json"
    data = json.load(open(path))
    x = np.array(data["x"])
    orig = load()
    ri0 = CV.coverage_report(orig, 1e7, 4.0)["relative_illumination"]
    W, H = ff_gate(orig)
    rx0 = start_rx()
    M = Merit(rx0)
    base = unpack(rx0, M.normalize(x))
    sq_nom = CFG.get("sq") or O.desqueeze(orig)               # 标称解挤压：变形器重配时 = 新压缩比（如 1.65）
    base.setdefault("anamorphic", {})["desqueeze"] = sq_nom
    fname = {"FF": "全画幅", "IMAX": "IMAX 15/70", "65MM": "65 mm 5 齿孔"}[FMT]
    base["sensor"] = {"format": f"{fname} {W:g} × {H:g} mm（解挤压后 {W * sq_nom / H:.2f}:1）", "width": W, "height": H,
                      "max_gate": [W, H]}
    if any(sf["id"] == "K1" for g in base["groups"] for sf in g["surfaces"]):
        # 补偿板在 ∞ 时是平行平板、位于物方平行光中：厚度对成像没有影响，优化器会让它漂走；恢复原厚度（马鞍面最大时边厚够）
        _sf(base, "K1")["d"] = _sf(orig, "K1")["d"]
    best = None
    for frac in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        r = copy.deepcopy(base)
        for g in r["groups"]:
            for sf in g["surfaces"]:
                sf.pop("aperture", None)
        r, _ = O.apply_apertures(r, vignette=frac)
        BP._cap_apertures_to_edges(r, min_glass=0.5, min_air=0.0)
        ri = CV.coverage_report(r, 1e7, 4.0)["relative_illumination"]   # 与 fingerprint 同一（完整）估算
        d = abs(ri["corner"] - ri0["corner"])
        if best is None or d < best[0]:
            best = (d, frac, r, ri)
    _, frac, r, ri = best
    name, short, tag = NAMES.get(CFG.get("name"), NAMES.get(key))
    m = r["meta"]
    m.update(name=name, short_name=short, tagline=tag, order=m.get("order", 0) + CFG.get("order_add", 20))
    if CFG.get("repower"):
        m["note"] = (f"不是任何真实镜头：设想厂商出这支镜头的全画幅版。BlenderLens 用 tools/reoptimize.py 设计（玻璃与片数不变）："
                     f"焦距 {M.fy0:.0f} mm 不变；变形器按解析关系重配成 {CFG['sq']}×（负组焦距 = 正组 / 压缩比，再求间隔使 X 向无焦），"
                     f"然后全部曲率与间隔一起优化。风格目标 = 原版在它自己 S35 光窗上同一相对位置的：X / Y 光斑 / 画高（中心 → 四角的软硬比例）、"
                     f"横 / 竖畸变 %（容差 {100 * CFG.get('dist_tol', 0.005):.1f} 个百分点）、f/4 相对照度；后截距 ≥ 原版的 90%。"
                     f"角落放行 {frac:.2f}（四角照度 {ri['corner']:.2f}，原版 {ri0['corner']:.2f}）。")
    else:
        m["note"] = (f"不是任何真实镜头：BlenderLens 用 tools/reoptimize.py 按原设计重新优化（可变：{', '.join(REAR)} 的曲率与相关间隔；玻璃不换）。"
                     f"约束：垂直焦距 {M.fy0:.1f} mm、压缩比、f 值、原设计视场内的主光线像高（畸变分布）与原设计相同；"
                     f"清晰度 = 原版在原光窗上同一相对位置的 X / Y 光斑 / 画高（双向匹配：不比原版锐、也不比原版糊）；"
                     f"角落放行 {frac:.2f}（全画幅四角照度 {ri['corner']:.2f}，原版原光窗 {ri0['corner']:.2f}）。")
    m["reoptimized"] = dict(tool="tools/reoptimize.py", base=PRESET, variables=REAR, gaps=GAPS, data=os.path.basename(path),
                            corner_pupil_fraction=frac, corner_RI=ri["corner"], corner_RI_original=ri0["corner"])
    m.pop("rescaled", None)
    for g in r["groups"]:
        g.pop("aperture_source", None)
    if r["focus"].get("compensator"):                       # 变形器重配后发腮不同：按新设计重算补偿凸轮表（补偿板位置 / 厚度保持优化结果）
        k1, k2 = _sf(r, "K1"), _sf(r, "K2")
        aps = {i: copy.deepcopy(_sf(r, i).get("aperture")) for i in ("K1", "K2")}
        r = BP.add_compensator(r, gap=k2["d"], thickness=k1["d"], level=r.get("look", {}).get("mumps_correction", 0.0))
        for i, a_ in aps.items():                          # add_compensator 按 A1 口径 + 1 mm 估板的口径：全画幅视场下太小（挡角落），
            if a_:                                          # 沿用按新视场估出的口径
                _sf(r, i)["aperture"] = a_
    O.save(r, os.path.join(O.PRESCRIPTION_DIR, out_name))
    print(f"saved {out_name}: corner pupil {frac}  corner RI {ri['corner']} (original {ri0['corner']})")
    return r


def fingerprint(new_name=None):
    """风格指纹：原版（原光窗）vs 重新优化（全画幅），同一相对位置。"""
    import coverage as CV
    key = PRESET.replace(".json", "")
    orig = load()
    new = O.load(os.path.join(O.PRESCRIPTION_DIR, new_name or f"{key}_ff.json"))
    rows = {}
    for tag, rx in (("原版", orig), ("全画幅重优化", new)):
        S = O.layout(rx)
        fy, fx = O.paraxial_infinity(S, "Ry")[0], O.paraxial_infinity(S, "Rx")[0]
        mfd = rx["focus"]["min_distance_mm"]
        rep = CV.coverage_report(rx, 1e7, 4.0)
        rows[tag] = dict(gate=[round(rx["sensor"]["width"], 2), rx["sensor"]["height"]], fy=round(fy, 3), squeeze=round(fy / fx, 4),
                         squeeze_mfd=round(O.squeeze_at(rx, mfd), 4), mfd=mfd,
                         distortion_pct=rep["distortion_pct"], bow_px_4k=rep["edge_vertical_bow_px"],
                         RI=rep["relative_illumination"], half_field=rep["half_field_deg"]["current_diag"],
                         crop_factor=rep["format"]["crop_factor_vs_36x24"])
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    return rows



def compare(path=None):
    path = path or os.path.join(ROOT, "renders", "reopt", PRESET.replace(".json", "_ff_opt.json"))
    new = json.load(open(path))["rx"]
    orig = open_apertures(load())
    g = load()["sensor"]
    Wf, Hf = ff_gate(orig)
    print(f"原版（原光窗 {g['width']:.1f} × {g['height']:.1f}）  vs  原版直接上全画幅  vs  重新优化（全画幅 {Wf} × {Hf}）；‰ = 光斑 / 光窗高")
    a = quality(orig, g["width"], g["height"]); b = quality(orig, Wf, Hf); c = quality(new, Wf, Hf)
    for x, y, z in zip(a, b, c):
        print(f"  ({x['u']:.1f},{x['v']:.1f})  S35 {x['rms_um']:6.1f} µm {x['per_mille_of_height']:5.2f}‰   "
              f"原版全画幅 {y['rms_um']:6.1f} µm {y['per_mille_of_height']:5.2f}‰   重优化全画幅 {z['rms_um']:6.1f} µm {z['per_mille_of_height']:5.2f}‰")


def continuation2(name="c_ff", f_end=50.0, sq_end=1.65, n=10, iters=2, prev=None, k0=0):
    """焦距与压缩比一起走：从起点（原设计整支放大到画幅）到 (f_end, sq_end)，焦距按几何、压缩比按线性插值。"""
    use(name)
    rx0 = start_rx()
    S = O.layout(rx0)
    f0 = O.paraxial_infinity(S, "Ry")[0]; q0 = f0 / O.paraxial_infinity(S, "Rx")[0]
    nr = len(REAR)
    for i in range(k0 + 1, n + 1):
        t = i / n
        f, q = f0 * (f_end / f0) ** t, q0 + (sq_end - q0) * t
        out = f"{name}_{i:02d}.json"
        x = pack(rx0) if prev is None else np.array(json.load(open(prev))["x"])
        fy_now = O.paraxial_infinity(O.layout(unpack(rx0, x)), "Ry")[0]
        r = f / fy_now
        x[:nr] /= r; x[nr:] *= r
        seed = os.path.join(ROOT, "renders", "reopt", f"{name}_{i:02d}_seed.json")
        json.dump(dict(x=x.tolist()), open(seed, "w"))
        print(f"==== step {i}/{n}: fy {fy_now:.1f} → {f:.1f} mm, squeeze → {q:.3f} ====", flush=True)
        optimize(iters=iters, nfev=80, out=out, start=seed, fy_target=f, sq_target=q)
        prev = os.path.join(ROOT, "renders", "reopt", out)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if cmd == "optimize" and len(sys.argv) > 3:
        use(sys.argv[3])
    if cmd == "optimize":
        name_ = sys.argv[3] if len(sys.argv) > 3 else None
        optimize(iters=int(sys.argv[2]) if len(sys.argv) > 2 else 6, fy_target=CFG.get("fy"), sq_target=CFG.get("sq"),
                 out=f"{name_}_opt.json" if name_ else None, start=sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == "continuation2":
        continuation2(sys.argv[2] if len(sys.argv) > 2 else "c_ff")
    elif cmd == "continuation":
        # continuation <name> [<从哪个结果接着跑> <起始焦距>]
        name = sys.argv[2] if len(sys.argv) > 2 else "neil_imax"
        if len(sys.argv) > 4:
            f0 = float(sys.argv[4])
            steps = tuple(round(f0 * 0.97 ** i, 1) for i in range(1, 40) if f0 * 0.97 ** i > 100.0) + (100.0,)
            continuation(name, steps=steps, prev=sys.argv[3])
        else:
            continuation(name)
    else:
        baseline()
