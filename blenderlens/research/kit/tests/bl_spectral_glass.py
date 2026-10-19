# Blender 5.3 色散材质与镜头色散共用路径波长（0.8.2）：白色天空透过一大块玻璃。
# 修好时（GPU 路径置 PATH_RAY_SPECTRAL、同一个 λ）：色散玻璃与无色散玻璃的色噪相当、平均颜色都接近白；
# 没修（CPU / OSL 路径，各抽一个波长、颜色加权两次）：色噪明显变大——只作对照记录，不判失败。
# 运行：Blender -b --factory-startup --python tests/bl_spectral_glass.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "spectral_glass")
os.makedirs(OUT, exist_ok=True)
gpu_ok, _ = bl.enable_gpu(use_gpu=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
cam = bl.build("neil_2x_50.json", scene=sc, height=120, units='MM')
cam.location = (0, 0, 0); cam.rotation_euler = (0, 0, 0)
glass = bpy.data.materials.new("glass")
if glass.node_tree is None:
    glass.use_nodes = True
pb = glass.node_tree.nodes["Principled BSDF"]
pb.inputs["Transmission Weight"].default_value = 1.0
pb.inputs["Roughness"].default_value = 0.0
pb.inputs["IOR"].default_value = 1.6
pb.inputs["Transmission Dispersion Abbe Number"].default_value = 20.0
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -2500)); slab = bpy.context.object
slab.scale = (6000, 6000, 80); slab.rotation_euler = (math.radians(25), math.radians(15), 0)
slab.data.materials.append(glass)
p = cam.data.blenderlens
bl.configure(cam, focus_m=2.5, f_number=4.0, dispersion=True, dispersion_mode='FULL')
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 120)
W, H = sc.render.resolution_x, sc.render.resolution_y
sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
sc.cycles.max_bounces = 16; sc.cycles.transmission_bounces = 16
sc.view_settings.view_transform = 'Standard'; sc.render.image_settings.file_format = 'OPEN_EXR'
pr = bpy.context.preferences.addons["cycles"].preferences
pr.compute_device_type = 'METAL'; pr.get_devices()
for d in pr.devices:
    d.use = d.type == 'METAL'

def render(dev, scale, seed, spp=64):
    pb.inputs["Transmission Dispersion Scale"].default_value = scale
    sc.cycles.device = dev; sc.cycles.samples = spp; sc.cycles.seed = seed
    sc.render.filepath = os.path.join(OUT, f"{dev}_{scale}_{seed}.exr")
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(sc.render.filepath, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(H, W, 4)[:, :, :3]
    bpy.data.images.remove(im)
    return a

def stats(dev, scale):
    a0, a1 = render(dev, scale, 1), render(dev, scale, 2)
    m = 0.5 * (a0 + a1)
    core = m[H // 4: 3 * H // 4, W // 4: 3 * W // 4]           # 中心区域（避开暗角）
    d = (a0 - a1)[H // 4: 3 * H // 4, W // 4: 3 * W // 4]
    chroma = d - d.mean(axis=2, keepdims=True)
    mean = core.reshape(-1, 3).mean(axis=0)
    return dict(mean_rgb=mean.round(4).tolist(), tint=round(float(np.abs(mean / mean.mean() - 1).max()), 4),
                chroma_noise=round(float(np.sqrt((chroma ** 2).mean() / 2)), 5))

rep = {"gpu": gpu_ok, "diagnose_cpu": None}
sc.cycles.device = 'CPU'
pb.inputs["Transmission Dispersion Scale"].default_value = 1.0
rep["diagnose_cpu"] = [i["code"] for i in bl.diagnose()]
if gpu_ok:
    rep["gpu_plain"] = stats('GPU', 0.0)
    rep["gpu_dispersive"] = stats('GPU', 1.0)
    rep["gpu_noise_ratio"] = round(rep["gpu_dispersive"]["chroma_noise"] / max(rep["gpu_plain"]["chroma_noise"], 1e-9), 3)
rep["cpu_plain"] = stats('CPU', 0.0)
rep["cpu_dispersive"] = stats('CPU', 1.0)
rep["cpu_noise_ratio"] = round(rep["cpu_dispersive"]["chroma_noise"] / max(rep["cpu_plain"]["chroma_noise"], 1e-9), 3)
rep["PASS"] = bool(gpu_ok and rep["gpu_noise_ratio"] < 1.3 and rep["gpu_dispersive"]["tint"] < 0.01
                   and "spectral_conflict_cpu" in rep["diagnose_cpu"])
print("SPECGLASS", json.dumps(rep))
