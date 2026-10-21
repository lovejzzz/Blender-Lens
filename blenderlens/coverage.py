# 覆盖与畸变报告（0.8.6，MessyRoom 广角需求 W2 / W3）。纯光学，不依赖 bpy。
#   coverage_report(rx, focus_mm, f_number) → 像圈、四角相对照度、横 / 竖径向畸变、画面边缘竖直线弯曲（4K 像素）、
#   当前视场 vs 能覆盖的视场、以及一句结论与警告。
# 定义：
#   · 相对照度 RI：与 Analytic 渲染同一个估计器——在出瞳平面的采样盘上均匀取点，从传感器点射过去，能穿过整支镜头的
#     光线按 cos⁴ 加权求和（pupil._passes，与 OSL / GPU 同一判据），以画面中心为 1。渲染的四角照度与它对照
#     （tests/bl_coverage.py）。注意 optics.pupil_transmission（瞄准光阑的网格）会放过「镜片边缘厚度为负」处的光线
#     （两面在口径内相交，光线要倒着走），所以不用它。
#   · 像圈：沿对角线 RI 降到 2% 的位置（再往外就是「舷窗」的黑边）。
#   · 能覆盖的视场：RI ≥ 阈值（默认 0.35）的最大对角线视场；当前视场 = 光窗四角的主光线物方角。
#   · 畸变：主光线追迹（d 线）。横向：画面右边缘中点 x_real 与 fx·tan(θx) 之差 / 后者（fx = 水平焦距）；竖向同理用 fy；
#     角落按两向分别给。负 = 桶形。
#   · 竖直线弯曲：物方一条竖直线（θx 固定为 95% 画宽处的物方角）从画面中间到 95% 画高，在像面上水平偏了多少，按输出像素
#     （默认 4K 高 2160）。负 = 向画面中心弯（桶形）。像点用正向追迹主光线 + 二分求得；那条线在画面上边已经出画（或
#     追不到）时为 None。
import math
import numpy as np

try:
    from . import optics as O
except ImportError:                                    # 纯 Python（tests、tools）
    import optics as O

RI_COVER = 0.35            # MessyRoom W1：四角照度 ≥ 中心的 35% 才算覆盖
RI_CIRCLE = 0.02           # 像圈边缘


def _pu():
    try:
        from . import pupil as PU
    except ImportError:
        import pupil as PU
    return PU


class _Estimator:
    """出瞳平面采样盘上的 cos⁴ 加权通光量（OSL 的直射光路估计器）。"""

    def __init__(self, rx, focus_mm, f_number, m, margin=None):
        try:
            from . import oslgen as G
        except ImportError:
            import oslgen as G
        PU = _pu()
        z_exp, r_exp, z_stop, r_stop = G.exit_pupil(rx)
        d1, d2 = O.solve_focus(rx, focus_mm)
        self.S = O.layout(rx, d1, d2)
        st_id = O.stop_group(rx)["stop"]["after_surface"]
        mv = next(sf["move"] for sf in self.S if sf["id"] == st_id)
        sh = mv[0] * d1 + mv[1] * d2
        k = O.nominal_f_number(rx) / f_number
        self.z_exp, self.z_stop, self.r_stop = z_exp + sh, z_stop + sh, r_stop * k
        if margin is None:                                  # 不指定时按出瞳表的采样盘（与渲染一致）；指定时不建表（优化器里用，快）
            try:
                margin = float(G.pupil_table(rx)["r_samp"]) / r_exp
            except Exception:                               # noqa: BLE001
                margin = 1.4
        r_s = r_exp * k * margin
        u = (np.arange(m) + 0.5) / m * 2 - 1
        gx, gy = np.meshgrid(u * r_s, u * r_s)
        ins = gx ** 2 + gy ** 2 <= r_s ** 2
        self.qx, self.qy = gx[ins], gy[ins]
        self.PU = PU

    def __call__(self, field):
        n = len(self.qx)
        P = np.tile([field[0], field[1], 0.0], (n, 1))
        D = np.column_stack([self.qx - field[0], self.qy - field[1], np.full(n, self.z_exp)])
        D /= np.linalg.norm(D, axis=1)[:, None]
        ok = self.PU._passes(self.S, self.z_stop, self.r_stop, P, D, O.LAMBDA_D)
        return float((ok * D[:, 2] ** 4).sum())


def _corner_angle(ang, W, H, fx, fy, n=12):
    """光窗四角的物方主光线半视场（对角线）。0.9.0 修正（MessyRoom 报：PV 长焦端全画幅显示 4.7°）：角落超出镜头能成像的范围时，
    主光线追迹会给出乱的角度（S35 51.8°、全画幅 116.8°，同一支镜头）。沿对角线逐点追，只用连续、单调的点；角落不可信时
    用最后一个可信点的「真实 / 近轴」tan 比外推到角落，并标记 estimated=True。"""
    par = lambda t: math.hypot(t * W / 2 / fx, t * H / 2 / fy)          # 近轴 tan
    last, prev, step = None, 0.0, None
    for i in range(1, n + 1):
        t = i / n
        a = ang(t * W / 2, t * H / 2)
        if a is None or not (prev < a < 89.0) or (step is not None and a - prev > 3.0 * step + 2.0):
            break                                      # 追不过去 / 不单调 / 突然跳大 → 后面的都不可信
        step, prev, last = a - prev, a, (t, a)
    if last is None:
        return None, True
    t0, a0 = last
    if t0 >= 1.0:
        return a0, False
    r = math.tan(math.radians(a0)) / par(t0)
    return math.degrees(math.atan(r * par(1.0))), True


def _chief_dir(LM, sx, sy):
    """传感器点 → 物方主光线方向（镜头坐标，物方 +Z）。"""
    from_lp = _lp()
    P0 = np.array([[sx, sy, 0.0]])
    D0 = np.array([[-sx, -sy, LM.z_exp]]); D0 /= np.linalg.norm(D0)
    Po, Do = from_lp._trace_out(LM.S, LM.z_stop, LM.r_stop, P0, D0, O.LAMBDA_D)
    return Do[0] if np.isfinite(Do).all() else None


def _chief_tan(LM, sx, sy, lam):
    """传感器点 → 物方主光线的 (tan θx, tan θy)，波长 lam（µm）。"""
    P0 = np.array([[sx, sy, 0.0]])
    D0 = np.array([[-sx, -sy, LM.z_exp]]); D0 /= np.linalg.norm(D0)
    Po, Do = _lp()._trace_out(LM.S, LM.z_stop, LM.r_stop, P0, D0, lam)
    if not np.isfinite(Do).all():
        return None
    return Do[0, 0] / Do[0, 2], Do[0, 1] / Do[0, 2]


def _flare():
    try:
        from . import flare as FL
    except ImportError:
        import flare as FL
    return FL


def lateral_color_px(LM, W, H, desq, out_h=2160, frac=0.97, rx=None, focus_mm=math.inf, f_number=None, n_grid=90):
    """横向色差（0.9.2，MessyRoom 的美术调研：4K 角落 1–2 px 被认为是真实的）：同一个物点，两个波长在像面上的光斑重心之差，
    换算成解挤压后、out_h 行输出上的像素（渲染里看到的色边就是各波长光斑重心的错位）。另给只用主光线的值（chief_*，
    慧差大时会比重心大一倍左右，只作参考）。→ {edge_h / edge_v / corner: {"F_C": [x, y, 合], "400_700": [...], "chief_F_C": [...]}}；
    x、y 带符号：蓝相对红向外为正。"""
    FL = _flare()
    out_w = out_h * W / H * desq
    pts = dict(edge_h=(frac * W / 2, 0.0), edge_v=(0.0, frac * H / 2), corner=(frac * W / 2, frac * H / 2))
    lens = FL.Lens(rx, focus_mm, f_number) if rx is not None else None
    res = {}
    for name, (sx, sy) in pts.items():
        row = {}
        # 主光线（线性化：同一物方方向，s_λ ≈ s − (tan_λ − tan_d) / (∂tan/∂s)）
        t0 = _chief_tan(LM, sx, sy, O.LAMBDA_D); h = 1e-3
        tx1 = _chief_tan(LM, sx + h, sy, O.LAMBDA_D); ty1 = _chief_tan(LM, sx, sy + h, O.LAMBDA_D)
        b, r = _chief_tan(LM, sx, sy, 0.4861), _chief_tan(LM, sx, sy, 0.6563)
        if None not in (t0, tx1, ty1, b, r):
            dtx, dty = (tx1[0] - t0[0]) / h, (ty1[1] - t0[1]) / h
            dx = -((b[0] - r[0]) / dtx) if abs(dtx) > 1e-12 and sx else 0.0
            dy = -((b[1] - r[1]) / dty) if abs(dty) > 1e-12 and sy else 0.0
            px, py = dx / W * out_w * (1 if sx >= 0 else -1), dy / H * out_h * (1 if sy >= 0 else -1)
            row["chief_F_C"] = [round(float(px), 2), round(float(py), 2), round(float(math.hypot(px, py)), 2)]
        # 光斑重心：物点 = 本像点的 d 线主光线反向追到对焦距离（∞ 时取很远），从物点向前片发一束均匀光线，各波长重心之差
        if lens is not None:
            try:
                S_ = lens.S
                pnt, dv = O.trace_reverse(S_, (sx, sy, 0.0), O.aim_through_stop(S_, (sx, sy), (0.0, 0.0), lens.z_stop), clip=False)
                dist_ = focus_mm if math.isfinite(focus_mm) else 1.0e7
                t = (dist_ - pnt[2]) / dv[2]
                src = (pnt[0] + t * dv[0], pnt[1] + t * dv[1], dist_)
                seq = FL.Lens.seq_direct(len(S_))
                for ng in (n_grid, 4 * n_grid):             # 先粗找通光区（极广角的边缘可能一条都过不去，再密一些找）
                    P_, D_, wt = FL._source_rays(lens, src, ng)
                    idx, X, Y, w, Do = lens.trace(P_, D_, seq, O.LAMBDA_D, False, 0.0, 1.38)
                    if len(X):
                        break
                if 0 < len(X) < 400:                        # 广角边缘：能过镜头的只占前片一小块 → 在那一块（外扩一格）里重新加密
                    z0 = S_[0]["z"] + 5.0
                    Tg = P_[np.asarray(idx)] + D_[np.asarray(idx)] * ((z0 - P_[np.asarray(idx), 2]) / D_[np.asarray(idx), 2])[:, None]
                    ext = O._outer_extent(S_[0])
                    cell = 2.04 * max(ext) / ng
                    x0, x1 = Tg[:, 0].min() - cell, Tg[:, 0].max() + cell
                    y0, y1 = Tg[:, 1].min() - cell, Tg[:, 1].max() + cell
                    gx, gy = np.meshgrid(np.linspace(x0, x1, n_grid), np.linspace(y0, y1, n_grid))
                    T_ = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z0)], 1)
                    D_ = T_ - np.asarray(src, float); dd = np.linalg.norm(D_, axis=1); D_ = D_ / dd[:, None]
                    P_ = np.repeat(np.asarray(src, float)[None, :], len(T_), 0)
                    wt = (-D_[:, 2]) / (dd * dd); wt = wt / wt.mean()
                cen = {}
                for lam in (0.4861, 0.6563, 0.400, 0.700):
                    idx, X, Y, w, Do = lens.trace(P_, D_, seq, lam, False, 0.0, 1.38)
                    if len(X) < 8:
                        raise ValueError("光线过不去")
                    ww = np.asarray(w) * wt[np.asarray(idx)]
                    cen[lam] = (float(np.average(X, weights=ww)), float(np.average(Y, weights=ww)))
                for tag, (lb, lr) in (("F_C", (0.4861, 0.6563)), ("400_700", (0.400, 0.700))):
                    dx, dy = cen[lb][0] - cen[lr][0], cen[lb][1] - cen[lr][1]
                    px = dx / W * out_w * (1 if sx >= 0 else -1) if sx else 0.0
                    py = dy / H * out_h * (1 if sy >= 0 else -1) if sy else 0.0
                    row[tag] = [round(float(px), 2), round(float(py), 2), round(float(math.hypot(px, py)), 2)]
            except Exception:                              # 角落追不过去（缩得太短、不覆盖）→ 该点没有结果
                row["F_C"] = row["400_700"] = None
        res[name] = row
    res["out_height"] = out_h
    return res


def _lp():
    try:
        from . import lenspack as LP
    except ImportError:
        import lenspack as LP
    return LP


def coverage_report(rx, focus_mm=math.inf, f_number=None, out_h=2160, n=21, ri_cover=RI_COVER, quick=False):
    """→ dict（见文件头）。rx：当前处方（已缩放 / 已套光窗）；focus_mm：对焦（从像面量，mm）；out_h：弯曲按这个输出高度算。
    quick：只算中心 / 边缘 / 四角的相对照度与结论（约 1 秒，build 用）；完整报告约 5–15 秒。"""
    LP = _lp()
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    f_number = max(f_number or 0.0, O.nominal_f_number(rx))         # 与渲染一致：不会比镜头最大光圈更大
    S = O.layout(rx, *O.solve_focus(rx, focus_mm))
    fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
    try:
        from . import oslgen as G
    except ImportError:
        import oslgen as G
    est = _Estimator(rx, focus_mm, f_number, m=2 * n + 30)
    ri0 = est((0.0, 0.0)) or 1e-9
    ri = lambda x, y: est((x, y)) / ri0
    pts = dict(edge_h=(W / 2, 0.0), edge_v=(0.0, H / 2), corner=(W / 2, H / 2))
    rel = {k: round(ri(*v), 4) for k, v in pts.items()}
    if quick:
        covers = rel["corner"] >= ri_cover
        warnings = []
        if rel["corner"] < RI_CIRCLE or rel["edge_h"] < RI_CIRCLE or rel["edge_v"] < RI_CIRCLE:
            warnings.append(f"画面边缘 / 四角几乎没有光（相对照度 边 {rel['edge_h']:.0%} / {rel['edge_v']:.0%}，角 {rel['corner']:.0%}）："
                            "像圈比光窗小，会出现舷窗式黑边——焦距缩得太短，或换覆盖更大的镜头")
        elif not covers:
            warnings.append(f"四角相对照度 {rel['corner']:.0%} < {ri_cover:.0%}：四角明显发暗")
        return dict(relative_illumination=rel, covers=bool(covers), ri_threshold=ri_cover, f_number=f_number, warnings=warnings,
                    quick=True, format=format_info(rx))
    # 沿对角线找像圈与能覆盖的位置
    diag = math.hypot(W / 2, H / 2)
    ux, uy = W / 2 / diag, H / 2 / diag
    rs = np.linspace(0.0, 1.6 * diag, 33)
    prof = [1.0] + [ri(r * ux, r * uy) for r in rs[1:]]

    def cross(level):
        for a, b, ra, rb in zip(prof[:-1], prof[1:], rs[:-1], rs[1:]):
            if a >= level > b:
                return float(ra + (a - level) / max(a - b, 1e-12) * (rb - ra))
        return float(rs[-1]) if prof[-1] >= level else 0.0
    r_circle, r_cover = cross(RI_CIRCLE), cross(ri_cover)
    # 视场（物方主光线角，对角线）
    LM = LP.LensMap(rx, focus_mm)
    def ang(sx, sy):
        d = _chief_dir(LM, sx, sy)
        return None if d is None else math.degrees(math.atan2(math.hypot(d[0], d[1]), d[2]))
    cur, cur_est = _corner_angle(ang, W, H, fx, fy)
    cov = ang(r_cover * ux, r_cover * uy) if r_cover > 0 else 0.0
    # 畸变
    def dist(sx, sy, axis):
        d = _chief_dir(LM, sx, sy)
        if d is None:
            return None
        if axis == "x":
            ideal = fx * d[0] / d[2]
            return None if abs(ideal) < 1e-9 else round(100.0 * (abs(sx) - abs(ideal)) / abs(ideal), 3)
        ideal = fy * d[1] / d[2]
        return None if abs(ideal) < 1e-9 else round(100.0 * (abs(sy) - abs(ideal)) / abs(ideal), 3)
    dist_pct = dict(edge_h=dist(W / 2, 0.0, "x"), edge_v=dist(0.0, H / 2, "y"),
                    corner_h=dist(W / 2, H / 2, "x"), corner_v=dist(W / 2, H / 2, "y"))
    # 竖直线弯曲：物方一条竖直线（tan θx = 95% 画宽处那条中心行上的值）；在传感器的每个高度上，沿 x 二分求出
    # 它的像点（正向追迹主光线，不用反投影表——大畸变时表的范围不够）
    def tanx(sx, sy):
        d = _chief_dir(LM, sx, sy)
        return None if d is None else d[0] / d[2]
    bow_px = None
    t0 = tanx(0.95 * W / 2, 0.0)
    if t0 is not None:
        def solve_x(sy):
            lo, hi = 0.0, 0.999 * W / 2
            flo, fhi = tanx(lo, sy), tanx(hi, sy)
            if flo is None or fhi is None or (flo - t0) * (fhi - t0) > 0:
                return None
            for _ in range(40):
                mid = 0.5 * (lo + hi); fm = tanx(mid, sy)
                if fm is None:
                    return None
                if (flo - t0) * (fm - t0) <= 0:
                    hi, fhi = mid, fm
                else:
                    lo, flo = mid, fm
            return 0.5 * (lo + hi)
        x_mid, x_top = solve_x(0.0), solve_x(0.95 * H / 2)
        if x_mid is not None and x_top is not None:
            out_w = out_h * W / H * O.desqueeze(rx)
            bow_px = round(float((abs(x_top) - abs(x_mid)) / W * out_w), 2)
    try:
        lateral_px = lateral_color_px(LM, W, H, O.desqueeze(rx), out_h, rx=rx, focus_mm=focus_mm, f_number=f_number)
    except Exception as e:                                 # 色差只是附加信息，失败不影响覆盖报告
        lateral_px = dict(error=repr(e)[:120])
    covers = rel["corner"] >= ri_cover
    hard_edge = r_circle < diag                        # 像圈比光窗对角线小：画面里有「舷窗」黑边
    warnings = []
    if hard_edge:
        warnings.append(f"像圈 {2 * r_circle:.1f} mm < 光窗对角线 {2 * diag:.1f} mm：画面四周会有舷窗式的黑边（机械渐晕）")
    elif not covers:
        warnings.append(f"四角相对照度 {rel['corner']:.0%} < {ri_cover:.0%}：四角明显发暗")
    if cur is not None and cov is not None and cur > cov + 0.05:
        warnings.append(f"当前对角线半视场 {cur:.1f}° 超过这支镜头能覆盖的 {cov:.1f}°（照度 ≥ {ri_cover:.0%}）：焦距缩得太短")
    return dict(gate_mm=[W, H], gate_diagonal_mm=round(2 * diag, 2), image_circle_mm=round(2 * r_circle, 2),
                format=format_info(rx),
                relative_illumination=rel, covers=bool(covers and not hard_edge), ri_threshold=ri_cover,
                half_field_deg=dict(current_diag=None if cur is None else round(cur, 2),
                                    current_diag_estimated=cur_est,
                                    max_covered_diag=None if cov is None else round(cov, 2)),
                distortion_pct=dist_pct, edge_vertical_bow_px=bow_px, bow_out_height=out_h, lateral_color_px=lateral_px,
                f_number=f_number, focus_mm=None if not math.isfinite(focus_mm) else focus_mm, warnings=warnings)


FF_DIAG = math.hypot(36.0, 24.0)


def format_info(rx):
    """画幅换算（0.8.8 后 MessyRoom 的建议）：光窗对角线 vs 全画幅 36 × 24（crop factor）；同样取景、同样 f 值时景深与 crop factor
    成正比（光窗小 → 景深深）。全画幅版本另给与 Super 35 原设计（同一光窗方式）的对角线比——2× 镜头的 16:9_NATIVE 在
    全画幅上受 24 mm 高度限制，只比 S35 大 8% 左右，景深也只浅这么多。"""
    s = rx["sensor"]
    diag = math.hypot(s["width"], s["height"])
    out = dict(gate_diagonal_mm=round(diag, 2), crop_factor_vs_36x24=round(FF_DIAG / diag, 3),
               dof_vs_36x24_same_framing_and_stop=round(FF_DIAG / diag, 3))
    fm = rx.get("meta", {}).get("format")
    if fm and fm.get("name") == "FF":
        k = fm["factor"]
        fa = s.get("full_aperture") or s
        base = dict(rx, sensor={"width": fa["width"] / k, "height": fa["height"] / k})
        g = s.get("gate") or "FULL"
        try:
            b = O.apply_gate(base, g) if g not in ("FULL", "CUSTOM") else base
            d0 = math.hypot(b["sensor"]["width"], b["sensor"]["height"])
            out["vs_s35_design"] = dict(gate=g, s35_gate_mm=[round(b["sensor"]["width"], 2), round(b["sensor"]["height"], 2)],
                                        gate_diagonal_ratio=round(diag / d0, 3), dof_factor=round(d0 / diag, 3),
                                        lens_scale=round(k, 4),
                                        note="同样取景、同样 f 值：景深 × dof_factor（< 1 = 更浅）；lens_scale 是镜头放大倍数")
        except Exception:                                  # noqa: BLE001
            pass
    return out


def relative_illumination(rx, points, focus_mm=math.inf, f_number=None, m=72, margin=None):
    """画面上任意点（传感器坐标的比例 (u, v)，−1…1，1 = 光窗边缘）的相对照度（中心 = 1），与渲染同一估计器。"""
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    f_number = max(f_number or 0.0, O.nominal_f_number(rx))
    est = _Estimator(rx, focus_mm, f_number, m=m, margin=margin)
    c = est((0.0, 0.0)) or 1e-9
    return [est((u * W / 2, v * H / 2)) / c for u, v in points]
