# 一条命令渲一个镜头（0.8.4，MessyRoom R5）：bl.render_shot
#   0.9.2：visibility / pre / post（渲完还原）、qc（孤立亮点质检）、spike_report / temporal_denoise(spike_suppress)、EXR 头的相机输入指纹
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
calls = []
cube = bpy.data.objects["Cube"]
try:                                                      # 0.9.2：名字不存在 → 渲染前 ValueError
    bl.render_shot(source_camera="Src", frames=[1], out_dir=os.path.join(OUT, "bad"), preset="c_series_style", height=60,
                   visibility={"NoSuchObject": {"visible_camera": False}})
    rep["visibility_bad_name_rejected"] = False
except ValueError:
    rep["visibility_bad_name_rejected"] = not os.path.exists(os.path.join(OUT, "bad", "f_0001.exr"))
log = bl.render_shot(source_camera="Src", frames=range(1, 5), out_dir=OUT, preset="c_series_style", height=120,
                     passes="temporal", spp=8, exposure="pinhole", guard=True, guard_min_gb=0.0,
                     visibility={"Cube": {"visible_camera": False}},
                     pre=lambda scene: calls.append(("pre", cube.visible_camera)),
                     post=lambda scene: calls.append(("post", cube.visible_camera)))
rep["visibility"] = dict(calls=calls, restored=cube.visible_camera)
rep["qc"] = dict(total=log.get("spike_report", {}).get("total"), heatmaps=sorted(os.path.basename(v) for v in (log.get("spike_report", {}).get("heatmaps") or {}).values()),
                 error=log.get("spike_report", {}).get("error"))
# 0.9.2：孤立亮点——在第 2 帧注入一个 3×3 的亮点（Combined 与 BL_Final），spike_report 找得到、temporal_denoise(spike_suppress) 压得掉
import shutil as _sh
from blenderlens import passes as PS, spikes as SP
SPK = os.path.join(OUT, "spikes"); _sh.rmtree(SPK, ignore_errors=True); os.makedirs(SPK)
for t in range(1, 5):
    _sh.copy(os.path.join(OUT, f"f_{t:04d}.exr"), os.path.join(SPK, f"f_{t:04d}.exr"))
f2 = os.path.join(SPK, "f_0002.exr"); parts = PS.read(f2)
H_, W_ = PS.find(parts, "Combined")["channels"]["R"].shape
yy_, xx_ = H_ // 2, W_ // 2
for name in ("Combined", "BL_Final"):
    ch = PS.find(parts, name)["channels"]
    for c in "RGB":
        a = ch[c].astype(np.float32).copy(); a[yy_ - 1:yy_ + 2, xx_ - 1:xx_ + 2] = 50.0; ch[c] = a
PS.write(f2, parts)
sr = bl.spike_report(SPK, [2, 3], layers=("Combined", "BL_Final"), conf=0.0)
fr2 = next(r for r in sr["per_frame"] if r["frame"] == 2)
td = bl.temporal_denoise(SPK, os.path.join(SPK, "out"), frames=[2], half=False, spike_suppress=dict(conf=0.0))
td0 = bl.temporal_denoise(SPK, os.path.join(SPK, "out0"), frames=[2], half=False)
fin = PS.rgb(PS.find(PS.read(os.path.join(SPK, "out", "f_0002.exr")), "BL_Final"))
fin0 = PS.rgb(PS.find(PS.read(os.path.join(SPK, "out0", "f_0002.exr")), "BL_Final"))
rep["spikes"] = dict(found=fr2.get("BL_Final", {}).get("count"), found_comb=fr2.get("Combined", {}).get("count"),
                     heatmap=os.path.exists(sr["heatmaps"].get("BL_Final", "")),
                     center_after=round(float(fin[yy_, xx_].max()), 3), center_without=round(float(fin0[yy_, xx_].max()), 3),
                     suppressed=td["per_frame"][0].get("spikes_suppressed"),
                     header=PS.find(PS.read(os.path.join(SPK, "out", "f_0002.exr")), "BL_Final")["header"].get("blenderlens/spikes_suppressed"))
# 0.9.2：相机输入指纹写进 EXR 头，与 bl.verify_inputs 一致
hdr = json.loads(PS.find(PS.read(os.path.join(OUT, "f_0001.exr")), "Combined")["header"]["blenderlens/inputs"])
rep["inputs_header_matches_verify"] = hdr.get("all") == bl.verify_inputs("c_series_style.json", gate="16:9_NATIVE")["inputs"]["all"]
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
# 0.9.4：render_shot 的 aspect / gate_size_mm（交付比例直接改光窗，不事后裁切）
AOUT = os.path.join(OUT, "aspect")
la = bl.render_shot(source_camera="Src", frames=[1], out_dir=AOUT, preset="c_series_style_ff", height=100, aspect=2.37,
                    spp=4, passes="delivery", guard=False, qc=False)
ra = (sc.render.resolution_x, sc.render.resolution_y)
ha = json.loads(PS.find(PS.read(os.path.join(AOUT, "f_0001.exr")), "Combined")["header"]["blenderlens/gate"])
try:
    bl.render_shot(source_camera="Src", frames=[1], out_dir=os.path.join(OUT, "aspect_bad"), aspect=2.37)
    aspect_needs_preset = False
except ValueError:
    aspect_needs_preset = True
rep["aspect"] = dict(log_gate=la.get("gate_size_mm"), resolution=list(ra), res_ratio=round(ra[0] / ra[1], 3), header=ha,
                     needs_preset=aspect_needs_preset)
rep["PASS"] = bool(rep["follow_matrix_err"] < 1e-4 and abs(rep["follow_fstop"] - 4.0) < 1e-3
                   and abs(rep["follow_focus"] - 12.0) < 1e-3
                   and rep["files"] == ["f_0001.exr", "f_0002.exr", "f_0003.exr", "f_0004.exr"]
                   and all(r["rendered"] and r["has_timing"] for r in rep["records"]) and rep["film_exposure_ok"]
                   and rep["log_on_disk"] and rep["resume"] == [(1, False, "exists"), (2, True, None), (3, False, "exists"),
                                                                 (4, False, "exists")]
                   and rep["resume_kept_old"] and rep["guard"]["rendered"] is False and rep["guard"]["stopped"]
                   and rep["guard"]["avail"] is not None and rep["guard_file_absent"]
                   and rep["visibility_bad_name_rejected"] and rep["visibility"]["calls"] == [("pre", False), ("post", False)]
                   and rep["visibility"]["restored"] is True and rep["qc"]["error"] is None and rep["qc"]["total"] is not None
                   and rep["spikes"]["found"] and rep["spikes"]["found"] >= 1 and rep["spikes"]["heatmap"]
                   and rep["spikes"]["center_after"] < 0.5 * rep["spikes"]["center_without"] and rep["spikes"]["suppressed"]
                   and rep["spikes"]["header"] is not None and rep["inputs_header_matches_verify"]
                   and rep["aspect"]["needs_preset"] and abs(rep["aspect"]["header"]["aspect"] - 2.37) < 1e-3
                   and abs(rep["aspect"]["res_ratio"] - 2.37) < 0.02 and rep["aspect"]["log_gate"] is not None
                   and abs(rep["aspect"]["log_gate"][1] - 24.0) < 1e-6)
print("SHOT", json.dumps(rep, default=str))
