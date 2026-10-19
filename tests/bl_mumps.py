# 发腮补偿（0.8）：最近对焦处放一个发光圆盘，量解挤压后画面里它的宽高比（二阶矩）。
# 补偿强度 0：圆变「胖」（宽高比 ≈ 解挤压 / 实际压缩比 ≈ 1.19）；1：恢复为圆（≈ 2.0 / 2.011）。
# CPU（OSL）与 GPU 路径一致；Geometry 模式（补偿板网格随对焦改形）一致。
# 运行：Blender -b --factory-startup --python tests/bl_mumps.py [-- c_series_style.json]
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, optics as O
OUT = os.path.join(ROOT, "renders", "mumps")
os.makedirs(OUT, exist_ok=True)
PRESET = next((a for a in sys.argv[sys.argv.index("--") + 1:] if a.endswith(".json")), "c_series_style.json") if "--" in sys.argv else "c_series_style.json"
gpu_ok, _ = bl.enable_gpu(use_gpu=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
rig.black_world(sc.world)
cam = bl.build(PRESET, scene=sc, height=200, units='MM')
cam.location = (0, 0, 0); cam.rotation_euler = (0, 0, 0)
rx = rig.read_prescription()
mfd = rx["focus"]["min_distance_mm"]
D = O.desqueeze(rx)
em = bpy.data.materials.new("e")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 5.0; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
# 圆盘：物距 = 最近对焦（从像面量），半径取画面高度的约 1/4
fy = O.paraxial_infinity(O.layout(rx), "Ry")[0]
R = 0.22 * rx["sensor"]["height"] * mfd / fy
bpy.ops.mesh.primitive_circle_add(vertices=256, radius=R, fill_type='NGON', location=(0, 0, -mfd))
bpy.context.object.data.materials.append(em)
p = cam.data.blenderlens
p.dispersion = False
bl.set_focus(cam, metres=mfd / 1000.0); bl.set_stop(cam, 5.6)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rx, 200)
W, H = sc.render.resolution_x, sc.render.resolution_y
sc.cycles.use_denoising = False
sc.view_settings.view_transform = 'Standard'; sc.render.image_settings.file_format = 'OPEN_EXR'
pr = bpy.context.preferences.addons["cycles"].preferences
pr.compute_device_type = 'METAL'; pr.get_devices()
for d in pr.devices:
    d.use = d.type == 'METAL'

def aspect(tag, dev, spp, mode='ANALYTIC'):
    p.mode = mode
    rig.render_preset(sc, mode)
    sc.cycles.device = dev
    sc.cycles.samples = spp
    sc.render.filepath = os.path.join(OUT, f"{PRESET[:-5]}_{tag}_{mode.lower()}_{dev}.exr")
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(sc.render.filepath, check_existing=False)
    a = np.array(im.pixels[:], np.float64).reshape(H, W, 4)[:, :, :3].mean(2)
    bpy.data.images.remove(im)
    m = a > 0.5 * a.max()                                     # 圆盘内部（实心，边缘软）
    ys, xs = np.nonzero(m)
    return round(float(xs.std() / ys.std()), 4), round(float(m.sum()), 1)

rep = {"preset": PRESET, "gpu": gpu_ok, "desqueeze": D, "mfd_mm": mfd, "resolution": [W, H]}
for lev in (0.0, 1.0):
    p.mumps_correction = lev
    rx_l = rig.read_prescription()
    rep[f"squeeze_paraxial_{lev:g}"] = round(O.squeeze_at(rx_l, mfd), 4)
    rep[f"expected_aspect_{lev:g}"] = round(D / O.squeeze_at(rx_l, mfd), 4)
    rep[f"aspect_cpu_{lev:g}"] = aspect(f"lev{lev:g}", 'CPU', 64)[0]
    if gpu_ok:
        rep[f"aspect_gpu_{lev:g}"] = aspect(f"lev{lev:g}", 'GPU', 64)[0]
rep["aspect_geometry_1"] = aspect("lev1", 'CPU', 48, 'GEOMETRY')[0]
ok_ = all(abs(rep[f"aspect_cpu_{l:g}"] / rep[f"expected_aspect_{l:g}"] - 1) < 0.01 for l in (0.0, 1.0))
if gpu_ok:
    ok_ &= all(abs(rep[f"aspect_gpu_{l:g}"] / rep[f"aspect_cpu_{l:g}"] - 1) < 0.003 for l in (0.0, 1.0))
ok_ &= abs(rep["aspect_geometry_1"] / rep["aspect_cpu_1"] - 1) < 0.01
rep["PASS"] = bool(ok_)
print("MUMPS", json.dumps(rep))
