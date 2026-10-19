# 工作色彩空间（0.8.2）：在非 Rec.709 的工作色彩空间（ACEScg）里，白色天空开镜头色散后的平均颜色
# 应与关色散时相同（色散只改方向、不改颜色）——GPU（Cycles 的 rec709_to_rgb）与 CPU / OSL（插件写入的矩阵）都要对。
# 0.8.1 及以前：传感器权重按 Rec.709 直接当工作空间 RGB，ACEScg 里白天空会偏色。
# 运行：Blender -b --factory-startup --python tests/bl_colorspace.py
import os, sys, json
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "colorspace")
os.makedirs(OUT, exist_ok=True)
gpu_ok, _ = bl.enable_gpu(use_gpu=True)
rep = {"gpu": gpu_ok}
bpy.ops.wm.set_working_color_space(working_space='ACEScg')       # bpy.data.colorspace.working_space 是只读的
rep["working_space"] = bpy.data.colorspace.working_space
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
cam = bl.build("c_series_style.json", scene=sc, height=90, units='MM')
bl.configure(cam, f_number=4.0)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 90)
W, H = sc.render.resolution_x, sc.render.resolution_y
sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False; sc.cycles.samples = 256
sc.render.image_settings.file_format = 'OPEN_EXR'
pr = bpy.context.preferences.addons["cycles"].preferences
pr.compute_device_type = 'METAL'; pr.get_devices()
for d in pr.devices:
    d.use = d.type == 'METAL'

def mean_rgb(dev, disp):
    bl.configure(cam, dispersion=disp)
    sc.cycles.device = dev
    sc.render.filepath = os.path.join(OUT, f"{dev}_{int(disp)}.exr")
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(sc.render.filepath, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(H, W, 4)[H // 3: 2 * H // 3, W // 3: 2 * W // 3, :3]
    bpy.data.images.remove(im)
    return a.reshape(-1, 3).mean(axis=0)

for dev in (('GPU', 'CPU') if gpu_ok else ('CPU',)):
    off, on = mean_rgb(dev, False), mean_rgb(dev, True)
    rep[dev] = dict(off=off.round(4).tolist(), on=on.round(4).tolist(), max_rel=round(float(np.abs(on / off - 1).max()), 4))
codes = [i["code"] for i in bl.diagnose()]
rep["diagnose_working_space"] = "working_space" in codes
rep["PASS"] = bool(rep["working_space"] == "ACEScg" and all(rep[d]["max_rel"] < 0.01 for d in ("GPU", "CPU") if d in rep))
print("COLORSPACE", json.dumps(rep))
