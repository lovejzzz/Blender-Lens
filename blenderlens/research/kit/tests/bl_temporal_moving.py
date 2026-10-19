# 运动中的闪烁测量（0.8.4，MessyRoom R1）：temporal_check(mode="moving")
#   1. 运动补偿有效：同一种子（没有采样噪点的帧间变化）+ 相机横摇，运动补偿残差 ≪ 不补偿的帧差（剩下的只是对齐误差）；
#   2. 量得到闪烁：动画种子下，spp ×8 的变体闪烁明显变小（pct_of_base < 60）；
#   3. 速度直方图对：中位速度 = 相邻帧整幅位移（相位相关实测）；
#   4. 输出齐：三项指标 + detail、热力图 PNG、每个速度区间的闪烁、json。
# 运行：Blender -b --factory-startup --python tests/bl_temporal_moving.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS, motion as MO
OUT = os.path.join(ROOT, "renders", "temporal_moving")
os.makedirs(OUT, exist_ok=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.25, 0.3, 0.4, 1)
cam = bl.build("c_series_style.json", scene=sc, height=200, units='MM')

def checker(name, scale, c1, c2):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; b = nt.nodes.get("Principled BSDF")
    ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = scale
    ch.inputs["Color1"].default_value = (*c1, 1); ch.inputs["Color2"].default_value = (*c2, 1)
    nt.links.new(ch.outputs[0], b.inputs["Base Color"])
    return m

bpy.ops.mesh.primitive_plane_add(size=40000, location=(0, -800, -10000))
ground = bpy.context.object; ground.data.materials.append(checker("g", 60.0, (0.8, 0.8, 0.8), (0.1, 0.1, 0.1)))
wall = checker("w", 12.0, (0.7, 0.4, 0.2), (0.15, 0.2, 0.3))
for x in (-2500, -800, 900, 2600):
    bpy.ops.mesh.primitive_cube_add(size=900, location=(x, -350, -6000 - abs(x) * 0.4))
    bpy.context.object.data.materials.append(wall)
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(-50), math.radians(20), 0)); bpy.context.object.data.energy = 3.0
sc.frame_start, sc.frame_end = 1, 8
cam.rotation_mode = 'XYZ'
for f in (1, 8):                                      # 匀速横摇：每帧 0.5°
    cam.rotation_euler = (0, math.radians(0.5 * (f - 1)), 0); cam.location = (0, 0, 0)
    cam.keyframe_insert("rotation_euler", frame=f)
for fc in cam.animation_data.action.fcurves if hasattr(cam.animation_data.action, "fcurves") else []:
    for kp in fc.keyframe_points:
        kp.interpolation = 'LINEAR'
try:                                                   # Blender 5.x：分层动作
    for layer in cam.animation_data.action.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                for fc in cb.fcurves:
                    for kp in fc.keyframe_points:
                        kp.interpolation = 'LINEAR'
except AttributeError:
    pass
bl.configure(cam, focus_m=6.0, f_number=8.0, dispersion=False)
rig.render_preset(sc)
sc.cycles.use_denoising = False
sc.view_settings.view_transform = 'Standard'
rep = {}

# 1. 同一种子：帧间差只来自运动
sc.cycles.samples = 64; sc.cycles.seed = 5; sc.cycles.use_animated_seed = False
r1 = bl.temporal_check(sc, frame=1, n=6, height=200, mode="moving", out_dir=os.path.join(OUT, "fixed"))
tones, vecs = [], []
for k in range(6):
    parts = PS.read(os.path.join(OUT, "fixed", f"base_{1 + k:04d}.exr"))
    tones.append(MO.tone(PS.rgb(PS.find(parts, "BL_Final"))))
    vecs.append(np.stack([PS.find(parts, "BL_Vector")["channels"][c] for c in "XYZW"], -1))
naive = float(np.mean([np.abs(tones[t] - tones[t - 1])[8:-8, 8:-8].mean() for t in range(1, 6)]))
rep["fixed_seed"] = dict(mc_frame_mean=r1["base"]["frame_mean"], naive_frame_mean=round(naive, 6),
                         ratio=round(r1["base"]["frame_mean"] / naive, 4))

# 3. 速度：相位相关测相邻帧整幅位移（中间一块，避开边缘渐晕与畸变差异）
def shift(a, b):
    H, W = a.shape
    a = a[H // 4:3 * H // 4, W // 4:3 * W // 4]; b = b[H // 4:3 * H // 4, W // 4:3 * W // 4]
    F = np.fft.fft2(a - a.mean()) * np.conj(np.fft.fft2(b - b.mean()))
    r = np.fft.ifft2(F / np.maximum(np.abs(F), 1e-12)).real
    j, i = np.unravel_index(np.argmax(r), r.shape)
    def sub(c, n, get):
        y0, y1, y2 = get((c - 1) % n), get(c), get((c + 1) % n)
        d = (y0 - y2) / (2 * (y0 - 2 * y1 + y2)) if (y0 - 2 * y1 + y2) != 0 else 0.0
        v = c + d
        return v - n if v > n / 2 else v
    dx = sub(i, r.shape[1], lambda k: r[j, k]); dy = sub(j, r.shape[0], lambda k: r[k, i])
    return dx, dy
meas = [shift(tones[t], tones[t - 1]) for t in range(1, 6)]
meas_px = float(np.median([math.hypot(*m) for m in meas]))
H = r1["resolution"][1]
mot = r1["base"]["motion"]
rep["speed"] = dict(median_px_1080=mot["median_px"], median_px_render=round(mot["median_px"] * H / 1080.0, 3),
                    phase_corr_px=round(meas_px, 3), hist=mot["fraction"], flicker_by_speed=mot["flicker_by_speed"])
rep["speed"]["rel_err"] = round(abs(rep["speed"]["median_px_render"] - meas_px) / meas_px, 4)

# 2. 动画种子：有采样噪点闪烁；spp ×8 应该明显更稳
sc.cycles.samples = 16; sc.cycles.use_animated_seed = True
r2 = bl.temporal_check(sc, frame=1, n=6, height=200, mode="moving", out_dir=os.path.join(OUT, "noisy"),
                       variants=[{"name": "spp128", "spp": 128}])
rep["noisy"] = dict(base=r2["base"], spp128_pct=r2["variants"][0]["pct_of_base"])
rep["noisy"]["base"].pop("motion", None)
rep["outputs"] = dict(heatmap=os.path.exists(r2["base"]["heatmap"]),
                      json=os.path.exists(os.path.join(OUT, "noisy", "temporal_moving_f1.json")),
                      keys=sorted(r2["base"]))
rep["mode_check"] = False
try:
    bl.temporal_check(sc, frame=1, n=2, mode="orbit")
except ValueError:
    rep["mode_check"] = True
rep["PASS"] = bool(rep["fixed_seed"]["ratio"] < 0.35 and rep["speed"]["rel_err"] < 0.1
                   and rep["noisy"]["spp128_pct"]["frame_mean"] < 60 and rep["noisy"]["spp128_pct"]["brightest2_mean"] < 80
                   and rep["outputs"]["heatmap"] and rep["outputs"]["json"] and rep["mode_check"])
print("TMOVING", json.dumps(rep))
