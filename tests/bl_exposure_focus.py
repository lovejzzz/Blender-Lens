# 曝光关键帧与对焦跟随（0.8.5，MessyRoom 0.8.5 R4 / R5）
#   R4：均匀中灰发光墙，film_exposure 打了阶跃关键帧（第 1–2 帧 −1.0 EV，第 3–4 帧 −1.7 EV，常数插值）。
#       render_shot(exposure="pinhole" / "pinhole_centre") 与 Cycles 普通针孔相机（同一曲线）比：整幅平均 / 画面中心差 ≤ 0.05 EV；
#       切点（2 → 3）的阶跃 = 关键帧的 −0.7 EV；渲完曲线恢复原样。
#   R5：相机横移，focus_track 跟住「门」（正对相机的薄板）：每帧主体处弥散 ≤ 0.5 px（4K）；
#       rack=[(5, 关东煮), (9, CRT)]：两端的对焦距离与沿射线实测的轴向距离差 ≤ 1%。
# 运行：Blender -b --factory-startup --python tests/bl_exposure_focus.py
import os, sys, json, math, shutil
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, focus as FC
OUT = os.path.join(ROOT, "renders", "exposure_focus")
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
sc.world = bpy.data.worlds.new("W")
if sc.world.node_tree is None:
    sc.world.use_nodes = True
sc.world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
m = bpy.data.materials.new("grey")
if m.node_tree is None:
    m.use_nodes = True
nt = m.node_tree; nt.nodes.clear()
o_ = nt.nodes.new("ShaderNodeOutputMaterial"); e = nt.nodes.new("ShaderNodeEmission")
e.inputs[0].default_value = (0.18, 0.18, 0.18, 1); e.inputs[1].default_value = 1.0; nt.links.new(e.outputs[0], o_.inputs[0])
bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 30, 0), rotation=(math.radians(90), 0, 0)); bpy.context.object.data.materials.append(m)
cd = bpy.data.cameras.new("Src"); src = bpy.data.objects.new("Src", cd); sc.collection.objects.link(src)
cd.lens = 35.0
src.rotation_euler = (math.radians(90), 0, 0)
sc.frame_start, sc.frame_end = 1, 4
for f, ev in ((1, -1.0), (3, -1.7)):
    sc.cycles.film_exposure = 2.0 ** ev; sc.keyframe_insert("cycles.film_exposure", frame=f)
for fc in bl._action_fcurves(sc.animation_data.action):
    for kp in fc.keyframe_points:
        kp.interpolation = 'CONSTANT'
sc.render.resolution_x, sc.render.resolution_y = 320, 180
sc.render.engine = 'CYCLES'; sc.cycles.samples = 16; sc.cycles.use_denoising = False
sc.view_settings.view_transform = 'Standard'; sc.view_settings.look = 'None'
sc.camera = src

def mean_of(path, centre=False):
    im = bpy.data.images.load(path, check_existing=False)
    a = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)[..., :3].mean(-1)
    bpy.data.images.remove(im)
    if centre:
        H, W = a.shape
        return float(a[int(H * 0.4):int(H * 0.6), int(W * 0.4):int(W * 0.6)].mean())
    return float(a.mean())

pin = {}
for f in (1, 2, 3, 4):                                    # Cycles 普通针孔相机
    sc.frame_set(f)
    sc.render.filepath = os.path.join(OUT, f"pin_{f}.exr"); sc.render.image_settings.file_format = 'OPEN_EXR'
    bpy.ops.render.render(write_still=True)
    pin[f] = (mean_of(sc.render.filepath), mean_of(sc.render.filepath, True))
keys_before = [round(kp.co[1], 6) for fc in bl._action_fcurves(sc.animation_data.action) for kp in fc.keyframe_points]
rep = dict(pinhole_means={f: round(v[0], 5) for f, v in pin.items()})
for mode, idx in (("pinhole", 0), ("pinhole_centre", 1)):
    d = os.path.join(OUT, mode)
    log = bl.render_shot(source_camera="Src", frames=range(1, 5), out_dir=d, preset="c_series_style" if mode == "pinhole" else None,
                         height=180, gate="16:9_NATIVE", units="SCENE", spp=16, exposure=mode, guard=False, flare=False)
    evs = {}
    for f in (1, 2, 3, 4):
        v = mean_of(os.path.join(d, f"f_{f:04d}.exr"), centre=(idx == 1))
        evs[f] = round(math.log2(v / pin[f][idx]), 4)
    rep[mode] = dict(ev_diff=evs, log=log["exposure"])
    bl_step = math.log2(mean_of(os.path.join(d, "f_0003.exr"), idx == 1) / mean_of(os.path.join(d, "f_0002.exr"), idx == 1))
    rep[mode]["cut_step_ev"] = round(bl_step, 4)
keys_after = [round(kp.co[1], 6) for fc in bl._action_fcurves(sc.animation_data.action) for kp in fc.keyframe_points]
rep["curve_restored"] = keys_before == keys_after

# R5：对焦跟随 + 拉焦
cam = bl.camera(sc)
def board(name, loc, size=0.6):
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc, rotation=(math.radians(90), 0, 0)); ob = bpy.context.object; ob.name = name
    return ob
door = board("Door", (0.6, 7.0, 0.0), 1.5); oden = board("Oden", (-0.3, 2.6, -0.2), 0.3); crt = board("CRT", (0.4, 3.9, 0.1), 0.4)
sc.frame_start, sc.frame_end = 1, 12
src.animation_data_clear()
for f in (1, 12):
    src.location = (-0.45 + 0.08 * (f - 1), -0.2 * (f - 1) / 11, 0.0); src.keyframe_insert("location", frame=f)
bl.follow_camera(cam, src, 1, 12)
trk = bl.focus_track(cam, "Door", frames=(1, 12))
W4, H4 = rig.desqueezed_resolution(rig.read_prescription(), 2160)
blur = []
for f, m_, who in trk:
    sc.frame_set(f); rig.apply_settings(cam)
    ax = FC.axial_mm(cam, door.matrix_world.translation)
    model = FC.DofModel(rig.read_prescription(), rig.focus_mm(cam), cam.data.blenderlens.f_number, W4, H4)
    blur.append(model.blur_px(ax)[0])
rep["track_max_blur_px_4k"] = round(max(blur), 4)
rk = bl.focus_track(cam, frames=(1, 12), rack=[(5, "Oden"), (9, "CRT")])
def ray_axial(f, ob):                                   # 沿射线实测：从相机射向目标，打到的点的轴向距离
    sc.frame_set(f); rig.apply_settings(cam)
    o = cam.matrix_world.translation; d = (ob.matrix_world.translation - o).normalized()
    dg = bpy.context.evaluated_depsgraph_get()
    hit, loc, _n, _i, hob, _m = sc.ray_cast(dg, o + d * 0.05, d)
    while hit and hob.name.startswith("BL_"):            # 镜头自己的网格（镜片 / 镜筒）跳过
        hit, loc, _n, _i, hob, _m = sc.ray_cast(dg, loc + d * 0.001, d)
    return FC.axial_mm(cam, loc) / 1000.0
fd = {f: m_ for f, m_, _w in rk}
rep["rack"] = dict(f5=fd[5], oden_ray=round(ray_axial(5, oden), 4), f9=fd[9], crt_ray=round(ray_axial(9, crt), 4),
                   who=[w for f, _m, w in rk if f in (3, 7, 11)])
rep["rack_err"] = round(max(abs(fd[5] / rep["rack"]["oden_ray"] - 1), abs(fd[9] / rep["rack"]["crt_ray"] - 1)), 5)
rep["PASS"] = bool(all(abs(v) <= 0.05 for k in ("pinhole", "pinhole_centre") for v in rep[k]["ev_diff"].values())
                   and all(abs(rep[k]["cut_step_ev"] + 0.7) < 0.02 for k in ("pinhole", "pinhole_centre"))
                   and rep["curve_restored"] and rep["track_max_blur_px_4k"] <= 0.5 and rep["rack_err"] <= 0.01)
print("EXPFOCUS", json.dumps(rep))
