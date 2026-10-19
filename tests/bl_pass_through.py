# 矢量与深度穿过透射面（0.8.5，MessyRoom 0.8.5 R1）：便利店橱窗
#   场景：店内（有纹理的墙、货架、几个盒子）前面一块玻璃（Principled，Transmission 1、Roughness 0.02、IOR 1.5），
#   相机横移（玻璃近、店内远 → 视差大）。同一组帧渲三种：
#     A 折射玻璃 + 0.8.5 穿透（默认）；B MessyRoom 的绕法（Transparent + Glossy（Fresnel）+ pass_alpha_threshold 0.5）；
#     C 折射玻璃、关掉穿透（bl_pass_through = False，= 0.8.4 的行为）。
#   验收：A 的运动补偿残差 ≤ B 的 1.1 倍；玻璃后的区域 A < 0.8 C；不透明像素（离玻璃 3 px 以外）的 BL_Vector 与 C 逐像素一致；
#   BL_VectorSurface / BL_DepthSurface 存在，Depth 在玻璃处是店内的距离；玻璃处置信度按反射占比降。
# 运行：Blender -b --factory-startup --python tests/bl_pass_through.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS, motion as MO
OUT = os.path.join(ROOT, "renders", "pass_through")
os.makedirs(OUT, exist_ok=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.4, 0.45, 0.55, 1)

def mat(name, scale=None, c1=(0.8, 0.8, 0.8), c2=(0.1, 0.1, 0.1), emit=0.0):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; b = nt.nodes.get("Principled BSDF")
    if scale:
        ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = scale
        ch.inputs["Color1"].default_value = (*c1, 1); ch.inputs["Color2"].default_value = (*c2, 1)
        nt.links.new(ch.outputs[0], b.inputs["Base Color"])
    else:
        b.inputs["Base Color"].default_value = (*c1, 1)
    if emit:
        b.inputs["Emission Color"].default_value = (1, 0.95, 0.85, 1); b.inputs["Emission Strength"].default_value = emit
    return m

def glass_refr():
    m = bpy.data.materials.new("glass_refr")
    if m.node_tree is None:
        m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Transmission Weight"].default_value = 1.0; b.inputs["Roughness"].default_value = 0.02; b.inputs["IOR"].default_value = 1.5
    return m

def glass_mix():                                          # MessyRoom 的绕法
    m = bpy.data.materials.new("glass_mix")
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial"); mx = nt.nodes.new("ShaderNodeMixShader")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent"); gl = nt.nodes.new("ShaderNodeBsdfGlossy"); fr = nt.nodes.new("ShaderNodeFresnel")
    gl.inputs["Roughness"].default_value = 0.02; fr.inputs["IOR"].default_value = 1.52
    nt.links.new(fr.outputs[0], mx.inputs[0]); nt.links.new(tr.outputs[0], mx.inputs[1]); nt.links.new(gl.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], o.inputs[0])
    return m

# 店内（Y 向前）：后墙、地面、货架（盒子）
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 9, 2), rotation=(math.radians(90), 0, 0)); bpy.context.object.data.materials.append(mat("back", 6.0, (0.8, 0.6, 0.3), (0.15, 0.2, 0.35)))
bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 5, 0)); bpy.context.object.data.materials.append(mat("floor", 12.0))
for i, x in enumerate((-2.0, -0.6, 0.8, 2.2)):
    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(x, 6.0 + 0.4 * i, 0.4 + 0.3 * (i % 2))); bpy.context.object.data.materials.append(mat(f"box{i}", 3.0, (0.9, 0.9, 0.2), (0.6, 0.1, 0.1)))
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.15, location=(0.0, 7.5, 2.4)); bpy.context.object.data.materials.append(mat("lamp", emit=30.0))
bpy.ops.object.light_add(type='AREA', location=(0, 6, 3.5)); L = bpy.context.object; L.data.energy = 600; L.data.size = 3
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(50), 0, math.radians(20))); bpy.context.object.data.energy = 2.0
# 橱窗框（不透明，测试「不在玻璃后」的像素）+ 玻璃
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 2.0, 1.4)); frame = bpy.context.object; frame.scale = (6.0, 0.08, 3.2)
frame.data.materials.append(mat("frame", c1=(0.15, 0.15, 0.17)))
bool_ = frame.modifiers.new("cut", 'BOOLEAN')
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 2.0, 1.4)); hole = bpy.context.object; hole.scale = (4.0, 0.5, 2.2); hole.hide_render = True; hole.hide_viewport = True
bool_.object = hole; bool_.operation = 'DIFFERENCE'
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 2.0, 1.4), rotation=(math.radians(90), 0, 0)); glass = bpy.context.object; glass.name = "Window"
glass.scale = (4.0, 2.2, 1)
cam = bl.build("c_series_style.json", sc, gate="16:9_NATIVE", units="SCENE", height=180)
cam.rotation_mode = 'XYZ'
for f in (1, 8):                                          # 横移 0.06 m/帧
    cam.location = (-0.25 + 0.06 * (f - 1), -9.0, 1.4); cam.rotation_euler = (math.radians(90), 0, 0)
    cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
for fc in bl._action_fcurves(cam.animation_data.action):
    for kp in fc.keyframe_points:
        kp.interpolation = 'LINEAR'
bl.configure(cam, focus_m=6.0, f_number=5.6, dispersion=False)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 180)
sc.cycles.samples = 48; sc.cycles.use_denoising = True; sc.cycles.seed = 4; sc.cycles.use_animated_seed = False
sc.view_settings.view_transform = 'Standard'
vl = sc.view_layers[0]
FR = list(range(2, 8))

def run(label, material, pass_flag=None, alpha_thr=None):
    glass.data.materials.clear(); glass.data.materials.append(material)
    if pass_flag is None:
        glass.pop(rig.PASS_THROUGH_PROP, None)
    else:
        glass[rig.PASS_THROUGH_PROP] = pass_flag
    old_thr = vl.pass_alpha_threshold
    if alpha_thr is not None:
        vl.pass_alpha_threshold = alpha_thr
    d = os.path.join(OUT, label)
    try:
        bl.render_frames(sc, frames=FR, out_dir=d, name="f", passes="temporal", seed=4, animated_seed=False, skip_existing=False)
    finally:
        vl.pass_alpha_threshold = old_thr
    P = {t: PS.read(os.path.join(d, f"f_{t:04d}.exr")) for t in FR}
    return P

res = {"A": run("A_refr_through", glass_refr()), "B": run("B_mix_alpha", glass_mix(), alpha_thr=0.5),
       "C": run("C_refr_off", glass_refr(), pass_flag=False)}
rep = {}
def rgb(P, n): return PS.rgb(PS.find(P, n))
def vec(P, n="BL_Vector"): return np.stack([PS.find(P, n)["channels"][c] for c in "XYZW"], -1)
Pa = res["A"][FR[2]]
hdr = json.loads(PS.find(Pa, "Combined")["header"].get("blenderlens/pass_through", "{}"))
rep["pass_through_header"] = dict(objects=hdr.get("objects"), pixels=hdr.get("pixels"))
H, W = PS.rgb(PS.find(Pa, "Combined")).shape[:2]
# 玻璃后的区域：A 的 Depth 与 BL_DepthSurface 不同的像素
dA, dS = PS.find(Pa, "Depth")["channels"]["Z"], PS.find(Pa, "BL_DepthSurface")["channels"]["Z"]
behind = np.abs(dA - dS) > 0.3
rep["behind_frac"] = round(float(behind.mean()), 3)
rep["depth_through_m"] = round(float(np.median(dA[behind])), 2); rep["depth_surface_m"] = round(float(np.median(dS[behind])), 2)
inner = np.zeros((H, W), bool); inner[8:-8, 8:-8] = True
def resid(label, region):
    P = res[label]
    T = {t: MO.tone(rgb(P[t], "BL_Final")) for t in FR}
    rs = [np.abs(T[t] - MO.warp_prev(T[t - 1], vec(P[t])))[region] for t in FR[1:]]
    return float(np.mean([r.mean() for r in rs]))
def naive(label, region):
    P = res[label]; T = {t: MO.tone(rgb(P[t], "BL_Final")) for t in FR}
    return float(np.mean([np.abs(T[t] - T[t - 1])[region].mean() for t in FR[1:]]))
reg = inner & behind
rep["resid_behind"] = {k: round(resid(k, reg), 5) for k in "ABC"}
rep["resid_whole"] = {k: round(resid(k, inner), 5) for k in "ABC"}
rep["naive_behind_A"] = round(naive("A", reg), 5)
rep["A_over_B"] = round(rep["resid_whole"]["A"] / rep["resid_whole"]["B"], 3)
rep["A_over_C_behind"] = round(rep["resid_behind"]["A"] / rep["resid_behind"]["C"], 3)
near = behind.copy()                                      # 玻璃边缘的混合像素外扩 3 px 排除
for _ in range(3):
    near = near | np.roll(near, 1, 0) | np.roll(near, -1, 0) | np.roll(near, 1, 1) | np.roll(near, -1, 1)
opq = inner & ~near
dv = np.abs(vec(Pa) - vec(res["C"][FR[2]])).max(-1)[opq]
rep["opaque_vec_diff_px"] = dict(median=round(float(np.median(dv)), 5), p99=round(float(np.percentile(dv, 99)), 4), max=round(float(dv.max()), 3))
rep["surface_layers"] = [n for n in ("BL_VectorSurface", "BL_DepthSurface") if PS.find(Pa, n) is not None]
conf = PS.find(Pa, "BL_VectorConfidence")["channels"]["X"]
cs = json.loads(PS.find(Pa, "Combined")["header"]["blenderlens/vector_confidence"])
rep["conf_behind_mean"] = round(float(conf[reg].mean()), 3); rep["glass_term_mean"] = cs["terms"].get("glass")
rep["PASS"] = bool(rep["pass_through_header"]["objects"] == ["Window"] and rep["behind_frac"] > 0.2
                   and rep["A_over_B"] <= 1.1 and rep["A_over_C_behind"] < 0.8 and rep["opaque_vec_diff_px"]["max"] == 0.0
                   and rep["surface_layers"] == ["BL_VectorSurface", "BL_DepthSurface"] and rep["depth_through_m"] > rep["depth_surface_m"] + 1.0
                   and rep["glass_term_mean"] is not None and 0.5 < rep["glass_term_mean"] < 0.999)
print("PASSTHRU", json.dumps(rep))
