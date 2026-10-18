# 色散模式（R2）：FULL（逐采样波长）/ LATERAL_POST（关色散渲染 + 后期按处方加横向色差）/ OFF。
# 1. 横向色差是否一致：边缘点光源像的 R、B 通道质心水平差（px），FULL（1024 spp）对 LATERAL_POST（1024 spp）；
# 2. 噪点：同 spp（64）下三种模式的亮度噪点与色噪（两种子之差）。
# GPU 路径可用时用 GPU（快），否则 CPU。运行：Blender -b --factory-startup --python tests/bl_dispersion.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "disp")
os.makedirs(OUT, exist_ok=True)
gpu_ok, _ = bl.enable_gpu(use_gpu=True)

sc = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
sc.unit_settings.scale_length = 1.0
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[1].default_value = 0.0
em = bpy.data.materials.new("pt")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 50.0; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
cam = bl.build("c_series_style.json", sc, focal_mm=40.0, gate="16:9", units="SCENE", height=270)
cam.location = (0, 0, 0); cam.rotation_euler = (math.radians(90), 0, 0)
bl.set_focus(cam, metres=6.0); bl.set_stop(cam, 5.6)               # 收光圈：点像小，横向色差看得清
# 在焦点光源：水平一排，从中心到边缘
rx = rig.read_prescription()
for fx in (0.0, 0.2, 0.4, 0.6, 0.8, 0.92):
    # 6 m 处的水平物方位置，按视场比例放（近似即可，测的是像的质心）
    X = fx * 6.0 * math.tan(math.radians(36))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.006, location=(X, 6.0, 0.0))
    bpy.context.active_object.data.materials.append(em)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rx, 270)
sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
sc.view_settings.view_transform = 'Standard'
W, H = sc.render.resolution_x, sc.render.resolution_y

def render(mode, spp, seed):
    p = cam.data.blenderlens
    p.dispersion = mode != 'OFF'
    p.dispersion_mode = 'FULL' if mode == 'OFF' else mode
    rig.apply_settings(cam)
    sc.cycles.samples = spp; sc.cycles.seed = seed
    fp = os.path.join(OUT, f"{mode}_{spp}_{seed}.exr")
    rig.render_frame(sc, fp, flare=False)
    im = bpy.data.images.load(fp, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(H, W, 4)[::-1, :, :3]
    bpy.data.images.remove(im)
    return a

def centroids(a):
    """每个点像：R、G、B 通道的水平质心（px）。点像所在行附近取窗口。"""
    lum = a.mean(axis=2)
    row = int(np.argmax(lum.sum(axis=1)))
    band = a[max(row - 6, 0): row + 7]
    prof = band.sum(axis=0)                       # W × 3
    L = prof.sum(axis=1)
    out = []
    i = 0
    thr = 0.02 * L.max()
    while i < W:
        if L[i] > thr:
            j = i
            while j < W and L[j] > thr:
                j += 1
            xs = np.arange(i, j)
            cs = [float((xs * prof[i:j, c]).sum() / max(prof[i:j, c].sum(), 1e-12)) for c in range(3)]
            out.append(dict(x=round(cs[1], 2), r_minus_b_px=round(cs[0] - cs[2], 3)))
            i = j
        else:
            i += 1
    return out

full = render('FULL', 1024, 1)
post = render('LATERAL_POST', 1024, 1)
cf, cp = centroids(full), centroids(post)
rep = {"gpu": gpu_ok, "resolution": [W, H], "fringe_full": cf, "fringe_lateral_post": cp}
pairs = [(a, b) for a in cf for b in cp if abs(a["x"] - b["x"]) < 3]
rep["fringe_diff_px"] = [round(a["r_minus_b_px"] - b["r_minus_b_px"], 3) for a, b in pairs]
noise = {}
for mode in ('FULL', 'LATERAL_POST', 'OFF'):
    a0, a1 = render(mode, 64, 3), render(mode, 64, 4)
    d = a0 - a1
    lum = d.mean(axis=2)
    chroma = d - lum[..., None]
    lit = (0.5 * (a0 + a1)).mean(axis=2) > 1e-3
    noise[mode] = dict(lum_noise=round(float(np.sqrt((lum[lit] ** 2).mean() / 2)), 5),
                       chroma_noise=round(float(np.sqrt((chroma[lit] ** 2).mean() / 2)), 5))
rep["noise_64spp"] = noise
rep["PASS"] = bool(pairs and max(abs(v) for v in rep["fringe_diff_px"]) < 0.3
                   and noise["LATERAL_POST"]["chroma_noise"] < noise["FULL"]["chroma_noise"])
print("DISP", json.dumps(rep, ensure_ascii=False))
