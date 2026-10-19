# BL_Vector 的可信度（0.8.4，MessyRoom R2）：BL_VectorConfidence 层 + 背景（天空）矢量 + 发光体边缘精度
#   场景：相机横摇；带纹理的天空（环境）、地面与墙；一个盒子在墙前横移（遮挡 / 去遮挡）；盒子上一盏亮灯（发光体）；
#   一面镜子（倒影不跟表面走）。frame 2 写多层 EXR（passes="temporal"），frame 1 / 3 用来做真值。
#   1. 天空：无穷远矢量让天空对齐（warp 残差 ≪ 矢量为 0 时）；
#   2. 置信度能预测对齐误差：conf > 0.9 的像素平均误差 ≪ conf < 0.3 的像素；
#   3. 去遮挡条带、镜子、灯的外圈置信度低；盒子正面、地面置信度高；
#   4. 发光体本身的矢量准（灯的质心实测位移 vs 灯中心的 BL_Vector，< 0.35 px）。
# 运行：Blender -b --factory-startup --python tests/bl_vector_conf.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS, motion as MO
OUT = os.path.join(ROOT, "renders", "vector_conf")
os.makedirs(OUT, exist_ok=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
nt = w.node_tree
nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 12.0; nz.inputs["Detail"].default_value = 8.0
crp = nt.nodes.new("ShaderNodeValToRGB"); crp.color_ramp.elements[0].color = (0.05, 0.1, 0.3, 1); crp.color_ramp.elements[1].color = (0.8, 0.8, 0.9, 1)
nt.links.new(nz.outputs["Fac"], crp.inputs[0]); nt.links.new(crp.outputs[0], nt.nodes["Background"].inputs[0])
cam = bl.build("c_series_style.json", scene=sc, height=240, units='MM')

def mat(name, scale=None, c1=(0.8, 0.8, 0.8), c2=(0.1, 0.1, 0.1), metal=0.0, rough=0.5, emit=0.0):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; b = nt.nodes.get("Principled BSDF")
    b.inputs["Metallic"].default_value = metal; b.inputs["Roughness"].default_value = rough
    if scale:
        ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = scale
        ch.inputs["Color1"].default_value = (*c1, 1); ch.inputs["Color2"].default_value = (*c2, 1)
        nt.links.new(ch.outputs[0], b.inputs["Base Color"])
    else:
        b.inputs["Base Color"].default_value = (*c1, 1)
    if emit:
        b.inputs["Emission Color"].default_value = (1.0, 0.85, 0.6, 1); b.inputs["Emission Strength"].default_value = emit
    return m

bpy.ops.mesh.primitive_plane_add(size=60000, location=(0, -900, -20000), rotation=(math.radians(90), 0, 0))   # 地面（Y 向上）
bpy.context.object.data.materials.append(mat("ground", 80.0))
bpy.ops.mesh.primitive_plane_add(size=3000, location=(-900, 600, -9000))                                     # 墙（正对相机）
bpy.context.object.data.materials.append(mat("wall", 7.0, (0.7, 0.4, 0.2), (0.15, 0.2, 0.3)))
bpy.ops.mesh.primitive_plane_add(size=1800, location=(2600, -300, -7000), rotation=(math.radians(90), math.radians(-35), 0))
mirror = bpy.context.object; mirror.name = "Mirror"; mirror.data.materials.append(mat("mirror", c1=(0.95, 0.95, 0.95), metal=1.0, rough=0.0))
bpy.ops.mesh.primitive_cube_add(size=700, location=(-1200, -300, -6500)); box = bpy.context.object; box.name = "Box"
box.data.materials.append(mat("box", 5.0, (0.9, 0.9, 0.2), (0.2, 0.1, 0.05)))
bpy.ops.mesh.primitive_uv_sphere_add(radius=45, location=(-1200, 150, -6100)); lamp = bpy.context.object; lamp.name = "Lamp"
lamp.data.materials.append(mat("lamp", c1=(1, 1, 1), emit=60.0))
lamp.parent = box; lamp.matrix_parent_inverse = box.matrix_world.inverted()
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(-50), math.radians(25), 0)); bpy.context.object.data.energy = 3.0
sc.frame_start, sc.frame_end = 1, 3
cam.rotation_mode = 'XYZ'
for f in (1, 2, 3):
    box.location = (-1200 + 80 * (f - 2), -300, -6500); box.keyframe_insert("location", frame=f)
    cam.rotation_euler = (0, math.radians(0.12 * (f - 2)), 0); cam.keyframe_insert("rotation_euler", frame=f)
bl.configure(cam, focus_m=6.5, f_number=5.6, dispersion=False)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 240)
W, H = sc.render.resolution_x, sc.render.resolution_y
sc.cycles.samples = 96; sc.cycles.use_denoising = True; sc.cycles.seed = 7; sc.cycles.use_animated_seed = False
sc.view_settings.view_transform = 'Standard'
vl = sc.view_layers[0]
rep = {}
imgs = {}
for f in (1, 2, 3):
    sc.frame_set(f)
    path = os.path.join(OUT, f"f{f}.exr")
    bl.render_still(path, flare=False, passes="temporal")
    imgs[f] = PS.read(path)
P2 = imgs[2]
names = [p["pass_"] for p in P2]
rep["has_conf"] = "BL_VectorConfidence" in names
vec = np.stack([PS.find(P2, "BL_Vector")["channels"][c] for c in "XYZW"], -1).astype(np.float32)
conf = PS.find(P2, "BL_VectorConfidence")["channels"]["X"].astype(np.float32)
comb = {f: PS.rgb(PS.find(imgs[f], "Combined")) for f in (1, 2, 3)}
hdr = PS.find(P2, "Combined")["header"]
rep["conf_stats"] = json.loads(hdr["blenderlens/vector_confidence"])
t1, t2 = MO.tone(comb[1]), MO.tone(comb[2])
E = np.abs(t2 - MO.warp_prev(t1, vec))                   # 真值：用矢量对齐后的帧间差（同一种子，没有采样噪点变化）
E0 = np.abs(t2 - t1)
b = 6
inner = np.zeros((H, W), bool); inner[b:-b, b:-b] = True
# 物体区域：用 Cryptomatte 读不到（temporal 不留），改用简单判据：天空 = Depth 为 0 或很大
dep = PS.find(P2, "Depth")["channels"]["Z"]
sky = inner & ((dep <= 0) | (dep > 1e5))
rep["sky_frac"] = round(float(sky.mean()), 3)
rep["sky_err_with_vec"] = round(float(E[sky].mean()), 5)
rep["sky_err_no_vec"] = round(float(E0[sky].mean()), 5)
hi = inner & (conf > 0.9); lo = inner & (conf < 0.3)
rep["frac_hi"] = round(float(hi.mean()), 3); rep["frac_lo"] = round(float(lo.mean()), 3)
rep["err_hi"] = round(float(E[hi].mean()), 5); rep["err_lo"] = round(float(E[lo].mean()), 5)
rep["err_ratio_lo_hi"] = round(rep["err_lo"] / max(rep["err_hi"], 1e-9), 2)
# 误差最大的 1% 像素里，置信度低的占比
top = inner & (E >= np.percentile(E[inner], 99))
rep["top1pct_err_conf_mean"] = round(float(conf[top].mean()), 3)
# 区域：镜子（Roughness 低、Specular Albedo 高）
sa = PS.rgb(PS.find(P2, "Denoising Specular Albedo")).mean(-1); ro = PS.find(P2, "Denoising Roughness")["channels"]["X"]
mir = inner & (sa > 0.5) & (ro < 0.05)
rep["mirror_frac"] = round(float(mir.mean()), 4)
rep["mirror_conf"] = round(float(conf[mir].mean()), 3) if mir.any() else None
# 灯：亮度 > 1.5；质心位移实测 vs 中心矢量
def centroid(img):
    L = img.mean(-1); m = L > 1.5
    ys, xs = np.nonzero(m); wv = L[m]
    return float((xs * wv).sum() / wv.sum()), float((ys * wv).sum() / wv.sum()), m
c1, c2, c3 = centroid(comb[1]), centroid(comb[2]), centroid(comb[3])
hot = c2[2]
row, col = int(round(c2[1])), int(round(c2[0]))
v = [float(np.median(vec[row - 1:row + 2, col - 1:col + 2, k])) for k in range(4)]
meas = [c1[0] - c2[0], -(c1[1] - c2[1]), c2[0] - c3[0], -(c2[1] - c3[1])]      # 行向下 → y 向上
rep["lamp_vec"] = [round(x, 3) for x in v]; rep["lamp_meas"] = [round(x, 3) for x in meas]
rep["lamp_err_px"] = round(max(abs(a - b_) for a, b_ in zip(v, meas)), 3)
ring = np.zeros_like(hot)
for dy in range(-4, 5):
    for dx in range(-4, 5):
        ring |= np.roll(np.roll(hot, dy, 0), dx, 1)
ring &= ~hot & inner
rep["lamp_ring_conf"] = round(float(conf[ring].mean()), 3) if ring.any() else None
rep["PASS"] = bool(rep["has_conf"] and rep["sky_frac"] > 0.05 and rep["sky_err_with_vec"] < 0.3 * rep["sky_err_no_vec"]
                   and rep["err_ratio_lo_hi"] > 4 and rep["top1pct_err_conf_mean"] < 0.5
                   and rep["mirror_conf"] is not None and rep["mirror_conf"] < 0.3 and rep["lamp_err_px"] < 0.35
                   and rep["lamp_ring_conf"] is not None and rep["lamp_ring_conf"] < 0.7 and rep["frac_hi"] > 0.4)
print("VCONF", json.dumps(rep))
