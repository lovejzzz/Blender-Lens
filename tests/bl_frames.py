# R5 曝光差 API 与 R6 分帧渲染。
# R5：均匀白环境下，BlenderLens 与同机位 Cycles 普通针孔相机的平均亮度之比 → EV，对照 api.exposure_vs_pinhole() 的预测。
# R6：api.render_frames —— 关键帧对焦；已有帧跳过；删掉的帧重渲与原来逐像素一致（确定性）；别的机器的锁 → 跳过。
# 运行：Blender -b --factory-startup --python tests/bl_frames.py
import os, sys, json, math, shutil, time
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "frames")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
rep = {}

def load(fp):
    im = bpy.data.images.load(fp, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(im.size[1], im.size[0], 4)[:, :, :3]
    bpy.data.images.remove(im)
    return a

# ── R5 ──
sc = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
sc.unit_settings.scale_length = 1.0
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
cam = bl.build("c_series_style.json", sc, focal_mm=40.0, gate="16:9", units="SCENE", height=180)
bl.set_focus(cam, metres=8.5); bl.set_stop(cam, 2.3)
sc.cycles.samples = 256; sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
sc.view_settings.view_transform = 'Standard'; sc.render.image_settings.file_format = 'OPEN_EXR'
sc.render.filepath = os.path.join(OUT, "lens.exr"); bpy.ops.render.render(write_still=True)
lens = load(sc.render.filepath)
pc = bpy.data.cameras.new("pin"); po = bpy.data.objects.new("pin", pc); sc.collection.objects.link(po)
sc.camera = po
sc.render.filepath = os.path.join(OUT, "pin.exr"); bpy.ops.render.render(write_still=True)
pin = load(sc.render.filepath)
sc.camera = cam
pred = bl.exposure_vs_pinhole(cam)
meas_mean = float(lens.mean() / pin.mean())
H, W = lens.shape[:2]
meas_centre = float(lens[H // 2 - 4: H // 2 + 4, W // 2 - 4: W // 2 + 4].mean() / pin[H // 2 - 4: H // 2 + 4, W // 2 - 4: W // 2 + 4].mean())
rep["R5"] = dict(predicted=pred, measured_frame_mean_ev=round(math.log2(meas_mean), 3), measured_centre_ev=round(math.log2(meas_centre), 3))

# ── R6 ──
for o in list(bpy.data.objects):
    if o.name == "pin":
        bpy.data.objects.remove(o)
mat = bpy.data.materials.new("chk")
if mat.node_tree is None:
    mat.use_nodes = True
ck = mat.node_tree.nodes.new("ShaderNodeTexChecker"); ck.inputs["Scale"].default_value = 20
mat.node_tree.links.new(ck.outputs[0], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 3.0, 0), rotation=(math.radians(90), 0, 0))
bpy.context.active_object.scale = (2, 1.2, 1); bpy.context.active_object.data.materials.append(mat)
cam.location = (0, 0, 0); cam.rotation_euler = (math.radians(90), 0, 0)
p = cam.data.blenderlens
for fr, f_m in ((1, 1.5), (2, 3.0), (3, 6.0)):
    p.focus_distance = f_m / (rig.mm_per_unit(sc) / 1000.0)
    p.keyframe_insert("focus_distance", frame=fr)
sc.cycles.samples = 16
fdir = os.path.join(OUT, "seq")
t = time.time(); done1 = bl.render_frames(sc, [1, 2, 3], fdir, name="shot"); t1 = time.time() - t
first2 = load(os.path.join(fdir, "shot_0002.exr"))
done2 = bl.render_frames(sc, [1, 2, 3], fdir, name="shot")                    # 全部已有 → 跳过
os.remove(os.path.join(fdir, "shot_0002.exr"))
done3 = bl.render_frames(sc, [1, 2, 3], fdir, name="shot")                    # 只补渲第 2 帧
again2 = load(os.path.join(fdir, "shot_0002.exr"))
os.remove(os.path.join(fdir, "shot_0003.exr"))
open(os.path.join(fdir, "shot_0003.exr.lock"), "w").write("other-mac pid 1")   # 另一台机器正在渲第 3 帧
done4 = bl.render_frames(sc, [1, 2, 3], fdir, name="shot")
f1, f3 = load(os.path.join(fdir, "shot_0001.exr")), None
rep["R6"] = dict(first_run=done1, rerun=done2, after_delete_2=done3, with_foreign_lock_on_3=done4,
                 deterministic_max_abs_diff=float(np.abs(first2 - again2).max()),
                 focus_animated=bool(np.abs(first2 - f1).mean() > 1e-4), seconds_3_frames=round(t1, 1),
                 leftover_partials=[n for n in os.listdir(fdir) if ".partial" in n])
r5 = rep["R5"]
rep["PASS"] = bool(abs(r5["predicted"]["frame_mean_ev"] - r5["measured_frame_mean_ev"]) < 0.05
                   and abs(r5["predicted"]["centre_ev"] - r5["measured_centre_ev"]) < 0.05
                   and done1 == [1, 2, 3] and done2 == [] and done3 == [2] and done4 == []
                   and rep["R6"]["deterministic_max_abs_diff"] == 0.0 and rep["R6"]["focus_animated"]
                   and not rep["R6"]["leftover_partials"])
print("FRAMES", json.dumps(rep, ensure_ascii=False))
