# 一个 .blend 多支镜头（0.8.5，MessyRoom 0.8.5 R3）：bl.render_sequence
#   场景里两个源相机、时间线标记切相机（S1：第 1–3 帧，S2：第 4–6 帧），两个镜头焦距不同（21 / 40 mm）。
#   1. 同一进程（GPU）一条命令渲完两个镜头；镜头清单可由时间线标记得到；
#   2. 每帧与「每个镜头单独开一个 Blender 进程渲」逐像素一致（GPU 内核随镜头改写后重新编译）。
#      GPU 渲染本身不逐位确定（Metal 原子累加的顺序每次不同）：同一帧两次渲染最大差到 3e-4、平均 < 1e-6 → 按 max < 2e-3、mean < 1e-5 判；
#   3. 日志按镜头分段（sequence_log.json + 每个镜头自己的 f_log.json）；
#   4. 搭镜头不改场景自己的渲染设置（设备、弹射数……）；两个镜头的焦距确实不同、都在 GPU 上渲。
# 运行：Blender -b --factory-startup --python tests/bl_render_sequence.py
import os, sys, json, math, shutil, subprocess
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS
OUT = os.path.join(ROOT, "renders", "render_sequence")
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
GPU = bl.enable_gpu(use_gpu=True)[0]
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
sc.world = bpy.data.worlds.new("W")
if sc.world.node_tree is None:
    sc.world.use_nodes = True
sc.world.node_tree.nodes["Background"].inputs[0].default_value = (0.3, 0.35, 0.45, 1)
m = bpy.data.materials.new("chk")
if m.node_tree is None:
    m.use_nodes = True
nt = m.node_tree; b = nt.nodes.get("Principled BSDF")
ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = 30.0; nt.links.new(ch.outputs[0], b.inputs["Base Color"])
bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 15, 0)); bpy.context.object.data.materials.append(m)
for x in (-2, 0, 2):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, 8, 0.5)); bpy.context.object.data.materials.append(m)
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(50), 0, math.radians(30))); bpy.context.object.data.energy = 3
cams = {}
for name, x0 in (("S1_EXT", -1.0), ("S2_AISLE", 1.0)):
    cd = bpy.data.cameras.new(name); ob = bpy.data.objects.new(name, cd); sc.collection.objects.link(ob)
    cd.dof.use_dof = True; cd.dof.focus_distance = 8.0; cd.dof.aperture_fstop = 4.0
    for f in (1, 6):
        ob.location = (x0 + 0.05 * f, -1.0, 1.4); ob.rotation_euler = (math.radians(88), 0, math.radians(2 * x0)); ob.keyframe_insert("location", frame=f)
    ob.keyframe_insert("rotation_euler", frame=1)
    cams[name] = ob
m1 = sc.timeline_markers.new("S1", frame=1); m1.camera = cams["S1_EXT"]
m2 = sc.timeline_markers.new("S2", frame=4); m2.camera = cams["S2_AISLE"]
sc.frame_start, sc.frame_end = 1, 6
sc.render.engine = 'CYCLES'; sc.cycles.samples = 8; sc.cycles.use_denoising = False
sc.cycles.device = 'GPU' if GPU else 'CPU'
sc.view_settings.view_transform = 'Standard'
sc.render.resolution_y = 120
sc.cycles.max_bounces = 7; sc.cycles.diffuse_bounces = 2           # 场景自己的设置：搭镜头后要保留
blend = os.path.join(OUT, "seq.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
rep = dict(gpu=GPU)
shots = bl.shots_from_markers(sc)
rep["from_markers"] = shots
FOCAL = {"S1": 21.0, "S2": 40.0}
for sh in shots:
    sh["focal_mm"] = FOCAL[sh["name"]]
COMMON = dict(preset="c_series_style", passes=["Combined"], spp=8, guard=False, height=120, gate="16:9_NATIVE", units="SCENE",
              flare=False, settings={"dispersion": False})
seq = bl.render_sequence(shots, out_dir=os.path.join(OUT, "seq"), **COMMON)
rep["sequence_shots"] = [dict(name=s["name"], frames=s["frames"], rendered=(s["summary"] or {}).get("rendered")) for s in seq["shots"]]
rep["sequence_log"] = os.path.exists(os.path.join(OUT, "seq", "sequence_log.json"))
# 每个镜头单独一个进程
BLENDER = bpy.app.binary_path
for sh in shots:
    code = (f"import sys; sys.path.insert(0, {ROOT!r}); import blenderlens; blenderlens.register(); import blenderlens.api as bl\n"
            f"bl.enable_gpu(use_gpu=True) if {GPU!r} else None\n"
            f"bl.render_shot(source_camera={sh['source']!r}, frames=range({sh['frames'][0]}, {sh['frames'][1] + 1}), "
            f"out_dir={os.path.join(OUT, 'sep', sh['name'])!r}, focal_mm={sh['focal_mm']}, **{COMMON!r})\n")
    r = subprocess.run([BLENDER, "-b", blend, "--python-expr", code], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
diffs = {}
for sh in shots:
    for f in range(sh["frames"][0], sh["frames"][1] + 1):
        a = PS.rgb(PS.find(PS.read(os.path.join(OUT, "seq", sh["name"], f"f_{f:04d}.exr")), "BL_Final"))
        b_ = PS.rgb(PS.find(PS.read(os.path.join(OUT, "sep", sh["name"], f"f_{f:04d}.exr")), "BL_Final"))
        diffs[f] = (float(np.abs(a - b_).max()), float(np.abs(a - b_).mean()))
rep["seq_vs_separate_maxdiff"] = diffs
w = {s["name"]: PS.rgb(PS.find(PS.read(os.path.join(OUT, "seq", s["name"], f"f_{s['frames'][0]:04d}.exr")), "BL_Final")).shape[1] for s in shots}
rep["widths"] = w
logs = {s["name"]: json.load(open(os.path.join(OUT, "seq", s["name"], "f_log.json"))) for s in shots}
rep["devices"] = {k: v["state"]["render"]["device"] for k, v in logs.items()}
rep["efl_y"] = {k: round(v["state"]["lens"]["efl_y"], 2) for k, v in logs.items()}
rep["kept_bounces"] = [sc.cycles.max_bounces, sc.cycles.diffuse_bounces]
rep["PASS"] = bool(len(shots) == 2 and shots[0]["frames"] == (1, 3) and shots[1]["frames"] == (4, 6)
                   and [s["rendered"] for s in rep["sequence_shots"]] == [3, 3] and rep["sequence_log"]
                   and max(d[0] for d in diffs.values()) < 2e-3 and max(d[1] for d in diffs.values()) < 1e-5 and len(diffs) == 6 and rep["kept_bounces"] == [7, 2]
                   and abs(rep["efl_y"]["S1"] - 21) < 0.2 and abs(rep["efl_y"]["S2"] - 40) < 0.2
                   and all(d == ("GPU" if GPU else "CPU") for d in rep["devices"].values()))
print("SEQUENCE", json.dumps(rep, default=str))
