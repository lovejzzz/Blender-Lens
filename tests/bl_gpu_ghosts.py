# GPU 路径的相机端鬼影（0.7）对照 CPU / OSL 参考实现：
# 黑背景 + 画面外侧一块很亮的发光面板（窗户类大面积亮源），开鬼影（增益放大便于比较），
# 比较：去掉直射像后鬼影区域的总能量、模糊后逐像素差、时间。运行：Blender -b --factory-startup --python tests/bl_gpu_ghosts.py
import os, sys, json, math, time
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "gpu")
os.makedirs(OUT, exist_ok=True)
ok, why = bl.enable_gpu(use_gpu=False)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
rig.black_world(sc.world)
cam = bl.build("c_series_style.json", scene=sc, height=101, units='MM')
cam.location = (0, 0, 0); cam.rotation_euler = (0, 0, 0)
em = bpy.data.materials.new("e")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 400.0; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
bpy.ops.mesh.primitive_plane_add(size=1, location=(1300, 250, -6000)); pl = bpy.context.object
pl.scale = (500, 350, 1); pl.data.materials.append(em)
p = cam.data.blenderlens
p.ghosts = True; p.ghost_gain = 20.0; p.dispersion = False
bl.set_focus(cam, metres=6.0); bl.set_stop(cam, 4.0)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = 240, 101
sc.cycles.samples = 2048; sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
sc.view_settings.view_transform = 'Standard'; sc.render.image_settings.file_format = 'OPEN_EXR'
pr = bpy.context.preferences.addons["cycles"].preferences
pr.compute_device_type = 'METAL'; pr.get_devices()
for d in pr.devices:
    d.use = d.type == 'METAL'

def render(dev):
    sc.cycles.device = dev
    sc.render.filepath = os.path.join(OUT, f"ghost_{dev}.exr")
    t = time.time(); bpy.ops.render.render(write_still=True); dt = time.time() - t
    im = bpy.data.images.load(sc.render.filepath, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(im.size[1], im.size[0], 4)[:, :, :3]
    bpy.data.images.remove(im)
    return a, dt, sc.cycles.device

def blur(x, s=3.0):
    H, W = x.shape
    fy = np.fft.fftfreq(H)[:, None]; fx = np.fft.fftfreq(W)[None, :]
    return np.fft.ifft2(np.fft.fft2(x) * np.exp(-2 * math.pi ** 2 * s * s * (fx * fx + fy * fy))).real

render('GPU')                                            # 重编内核（鬼影设置编进头文件）
g, tg, dg = render('GPU')
c, tc, dc = render('CPU')
lg, lc = g.mean(2), c.mean(2)
direct = lc > 0.3 * lc.max()                             # 直射像（面板）掩掉，只比鬼影
mask = ~direct
ghost_g, ghost_c = lg[mask].sum(), lc[mask].sum()
rep = dict(enable=ok, device_gpu=dg, device_cpu=dc, seconds_gpu=round(tg, 2), seconds_cpu=round(tc, 2),
           ghost_energy_gpu=round(float(ghost_g), 3), ghost_energy_cpu=round(float(ghost_c), 3),
           ghost_rel_diff=round(float(ghost_g / max(ghost_c, 1e-9) - 1), 4),
           blurred_rel_rms_ghost=round(float(np.sqrt(np.mean((blur(lg)[mask] - blur(lc)[mask]) ** 2)) / max(blur(lc)[mask].mean(), 1e-9)), 4),
           ghost_fraction_of_frame=round(float(ghost_c / lc.sum()), 4))
rep["PASS"] = bool(dg == 'GPU' and abs(rep["ghost_rel_diff"]) < 0.03 and rep["blurred_rel_rms_ghost"] < 0.15)
print("GHOSTS", json.dumps(rep))
