# 收敛测试（回应改进建议 §7.5：真实场景 4× 采样噪点只降 1.5×）。
# 场景：棋盘格板 + 一排很小很亮的焦外点光源（萤火虫源）。比较 64 → 256 spp 的噪点下降（理想 2×）：
#   色散 开 / 关（第三维随机数是否与采样序列相关）× 自适应采样 开 / 关。
# 噪点 = 两个随机种子之差的 RMS / √2；同时给 x/(1+x) 压缩后的值（与 bl_bench 一致，避免极亮点主导）。
# 运行：Blender -b --factory-startup --python tests/bl_convergence.py
import os, sys, json
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders")

sc = bpy.context.scene
for ob in list(sc.objects):
    bpy.data.objects.remove(ob)
rig.black_world(sc.world)

def emission(name, color, strength, checker=False):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial"); e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Strength"].default_value = strength
    if checker:
        tc = nt.nodes.new("ShaderNodeTexCoord"); ck = nt.nodes.new("ShaderNodeTexChecker")
        ck.inputs["Scale"].default_value = 10.0
        ck.inputs["Color1"].default_value = (*color, 1); ck.inputs["Color2"].default_value = (0.03, 0.03, 0.03, 1)
        nt.links.new(tc.outputs["UV"], ck.inputs["Vector"]); nt.links.new(ck.outputs["Color"], e.inputs["Color"])
    else:
        e.inputs["Color"].default_value = (*color, 1)
    nt.links.new(e.outputs[0], o.inputs["Surface"])
    return m

cam = bl.build("c_series_style.json", scene=sc, units='MM', focal_mm=40.0, height=270)
cam.location = (0, 0, 0); cam.rotation_euler = (1.5708, 0, 0)
bl.set_focus(cam, metres=2.0)
bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 2000, 0), rotation=(1.5708, 0, 0))
pl = bpy.context.active_object; pl.scale = (1400, 900, 1)
pl.data.materials.append(emission("chk", (0.8, 0.7, 0.6), 0.6, checker=True))
lamp = emission("lamp", (1.0, 0.75, 0.45), 3000.0)
for i in range(-6, 7):                                     # 焦外点光源：12 m 远、半径 15 mm
    bpy.ops.mesh.primitive_uv_sphere_add(radius=15, location=(i * 900, 12000, 600 + 250 * (i % 3)))
    bpy.context.active_object.data.materials.append(lamp)

def render(spp, seed, dispersion, adaptive):
    cam.data.blenderlens.dispersion = dispersion
    rig.apply_settings(cam)
    c = sc.cycles
    c.samples = spp; c.seed = seed; c.use_denoising = False; c.use_adaptive_sampling = adaptive
    sc.view_settings.view_transform = 'Standard'
    sc.render.image_settings.file_format = 'OPEN_EXR'
    path = os.path.join(OUT, "conv_tmp.exr"); sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(path, check_existing=False)
    H, W = sc.render.resolution_y, sc.render.resolution_x
    a = np.array(im.pixels[:], dtype=np.float64).reshape(H, W, 4)[:, :, :3]
    bpy.data.images.remove(im)
    return a

def noise(a, b):
    return dict(raw=float(np.sqrt(((a - b) ** 2).mean() / 2)),
                compressed=float(np.sqrt(((a / (1 + a) - b / (1 + b)) ** 2).mean() / 2)))

rig.render_preset(sc)
rep = {}
for dispersion in (True, False):
    for adaptive in (True, False):
        r = {}
        for spp in (64, 256):
            r[spp] = noise(render(spp, 1, dispersion, adaptive), render(spp, 2, dispersion, adaptive))
        key = f"disp={'on' if dispersion else 'off'},adaptive={'on' if adaptive else 'off'}"
        rep[key] = {k: round(r[64][k] / r[256][k], 3) for k in ("raw", "compressed")}
        print("CONV_ROW", key, json.dumps(rep[key]), flush=True)
print("CONV", json.dumps(rep))
