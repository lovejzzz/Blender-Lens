# 湿地面上光源倒影的运动矢量（0.8.5，MessyRoom 0.8.5 R6）
#   场景：湿街面（平面，粗糙度由噪声控制在 0.04–0.4，自动识别为反射体）、发光招牌和路灯、楼面；相机贴地缓慢横移。
#   BL_VectorReflection（虚像点投影）+ BL_ReflectionShare（光泽层 / Combined）。
#   验收（MessyRoom）：倒影为主的区域（占比 > 0.5），用倒影矢量的运动补偿残差 < 不补偿时的 50%；
#   另报告只用地面矢量（BL_Vector）时的残差作对照。
# 运行：Blender -b --factory-startup --python tests/bl_reflection.py
import os, sys, json, math, shutil
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS, motion as MO
OUT = os.path.join(ROOT, "renders", "reflection")
shutil.rmtree(OUT, ignore_errors=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
sc.world = bpy.data.worlds.new("W")
if sc.world.node_tree is None:
    sc.world.use_nodes = True
sc.world.node_tree.nodes["Background"].inputs[0].default_value = (0.01, 0.012, 0.02, 1)

def emit(name, col, s):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (0.05, 0.05, 0.05, 1)
    b.inputs["Emission Color"].default_value = (*col, 1); b.inputs["Emission Strength"].default_value = s
    return m

wet = bpy.data.materials.new("wet")
if wet.node_tree is None:
    wet.use_nodes = True
nt = wet.node_tree; b = nt.nodes.get("Principled BSDF")
b.inputs["Base Color"].default_value = (0.03, 0.03, 0.035, 1)
nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 3.0
mr = nt.nodes.new("ShaderNodeMapRange"); mr.inputs["To Min"].default_value = 0.04; mr.inputs["To Max"].default_value = 0.4
nt.links.new(nz.outputs["Fac"], mr.inputs[0]); nt.links.new(mr.outputs[0], b.inputs["Roughness"])
bpy.ops.mesh.primitive_plane_add(size=80, location=(0, 30, 0)); street = bpy.context.object; street.name = "Street"
street.data.materials.append(wet)
wall = bpy.data.materials.new("wall")
if wall.node_tree is None:
    wall.use_nodes = True
nt = wall.node_tree; bw = nt.nodes.get("Principled BSDF")
ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = 8.0; nt.links.new(ch.outputs[0], bw.inputs["Base Color"])
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 22, 4)); bld = bpy.context.object; bld.scale = (30, 1, 8); bld.data.materials.append(wall)
for i, (x, z, col) in enumerate(((-4, 3.0, (1, 0.2, 0.5)), (-1, 4.2, (0.2, 0.6, 1)), (2.5, 2.6, (1, 0.8, 0.2)), (5.5, 3.6, (0.3, 1, 0.4)))):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, 20.5, z)); sg = bpy.context.object; sg.scale = (1.6, 0.1, 0.5)
    sg.data.materials.append(emit(f"sign{i}", col, 25.0))
for i, x in enumerate((-6, 0.5, 7)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.25, location=(x, 12, 4.5)); bpy.context.object.data.materials.append(emit(f"lamp{i}", (1, 0.85, 0.6), 60.0))
    bpy.ops.mesh.primitive_cylinder_add(radius=0.06, depth=4.5, location=(x, 12, 2.25))
bpy.ops.object.light_add(type='AREA', location=(0, 10, 6)); L = bpy.context.object; L.data.energy = 800; L.data.size = 6
cam = bl.build("c_series_style.json", sc, gate="16:9_NATIVE", units="SCENE", height=180)
cam.rotation_mode = 'XYZ'
for f in (1, 7):                                          # 贴地缓慢横移
    cam.location = (-0.3 + 0.05 * (f - 1), -2.0, 0.9); cam.rotation_euler = (math.radians(88), 0, 0)
    cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
for fc in bl._action_fcurves(cam.animation_data.action):
    for kp in fc.keyframe_points:
        kp.interpolation = 'LINEAR'
bl.configure(cam, focus_m=12.0, f_number=5.6, dispersion=False)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 180)
sc.cycles.samples = 64; sc.cycles.use_denoising = True
sc.view_settings.view_transform = 'Standard'
rep = dict(reflectors=[r[0].name for r in rig.reflector_objects(sc)])
FR = list(range(1, 8))
bl.render_frames(sc, frames=FR, out_dir=OUT, name="f", passes="temporal", seed=6, animated_seed=False)
P = {t: PS.read(os.path.join(OUT, f"f_{t:04d}.exr")) for t in FR}
v4 = lambda Pt, n: np.stack([PS.find(Pt, n)["channels"][c] for c in "XYZW"], -1)
share = {t: PS.find(P[t], "BL_ReflectionShare")["channels"]["X"] for t in FR}
rep["reflection_header"] = json.loads(PS.find(P[4], "Combined")["header"]["blenderlens/reflection"])
T = {t: MO.tone(PS.rgb(PS.find(P[t], "BL_Final"))) for t in FR}
H, W = T[1].shape
inner = np.zeros((H, W), bool); inner[8:-8, 8:-8] = True
res = {"reflection": [], "surface": [], "none": []}
for t in FR[1:]:
    reg = inner & (share[t] > 0.5) & (T[t] > np.percentile(T[t][inner], 50))      # 倒影为主、且较亮（倒影里的灯和招牌）
    eff = MO.effective_vectors(v4(P[t], "BL_Vector"), v4(P[t], "BL_VectorReflection"), share[t])
    res["reflection"].append(float(np.abs(T[t] - MO.warp_prev(T[t - 1], eff))[reg].mean()))
    res["surface"].append(float(np.abs(T[t] - MO.warp_prev(T[t - 1], v4(P[t], "BL_Vector")))[reg].mean()))
    res["none"].append(float(np.abs(T[t] - T[t - 1])[reg].mean()))
    rep.setdefault("region_px", []).append(int(reg.sum()))
rep["resid"] = {k: round(float(np.mean(v)), 6) for k, v in res.items()}
rep["reflection_over_none"] = round(rep["resid"]["reflection"] / rep["resid"]["none"], 3)
rep["surface_over_none"] = round(rep["resid"]["surface"] / rep["resid"]["none"], 3)
conf = PS.find(P[4], "BL_VectorConfidence")["channels"]["X"]
rep["conf_on_reflection_region"] = round(float(conf[inner & (share[4] > 0.5)].mean()), 3)
rep["PASS"] = bool(rep["reflectors"] == ["Street"] and min(rep["region_px"]) > 200 and rep["reflection_over_none"] < 0.5
                   and rep["resid"]["reflection"] < rep["resid"]["surface"])
print("REFLECT", json.dumps(rep))
