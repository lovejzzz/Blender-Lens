# 一条命令渲一个镜头（0.8.4，MessyRoom R5）：bl.render_shot
#   1. 跟随：BlenderLens 相机每帧与源相机重合，对焦距离与 f 值也跟着源相机（烘焙成关键帧）；
#   2. exposure="pinhole"：film_exposure 乘 2^(−frame_mean_ev)（0.8.5：渲完恢复原值）；
#   3. 每帧一个多层 EXR + 日志（用时、各步用时、峰值内存、可用内存、文件大小）；
#   4. 续渲：删掉一帧再跑，只渲那一帧，其余记为「已存在」，日志接着写；
#   5. 内存守护：可用内存永远不够时（guard_min_gb 设得很大、不等待）不渲，日志写明原因。
# 运行：Blender -b --factory-startup --python tests/bl_render_shot.py
import os, sys, json, math, shutil
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders", "render_shot")
shutil.rmtree(OUT, ignore_errors=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.3, 0.35, 0.45, 1)
bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, 0))
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 12, 1))
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(40), 0, math.radians(30)))
cd = bpy.data.cameras.new("Src"); src = bpy.data.objects.new("Src", cd); sc.collection.objects.link(src)
cd.dof.use_dof = True
sc.camera = src
for f, (x, fd, fs) in {1: (-1.0, 10.0, 2.8), 2: (-0.5, 11.0, 3.5), 3: (0.0, 12.0, 4.0), 4: (0.5, 13.0, 5.6)}.items():
    src.location = (x, -2.0, 1.6); src.rotation_euler = (math.radians(90), 0, math.radians(-3 * x))
    src.keyframe_insert("location", frame=f); src.keyframe_insert("rotation_euler", frame=f)
    cd.dof.focus_distance = fd; cd.dof.aperture_fstop = fs
    cd.dof.keyframe_insert("focus_distance", frame=f); cd.dof.keyframe_insert("aperture_fstop", frame=f)
sc.frame_start, sc.frame_end = 1, 4
sc.render.resolution_y = 120
sc.cycles.use_denoising = False
rep = {}
log = bl.render_shot(source_camera="Src", frames=range(1, 5), out_dir=OUT, preset="c_series_style", height=120,
                     passes="temporal", spp=8, exposure="pinhole", guard=True, guard_min_gb=0.0)
cam = bl.camera(sc)
sc.frame_set(3); rig.apply_settings(cam)
d = (np.array(cam.matrix_world) - np.array(src.matrix_world))
rep["follow_matrix_err"] = float(np.abs(d).max())
p = cam.data.blenderlens
rep["follow_focus"] = round(p.focus_distance, 3); rep["follow_fstop"] = round(p.f_number, 3)
rep["files"] = sorted(f for f in os.listdir(OUT) if f.endswith(".exr"))
rep["records"] = [dict(frame=r["frame"], rendered=r["rendered"], has_timing=bool(r.get("timing")),
                       max_rss=r.get("max_rss_gb"), size_mb=r.get("size_mb")) for r in log["records"]]
rep["exposure"] = log["exposure"]
sc.frame_set(1); rig.apply_settings(cam)                 # render_shot 按第一帧算（整镜一个常数）
ev = bl.exposure_vs_pinhole(cam)["frame_mean_ev"]
rep["film_exposure_ok"] = (abs(log["exposure"]["factor"] - 2.0 ** (-ev)) < 0.02 * 2.0 ** (-ev)   # 0.8.5：乘到原值上，渲完恢复
                          and abs(sc.cycles.film_exposure - 1.0) < 1e-6)
rep["log_on_disk"] = os.path.exists(os.path.join(OUT, "f_log.json"))
rep["summary"] = log.get("summary")
# 4. 续渲
os.remove(os.path.join(OUT, "f_0002.exr"))
log2 = bl.render_shot(source_camera="Src", frames=range(1, 5), out_dir=OUT, spp=8, passes="temporal", guard=False)
new = log2["records"][len(log["records"]):]
rep["resume"] = [(r["frame"], r["rendered"], r.get("skipped")) for r in new]
rep["resume_kept_old"] = len(log2["records"]) == len(log["records"]) + 4 and "resumed_from" in log2
# 5. 内存守护
os.remove(os.path.join(OUT, "f_0003.exr"))
log3 = bl.render_shot(source_camera="Src", frames=[3], out_dir=OUT, spp=8, passes="temporal", guard=True,
                      guard_min_gb=1e6, guard_wait_s=0)
last = log3["records"][-1]
rep["guard"] = dict(rendered=last["rendered"], skipped=last.get("skipped"), stopped="stopped" in log3,
                    avail=last.get("avail_gb_before"))
rep["guard_file_absent"] = not os.path.exists(os.path.join(OUT, "f_0003.exr"))
rep["PASS"] = bool(rep["follow_matrix_err"] < 1e-4 and abs(rep["follow_fstop"] - 4.0) < 1e-3
                   and abs(rep["follow_focus"] - 12.0) < 1e-3
                   and rep["files"] == ["f_0001.exr", "f_0002.exr", "f_0003.exr", "f_0004.exr"]
                   and all(r["rendered"] and r["has_timing"] for r in rep["records"]) and rep["film_exposure_ok"]
                   and rep["log_on_disk"] and rep["resume"] == [(1, False, "exists"), (2, True, None), (3, False, "exists"),
                                                                 (4, False, "exists")]
                   and rep["resume_kept_old"] and rep["guard"]["rendered"] is False and rep["guard"]["stopped"]
                   and rep["guard"]["avail"] is not None and rep["guard_file_absent"])
print("SHOT", json.dumps(rep, default=str))
