# 光学核心：读处方、近轴追迹、真实光线追迹、口径估算、对焦。
# 纯 Python，不依赖 bpy —— Blender 内外都能跑、能单测。
# 约定：原点 = 像面；物方 +Z；光沿 −Z；表面 z = z_vertex − sag(x, y)；单位 mm。
# R > 0：曲率中心在像方。柱面：R_x 或 R_y 之一为 null（平）。
import json, math, os

INF = math.inf
LAMBDA_D, LAMBDA_F, LAMBDA_C = 0.5875618, 0.4861327, 0.6562725   # μm
PRESCRIPTION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prescriptions")
DEFAULT_PRESCRIPTION = os.path.join(PRESCRIPTION_DIR, "c_series_style.json")
BIG = 1.0e9


# ───────────────────────── 处方 ─────────────────────────

def load(path=DEFAULT_PRESCRIPTION):
    with open(path, encoding="utf-8") as f:
        return normalize(json.load(f))

def save(rx, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rx, f, ensure_ascii=False, indent=2)
        f.write("\n")

def list_presets():
    """prescriptions/ 下所有处方：[(文件名, 名称, 简介), …]，按 meta.order 排序。"""
    out = []
    for fn in sorted(os.listdir(PRESCRIPTION_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(PRESCRIPTION_DIR, fn), encoding="utf-8") as f:
                m = json.load(f)["meta"]
            out.append((m.get("order", 99), fn, m.get("short_name", m["name"]), m.get("tagline", "")))
    return [t[1:] for t in sorted(out)]

def normalize(rx):
    """旧格式兼容：focus.unit/front_member → focus.groups；光阑所在组。"""
    f = rx.get("focus", {})
    if "groups" not in f and ("unit" in f or "front_member" in f):
        f["groups"] = [{"ids": f.pop("unit", []), "moves": [1, 0]},
                       {"ids": f.pop("front_member", []), "moves": [1, 1]}]
        f.setdefault("solve", ["Ry", "Rx"])
        rx["focus"] = f
    return rx

def glass_from_code(code):
    """六位玻璃代码 → (n_d, V_d)：例如 438950 → 1.438, 95.0。"""
    code = str(code)
    return 1.0 + int(code[:3]) / 1000.0, int(code[3:]) / 10.0

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
    """整支镜头的面列表（R、d、非球面已按各组 scale 缩放；aperture 为物理 mm）。"""
    out = []
    for g in rx["groups"]:
        s = g.get("scale", 1.0)
        for sf in g["surfaces"]:
            R = lambda v: INF if v is None else v * s
            glass = rx["glasses"][sf["medium"]]
            # 非球面系数 A_{2m} 以 r^{2m} 计：缩放 s 时 A_{2m} → A_{2m} · s^{1−2m}
            asph = [a * s ** (1 - 2 * (m + 2)) for m, a in enumerate(sf.get("asph", []) or [])]
            out.append(dict(
                id=sf["id"], group=g["id"],
                Rx=R(sf["R_x"]), Ry=R(sf["R_y"]),
                kx=sf.get("k_x", sf.get("k", 0.0)), ky=sf.get("k_y", sf.get("k", 0.0)),
                asph=asph,
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

def _focus_moves(rx):
    """{面 id: (c1, c2)}：对焦时该面移动 c1·Δ1 + c2·Δ2。"""
    mv = {}
    for g in rx.get("focus", {}).get("groups", []):
        m = tuple(g["moves"]) + (0,) * (2 - len(g["moves"]))
        for i in g["ids"]:
            mv[i] = m
    return mv

def layout(rx, d1=0.0, d2=0.0):
    """各面顶点 z（像面原点）。像距：处方给 image_distance_mm 时用它，否则用垂直近轴后焦距。"""
    S = flatten(rx)
    bfl = rx.get("image_distance_mm")
    if bfl is None:
        _, bfl = paraxial_infinity(S, "Ry")
    z = [0.0] * len(S)
    z[-1] = bfl
    for i in range(len(S) - 2, -1, -1):
        z[i] = z[i + 1] + S[i]["d"]
    mv = _focus_moves(rx)
    for i, sf in enumerate(S):
        c1, c2 = mv.get(sf["id"], (0, 0))
        z[i] += c1 * d1 + c2 * d2
        sf["z"] = z[i]
        sf["move"] = (c1, c2)
    for i in range(len(S) - 1):
        S[i]["d"] = S[i]["z"] - S[i + 1]["z"]
    S[-1]["d"] = S[-1]["z"]
    return S

def _image_z(S, axis, obj_z):
    """物点在 z = obj_z（轴上），返回近轴像点 z（像面原点下）。"""
    img_from_last, _ = paraxial_finite(S, axis, obj_z - S[0]["z"])
    return S[-1]["z"] - img_from_last

def _image_z_inf(S, axis):
    return S[-1]["z"] - paraxial_infinity(S, axis)[1]

def solve_focus(rx, obj_dist):
    """物距 obj_dist（从像面量起，mm；inf = 无穷远）→ (Δ1, Δ2)。
    目标：每个求解截面的近轴像点回到它在无穷远合焦时的位置。一个截面 → 只解 Δ1；两个 → 二维牛顿。"""
    if math.isinf(obj_dist):
        return 0.0, 0.0
    axes = rx.get("focus", {}).get("solve", ["Ry"])
    S0 = layout(rx)
    target = [_image_z_inf(S0, a) for a in axes]
    def F(d1, d2):
        S = layout(rx, d1, d2)
        return [_image_z(S, a, obj_dist) - t for a, t in zip(axes, target)]
    x = [0.0, 0.0]
    h = 1e-4
    for _ in range(40):
        f = F(*x)
        if max(abs(v) for v in f) < 1e-10:
            break
        if len(axes) == 1:
            f1 = F(x[0] + h, x[1])
            x[0] -= f[0] / ((f1[0] - f[0]) / h)
        else:
            fa = F(x[0] + h, x[1]); fb = F(x[0], x[1] + h)
            j11, j21 = (fa[0] - f[0]) / h, (fa[1] - f[1]) / h
            j12, j22 = (fb[0] - f[0]) / h, (fb[1] - f[1]) / h
            det = j11 * j22 - j12 * j21
            x[0] -= (j22 * f[0] - j12 * f[1]) / det
            x[1] -= (-j21 * f[0] + j11 * f[1]) / det
    return x[0], x[1]

def focus_residual(rx, obj_dist):
    """对焦后两截面像点相对各自无穷远位置的偏差（mm）：单自由度对焦时可看出残余像散。"""
    d1, d2 = solve_focus(rx, obj_dist)
    S, S0 = layout(rx, d1, d2), layout(rx)
    if math.isinf(obj_dist):
        return 0.0, 0.0
    return tuple(_image_z(S, a, obj_dist) - _image_z_inf(S0, a) for a in ("Ry", "Rx"))

def squeeze_at(rx, obj_dist):
    """该物距下的实际压缩比 = 垂直放大率 / 水平放大率（无穷远时用 EFL 比）。"""
    d1, d2 = solve_focus(rx, obj_dist)
    S = layout(rx, d1, d2)
    if math.isinf(obj_dist):
        return paraxial_infinity(S, "Ry")[0] / paraxial_infinity(S, "Rx")[0]
    L = obj_dist - S[0]["z"]
    return paraxial_finite(S, "Ry", L)[1] / paraxial_finite(S, "Rx", L)[1]

def desqueeze(rx):
    return rx.get("anamorphic", {}).get("desqueeze", 2.0)


# ───────────────────────── 曲面 ─────────────────────────

def sag_and_grad(x, y, cx, cy, kx=0.0, ky=0.0, asph=()):
    """双二次曲面 + 转动对称高次项（A4 r⁴ + A6 r⁶ + …）的矢高与解析梯度；超出有效区返回 None。"""
    N = cx * x * x + cy * y * y
    Q = 1.0 - (1 + kx) * cx * cx * x * x - (1 + ky) * cy * cy * y * y
    if Q <= 0.0:
        return None
    Sq = math.sqrt(Q)
    den = 1.0 + Sq
    dSx = -(1 + kx) * cx * cx * x / Sq
    dSy = -(1 + ky) * cy * cy * y / Sq
    s = N / den
    gx = (2 * cx * x * den - N * dSx) / (den * den)
    gy = (2 * cy * y * den - N * dSy) / (den * den)
    if asph:
        r2 = x * x + y * y
        p = r2                                  # r^{2m-2}，m 从 2 起
        for m, a in enumerate(asph, start=2):
            s += a * p * r2
            g = a * 2 * m * p
            gx += g * x; gy += g * y
            p *= r2
    return s, gx, gy

def surf_sag(sf, x, y):
    return sag_and_grad(x, y, curv(sf["Rx"]), curv(sf["Ry"]), sf["kx"], sf["ky"], sf.get("asph") or ())


# ───────────────────────── 口径 ─────────────────────────
# JSON 每面 "aperture"（物理 mm）：
#   {"shape": "rect", "semi_x", "semi_y"}                          整片矩形（柱面镜片），光学面铺满
#   {"shape": "circle", "clear", "outer", "semi_x"?, "semi_y"?}    圆片：clear 外到 outer 为黑色平台；
#                                                                  semi_x / semi_y = 遮罩（切边、矩形视场光阑）

def ap_limits(a):
    """(clear 半径, |x| 上限, |y| 上限, 外半径)；缺省用 BIG。"""
    if not a:
        return BIG, BIG, BIG, BIG
    if a["shape"] == "rect":
        return BIG, a["semi_x"], a["semi_y"], math.hypot(a["semi_x"], a["semi_y"])
    return (a.get("clear") or a["outer"], a.get("semi_x") or BIG, a.get("semi_y") or BIG, a["outer"])

def inside_aperture(sf, x, y):
    c, sx, sy, _ = ap_limits(sf.get("ap"))
    return x * x + y * y <= c * c and abs(x) <= sx and abs(y) <= sy

def surface_z(sf, x, y):
    """含平台的实际表面 z：圆片在 clear 外取通光边缘的 z（沿同一方向）；超出外缘返回 None。遮罩不改面形。"""
    a = sf.get("ap")
    if a and a["shape"] == "circle":
        r = math.hypot(x, y)
        if r > a["outer"] + 1e-9:
            return None
        c = a.get("clear") or a["outer"]
        if r > c:
            x, y = x * c / r, y * c / r
    elif a and a["shape"] == "rect":
        if abs(x) > a["semi_x"] + 1e-9 or abs(y) > a["semi_y"] + 1e-9:
            return None
    q = surf_sag(sf, x, y)
    return None if q is None else sf["z"] - q[0]


# ───────────────────────── 真实光线追迹 ─────────────────────────

def _intersect(sf, p, d):
    t = (sf["z"] - p[2]) / d[2]
    for _ in range(30):
        x, y = p[0] + t * d[0], p[1] + t * d[1]
        r = surf_sag(sf, x, y)
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
    r = surf_sag(sf, x, y)
    if r is None:
        return None
    s, gx, gy = r
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

def stop_group(rx):
    return next(g for g in rx["groups"] if "stop" in g)

def nominal_f_number(rx):
    g = stop_group(rx)
    return g["stop"].get("f_number", g.get("f_number", 2.0))

def stop_geometry(rx, S, f_number=None):
    """光阑平面 z 与半径。
    处方给 stop.semi_diameter（在 stop.f_number 下）时直接按 f 值缩放；
    否则按垂直截面近轴边缘光线定 f/N（N = 整支镜头垂直 EFL / 入瞳直径）。"""
    st = stop_group(rx)["stop"]
    i = next(k for k, sf in enumerate(S) if sf["id"] == st["after_surface"])
    gap = S[i]["z"] - S[i + 1]["z"]
    off = st["offset_mm"] if "offset_mm" in st else st.get("offset_fraction", 0.5) * gap
    z_stop = S[i]["z"] - off
    N0 = nominal_f_number(rx)
    N = f_number or N0
    if "semi_diameter" in st:
        return z_stop, st["semi_diameter"] * N0 / N
    efl_y, _ = paraxial_infinity(S, "Ry")
    y, nu, n = efl_y / (2.0 * N), 0.0, 1.0
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


# ───────────────────────── 口径估算 ─────────────────────────

def estimate_apertures(rx, margin=0.07, vignette=1.0, n_pupil=48, fields=None):
    """真实追迹：传感器若干视场点 × 光阑上一圈点（+中心），从像面穿过光阑点一路追到物方，
    记录每面上的最大 |x|、|y|、r。vignette = 角落视场允许通过的光瞳比例（1 = 无渐晕），
    中间视场按 1 − (1 − v)·h 线性过渡（h = 视场 / 角落视场）。光阑按处方 look.f_number（设计光圈）。"""
    S = layout(rx)
    for sf in S:
        sf["ap"] = None
    z_stop, r_stop = stop_geometry(rx, S, rx.get("look", {}).get("f_number"))
    W, H = rx["sensor"]["width"] / 2, rx["sensor"]["height"] / 2
    if fields is None:
        fields = [(0, 0), (W, 0), (0, H), (W, H), (0.7 * W, 0.7 * H), (W, 0.5 * H), (0.5 * W, H),
                  (0.5 * W, 0), (0, 0.5 * H)]
    hc = math.hypot(W, H)
    ext = {sf["id"]: [0.0, 0.0, 0.0] for sf in S}
    failed = 0
    for fx, fy in fields:
        frac = 1.0 - (1.0 - vignette) * (math.hypot(fx, fy) / hc)
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
    return (sf_json["R_y"] is None) != (sf_json["R_x"] is None)

def apply_apertures(rx, margin=0.07, vignette=None, round_to=0.25):
    """估算口径并写回 rx（每面 "aperture"）。aperture_source = "patent" 的组保留专利给定口径。返回 (rx, info)。
    vignette = 角落视场放行的光瞳比例（机械渐晕、猫眼强弱）；默认取处方 look.corner_pupil_fraction，再缺省 0.6。"""
    if vignette is None:
        vignette = rx.get("look", {}).get("corner_pupil_fraction", 0.6)
    ap, (z_stop, r_stop), failed = estimate_apertures(rx, margin, vignette)
    up = lambda v: math.ceil(v * (1 + margin) / round_to) * round_to
    fixed = {sf["id"] for g in rx["groups"] if g.get("aperture_source") == "patent" for sf in g["surfaces"]}
    by_id = {sf["id"]: sf for g in rx["groups"] for sf in g["surfaces"]}
    for ids in element_groups(rx):
        if any(i in fixed for i in ids):
            continue
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
            if i in fixed:
                continue
            a = by_id[i]["aperture"]
            if a["shape"] == "circle" and a["clear"] - round_to >= ap[i]["semi_r"]:
                a["clear"] -= round_to
                shrunk = True
            elif a["shape"] == "rect":                 # 矩形镜片（整片同一口径）：两向各回收一档
                el = next(ids for ids in element_groups(rx) if i in ids)
                need_x = max(ap[k]["semi_x"] for k in el); need_y = max(ap[k]["semi_y"] for k in el)
                for key, need in (("semi_x", need_x), ("semi_y", need_y)):
                    new = a[key] - round_to
                    if new >= need:
                        for k in el:
                            by_id[k]["aperture"][key] = new
                        shrunk = True
        if not shrunk:
            break
        for ids in element_groups(rx):           # 外径跟着收：= 该片各面 clear 的最大值
            if not any(i in fixed for i in ids) and by_id[ids[0]]["aperture"]["shape"] == "circle":
                outer = max(by_id[i]["aperture"]["clear"] for i in ids)
                for i in ids:
                    by_id[i]["aperture"]["outer"] = outer
        adjusted.append(clash[0]["between"])
    st = stop_group(rx)["stop"]
    st["semi_diameter_at_f_number"] = round(r_stop, 3)
    st["z_at_infinity"] = round(z_stop, 3)
    rx["aperture_design"] = {
        "method": "真实光线追迹：传感器 9 个视场点 × 光阑边缘 48 点 + 中心，追到物方取各面最大高度",
        "margin": margin, "corner_pupil_fraction": vignette, "round_to_mm": round_to,
        "note": f"角落视场只放行 {vignette:.0%} 光瞳 → 机械渐晕，即边缘猫眼焦外的来源；收光圈后渐晕消失、焦外变圆"}
    return rx, dict(z_stop=z_stop, r_stop=r_stop, failed=failed, clash_fixed=adjusted)

def _outer_extent(sf):
    a = sf["ap"]
    if a["shape"] == "rect":
        return a["semi_x"], a["semi_y"]
    return min(a["outer"], a.get("semi_x") or BIG), min(a["outer"], a.get("semi_y") or BIG)

def edge_report(rx, d1=0.0, d2=0.0, samples=60):
    """相邻两面在共同口径内的最小间距：玻璃 = 边厚，空气 = 间隙（负值 = 相撞）。"""
    S = layout(rx, d1, d2)
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

def bokeh_aspect(rx, focus_dist, obj_dist, desq=None):
    """真实追迹：对焦在 focus_dist 时，obj_dist 处点光源在像面中心的弥散高宽（mm）与解挤压后高宽比。
    取光阑上下/左右边缘两条光线在物面上的足迹，乘以该物距的横向放大率。"""
    desq = desq or desqueeze(rx)
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
    return dict(blur_x=bx, blur_y=by, aspect_desqueezed=by / (desq * bx))


def pupil_transmission(rx, field, f_number=None, focus_dist=math.inf, n=41):
    """传感器点 field 处，光阑上均匀网格中有多少比例的光线不被任何口径挡住（机械渐晕；猫眼焦外的面积比）。"""
    d1, d2 = solve_focus(rx, focus_dist)
    S = layout(rx, d1, d2)
    zs, rs = stop_geometry(rx, S, f_number)
    tot = passed = 0
    for i in range(n):
        for j in range(n):
            px = (2 * i / (n - 1) - 1) * rs
            py = (2 * j / (n - 1) - 1) * rs
            if px * px + py * py > rs * rs:
                continue
            tot += 1
            d = aim_through_stop(S, field, (px, py), zs)
            if d is not None and trace_reverse(S, (field[0], field[1], 0.0), d, stop=(zs, rs * 1.0001), clip=True) is not None:
                passed += 1
    return passed / tot


# ───────────────────────── 统一焦段 ─────────────────────────

def _scale_aperture(a, k):
    return {key: (v * k if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for key, v in a.items()}

def rescale_to_vertical_efl(rx, target_mm, reestimate=True):
    """整支镜头按比例缩放到垂直焦距 target_mm（压缩比、f 值、像差形态不变）。
    处方口径为估算的组（非 aperture_source=patent）按新视场重新估算并处理相撞；专利口径按比例缩放。"""
    import copy
    rx = copy.deepcopy(rx)
    fy = paraxial_infinity(layout(rx), "Ry")[0]
    k = target_mm / fy
    for g in rx["groups"]:
        g["scale"] = g.get("scale", 1.0) * k
        if "target_f" in g:
            g["target_f"] = round(g["target_f"] * k, 3)
        for sf in g["surfaces"]:
            if sf.get("aperture"):
                sf["aperture"] = _scale_aperture(sf["aperture"], k)
        st = g.get("stop")
        if st:
            for key in ("semi_diameter", "offset_mm", "semi_diameter_at_f_number", "z_at_infinity"):
                if key in st:
                    st[key] = st[key] * k
    if rx.get("image_distance_mm") is not None:
        rx["image_distance_mm"] *= k
    # 整支镜头等比缩放 → 最近对焦距离（从像面量起）也等比缩放，否则缩大的镜头会「对焦到镜头里面」
    if rx.get("focus", {}).get("min_distance_mm"):
        rx["focus"]["min_distance_mm"] = round(rx["focus"]["min_distance_mm"] * k, 1)
    if rx.get("look", {}).get("min_focus_mm"):
        rx["look"]["min_focus_mm"] = round(rx["look"]["min_focus_mm"] * k, 1)
    m = rx["meta"]
    m["rescaled"] = {"from_vertical_efl": round(fy, 3), "to_vertical_efl": target_mm, "factor": round(k, 5)}
    m["short_name"] = f'{m.get("short_name", m["name"])} @ {target_mm:g} mm'
    if reestimate and any(g.get("aperture_source") != "patent" for g in rx["groups"]):
        rx, _ = apply_apertures(rx)
    return rx


# ───────────────────────── 画幅 / 光窗 ─────────────────────────

GATES = {
    "FULL": "处方原光窗（Full aperture）",
    "2.39": "2.39:1 抽取（解挤压后 2.39:1，从处方光窗里裁出的最大矩形）",
    "16:9": "16:9 抽取（解挤压后 16:9，从处方光窗里裁出的最大矩形）",
    "16:9_NATIVE": "原生 16:9 满像圈（解挤压后 16:9，不裁切：取镜头像圈内最大的矩形，画面比处方光窗更高）",
    "CUSTOM": "自定义（压缩前的光窗宽 × 高，mm）",
}

def image_circle_radius(rx):
    """镜头设计覆盖的像圈半径：处方原光窗（full_aperture）的半对角线。Panavision 后置 = 13.60（专利像高）。"""
    s = rx["sensor"].get("full_aperture") or rx["sensor"]
    return 0.5 * math.hypot(s["width"], s["height"])

def apply_gate(rx, gate="FULL", width=None, height=None):
    """按画幅改处方的 sensor（光窗，压缩前）。口径不重算：镜头本身不变，只是用光窗里的一部分。
    2.39 / 16:9：解挤压后达到该宽高比、且装在处方原光窗里的最大矩形（与原光窗同中心）。"""
    import copy
    if gate in (None, "", "FULL"):
        return rx
    rx = copy.deepcopy(rx)
    s = rx["sensor"]
    W, H = s["width"], s["height"]
    D = desqueeze(rx)
    if gate == "CUSTOM":
        if not (width and height):
            raise ValueError("自定义画幅需要宽和高（mm）")
        w, h = float(width), float(height)
    elif gate == "16:9_NATIVE":
        a = (16.0 / 9.0) / D                                  # 压缩前光窗的宽高比
        R = image_circle_radius(rx)
        h = 2.0 * R / math.sqrt(1.0 + a * a)
        w = a * h
    else:
        target = {"2.39": 2.39, "16:9": 16.0 / 9.0}[gate]
        if W * D / H >= target:
            h, w = H, target * H / D
        else:
            w, h = W, W * D / target
    s.setdefault("full_aperture", {"width": W, "height": H, "format": s.get("format")})
    s["width"], s["height"] = round(w, 4), round(h, 4)
    s["gate"] = gate
    s["format"] = f'{GATES[gate].split("（")[0]}：{w:.2f} × {h:.2f} mm（原光窗 {W:g} × {H:g}）'
    return rx

def gate_corner_transmission(rx, f_number=None, focus_dist=math.inf, n=25):
    """光窗四角与上边缘中点（取 98% 处）的光瞳通过率取小：判断该画幅是否超出像圈（过低 = 发黑）。"""
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    return min(pupil_transmission(rx, (0.49 * W, 0.49 * H), f_number=f_number, focus_dist=focus_dist, n=n),
               pupil_transmission(rx, (0.0, 0.49 * H), f_number=f_number, focus_dist=focus_dist, n=n))
