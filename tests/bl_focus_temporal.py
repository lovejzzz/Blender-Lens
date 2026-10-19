# 对焦助手与闪烁检查（0.8.3，MessyRoom R2 / R3）
#   R2：focus_on(物体 / 画面坐标) 给出正确物距；stop_for(19, 38) 合理；对焦在 10 m 而主体在 19 m 时 diagnose 报 warning；
#       focus_pull 从 A 拉到 B 的关键帧单调、首尾对。
#   R3：temporal_check 测得到逐帧种子造成的闪烁；提高采样、隐藏体积都会降低闪烁；相机与对焦在测试后恢复。
# 运行：Blender -b --factory-startup --python tests/bl_focus_temporal.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "focus_temporal")
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
cam = bl.build("c_series_style.json", scene=sc, height=135, units='SCENE')   # 米制场景
sc.unit_settings.scale_length = 1.0
cam.location = (0, 0, 1.6); cam.rotation_euler = (math.radians(90), 0, 0)    # 看 +Y
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 19.0, 1.6)); room = bpy.context.object; room.name = "Room"
bpy.ops.mesh.primitive_cube_add(size=3.0, location=(4.0, 38.0, 3.0)); sign = bpy.context.object; sign.name = "Sign"
rep = {}
# R2
d_room = bl.focus_on(cam, "Room")
d_sign = bl.focus_on(cam, sign, apply=False)
d_center = bl.focus_on(cam, (0.5, 0.5), apply=False)
rep["focus_on"] = dict(room=d_room, sign=d_sign, center_ray=d_center)
rep["stop_for"] = bl.stop_for(cam, 19.0, 38.0, coc_px=1.0)
bl.set_focus(cam, metres=10.0); bl.set_stop(cam, 2.3)
keep_res = (sc.render.resolution_x, sc.render.resolution_y)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 2160)   # 4K 输出下判断清晰
cam["bl_subject"] = "Room"
diag = [i for i in bl.diagnose() if i["code"] == "subject_out_of_focus"]
rep["diagnose"] = diag[0]["message"] if diag else None
rep["dof_10m"] = bl.dof_range(cam)
sc.render.resolution_x, sc.render.resolution_y = keep_res
sc.frame_start, sc.frame_end = 1, 10
pull = bl.focus_pull(cam, "Room", "Sign", 1, 10)
rep["pull"] = pull
mono = all(pull[k][1] <= pull[k + 1][1] + 1e-9 for k in range(len(pull) - 1))
# R3：低采样、逐帧变种子 → 闪烁；体积雾 "haze"
del cam["bl_subject"]
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[1].default_value = 0.02
bpy.ops.object.light_add(type='POINT', location=(1.0, 12.0, 3.0)); lamp = bpy.context.object; lamp.data.energy = 3000
bpy.ops.mesh.primitive_cube_add(size=30.0, location=(0, 18, 3)); haze = bpy.context.object; haze.name = "haze"
vm = bpy.data.materials.new("vol")
if vm.node_tree is None:
    vm.use_nodes = True
nt = vm.node_tree; nt.nodes.clear()
o_ = nt.nodes.new("ShaderNodeOutputMaterial"); v_ = nt.nodes.new("ShaderNodeVolumePrincipled")
v_.inputs["Density"].default_value = 0.05; nt.links.new(v_.outputs[0], o_.inputs["Volume"])
haze.data.materials.append(vm)
bl.set_focus(cam, metres=19.0)
sc.cycles.samples = 16; sc.cycles.use_animated_seed = True; sc.cycles.use_denoising = False
mw0 = cam.matrix_world.copy(); f0 = cam.data.blenderlens.focus_distance
t = bl.temporal_check(sc, frame=3, n=4, height=135, variants=[{"spp": 128}, {"hide": ["haze"]}], out_dir=OUT)
rep["temporal"] = t
rep["restored"] = bool(np.allclose(np.array(cam.matrix_world), np.array(mw0), atol=1e-6))
pct = {v["name"]: v["pct_of_base"]["frame_mean"] for v in t["variants"]}
rep["PASS"] = bool(abs(d_room - 19.0) < 0.05 and abs(d_sign - 38.0) < 0.05 and d_center is not None and abs(d_center - 18.0) < 0.1
                   and rep["restored"] and rep["stop_for"]["feasible"] and 2.0 <= rep["stop_for"]["f_number"] <= 22 and 20 < rep["stop_for"]["focus_m"] < 30
                   and rep["diagnose"] is not None and mono and abs(pull[0][1] - 19.0) < 0.05 and abs(pull[-1][1] - 38.0) < 0.05
                   and t["base"]["frame_mean"] > 0 and pct["v1"] < 70 and pct["v2"] < 100 and os.path.exists(t["base"]["heatmap"]))
print("FOCUSTEMP", json.dumps(rep, ensure_ascii=False, default=str))
