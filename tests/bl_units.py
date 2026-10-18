# 场景单位一致性（A1）：同一画面分别在毫米场景与米制场景（物体缩小 1000 倍）里渲染，逐像素比较。
# 另测：GPU（非 OptiX）自动回退 CPU（A3）、稳定 API（D2）、画幅（A2）。
# 运行：Blender -b --factory-startup --python tests/bl_units.py
import os, sys, json
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
import blenderlens.optics as O
OUT = os.path.join(ROOT, "renders")
os.makedirs(OUT, exist_ok=True)


def emissive(name, color, strength):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial"); e = nt.nodes.new("ShaderNodeEmission")
    tc = nt.nodes.new("ShaderNodeTexCoord"); ck = nt.nodes.new("ShaderNodeTexChecker")
    ck.inputs["Scale"].default_value = 8.0
    ck.inputs["Color1"].default_value = (*color, 1); ck.inputs["Color2"].default_value = (0.05, 0.05, 0.05, 1)
    nt.links.new(tc.outputs["UV"], ck.inputs["Vector"]); nt.links.new(ck.outputs["Color"], e.inputs["Color"])
    e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs[0], o.inputs["Surface"])
    return m

def make_scene(name, k):
    """k = 每 mm 的场景单位数（毫米场景 1，米制 0.001）。物体按 mm 设计，乘 k 放进场景。"""
    sc = bpy.data.scenes.new(name)
    bpy.context.window.scene = sc if bpy.context.window else None
    us = sc.unit_settings
    us.system = 'METRIC'; us.scale_length = 0.001 if k == 1 else 1.0
    w = bpy.data.worlds.new(name + "_w"); sc.world = w; rig.black_world(w)
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    pl = bpy.context.active_object
    if pl.users_scene[0] != sc:
        for c in pl.users_collection: c.objects.unlink(pl)
        sc.collection.objects.link(pl)
    pl.scale = (2400 * k, 1000 * k, 1)
    pl.location = (0, 3000 * k, 0); pl.rotation_euler = (1.5708, 0, 0)
    pl.data.materials.append(emissive(name + "_chk", (0.9, 0.7, 0.5), 1.0))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=40 * k, location=(500 * k, 9000 * k, 300 * k))
    sp = bpy.context.active_object
    if sp.users_scene[0] != sc:
        for c in sp.users_collection: c.objects.unlink(sp)
        sc.collection.objects.link(sp)
    sp.data.materials.append(emissive(name + "_sp", (1, 1, 1), 50.0))
    return sc

def render(sc, tag):
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 64; sc.cycles.seed = 3; sc.cycles.use_denoising = False
    sc.cycles.use_adaptive_sampling = False
    sc.render.resolution_x, sc.render.resolution_y = 400, 170
    sc.view_settings.view_transform = 'Standard'
    path = os.path.join(OUT, f"units_{tag}.exr")
    sc.render.image_settings.file_format = 'OPEN_EXR'
    sc.render.filepath = path
    with bpy.context.temp_override(scene=sc):
        bpy.ops.render.render(write_still=True, scene=sc.name)
    im = bpy.data.images.load(path, check_existing=False)
    a = np.array(im.pixels[:], dtype=np.float64).reshape(170, 400, 4)[:, :, :3]
    bpy.data.images.remove(im)
    return a

rep = {}
imgs = {}
for tag, k, units in (("mm", 1.0, 'MM'), ("m", 0.001, 'SCENE')):
    sc = make_scene("S_" + tag, k)
    cam = bl.build("c_series_style.json", scene=sc, units=units, height=170, name="BL_Camera_" + tag)
    cam.location = (0, 0, 0); cam.rotation_euler = (1.5708, 0, 0)
    bl.set_focus(cam, metres=3.0)
    info = bl.lens_info(cam)
    rep[tag] = dict(units=info["units"], mm_per_unit=info["mm_per_scene_unit"], focus_mm=round(info["focus_mm"], 3),
                    squeeze=round(info["squeeze"], 4), rig_scale=[round(c.scale[0], 6) for c in cam.children if c.name.startswith("BL_Rig")],
                    unit_scale_param=round(cam.data.cycles_custom["unit_scale"], 6), clip_start=cam.data.clip_start)
    rig.render_preset(sc)
    sc.render.resolution_x, sc.render.resolution_y = 400, 170
    imgs[tag] = render(sc, tag)
a, b = imgs["mm"], imgs["m"]
rep["mean_mm"] = round(float(a.mean()), 5); rep["mean_m"] = round(float(b.mean()), 5)
rep["rel_mean_abs_diff"] = round(float(np.abs(a - b).mean() / a.mean()), 5)
rep["max_abs_diff"] = round(float(np.abs(a - b).max()), 5)

# A3：GPU（非 OptiX）→ render_init 自动改 CPU（每个 .blend 只有一支镜头：用米制场景这支）
sc = bpy.data.scenes["S_m"]
pr = bpy.context.preferences.addons["cycles"].preferences
backend = None
for t in ('OPTIX', 'METAL', 'CUDA', 'HIP', 'ONEAPI'):
    try:
        pr.compute_device_type = t; pr.get_devices()
        if any(d.type == t for d in pr.devices):
            backend = t; break
    except TypeError:
        pass
if backend:
    for d in pr.devices: d.use = True
    sc.cycles.device = 'GPU'
    rep["gpu_backend"] = backend
    rep["gpu_unsupported_flag"] = rig.gpu_unsupported(sc)
    g = render(sc, "gpu")
    rep["gpu_mean"] = round(float(g.mean()), 5)
    rep["gpu_device_after"] = sc.cycles.device
    pr.compute_device_type = 'NONE'
    sc.cycles.device = 'CPU'

# D2：别名入口
rep["alias_module"] = sys.modules["blenderlens.api"].__name__

# A2：画幅
sc = bpy.data.scenes["S_mm"]
cam = bl.build("c_series_style.json", scene=sc, gate="16:9", height=540, name="BL_Camera_gate")
rx = rig.read_prescription()
rep["gate_169"] = dict(sensor=[rx["sensor"]["width"], rx["sensor"]["height"]], res=[sc.render.resolution_x, sc.render.resolution_y],
                       sensor_width_cam=round(cam.data.sensor_width, 4))
# A2：原生 16:9 满像圈（不裁切）：输出 16:9，画面比原光窗更高，上边缘与四角不发黑（白环境下与预测通过率对照）
for ob in list(sc.objects):
    if not ob.name.startswith("BL_"):
        bpy.data.objects.remove(ob)
sc.world = bpy.data.worlds.new("white"); sc.world.use_nodes = True
bg = sc.world.node_tree.nodes["Background"]; bg.inputs["Color"].default_value = (1, 1, 1, 1); bg.inputs["Strength"].default_value = 1.0
cam = bl.build("c_series_style.json", scene=sc, gate="16:9_NATIVE", height=180, name="BL_Camera_native")
rx = rig.read_prescription()
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rx, 180)
sc.cycles.samples = 256; sc.cycles.seed = 1; sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
sc.render.image_settings.file_format = 'OPEN_EXR'
sc.render.filepath = os.path.join(OUT, "units_native169.exr")
with bpy.context.temp_override(scene=sc):
    bpy.ops.render.render(write_still=True, scene=sc.name)
im = bpy.data.images.load(sc.render.filepath, check_existing=False)
H, W = sc.render.resolution_y, sc.render.resolution_x
a = np.array(im.pixels[:], dtype=np.float64).reshape(H, W, 4)[::-1, :, :3].mean(axis=2)
c = a[H // 2 - 6: H // 2 + 6, W // 2 - 6: W // 2 + 6].mean()
rep["native_169"] = dict(sensor=[rx["sensor"]["width"], rx["sensor"]["height"]], res=[W, H],
                         top_rel=round(float(a[:6, W // 2 - 6: W // 2 + 6].mean() / c), 3),
                         corner_rel=round(float(a[:6, :6].mean() / c), 3),
                         pred_top=round(O.pupil_transmission(rx, (0.0, 0.49 * rx["sensor"]["height"]), cam.data.blenderlens.f_number, n=21), 3),
                         pred_corner=round(O.pupil_transmission(rx, (0.49 * rx["sensor"]["width"], 0.49 * rx["sensor"]["height"]), cam.data.blenderlens.f_number, n=21), 3))
ok = rep["rel_mean_abs_diff"] < 0.01 and rep["m"]["rig_scale"] == [0.001]
ok = ok and abs(W / H - 16 / 9) < 0.01 and rep["native_169"]["sensor"][1] > 18.6 and rep["native_169"]["corner_rel"] > 0.2
if backend and backend != 'OPTIX':
    ok = ok and abs(rep["gpu_mean"] - rep["mean_m"]) / rep["mean_m"] < 0.01
ok = ok and abs(rep["gate_169"]["res"][0] / rep["gate_169"]["res"][1] - 16 / 9) < 0.01
rep["PASS"] = bool(ok)
print("UNITS", json.dumps(rep, ensure_ascii=False))
