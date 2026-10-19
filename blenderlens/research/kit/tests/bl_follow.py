# api.follow_camera（MessyRoom 反馈：每帧只拷贝 matrix_world 会丢相机运动模糊）。
# 源相机动画 → 烘焙到 BlenderLens 相机：子帧位置在两帧之间（不是阶梯）、对焦距离有关键帧、
# 开运动模糊时点光源被拉成拖影（只拷贝矩阵时不拖）。运行：Blender -b --factory-startup --python tests/bl_follow.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders")
os.makedirs(OUT, exist_ok=True)

sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
rig.black_world(sc.world)
sc.frame_start, sc.frame_end = 1, 3
# 源相机：横向快速平移 + 轻微转动，景深对焦距离也在变
src_cam = bpy.data.cameras.new("src"); src = bpy.data.objects.new("src", src_cam); sc.collection.objects.link(src)
src_cam.dof.use_dof = True
for f, x, yaw, fd in ((1, -400.0, 0.0, 4000.0), (2, 0.0, 0.03, 5000.0), (3, 400.0, 0.06, 6000.0)):
    src.location = (x, 0.0, 0.0); src.rotation_euler = (0.0, yaw, 0.0); src_cam.dof.focus_distance = fd
    src.keyframe_insert("location", frame=f); src.keyframe_insert("rotation_euler", frame=f)
    src_cam.dof.keyframe_insert("focus_distance", frame=f)
cam = bl.build("c_series_style.json", scene=sc, height=135, units='MM')
n = bl.follow_camera(cam, src, substeps=2)
rep = {"keys": n}
sc.frame_set(1, subframe=0.5)
rep["src_x_1.5"] = round(src.matrix_world.translation.x, 2)
rep["lens_x_1.5"] = round(cam.matrix_world.translation.x, 2)
sc.frame_set(2)
rep["focus_frame2_mm"] = round(rig.focus_mm(cam), 1)
# 运动模糊：点光源，开 / 关跟随烘焙
em = bpy.data.materials.new("e")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 500.0; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
bpy.ops.mesh.primitive_uv_sphere_add(radius=15, location=(0, 0, -5000)); bpy.context.object.data.materials.append(em)
sc.render.use_motion_blur = True; sc.render.motion_blur_shutter = 1.0
sc.cycles.samples = 64; sc.cycles.use_denoising = False
sc.view_settings.view_transform = 'Standard'; sc.render.image_settings.file_format = 'OPEN_EXR'

def streak(tag):
    sc.frame_set(2)
    sc.render.filepath = os.path.join(OUT, f"follow_{tag}.exr")
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(sc.render.filepath, check_existing=False)
    a = np.array(im.pixels[:]).reshape(im.size[1], im.size[0], 4)[:, :, :3].mean(2)
    bpy.data.images.remove(im)
    cols = np.nonzero(a.max(axis=0) > 0.05 * a.max())[0]
    return int(cols.max() - cols.min() + 1) if len(cols) else 0

rep["streak_px_baked"] = streak("baked")
# 对照：清掉关键帧，只在第 2 帧拷贝矩阵（快门内不动）
cam.animation_data_clear()
sc.frame_set(2); cam.matrix_world = src.matrix_world.copy()
rep["streak_px_copied"] = streak("copied")
rep["PASS"] = bool(abs(rep["lens_x_1.5"] - rep["src_x_1.5"]) < 1.0 and abs(rep["focus_frame2_mm"] - 5000.0) < 1.0
                   and rep["streak_px_baked"] > 2 * rep["streak_px_copied"])
print("FOLLOW", json.dumps(rep))
