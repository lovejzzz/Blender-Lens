# 光学核心：读处方、近轴追迹、真实光线追迹、孔径估算、双组联动对焦。
# 纯 Python，不依赖 bpy —— Blender 内外都能跑、能单测。
# 约定：原点 = 像面；物方 +Z；光沿 −Z；表面 z = z_vertex − sag(x, y)；单位 mm。
import json, math, os

INF = math.inf
LAMBDA_D, LAMBDA_F, LAMBDA_C = 0.5875618, 0.4861327, 0.6562725   # μm
PRESCRIPTION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prescriptions")
DEFAULT_PRESCRIPTION = os.path.join(PRESCRIPTION_DIR, "rosin_nikon_2x50.json")


# ───────────────────────── 处方 ─────────────────────────

def load(path=DEFAULT_PRESCRIPTION):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save(rx, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rx, f, ensure_ascii=False, indent=2)
        f.write("\n")

def cauchy(nd, vd):
    """阿贝数 → Cauchy (A, B[μm²])，见文档第一节。"""
    if vd is None or nd == 1.0:
        return nd, 0.0
    B = (nd - 1.0) / (vd * (LAMBDA_F ** -2 - LAMBDA_C ** -2))
    return nd - B / LAMBDA_D ** 2, B

def index_at(glass, lam_um=LAMBDA_D):
    A, B = cauchy(glass["nd"], glass.get("vd"))
    return A + B / (lam_um * lam_um)

def flatten(rx):
    """整支镜头的面列表（R、d 已乘各组 scale；aperture 为物理 mm，None = 尚未估算）。"""
    out = []
    for g in rx["groups"]:
        s = g["scale"]
        for sf in g["surfaces"]:
            R = lambda v: INF if v is None else v * s
            glass = rx["glasses"][sf["medium"]]
            out.append(dict(
                id=sf["id"], group=g["id"],
                Rx=R(sf["R_x"]), Ry=R(sf["R_y"]),
                kx=sf.get("k_x", 0.0), ky=sf.get("k_y", 0.0),
                d=None if sf["d"] is None else sf["d"] * s,
                medium=sf["medium"], glass=glass, n=glass["nd"],
                ap=sf.get("aperture"),
                element=sf.get("element")))
    return out

def curv(R):
    return 0.0 if math.isinf(R) else 1.0 / R


# ───────────────────────── 近轴 ─────────────────────────

def _paraxial_core(S, axis, y, nu, n=1.0, until=None):
    """从第一面前出发，返回最后一面后的 (y, nu, n)。axis = 'Rx'/'Ry'。"""
    last = len(S) - 1 if until is None else until
    for i in range(last + 1):
        sf = S[i]
        P = 0.0 if math.isinf(sf[axis]) else (sf["n"] - n) / sf[axis]
        nu -= y * P
        n = sf["n"]
        if i < last:
            y += sf["d"] * nu / n
    return y, nu, n

def paraxial_infinity(S, axis):
    """平行光入射 → (EFL, BFL)。"""
    y, nu, n = _paraxial_core(S, axis, 1.0, 0.0)
    u = nu / n
    return -1.0 / u, -y / u

def paraxial_finite(S, axis, obj_dist_from_first):
    """轴上物点在第一面前 L 处 → (像距 from 最后一面, 横向放大率)。"""
    L = obj_dist_from_first
    u0 = 1e-3
    y, nu, n = _paraxial_core(S, axis, L * u0, u0)
    u = nu / n
    return -y / u, (u0 / nu)  # m = n0 u0 / (n' u')，n0 = 1


# ───────────────────────── 布局与对焦 ─────────────────────────

def layout(rx, delta_unit=0.0, delta_front=0.0):
    """各面顶点 z（像面原点）。无穷远合焦时 z_last = BFL(垂直)；对焦量叠加在 focus 组上。"""
    S = flatten(rx)
    _, bfl = paraxial_infinity(S, "Ry")
    z = [0.0] * len(S)
    z[-1] = bfl
    for i in range(len(S) - 2, -1, -1):
        z[i] = z[i + 1] + S[i]["d"]
    f = rx.get("focus", {})
    unit, front = set(f.get("unit", [])), set(f.get("front_member", []))
    for i, sf in enumerate(S):
        if sf["id"] in unit:
            z[i] += delta_unit
        elif sf["id"] in front:
            z[i] += delta_unit + delta_front
        sf["z"] = z[i]
    # 用新 z 回填各面 d（最后一面 d = 到像面）
    for i in range(len(S) - 1):
        S[i]["d"] = S[i]["z"] - S[i + 1]["z"]
    S[-1]["d"] = S[-1]["z"]
    return S

def _image_z(S, axis, obj_z):
    """物点在 z = obj_z（轴上），返回近轴像点 z（像面原点下）。"""
    img_from_last, _ = paraxial_finite(S, axis, obj_z - S[0]["z"])
    return S[-1]["z"] - img_from_last

def _secant(f, x0, x1, tol=1e-9, it=60):
    f0, f1 = f(x0), f(x1)
    for _ in range(it):
        if abs(f1 - f0) < 1e-15:
            break
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        x0, f0, x1, f1 = x1, f1, x2, f(x2)
        if abs(f1) < tol:
            break
    return x1

def solve_focus(rx, obj_dist):
    """物距 obj_dist（从像面量起，mm；inf = 无穷远）→ (Δ1, Δ2)。"""
    if math.isinf(obj_dist):
        return 0.0, 0.0
    d1 = _secant(lambda d: _image_z(layout(rx, d, 0.0), "Ry", obj_dist), 0.0, 1.0)
    d2 = _secant(lambda d: _image_z(layout(rx, d1, d), "Rx", obj_dist), 0.0, 1.0)
    return d1, d2

def squeeze_at(rx, obj_dist):
    """该物距下的实际压缩比 = 垂直放大率 / 水平放大率（无穷远时用 EFL 比）。"""
    d1, d2 = solve_focus(rx, obj_dist)
    S = layout(rx, d1, d2)
    if math.isinf(obj_dist):
        return paraxial_infinity(S, "Ry")[0] / paraxial_infinity(S, "Rx")[0]
    L = obj_dist - S[0]["z"]
    return paraxial_finite(S, "Ry", L)[1] / paraxial_finite(S, "Rx", L)[1]


# ───────────────────────── 曲面 ─────────────────────────

def sag_and_grad(x, y, cx, cy, kx=0.0, ky=0.0):
    """双二次曲面矢高及其解析梯度；超出有效区返回 None。"""
    N = cx * x * x + cy * y * y
    Q = 1.0 - (1 + kx) * cx * cx * x * x - (1 + ky) * cy * cy * y * y
    if Q <= 0.0:
        return None
    Sq = math.sqrt(Q)
    den = 1.0 + Sq
    dSx = -(1 + kx) * cx * cx * x / Sq
    dSy = -(1 + ky) * cy * cy * y / Sq
    gx = (2 * cx * x * den - N * dSx) / (den * den)
    gy = (2 * cy * y * den - N * dSy) / (den * den)
    return N / den, gx, gy


# ───────────────────────── 真实光线追迹 ─────────────────────────

def _intersect(sf, p, d):
    cx, cy = curv(sf["Rx"]), curv(sf["Ry"])
    t = (sf["z"] - p[2]) / d[2]
    for _ in range(30):
        x, y = p[0] + t * d[0], p[1] + t * d[1]
        r = sag_and_grad(x, y, cx, cy, sf["kx"], sf["ky"])
        if r is None:
            return None
        s, gx, gy = r
        g = p[2] + t * d[2] - sf["z"] + s
        dg = d[2] + gx * d[0] + gy * d[1]
        if abs(dg) < 1e-12:
            return None
        dt = g / dg
        t -= dt
        if abs(dt) < 1e-10:
            break
    x, y, z = p[0] + t * d[0], p[1] + t * d[1], p[2] + t * d[2]
    s, gx, gy = sag_and_grad(x, y, cx, cy, sf["kx"], sf["ky"])
    L = math.sqrt(gx * gx + gy * gy + 1.0)
    return (x, y, z), (gx / L, gy / L, 1.0 / L)

def _refract(d, nrm, n1, n2):
    c = -(d[0] * nrm[0] + d[1] * nrm[1] + d[2] * nrm[2])
    if c < 0:                                     # 法线翻到入射侧
        nrm = (-nrm[0], -nrm[1], -nrm[2]); c = -c
    eta = n1 / n2
    k = 1.0 - eta * eta * (1.0 - c * c)
    if k < 0:
        return None                               # 全反射
    a = eta * c - math.sqrt(k)
    return (eta * d[0] + a * nrm[0], eta * d[1] + a * nrm[1], eta * d[2] + a * nrm[2])

def media(S, lam_um=LAMBDA_D):
    """每面前、后介质折射率（n_before[i], n_after[i]）。"""
    after = [index_at(sf["glass"], lam_um) for sf in S]
    before = [1.0] + after[:-1]
    return before, after

def trace_reverse(S, p, d, lam_um=LAMBDA_D, start=None, stop=None, clip=True, record=None):
    """从像方往物方（+Z）追迹：依次经过 S[start]…S[0]。
    stop = (z_stop, r_stop) 时在穿过光阑平面时检查。返回 (p, d) 或 None（被挡/全反射）。"""
    nb, na = media(S, lam_um)
    i0 = len(S) - 1 if start is None else start
    for i in range(i0, -1, -1):
        sf = S[i]
        if stop is not None and sf["z"] > stop[0] >= p[2]:
            t = (stop[0] - p[2]) / d[2]
            sx, sy = p[0] + t * d[0], p[1] + t * d[1]
            if clip and sx * sx + sy * sy > stop[1] ** 2:
                return None
        hit = _intersect(sf, p, d)
        if hit is None:
            return None
        p, nrm = hit
        if record is not None:
            record[i] = p
        if clip and not inside_aperture(sf, p[0], p[1]):
            return None
        d = _refract(d, nrm, na[i], nb[i])
        if d is None:
            return None
    return p, d

def stop_geometry(rx, S, f_number=None):
    """光阑平面 z 与半径。半径按垂直截面（仅主镜）近轴边缘光线定 f/N。"""
    prime = next(g for g in rx["groups"] if g["id"] == "prime")
    st = prime["stop"]
    i = next(k for k, sf in enumerate(S) if sf["id"] == st["after_surface"])
    z_stop = S[i]["z"] - st["offset_fraction"] * (S[i]["z"] - S[i + 1]["z"])
    N = f_number or prime.get("f_number", 2.0)
    efl_y, _ = paraxial_infinity(S, "Ry")
    y_ep = efl_y / (2.0 * N)
    # 从物方平行光追到光阑平面
    y, nu, n = y_ep, 0.0, 1.0
    for k in range(i + 1):
        sf = S[k]
        P = 0.0 if math.isinf(sf["Ry"]) else (sf["n"] - n) / sf["Ry"]
        nu -= y * P
        n = sf["n"]
        dz = (S[k]["z"] - S[k + 1]["z"]) if k < i else (S[i]["z"] - z_stop)
        y += dz * nu / n
    return z_stop, abs(y)

def aim_through_stop(S, sensor_pt, stop_pt, z_stop, lam_um=LAMBDA_D):
    """求从传感器点出发、真实穿过光阑点 stop_pt 的初始方向（2D 牛顿）。"""
    i_stop = max(k for k, sf in enumerate(S) if sf["z"] > z_stop)   # 光阑物方一侧最近的面
    p0 = (sensor_pt[0], sensor_pt[1], 0.0)
    def land(a, b):
        L = math.sqrt(a * a + b * b + 1.0)
        d = (a / L, b / L, 1.0 / L)
        return _trace_segment(S, p0, d, lam_um, i_stop + 1, z_stop)
    a = (stop_pt[0] - p0[0]) / z_stop
    b = (stop_pt[1] - p0[1]) / z_stop
    h = 1e-6
    for _ in range(40):
        q = land(a, b)
        if q is None:
            return None
        ex, ey = q[0] - stop_pt[0], q[1] - stop_pt[1]
        if ex * ex + ey * ey < 1e-16:
            break
        qa, qb = land(a + h, b), land(a, b + h)
        if qa is None or qb is None:
            return None
        j11, j21 = (qa[0] - q[0]) / h, (qa[1] - q[1]) / h
        j12, j22 = (qb[0] - q[0]) / h, (qb[1] - q[1]) / h
        det = j11 * j22 - j12 * j21
        if abs(det) < 1e-14:
            return None
        a -= (j22 * ex - j12 * ey) / det
        b -= (-j21 * ex + j11 * ey) / det
    L = math.sqrt(a * a + b * b + 1.0)
    return (a / L, b / L, 1.0 / L)

def _trace_segment(S, p, d, lam_um, first_idx, z_end):
    """从像面追到光阑平面（只经过 S[first_idx:] 倒序），返回光阑平面交点 (x, y)。"""
    nb, na = media(S, lam_um)
    for i in range(len(S) - 1, first_idx - 1, -1):
        hit = _intersect(S[i], p, d)
        if hit is None:
            return None
        p, nrm = hit
        d = _refract(d, nrm, na[i], nb[i])
        if d is None:
            return None
    t = (z_end - p[2]) / d[2]
    return p[0] + t * d[0], p[1] + t * d[1]


# ───────────────────────── 孔径估算 ─────────────────────────
# 口径模型（写在 JSON 每面的 "aperture"，单位：物理 mm，已缩放）：
#   柱面镜片：{"shape": "rect", "semi_x", "semi_y"}，整片同一矩形，光学面铺满整片
#   球面镜片：{"shape": "circle", "clear", "outer"}，clear = 该面通光半径，
#            clear 到 outer 是平台（黑），outer = 整片外半径

def estimate_apertures(rx, margin=0.07, vignette=1.0, n_pupil=48, fields=None):
    """真实追迹：传感器若干视场点 × 光阑上一圈点（+中心），从像面穿过光阑点一路追到物方，
    记录每面上的最大 |x|、|y|、r。vignette = 角落视场允许通过的光瞳比例（1 = 无渐晕），
    中间视场按 1 − (1 − v)·h² 过渡（h = 视场 / 角落视场）。"""
    S = layout(rx)
    for sf in S:
        sf["ap"] = None
    z_stop, r_stop = stop_geometry(rx, S)
    W, H = rx["sensor"]["width"] / 2, rx["sensor"]["height"] / 2
    if fields is None:
        fields = [(0, 0), (W, 0), (0, H), (W, H), (0.7 * W, 0.7 * H), (W, 0.5 * H), (0.5 * W, H),
                  (0.5 * W, 0), (0, 0.5 * H)]
    hc = math.hypot(W, H)
    ext = {sf["id"]: [0.0, 0.0, 0.0] for sf in S}
    failed = 0
    for fx, fy in fields:
        frac = 1.0 - (1.0 - vignette) * (math.hypot(fx, fy) / hc) ** 2
        rp = r_stop * frac
        pupil = [(0.0, 0.0)] + [(rp * math.cos(2 * math.pi * k / n_pupil),
                                 rp * math.sin(2 * math.pi * k / n_pupil)) for k in range(n_pupil)]
        for px, py in pupil:
            d = aim_through_stop(S, (fx, fy), (px, py), z_stop)
            rec = {}
            r = None if d is None else trace_reverse(S, (fx, fy, 0.0), d, clip=False, record=rec)
            if r is None:
                failed += 1
                continue
            for i, q in rec.items():
                e = ext[S[i]["id"]]
                e[0] = max(e[0], abs(q[0])); e[1] = max(e[1], abs(q[1]))
                e[2] = max(e[2], math.hypot(q[0], q[1]))
    out = {k: dict(semi_x=v[0], semi_y=v[1], semi_r=v[2]) for k, v in ext.items()}
    return out, (z_stop, r_stop), failed

def element_groups(rx):
    """按「玻璃—玻璃—…—空气」切出镜片（含胶合件）：[[id, id, …], …]。"""
    groups, cur = [], []
    for g in rx["groups"]:
        for sf in g["surfaces"]:
            cur.append(sf["id"])
            if sf["medium"] == "air":
                if len(cur) > 1:
                    groups.append(cur)
                cur = []
    return groups

def _is_cylindrical(sf_json):
    return sf_json["R_y"] is None and sf_json["R_x"] is not None

def apply_apertures(rx, margin=0.07, vignette=0.6, round_to=0.25):
    """估算口径并写回 rx（每面 "aperture"）。返回 (rx, info)。"""
    ap, (z_stop, r_stop), failed = estimate_apertures(rx, margin, vignette)
    up = lambda v: math.ceil(v * (1 + margin) / round_to) * round_to
    by_id = {sf["id"]: sf for g in rx["groups"] for sf in g["surfaces"]}
    for ids in element_groups(rx):
        if any(_is_cylindrical(by_id[i]) for i in ids):
            sx = up(max(ap[i]["semi_x"] for i in ids))
            sy = up(max(ap[i]["semi_y"] for i in ids))
            for i in ids:
                by_id[i]["aperture"] = {"shape": "rect", "semi_x": sx, "semi_y": sy}
        else:
            outer = up(max(ap[i]["semi_r"] for i in ids))
            for i in ids:
                by_id[i]["aperture"] = {"shape": "circle", "clear": min(outer, up(ap[i]["semi_r"])), "outer": outer}
    for sf in by_id.values():
        sf.pop("D", None)
    # 空气间隔边缘相撞：回收两侧圆形面的余量（不小于光线实际高度），每次 round_to
    adjusted = []
    for _ in range(40):
        clash = [r for r in edge_report(rx) if r["kind"] == "air" and r["min_gap"] < 0.0]
        if not clash:
            break
        a_id, b_id = clash[0]["between"].split("→")
        shrunk = False
        for i in (a_id, b_id):
            a = by_id[i]["aperture"]
            if a["shape"] == "circle" and a["clear"] - round_to >= ap[i]["semi_r"]:
                a["clear"] -= round_to
                shrunk = True
        if not shrunk:
            break
        for ids in element_groups(rx):           # 外径跟着收：= 该片各面 clear 的最大值（不小于原来需要的）
            if by_id[ids[0]]["aperture"]["shape"] == "circle":
                outer = max(by_id[i]["aperture"]["clear"] for i in ids)
                for i in ids:
                    by_id[i]["aperture"]["outer"] = outer
        adjusted.append(clash[0]["between"])
    prime = next(g for g in rx["groups"] if g["id"] == "prime")
    prime["stop"]["semi_diameter_at_f_number"] = round(r_stop, 3)
    prime["stop"]["z_at_infinity"] = round(z_stop, 3)
    rx["aperture_design"] = {
        "method": "真实光线追迹：传感器 9 个视场点 × 光阑边缘 48 点 + 中心，追到物方取各面最大高度",
        "margin": margin, "corner_pupil_fraction": vignette, "round_to_mm": round_to,
        "note": "角落视场只放行 60% 光瞳 → 机械渐晕，即边缘猫眼焦外的来源；收光圈后渐晕消失、焦外变圆"}
    return rx, dict(z_stop=z_stop, r_stop=r_stop, failed=failed, clash_fixed=adjusted)

def inside_aperture(sf, x, y):
    a = sf.get("ap")
    if not a:
        return True
    if a["shape"] == "rect":
        return abs(x) <= a["semi_x"] and abs(y) <= a["semi_y"]
    return x * x + y * y <= a["clear"] ** 2

def surface_z(sf, x, y):
    """含平台的实际表面 z（平台 = 通光边缘处的 z 向外延伸）。超出外缘返回 None。"""
    a = sf.get("ap")
    if a and a["shape"] == "circle":
        r = math.hypot(x, y)
        if r > a["outer"] + 1e-9:
            return None
        if r > a["clear"]:
            x, y = a["clear"], 0.0
    elif a and a["shape"] == "rect":
        if abs(x) > a["semi_x"] + 1e-9 or abs(y) > a["semi_y"] + 1e-9:
            return None
    q = sag_and_grad(x, y, curv(sf["Rx"]), curv(sf["Ry"]), sf["kx"], sf["ky"])
    return None if q is None else sf["z"] - q[0]

def _outer_extent(sf):
    a = sf["ap"]
    return (a["semi_x"], a["semi_y"]) if a["shape"] == "rect" else (a["outer"], a["outer"])

def edge_report(rx, delta_unit=0.0, delta_front=0.0, samples=60):
    """相邻两面在共同口径内的最小间距：玻璃 = 边厚，空气 = 间隙（负值 = 相撞）。"""
    S = layout(rx, delta_unit, delta_front)
    rep = []
    for i in range(len(S) - 1):
        a, b = S[i], S[i + 1]
        (ax, ay), (bx, by) = _outer_extent(a), _outer_extent(b)
        ex, ey = min(ax, bx), min(ay, by)
        worst, where = math.inf, None
        for k in range(samples + 1):
            t = k / samples
            for x, y in ((t * ex, 0.0), (0.0, t * ey), (t * ex * 0.7071, t * ey * 0.7071), (t * ex, ey), (ex, t * ey)):
                za, zb = surface_z(a, x, y), surface_z(b, x, y)
                if za is None or zb is None:
                    continue
                if za - zb < worst:
                    worst, where = za - zb, (round(x, 2), round(y, 2))
        rep.append(dict(between=f"{a['id']}→{b['id']}", kind="air" if a["medium"] == "air" else "glass",
                        center=round(a["z"] - b["z"], 3), min_gap=round(worst, 3), at=where))
    return rep


# ───────────────────────── 焦外形状 ─────────────────────────

def bokeh_aspect(rx, focus_dist, obj_dist, desqueeze=2.0):
    """真实追迹：对焦在 focus_dist 时，obj_dist 处点光源在像面中心的弥散高宽（mm）与解挤压后高宽比。
    取光阑上下/左右边缘两条光线在物面上的足迹，乘以该物距的横向放大率。"""
    d1, d2 = solve_focus(rx, focus_dist)
    S = layout(rx, d1, d2)
    z_stop, r_stop = stop_geometry(rx, S)
    ext = []
    for ax in (0, 1):
        hits = []
        for sgn in (-1, 1):
            st = (sgn * r_stop * 0.999, 0.0) if ax == 0 else (0.0, sgn * r_stop * 0.999)
            d = aim_through_stop(S, (0.0, 0.0), st, z_stop)
            p, q = trace_reverse(S, (0.0, 0.0, 0.0), d, clip=False)
            t = (obj_dist - p[2]) / q[2]
            hits.append(p[ax] + t * q[ax])
        ext.append(abs(hits[1] - hits[0]))
    L = obj_dist - S[0]["z"]
    bx = ext[0] * abs(paraxial_finite(S, "Rx", L)[1])
    by = ext[1] * abs(paraxial_finite(S, "Ry", L)[1])
    return dict(blur_x=bx, blur_y=by, aspect_desqueezed=by / (desqueeze * bx))
