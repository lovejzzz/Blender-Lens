# 炫光在视频里的时间稳定性（0.8.4，MessyRoom R6）：运动镜头 + 小光源（霓虹、路灯）
#   1. 炫光层本身（正向追迹鬼影 + 衍射星芒）：相机极慢漂移（0.02°/帧，亚像素——最容易看出闪）与中速横摇（0.2°/帧），
#      总能量与亮度质心的二阶差分（平滑运动下应接近 0；跳 = 闪）；鬼影、星芒分开测；
#      0.8.3 的星芒在这里会跳 ±25%（按像素中心点采样 PSF、主斑硬挖），0.8.4 改成按像素面积积分；
#   2. 端到端：temporal_check(mode="moving") 开 / 关炫光，给出 flare_stability 与闪烁百分比。
# 运行：Blender -b --factory-startup --python tests/bl_flare_stability.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, temporal as TP
OUT = os.path.join(ROOT, "renders", "flare_stability")
os.makedirs(OUT, exist_ok=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.01, 0.01, 0.015, 1)
cam = bl.build("c_series_style.json", scene=sc, height=360, units='MM')

def emit(name, s, col):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial"); e = nt.nodes.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*col, 1); e.inputs[1].default_value = s; nt.links.new(e.outputs[0], o.inputs[0])
    return m

for (x, y, z, col) in ((2500, 900, -15000, (1, 0.3, 0.8)), (-1800, 300, -9000, (1, 0.7, 0.3))):      # 霓虹、路灯
    bpy.ops.mesh.primitive_uv_sphere_add(radius=30, location=(x, y, z)); ob = bpy.context.object
    ob.data.materials.append(emit("n", 4e4, col)); ob[rig.FLARE_PROP] = 1
bpy.ops.mesh.primitive_plane_add(size=60000, location=(0, -1200, -20000), rotation=(math.radians(90), 0, 0))
g = bpy.data.materials.new("g")
if g.node_tree is None:
    g.use_nodes = True
g.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.2, 0.2, 0.2, 1)
bpy.context.object.data.materials.append(g)
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(-60), 0, 0)); bpy.context.object.data.energy = 0.5
bl.configure(cam, focus_m=3.0, f_number=2.8, starburst=True, blades=6)
W, H = rig.desqueezed_resolution(rig.read_prescription(), 360)
rep = {}

def sweep(step_deg, n=10, starburst=True, ghosts=True):
    p = cam.data.blenderlens
    p.starburst = starburst
    gain = p.flare_gain
    if not ghosts:
        p.flare_gain = 0.0
    F = []
    try:
        for k in range(n):
            cam.rotation_euler = (math.radians(0.3 * step_deg * k), math.radians(step_deg * k), 0)
            bpy.context.view_layer.update()
            lay, used = rig._flare_layers(sc, cam, W, H, None, 160, 0.8)
            F.append(lay.copy())
    finally:
        p.flare_gain = gain; p.starburst = True
    return TP.flare_stability(F)

for step in (0.02, 0.2):
    rep[f"step_{step}"] = dict(ghosts=sweep(step, starburst=False), starburst=sweep(step, ghosts=False), both=sweep(step))

# 2. 端到端（相机横摇 0.1°/帧，6 帧）
cam.rotation_euler = (0, 0, 0)
sc.frame_start, sc.frame_end = 1, 6
cam.rotation_mode = 'XYZ'
for f in (1, 6):
    cam.rotation_euler = (0, math.radians(0.1 * (f - 1)), 0); cam.keyframe_insert("rotation_euler", frame=f)
rig.render_preset(sc)
sc.cycles.samples = 32; sc.cycles.use_denoising = False; sc.cycles.seed = 2; sc.cycles.use_animated_seed = True
sc.view_settings.view_transform = 'Standard'
r = bl.temporal_check(sc, frame=1, n=6, height=180, mode="moving", out_dir=os.path.join(OUT, "moving"),
                      variants=[{"name": "flare", "flare": True}])
rep["moving"] = dict(base=r["base"]["frame_mean"], flare_pct=r["variants"][0]["pct_of_base"],
                     flare_stability=r["variants"][0].get("flare_stability"))
# 极慢漂移：任何跳动都是闪；中速横摇：光源扫过渐晕 / 画面，二阶差分本来就有（平滑变化），放宽到 2%
lim = {0.02: 1.0, 0.2: 2.0}
rep["PASS"] = bool(all(rep[f"step_{s}"][k]["energy_jitter_pct"] < lim[s] for s in (0.02, 0.2) for k in ("ghosts", "starburst", "both"))
                   and rep["step_0.02"]["both"]["centroid_jitter_px"] < 0.5
                   and rep["moving"]["flare_stability"] is not None and rep["moving"]["flare_stability"]["energy_jitter_pct"] < 2.0)
print("FLARESTAB", json.dumps(rep))
