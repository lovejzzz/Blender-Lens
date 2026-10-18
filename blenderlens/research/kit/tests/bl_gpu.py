# GPU 路径（A-lite）验收：与 CPU / OSL 参考实现对比。
#   1. MessyRoom 合成基准场景（棋盘格 + 面光 + 9 个焦外点光源，C 系列 40 mm、16:9 抽取、对焦 3 m、f/2.3、960×540）
#      GPU 与 CPU 各 64 / 256 spp × 两个种子：时间、噪点（原始 / x/(1+x)）、平均亮度；
#   2. 画面一致性：256 spp 两种子平均后，亮度差、相位相关求整体位移（px）、模糊后逐像素差；
#   3. 动画：对焦打关键帧（1.5 m → 6 m），逐帧 GPU / CPU 对比；
#   4. 回退：Geometry 模式 → 自动改用 CPU。
# 运行：Blender -b --factory-startup --python tests/bl_gpu.py [-- quick]
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
QUICK = "quick" in sys.argv
SPPS = (16, 64) if QUICK else (64, 256)
W_PX, H_PX = (480, 270) if QUICK else (960, 540)

ok, why = bl.enable_gpu(use_gpu=False)                 # 必须在第一次 GPU 渲染之前
rep = {"enable": [ok, why], "machine": "Apple M2 Max (38-core GPU), Blender " + bpy.app.version_string}
if not ok:
    print("GPU", json.dumps(rep, ensure_ascii=False)); raise SystemExit(0)

sc = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
sc.unit_settings.scale_length = 1.0
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[1].default_value = 0.02
m = bpy.data.materials.new("chart")
if m.node_tree is None:
    m.use_nodes = True
nt = m.node_tree; b = nt.nodes["Principled BSDF"]
ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = 24
nt.links.new(ch.outputs[0], b.inputs["Base Color"])
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 3.0, 0)); p = bpy.context.active_object
p.scale = (2.0, 1.2, 1); p.rotation_euler = (math.radians(90), 0, 0); p.data.materials.append(m)
ld = bpy.data.lights.new("key", "AREA"); ld.size = 2; ld.energy = 400
k = bpy.data.objects.new("key", ld); sc.collection.objects.link(k); k.location = (0, 0.5, 2.5); k.rotation_euler = (math.radians(60), 0, 0)
em = bpy.data.materials.new("pt")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 400; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
for ix in (-1, 0, 1):
    for iz in (-1, 0, 1):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.02, location=(ix * 3.2, 12.0, iz * 1.8))
        bpy.context.active_object.data.materials.append(em)
cam = bl.build("c_series_style.json", sc, focal_mm=40.0, gate="16:9", units="SCENE", height=H_PX)
cam.location = (0, 0, 0); cam.rotation_euler = (math.radians(90), 0, 0)
bl.set_focus(cam, metres=3.0); bl.set_stop(cam, 2.3)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W_PX, H_PX, 100
sc.cycles.use_denoising = False
sc.render.image_settings.file_format = "OPEN_EXR"; sc.render.image_settings.color_depth = "32"
pr = bpy.context.preferences.addons["cycles"].preferences
pr.compute_device_type = 'METAL'; pr.get_devices()
for d in pr.devices:
    d.use = d.type == 'METAL'

def render(dev, spp, seed, tag):
    sc.cycles.device = dev; sc.cycles.samples = spp; sc.cycles.seed = seed
    fp = os.path.join(OUT, f"{tag}_{dev}_{spp}_s{seed}.exr"); sc.render.filepath = fp
    t = time.time(); bpy.ops.render.render(write_still=True); dt = time.time() - t
    im = bpy.data.images.load(fp, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(im.size[1], im.size[0], 4)[::-1, :, :3]
    bpy.data.images.remove(im)
    return a, dt, sc.cycles.device

def noise(a, b):
    tm = lambda x: x / (1 + x)
    return dict(raw=float(np.sqrt(np.mean((a - b) ** 2)) / math.sqrt(2)), tm=float(np.sqrt(np.mean((tm(a) - tm(b)) ** 2)) / math.sqrt(2)))

def phase_shift(a, b):
    """b 相对 a 的整体位移（px），相位相关 + 抛物线亚像素。"""
    A = np.fft.fft2(a - a.mean()); B = np.fft.fft2(b - b.mean())
    R = A * np.conj(B); R /= np.abs(R) + 1e-12
    r = np.fft.ifft2(R).real
    iy, ix = np.unravel_index(np.argmax(r), r.shape)
    def sub(v_m, v0, v_p):
        den = v_m - 2 * v0 + v_p
        return 0.0 if abs(den) < 1e-12 else 0.5 * (v_m - v_p) / den
    H, W = r.shape
    dy = iy + sub(r[(iy - 1) % H, ix], r[iy, ix], r[(iy + 1) % H, ix])
    dx = ix + sub(r[iy, (ix - 1) % W], r[iy, ix], r[iy, (ix + 1) % W])
    dy = dy - H if dy > H / 2 else dy; dx = dx - W if dx > W / 2 else dx
    return float(dx), float(dy)

render('GPU', 1, 0, "warm")                               # 内核编译（首次约 2 分钟），不计时
imgs = {}
for dev in ('GPU', 'CPU'):
    for spp in SPPS:
        a0, t0, d0 = render(dev, spp, 0, "bench"); a1, t1, d1 = render(dev, spp, 1, "bench")
        n = noise(a0, a1)
        rep[f"{dev}_{spp}"] = dict(seconds=round((t0 + t1) / 2, 2), noise_rms=round(n["raw"], 6), noise_tm=round(n["tm"], 5),
                                   mean=round(float(a0.mean()), 5), device_used=d0)
        imgs[(dev, spp)] = 0.5 * (a0 + a1)
        print("GPU_ROW", dev, spp, json.dumps(rep[f"{dev}_{spp}"]), flush=True)
hi = SPPS[-1]
g, c = imgs[('GPU', hi)], imgs[('CPU', hi)]
lg, lc = g.mean(axis=2), c.mean(axis=2)
from numpy.fft import fft2, ifft2
def blur(x, s=2.0):
    H, W = x.shape
    fy = np.fft.fftfreq(H)[:, None]; fx = np.fft.fftfreq(W)[None, :]
    return ifft2(fft2(x) * np.exp(-2 * math.pi ** 2 * s * s * (fx * fx + fy * fy))).real
dx, dy = phase_shift(np.log1p(lc), np.log1p(lg))
rep["compare"] = dict(mean_rel_diff=round(float(lg.mean() / lc.mean() - 1), 5), shift_px=[round(dx, 3), round(dy, 3)],
                      blurred_rel_rms=round(float(np.sqrt(np.mean((blur(lg) - blur(lc)) ** 2)) / lc.mean()), 5),
                      noise_floor_rel=round(float(rep[f"CPU_{hi}"]["noise_rms"] / lc.mean()), 5),
                      speedup_vs_cpu=round(rep[f"CPU_{hi}"]["seconds"] / rep[f"GPU_{hi}"]["seconds"], 2))
for spp in SPPS:
    rep["compare"][f"convergence_{SPPS[0]}_{SPPS[-1]}_{'GPU'}"] = round(rep[f"GPU_{SPPS[0]}"]["noise_rms"] / rep[f"GPU_{SPPS[-1]}"]["noise_rms"], 3)
print("GPU_ROW compare", json.dumps(rep["compare"]), flush=True)

# 3. 动画：对焦关键帧
p = cam.data.blenderlens
p.focus_distance = 1.5; p.keyframe_insert("focus_distance", frame=1)
p.focus_distance = 6.0; p.keyframe_insert("focus_distance", frame=2)
anim = {}
for fr in (1, 2):
    sc.frame_set(fr)
    a, _, _ = render('GPU', 64, 0, f"anim{fr}"); b2, _, _ = render('CPU', 64, 0, f"anim{fr}")
    anim[fr] = dict(focus_mm=round(rig.focus_mm(cam), 1), gpu_mean=round(float(a.mean()), 5), cpu_mean=round(float(b2.mean()), 5),
                    blurred_rel_rms=round(float(np.sqrt(np.mean((blur(a.mean(2)) - blur(b2.mean(2))) ** 2)) / b2.mean()), 5),
                    conv_dist=round(cam.data.stereo.convergence_distance, 4))
rep["animation"] = anim
print("GPU_ROW animation", json.dumps(anim), flush=True)

# 4. 回退：Geometry 模式
p.mode = 'GEOMETRY'
rep["fallback_geometry_reason"] = rig.gpu_block_reason(sc)
a, _, used = render('GPU', 4, 0, "fallback")
rep["fallback_geometry_device_used"] = used
p.mode = 'ANALYTIC'

c = rep["compare"]
rep["PASS"] = bool(abs(c["mean_rel_diff"]) < 0.01 and max(abs(c["shift_px"][0]), abs(c["shift_px"][1])) < 0.5
                   and rep["fallback_geometry_device_used"] == 'CPU'
                   and all(abs(v["gpu_mean"] / v["cpu_mean"] - 1) < 0.02 for v in anim.values()))
print("GPU", json.dumps(rep, ensure_ascii=False))
