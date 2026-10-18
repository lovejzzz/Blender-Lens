# 文档第四节四项验收（定量）：
#   1 竖向椭圆焦外：解挤压后光斑高宽比 ≈ 压缩比
#   2 边缘猫眼：f/2 角落光斑被机械渐晕切掉，f/4 时恢复
#   3 一字拉丝炫光（Geometry 模式）：光晕水平展开；关镀膜后仍是一字但颜色变中性
#   4 发腮：1 m 与无穷远实测压缩比
# 用法：Blender -b --factory-startup --python tests/bl_acceptance.py -- [bokeh|flare|mumps|all]
import bpy, os, sys, math, json
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
from blenderlens import rig, optics as O

OUT = os.path.join(ROOT, "renders")
which = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "all"
REPORT = {}

def fresh(height):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not hasattr(bpy.types.Camera, "blenderlens"):
        blenderlens.register()
    bpy.ops.blenderlens.build(height=height)
    sc = bpy.context.scene
    cam_ob = sc.camera
    cam_ob.location = (0, 0, 0); cam_ob.rotation_euler = (0, 0, 0)       # 视线 = 世界 −Z
    sc.world = bpy.data.worlds.new("W"); rig.black_world(sc.world)
    sc.view_settings.view_transform = 'Standard'
    sc.cycles.use_denoising = False
    sc.render.image_settings.file_format = 'OPEN_EXR'
    return sc, cam_ob

def emitter(name, loc, radius, strength, color=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=loc, segments=16, ring_count=8)
    ob = bpy.context.object; ob.name = name
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission"); o = nt.nodes.new("ShaderNodeOutputMaterial")
    e.inputs["Color"].default_value = (*color, 1); e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs[0], o.inputs[0]); ob.data.materials.append(m)
    return ob

def render(sc, name, samples):
    sc.cycles.samples = samples
    path = os.path.join(OUT, name + ".exr")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    W, H = sc.render.resolution_x, sc.render.resolution_y
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1, :, :3]   # 行 0 = 顶部
    bpy.data.images.remove(img)
    save_png(px, os.path.join(OUT, name + ".png"))
    return px

def save_png(px, path, exposure=1.0):
    """线性 → 简单胶片感色调（Reinhard + sRGB 伽马），仅用于看图。"""
    H, W, _ = px.shape
    x = np.asarray(px, dtype=np.float32) * exposure
    x = x / (1.0 + x)
    x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.clip(x, 0, None), 1 / 2.4) - 0.055)
    img = bpy.data.images.new("tmp_png", W, H, alpha=True)
    rgba = np.concatenate([np.clip(x, 0, 1), np.ones((H, W, 1), np.float32)], axis=2)[::-1].astype(np.float32)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = path; img.file_format = 'PNG'; img.save()
    bpy.data.images.remove(img)

def blob(px, cx, cy, half, rel=0.15):
    """窗口内光斑：二阶矩宽高（均匀椭圆 a = 2σ）、阈值面积。"""
    y0, y1 = max(0, cy - half), min(px.shape[0], cy + half)
    x0, x1 = max(0, cx - half), min(px.shape[1], cx + half)
    w = px[y0:y1, x0:x1].sum(axis=2)
    w = np.where(w > rel * w.max(), w, 0.0)
    ys, xs = np.mgrid[y0:y1, x0:x1]
    s = w.sum()
    mx, my = (w * xs).sum() / s, (w * ys).sum() / s
    vx, vy = (w * (xs - mx) ** 2).sum() / s, (w * (ys - my) ** 2).sum() / s
    return dict(cx=round(float(mx), 1), cy=round(float(my), 1),
                width=round(float(4 * math.sqrt(vx)), 2), height=round(float(4 * math.sqrt(vy)), 2),
                area=int((w > 0).sum()))

def image_pos(sc, cam_ob, world_xyz):
    """世界点 → 图像像素（用真实主光线反解；近似时用近轴）。"""
    rx = rig.read_prescription()
    p = cam_ob.data.blenderlens
    dist = math.inf if p.focus_distance <= 0 else p.focus_distance
    d1, d2 = O.solve_focus(rx, dist)
    S = O.layout(rx, d1, d2)
    fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
    x, y, z = world_xyz
    sx, sy = fx * x / -z, fy * y / -z            # 世界 +x → 画面右；+y → 画面上（近轴）
    W, H = sc.render.resolution_x, sc.render.resolution_y
    SW, SH = rx["sensor"]["width"], rx["sensor"]["height"]
    return int(round((sx / SW + 0.5) * W)), int(round((0.5 - sy / SH) * H))


# ───────────────── 1 + 2：焦外形状与猫眼 ─────────────────
def test_bokeh():
    H = 480
    sc, cam_ob = fresh(H)
    p = cam_ob.data.blenderlens
    p.focus_distance = 1200.0
    p.dispersion = False
    Z = -20000.0
    rx = rig.read_prescription()
    S = O.layout(rx)
    fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
    X = rx["sensor"]["width"] / 2 / fx * -Z           # 无穷远时画面半宽对应的物方 x
    Y = rx["sensor"]["height"] / 2 / fy * -Z
    spots = {"center": (0.0, 0.0), "edge_x": (0.82, 0.0), "edge_y": (0.0, 0.72), "corner": (0.80, 0.70)}
    for k, (u, v) in spots.items():
        emitter(f"L_{k}", (u * X, v * Y, Z), 25.0, 4.0e5)
    out = {}
    for N in (2.0, 4.0):
        p.f_number = N
        rig.render_preset(sc)
        px = render(sc, f"accept_bokeh_f{N:g}", 192)
        res = {}
        for k, (u, v) in spots.items():
            cx, cy = image_pos(sc, cam_ob, (u * X, v * Y, Z))
            res[k] = blob(px, cx, cy, 70)
        out[f"f{N:g}"] = res
    c2 = out["f2"]["center"]
    out["center_aspect_h_over_w_desqueezed"] = round(c2["height"] / c2["width"], 3)
    out["expected_aspect_raytrace"] = round(O.bokeh_aspect(rx, 1200.0, -Z)["aspect_desqueezed"], 3)
    out["squeeze_at_focus"] = round(O.squeeze_at(rx, 1200.0), 3)
    for N in ("f2", "f4"):
        out[f"corner_area_over_center_{N}"] = round(out[N]["corner"]["area"] / out[N]["center"]["area"], 3)
    REPORT["bokeh"] = out


# ───────────────── 3：一字拉丝炫光（Analytic + 序列鬼影）─────────────────
def halo_stats(px, cx, cy, core_r=14):
    lum = px.sum(axis=2)
    Hh, Ww = lum.shape
    ys, xs = np.mgrid[0:Hh, 0:Ww]
    core = (xs - cx) ** 2 + (ys - cy) ** 2 < core_r ** 2          # 去掉光源本身（及其焦外）
    halo = np.where(core, 0.0, lum)
    s = halo.sum()
    # 以光源为中心的二阶矩（不是以光晕重心）→ 对均匀噪声给出画面比例，对一字拉丝给出远大于它的比值
    sx = math.sqrt((halo * (xs - cx) ** 2).sum() / s); sy = math.sqrt((halo * (ys - cy) ** 2).sum() / s)
    band = np.abs(ys - cy) <= 6
    rgb = np.where(core[..., None], 0, px).reshape(-1, 3).sum(axis=0)
    return dict(sigma_x=round(sx, 1), sigma_y=round(sy, 1), sigma_ratio=round(sx / sy, 3),
                frame_aspect=round(Ww / Hh, 3),
                energy_in_pm6px_row_band=round(float(halo[band].sum() / s), 3),
                band_area_fraction=round(float(band.mean()), 3),
                halo_rgb_fraction=(rgb / rgb.sum()).round(3).tolist(),
                halo_over_source=float(s / max(1e-9, np.where(core, lum, 0).sum())))

def test_flare():
    """正向炫光通道（确定性）测形状与颜色；光源打在画面中右上方。"""
    H = 405
    res = {}
    for label, coated, nm in (("coated_100nm", True, 100.0), ("coated_135nm", True, 135.0), ("uncoated", False, 0.0)):
        sc, cam_ob = fresh(H)
        p = cam_ob.data.blenderlens
        p.coated = coated
        p.coating_nm = nm
        p.dispersion = False
        p.focus_distance = 0.0
        rig.render_preset(sc)
        sc.cycles.samples = 64
        pos = (2600.0, 700.0, -40000.0)
        src = emitter("Sun", pos, 60.0, 4.0e6, (1.0, 1.0, 1.0))
        src[rig.FLARE_PROP] = 1
        sc.view_settings.view_transform = 'AgX'
        out, used, layer = rig.render_with_flare(sc, os.path.join(OUT, f"accept_flare_{label}.png"))
        cx, cy = image_pos(sc, cam_ob, pos)
        st = halo_stats(layer, cx, cy)
        st["source_px"] = (cx, cy)
        st["sources"] = used
        save_png(layer, os.path.join(OUT, f"accept_flare_{label}_layer.png"), exposure=3.0)
        res[label] = st
    REPORT["flare"] = res


# ───────────────── 4：发腮 ─────────────────
def test_mumps():
    H = 480
    sc, cam_ob = fresh(H)
    p = cam_ob.data.blenderlens
    p.dispersion = False
    p.f_number = 5.6                                   # 收光圈，边缘更锐，便于量尺寸
    rx = rig.read_prescription()
    out = {}
    for dist in (math.inf, 1000.0):
        for ob in [o for o in bpy.data.objects if o.name.startswith("Sq")]:
            bpy.data.objects.remove(ob)
        Z = 60000.0 if math.isinf(dist) else dist
        side = 0.16 * Z                                # 画面里约同样大小
        bpy.ops.mesh.primitive_plane_add(size=side, location=(0, 0, -Z))
        sq = bpy.context.object; sq.name = "Sq"
        m = bpy.data.materials.new("SqM"); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
        e = nt.nodes.new("ShaderNodeEmission"); o = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(e.outputs[0], o.inputs[0]); sq.data.materials.append(m)
        p.focus_distance = 0.0 if math.isinf(dist) else dist
        rig.render_preset(sc)
        tag = "inf" if math.isinf(dist) else f"{int(dist)}mm"
        px = render(sc, f"accept_mumps_{tag}", 64)
        lum = px.sum(axis=2)
        cols = np.nonzero(lum[lum.shape[0] // 2] > 1.5)[0]
        rows = np.nonzero(lum[:, lum.shape[1] // 2] > 1.5)[0]
        w, h = cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1
        out[tag] = dict(width_px=int(w), height_px=int(h),
                        measured_squeeze=round(2.0 * h / w, 4),        # 输出按 2.0 解挤压：w/h = 2·m_x/m_y，压缩比 = m_y/m_x
                        predicted_squeeze=round(O.squeeze_at(rx, dist), 4))
    REPORT["mumps"] = out


tests = dict(bokeh=test_bokeh, flare=test_flare, mumps=test_mumps)
for k, f in tests.items():
    if which in (k, "all"):
        f()
        print("ACCEPT " + json.dumps({k: REPORT[k]}, ensure_ascii=False), flush=True)
with open(os.path.join(OUT, f"acceptance_{which}.json"), "w") as fh:
    json.dump(REPORT, fh, ensure_ascii=False, indent=2)
