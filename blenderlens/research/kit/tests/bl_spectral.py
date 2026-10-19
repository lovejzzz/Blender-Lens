# 波长分层采样（0.8，GPU 路径）：同一静止场景，不开运动模糊 → 波长取 Sobol 的 time 维（与出瞳联合分层）；
# 开运动模糊（场景不动，画面相同）→ 波长退回哈希（白噪声）。对照 4096 spp 参考：
#   1. 色噪（RGB 去掉亮度后的 RMSE）随 spp 的下降，分层应明显更低；
#   2. 无偏：两种方式的高 spp 平均颜色一致。
# 运行：Blender -b --factory-startup --python tests/bl_spectral.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "spectral")
os.makedirs(OUT, exist_ok=True)
gpu_ok, why = bl.enable_gpu(use_gpu=True)

sc = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
rig.black_world(sc.world)
cam = bl.build("neil_2x_50.json", scene=sc, height=135, units='MM')
cam.location = (0, 0, 0); cam.rotation_euler = (0, 0, 0)
em = bpy.data.materials.new("e")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 30.0; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
# 焦外的亮球（纵向色差的彩边）+ 边缘的小光源（横向色差）+ 在焦的亮条（边缘彩边）
for loc, r in (((0, 0, -1500), 40), ((-700, 300, -4000), 12), ((900, -350, -4000), 12), ((600, 380, -4000), 12)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc); bpy.context.object.data.materials.append(em)
bpy.ops.mesh.primitive_cube_add(size=1, location=(-300, -150, -4000)); bar = bpy.context.object
bar.scale = (400, 6, 6); bar.data.materials.append(em)
bl.set_focus(cam, metres=4.0); bl.set_stop(cam, 3.0)
p = cam.data.blenderlens
p.dispersion = True; p.dispersion_mode = 'FULL'
rig.apply_settings(cam)
rig.render_preset(sc)
rx = rig.read_prescription()
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rx, 135)
W, H = sc.render.resolution_x, sc.render.resolution_y
sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
sc.view_settings.view_transform = 'Standard'; sc.render.image_settings.file_format = 'OPEN_EXR'
pr = bpy.context.preferences.addons["cycles"].preferences
pr.compute_device_type = 'METAL'; pr.get_devices()
for d in pr.devices:
    d.use = d.type == 'METAL'
sc.cycles.device = 'GPU'

def render(tag, spp, seed, motion):
    sc.render.use_motion_blur = motion
    sc.cycles.samples = spp; sc.cycles.seed = seed
    sc.render.filepath = os.path.join(OUT, f"{tag}_{spp}_{seed}.exr")
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(sc.render.filepath, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(H, W, 4)[:, :, :3]
    bpy.data.images.remove(im)
    return a

ref = render("ref", 4096, 7, False)
lit = ref.mean(axis=2) > 0.02 * ref.mean(axis=2).max()
def chroma(a):
    return a - a.mean(axis=2, keepdims=True)
cref = chroma(ref)
rep = {"gpu": gpu_ok, "resolution": [W, H], "lit_px": int(lit.sum())}
for spp in (16, 64, 256):
    for tag, motion in (("stratified", False), ("hash", True)):
        errs = []
        for seed in (1, 2):
            a = render(tag, spp, seed, motion)
            errs.append(float(np.sqrt(((chroma(a) - cref)[lit] ** 2).mean())))
        rep[f"chroma_rmse_{tag}_{spp}"] = round(float(np.mean(errs)), 5)
    rep[f"gain_{spp}"] = round(rep[f"chroma_rmse_hash_{spp}"] / max(rep[f"chroma_rmse_stratified_{spp}"], 1e-12), 2)
hi_s, hi_h = render("stratified", 2048, 11, False), render("hash", 2048, 11, True)
rep["mean_rgb_stratified"] = hi_s[lit].mean(axis=0).round(4).tolist()
rep["mean_rgb_hash"] = hi_h[lit].mean(axis=0).round(4).tolist()
rep["mean_rel_diff"] = round(float(np.abs(hi_s[lit].mean(axis=0) / hi_h[lit].mean(axis=0) - 1).max()), 4)
rep["PASS"] = bool(gpu_ok and rep["gain_64"] > 1.5 and rep["mean_rel_diff"] < 0.01)
print("SPECTRAL", json.dumps(rep))
