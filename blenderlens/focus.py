# 对焦助手（0.8.3，MessyRoom R2）：对准物体 / 点 / 画面位置、景深范围、同时保住两个距离要多少光圈、逐帧拉焦。
# 距离一律与镜头对焦的定义相同：从像面沿光轴量的物距（米）——不是到相机的直线距离。
# 景深用每个截面（水平 / 垂直）的近轴离焦弥散：边缘光线过当前光阑，像方斜率 × 像点到传感器的距离，
# 换算到输出像素（解挤压后的画面），取两向较大者。不含像差（像差是聚焦时也有的底，DoF 计算器同样不计）。
import math
import numpy as np

try:
    from . import optics as O
except ImportError:
    import optics as O


# ───────────────────────── 纯光学（不依赖 bpy）─────────────────────────

def _marginal_blur_mm(rx, S, z_stop, r_stop, obj_mm, axis, z_ref=0.0):
    """物距 obj_mm（从像面量）的轴上点，边缘光线过光阑边缘（半径 r_stop），在 z = z_ref 的平面上的弥散直径（mm，该截面）。
    z_ref = 该截面对焦距离的像点位置 → 只算离焦（DoF 计算器的定义）；z_ref = 0 → 传感器上的实际弥散（含两向焦点差）。"""
    if not math.isfinite(obj_mm):
        y, nu = 1.0, 0.0                             # 平行光
    else:
        y, nu = 0.0, 1.0e-3                          # 从轴上物点出发
        L = obj_mm - S[0]["z"]
        y = L * nu
    n = 1.0
    y_stop = None
    for i, sf in enumerate(S):
        P = 0.0 if math.isinf(sf[axis]) else (sf["n"] - n) / sf[axis]
        nu -= y * P
        n = sf["n"]
        if i < len(S) - 1:
            z_next = S[i + 1]["z"]
            if y_stop is None and sf["z"] > z_stop >= z_next:
                y_stop = y + (sf["z"] - z_stop) * nu / n
            y += (sf["z"] - z_next) * nu / n
    if y_stop is None or y_stop == 0:
        return float("nan")
    k = r_stop / abs(y_stop)                          # 线性：缩放到边缘光线
    y_last, u = y * k, nu * k / n
    if u == 0:
        return float("inf")
    z_img = S[-1]["z"] - (-y_last / u)                # 像点 z（像面原点）
    return 2.0 * abs(u) * abs(z_img - z_ref)


class DofModel:
    """一个镜头、一个对焦距离、一个 f 值：任意物距的弥散（输出像素）。"""

    def __init__(self, rx, focus_mm, f_number, out_w, out_h):
        self.rx = rx
        self.focus_mm = focus_mm
        d1, d2 = O.solve_focus(rx, focus_mm)
        self.S = O.layout(rx, d1, d2)
        self.z_stop, self.r_stop = O.stop_geometry(rx, self.S, max(f_number, O.nominal_f_number(rx)))
        self.px_x = rx["sensor"]["width"] / out_w     # 每输出像素对应的传感器 mm（水平；解挤压后的宽 W 覆盖传感器宽）
        self.px_y = rx["sensor"]["height"] / out_h
        fz = focus_mm if math.isfinite(focus_mm) else math.inf
        self.zref = {a: O._image_z(self.S, a, fz) if math.isfinite(fz) else O._image_z_inf(self.S, a) for a in ("Rx", "Ry")}

    def blur_px(self, obj_mm):
        """离焦弥散（相对对焦面的像，输出像素）：(两向较大者, 水平, 垂直)。"""
        bx = _marginal_blur_mm(self.rx, self.S, self.z_stop, self.r_stop, obj_mm, "Rx", self.zref["Rx"]) / self.px_x
        by = _marginal_blur_mm(self.rx, self.S, self.z_stop, self.r_stop, obj_mm, "Ry", self.zref["Ry"]) / self.px_y
        return max(bx, by), bx, by

    def floor_px(self):
        """对焦面上传感器处的弥散（两向焦点不重合的设计残差；与对焦无关的底）。"""
        s = self.focus_mm if math.isfinite(self.focus_mm) else math.inf
        bx = _marginal_blur_mm(self.rx, self.S, self.z_stop, self.r_stop, s, "Rx") / self.px_x
        by = _marginal_blur_mm(self.rx, self.S, self.z_stop, self.r_stop, s, "Ry") / self.px_y
        return max(bx, by)


def _bisect(f, lo, hi, iters=60):
    """f(lo) 与 f(hi) 异号时找根（对数空间，距离跨几个数量级）。"""
    a, b = math.log(lo), math.log(hi)
    fa = f(math.exp(a))
    for _ in range(iters):
        m = 0.5 * (a + b)
        fm = f(math.exp(m))
        if (fm > 0) == (fa > 0):
            a, fa = m, fm
        else:
            b = m
    return math.exp(0.5 * (a + b))


def dof_range_mm(rx, focus_mm, f_number, out_w, out_h, coc_px=1.0, min_mm=None):
    """→ dict(near_mm, far_mm（inf = 到无穷远都清晰）, hyperfocal_mm, blur_at_focus_px, blur_inf_px)。"""
    min_mm = min_mm or max(50.0, 0.25 * (rx.get("focus", {}).get("min_distance_mm") or 500.0))
    M = DofModel(rx, focus_mm, f_number, out_w, out_h)
    g = lambda d: M.blur_px(d)[0] - coc_px
    s = focus_mm if math.isfinite(focus_mm) else 1e9
    near = min_mm if g(min_mm) <= 0 else _bisect(g, min_mm, s)
    binf = M.blur_px(math.inf)[0]
    far = math.inf if binf <= coc_px else _bisect(g, s, 1e9)
    def g_h(sf):                                       # 超焦距：对焦在 sf 时无穷远刚好 = coc
        return DofModel(rx, sf, f_number, out_w, out_h).blur_px(math.inf)[0] - coc_px
    lo_h = max(min_mm, 0.5 * (rx.get("focus", {}).get("min_distance_mm") or 500.0))
    try:
        hyper = _bisect(g_h, lo_h, 1e8) if g_h(lo_h) > 0 and g_h(1e8) < 0 else (lo_h if g_h(lo_h) <= 0 else math.inf)
    except (ValueError, ZeroDivisionError):
        hyper = math.inf
    return dict(near_mm=near, far_mm=far, hyperfocal_mm=hyper, blur_inf_px=binf, astigmatic_floor_px=M.floor_px())


def stop_for_mm(rx, near_mm, far_mm, out_w, out_h, coc_px=1.0, max_f=22.0):
    """同时保住 near_mm 与 far_mm（两个都在 coc 内）所需的最小 f 值和对焦距离。
    对每个 f 值，对焦距离取两端弥散相等的位置；再找最小的 f 使弥散 ≤ coc。"""
    N0 = O.nominal_f_number(rx)
    def balanced(N):
        h = lambda s: DofModel(rx, s, N, out_w, out_h).blur_px(near_mm)[0] - DofModel(rx, s, N, out_w, out_h).blur_px(far_mm)[0]
        s = _bisect(h, near_mm * 1.0001, far_mm if math.isfinite(far_mm) else 1e9)
        M = DofModel(rx, s, N, out_w, out_h)
        return s, max(M.blur_px(near_mm)[0], M.blur_px(far_mm)[0])
    s_max, b_max = balanced(max_f)
    if b_max > coc_px:
        return dict(feasible=False, f_number=max_f, focus_mm=s_max, blur_px=b_max,
                    note=f"f/{max_f:g} 时两端仍有 {b_max:.2f} px 弥散，超过 {coc_px} px")
    lo, hi = N0, max_f
    if balanced(lo)[1] <= coc_px:
        hi = lo
    for _ in range(40):
        if hi - lo < 0.01:
            break
        m = 0.5 * (lo + hi)
        if balanced(m)[1] <= coc_px:
            hi = m
        else:
            lo = m
    s, b = balanced(hi)
    return dict(feasible=True, f_number=round(hi, 2), focus_mm=s, blur_px=b)


# ───────────────────────── Blender（bpy）─────────────────────────

def _bpy():
    import bpy
    return bpy


def _out_size(scene):
    r = scene.render
    return (r.resolution_x * r.resolution_percentage / 100.0, r.resolution_y * r.resolution_percentage / 100.0)


def axial_mm(cam_ob, world_point):
    """世界坐标点 → 从像面沿光轴的物距（mm）：相机本地 −Z 方向的距离。"""
    from . import rig
    p = cam_ob.matrix_world.inverted() @ world_point
    return -p.z * rig.mm_per_unit(rig._cam_scene(cam_ob))


def chief_ray_world(cam_ob, u, v):
    """画面坐标 (u, v)（0–1，原点左下，(0.5, 0.5) 为中心）的主光线（过出瞳中心、d 线）→ (世界起点, 世界方向)。"""
    from mathutils import Vector
    from . import rig, lenspack as LP
    rx = rig._rx_cached()
    SW, SH = rx["sensor"]["width"], rx["sensor"]["height"]
    sx, sy = (u - 0.5) * SW, -(v - 0.5) * SH
    M = LP.LensMap(rx, rig.focus_mm(cam_ob))
    P0 = np.array([[sx, sy, 0.0]]); D0 = np.array([[-sx, -sy, M.z_exp]]); D0 /= np.linalg.norm(D0)
    Po, Do = LP._trace_out(M.S, M.z_stop, M.r_stop, P0, D0, O.LAMBDA_D)
    us = rig.mm_per_unit(rig._cam_scene(cam_ob))
    mw = cam_ob.matrix_world
    o = mw @ Vector((-Po[0, 0] / us, Po[0, 1] / us, -Po[0, 2] / us))
    d = (mw.to_3x3() @ Vector((-Do[0, 0], Do[0, 1], -Do[0, 2]))).normalized()
    return o, d


def target_point(cam_ob, target, scene=None):
    """target：物体（取原点）、(x, y, z) 世界坐标、或 (u, v) 画面坐标（沿主光线 ray_cast）。→ 世界坐标点（Vector）或 None。"""
    bpy = _bpy()
    from mathutils import Vector
    from . import rig
    scene = scene or rig._cam_scene(cam_ob)
    if isinstance(target, str):
        target = bpy.data.objects[target]
    if hasattr(target, "matrix_world"):
        return target.matrix_world.translation.copy()
    t = tuple(target)
    if len(t) == 3:
        return Vector(t)
    if len(t) == 2:
        o, d = chief_ray_world(cam_ob, t[0], t[1])
        us = rig.mm_per_unit(scene)
        hit, loc, *_rest = scene.ray_cast(bpy.context.evaluated_depsgraph_get(), o + d * (20.0 / us), d)
        return loc if hit else None
    raise ValueError("target 应为物体、物体名、(x, y, z) 或 (u, v)")


def focus_on(cam_ob, target, apply=True, keyframe=None):
    """对焦到 target（见 target_point）。返回物距（米，从像面沿光轴）；没打到东西返回 None。
    apply：写到相机的对焦距离；keyframe：帧号 → 同时打关键帧。"""
    from . import rig
    pt = target_point(cam_ob, target)
    if pt is None:
        return None
    mm = axial_mm(cam_ob, pt)
    if apply and mm > 0:
        p = cam_ob.data.blenderlens
        p.focus_distance = mm / rig.mm_per_unit(rig._cam_scene(cam_ob))
        if keyframe is not None:
            cam_ob.data.keyframe_insert("blenderlens.focus_distance", frame=keyframe)
        rig.apply_settings(cam_ob)
    return mm / 1000.0


def _ease(t, kind):
    if kind == 'LINEAR':
        return t
    return t * t * (3.0 - 2.0 * t)                    # SMOOTH


def focus_pull(cam_ob, a, b=None, frame_start=None, frame_end=None, ease='SMOOTH'):
    """逐帧对焦关键帧。b 为 None：一直跟住 a（物体会动也跟）；否则从 a 拉到 b（按 1/距离 插值 = 跟焦环的手感），
    每帧都重新求两个目标的距离（目标或相机在动也对）。返回 [(帧, 米)]。"""
    from . import rig
    scene = rig._cam_scene(cam_ob)
    fs = scene.frame_start if frame_start is None else frame_start
    fe = scene.frame_end if frame_end is None else frame_end
    cur = scene.frame_current
    out = []
    try:
        for f in range(fs, fe + 1):
            scene.frame_set(f)
            da = axial_mm(cam_ob, target_point(cam_ob, a))
            if b is None:
                d = da
            else:
                db = axial_mm(cam_ob, target_point(cam_ob, b))
                t = _ease((f - fs) / max(fe - fs, 1), ease)
                d = 1.0 / ((1 - t) / da + t / db)
            cam_ob.data.blenderlens.focus_distance = d / rig.mm_per_unit(scene)
            cam_ob.data.keyframe_insert("blenderlens.focus_distance", frame=f)
            out.append((f, round(d / 1000.0, 4)))
    finally:
        scene.frame_set(cur)
        rig.apply_settings(cam_ob)
    return out


def focus_track(cam_ob, target=None, frames=None, rack=None, ease='SMOOTH'):
    """对焦跟住一个点，逐帧烘焙（0.8.5，MessyRoom 0.8.5 R5）。target：物体 / 物体名 / 空物体 / (x, y, z) 世界点；
    frames：(起, 止) 或帧号序列（None = 场景起止帧）；每帧重新求轴向距离（相机、目标在动都对，配合 follow_camera）。
    rack=[(帧, 目标), …]：拉焦。每一项「到这一帧时对在这个目标上」；相邻两项之间按 1/距离 缓入缓出地拉（每帧两头都重新求距离），
    第一项之前跟住 target（没给 target 就跟第一项的目标），最后一项之后跟住最后一项。
    → [(帧, 米, 当前目标)]。"""
    from . import rig
    scene = rig._cam_scene(cam_ob)
    if frames is None:
        frames = range(scene.frame_start, scene.frame_end + 1)
    elif isinstance(frames, tuple) and len(frames) == 2:
        frames = range(int(frames[0]), int(frames[1]) + 1)
    frames = list(frames)
    keys = sorted((int(f), t) for f, t in (rack or []))
    if target is None and not keys:
        raise ValueError("focus_track：要给 target 或 rack")
    if target is not None:
        keys = [(min(frames[0], keys[0][0]) if keys else frames[0], target)] + keys
        if len(keys) > 1 and keys[0][0] == keys[1][0]:
            keys = keys[1:]
    label = lambda t: t if isinstance(t, str) else getattr(t, "name", str(tuple(round(x, 3) for x in t)))
    cur = scene.frame_current
    out = []
    try:
        for f in frames:
            scene.frame_set(f)
            dist = lambda t: axial_mm(cam_ob, target_point(cam_ob, t, scene))
            if f <= keys[0][0] or len(keys) == 1:
                k = 0 if f <= keys[0][0] else len(keys) - 1
                d, who = dist(keys[k][1]), label(keys[k][1])
            elif f >= keys[-1][0]:
                d, who = dist(keys[-1][1]), label(keys[-1][1])
            else:
                i = max(j for j in range(len(keys)) if keys[j][0] <= f)
                (fa, ta), (fb, tb) = keys[i], keys[i + 1]
                da, db = dist(ta), dist(tb)
                s = _ease((f - fa) / max(fb - fa, 1), ease)
                d = 1.0 / ((1 - s) / da + s / db)
                who = f"{label(ta)} → {label(tb)}"
            if not (d > 0):
                raise ValueError(f"focus_track：第 {f} 帧目标在镜头后面")
            cam_ob.data.blenderlens.focus_distance = d / rig.mm_per_unit(scene)
            cam_ob.data.keyframe_insert("blenderlens.focus_distance", frame=f)
            out.append((f, round(d / 1000.0, 4), who))
    finally:
        scene.frame_set(cur)
        rig.apply_settings(cam_ob)
    return out


def dof_range(cam_ob, coc_px=1.0):
    """当前光圈、对焦、输出分辨率与画幅下的清晰范围（米）：near_m、far_m（inf = 到无穷远）、hyperfocal_m，
    以及对焦面与无穷远处的弥散（像素）。弥散取水平 / 垂直较大者（输出像素，解挤压后）。"""
    from . import rig
    rx = rig._rx_cached()
    W, H = _out_size(rig._cam_scene(cam_ob))
    p = cam_ob.data.blenderlens
    r = dof_range_mm(rx, rig.focus_mm(cam_ob), p.f_number, W, H, coc_px)
    m = lambda v: (round(v / 1000.0, 3) if math.isfinite(v) else float("inf"))
    return dict(near_m=m(r["near_mm"]), far_m=m(r["far_mm"]), hyperfocal_m=m(r["hyperfocal_mm"]),
                focus_m=m(rig.focus_mm(cam_ob)), f_number=p.f_number, coc_px=coc_px, output_px=[int(W), int(H)],
                blur_inf_px=round(r["blur_inf_px"], 3),
                astigmatic_floor_px=round(r["astigmatic_floor_px"], 3))


def stop_for(cam_ob, near_m, far_m, coc_px=1.0, apply=False):
    """同时保住 near_m 与 far_m（米）所需的 f 值与对焦距离。apply=True 时写到相机。"""
    from . import rig
    rx = rig._rx_cached()
    W, H = _out_size(rig._cam_scene(cam_ob))
    r = stop_for_mm(rx, near_m * 1000.0, far_m * 1000.0 if math.isfinite(far_m) else math.inf, W, H, coc_px)
    out = dict(feasible=r["feasible"], f_number=r["f_number"], focus_m=round(r["focus_mm"] / 1000.0, 3),
               blur_px=round(r["blur_px"], 3), coc_px=coc_px, max_aperture=O.nominal_f_number(rx))
    if "note" in r:
        out["note"] = r["note"]
    if apply and r["feasible"]:
        p = cam_ob.data.blenderlens
        p.f_number = r["f_number"]
        p.focus_distance = r["focus_mm"] / rig.mm_per_unit(rig._cam_scene(cam_ob))
        rig.apply_settings(cam_ob)
    return out
