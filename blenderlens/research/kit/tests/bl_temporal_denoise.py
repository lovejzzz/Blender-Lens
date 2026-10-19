# 时间降噪（0.8.4，MessyRoom R3）：bl.temporal_denoise 对比 MessyRoom 的 hazefix（成片 v3 参数，--w all 模式移植）
#   场景（仿 Day 4 直升机镜头）：雾（世界体积散射）+ 几盏小灯 + 光滑地面（镜面倒影）+ 细纹理墙；相机缓慢环绕
#   （~1.4 px/帧 @1080）；每帧 16 spp + OIDN（逐帧降噪 → 雾里闪烁）；passes="temporal"。另渲高采样参考帧。
#   指标（第 4–7 帧，每边 3 帧窗口都在 1–10 里）：
#     · 运动补偿闪烁（MessyRoom flicker_moving.py 的定义）：处理后 / 原始；
#     · 细节（平均 |拉普拉斯|）：处理后 / 原始（MessyRoom 的定义；噪点多时它主要量的是噪点，只作参考）；
#     · 真实细节保留：处理后的高通在参考高通上的投影 / 参考高通的能量（噪点与参考不相关，平均掉）——要求 ≥ 原始的 97%；
#     · 与高采样参考的误差（x/(1+x) 亮度的 RMS）：处理后应比原始更接近参考；
#   验收：闪烁不差于 hazefix（≤ 1.05 ×），真实细节保留 ≥ 原始的 97%，误差 < 原始；另检查每像素用了几帧的诊断层。
# 运行：Blender -b --factory-startup --python tests/bl_temporal_denoise.py
import os, sys, json, math, shutil
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS, motion as MO
OUT = os.path.join(ROOT, "renders", "temporal_denoise")
SRC, DST, REF = os.path.join(OUT, "src"), os.path.join(OUT, "stable"), os.path.join(OUT, "ref")
QUICK = "--reuse" in sys.argv                         # 调试：已有渲染就不重渲
if not QUICK:
    shutil.rmtree(OUT, ignore_errors=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
nt = w.node_tree
nt.nodes["Background"].inputs[0].default_value = (0.01, 0.012, 0.02, 1)
vs = nt.nodes.new("ShaderNodeVolumeScatter"); vs.inputs["Density"].default_value = 0.035
nt.links.new(vs.outputs[0], nt.nodes["World Output"].inputs["Volume"])

def mat(name, base=(0.5, 0.5, 0.5), rough=0.5, metal=0.0, emit=0.0, ecol=(1, 0.8, 0.6), tex=None):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    t = m.node_tree; b = t.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*base, 1); b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if tex == "checker":
        ch = t.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = 40.0
        ch.inputs["Color1"].default_value = (0.6, 0.55, 0.5, 1); ch.inputs["Color2"].default_value = (0.08, 0.08, 0.1, 1)
        t.links.new(ch.outputs[0], b.inputs["Base Color"])
    elif tex == "noise":
        nz = t.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 60.0; nz.inputs["Detail"].default_value = 12.0
        t.links.new(nz.outputs["Color"], b.inputs["Base Color"])
    if emit:
        b.inputs["Emission Color"].default_value = (*ecol, 1); b.inputs["Emission Strength"].default_value = emit
    return m

bpy.ops.mesh.primitive_plane_add(size=120, location=(0, 20, 0)); bpy.context.object.data.materials.append(mat("floor", rough=0.06, tex="checker"))
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 22, 3)); wall = bpy.context.object; wall.scale = (14, 0.3, 6)
wall.data.materials.append(mat("wall", tex="noise"))
for i, (x, z, col) in enumerate(((-4, 3.5, (1, 0.5, 0.2)), (0, 4.5, (0.3, 0.6, 1)), (4, 3.0, (1, 0.2, 0.6)))):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.12, location=(x, 18, z)); bpy.context.object.data.materials.append(mat(f"lamp{i}", emit=80.0, ecol=col))
    bpy.ops.object.light_add(type='POINT', location=(x, 17.8, z)); L = bpy.context.object; L.data.energy = 400.0; L.data.color = col
    L.data.shadow_soft_size = 0.1
cam = bl.build("c_series_style.json", sc, gate="16:9_NATIVE", units="SCENE", height=270)
cam.rotation_mode = 'XYZ'
for f in (1, 10):                                     # 缓慢环绕
    a = math.radians(0.09 * (f - 1))
    cam.location = (8 * math.sin(a) - 0.0, 18 - 8 * math.cos(a) - 6, 2.2)
    cam.rotation_euler = (math.radians(88), 0, a)
    cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
for ad in (cam.animation_data,):
    for fc in bl._action_fcurves(ad.action):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
bl.configure(cam, focus_m=14.0, f_number=2.8, dispersion=False)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 270)
sc.view_settings.view_transform = 'Standard'
sc.cycles.use_adaptive_sampling = False
sc.cycles.volume_bounces = 1
FR = list(range(1, 11)); EV = [4, 5, 6, 7]
if not QUICK:
    sc.cycles.samples = 16; sc.cycles.use_denoising = True
    bl.render_frames(sc, frames=FR, out_dir=SRC, name="f", passes="temporal", seed=11)
    sc.cycles.samples = 768
    bl.render_frames(sc, frames=EV, out_dir=REF, name="r", passes=["Combined"], seed=99, vectors=False)
res = bl.temporal_denoise(SRC, DST, frames=EV)
rep = dict(per_frame=res["per_frame"])

# MessyRoom hazefix（--w all，--radius 3 --clamp 5 --margin 0.25，vfade 2,8）移植
def hazefix(t, R=3, CLAMP=5, MARGIN=0.25, VF=(2.0, 8.0)):
    def ld(tt):
        P = PS.read(os.path.join(SRC, f"f_{tt:04d}.exr"))
        d = dict(comb=PS.rgb(PS.find(P, "Combined")), vec=np.stack([PS.find(P, "BL_Vector")["channels"][c] for c in "XYZW"], -1))
        spec = PS.rgb(PS.find(P, "Denoising Specular Albedo")).mean(-1); rough = PS.find(P, "Denoising Roughness")["channels"]["X"]
        d["mirror"] = np.clip((spec - 0.08) / 0.12, 0, 1) * np.clip((0.35 - rough) / 0.2, 0, 1)
        return d
    cache = {tt: ld(tt) for tt in range(t - R, t + R + 1)}
    W5 = {0: 4.0, 1: 3.0, 2: 2.0, 3: 1.0}
    cur = cache[t]; H, W = cur["comb"].shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    from blenderlens.denoise import _minmax
    lo, hi = _minmax(cur["comb"], CLAMP); m = (hi - lo) * MARGIN; lo, hi = lo - m, hi + m
    acc = cur["comb"] * W5[0]; wsum = W5[0]
    for direction in (-1, 1):
        x, y = xx.copy(), yy.copy(); prevf = cur
        for k in range(1, R + 1):
            tt = t + direction * k
            v = MO.bilinear(prevf["vec"], x, y)
            if direction < 0:
                x = x + v[..., 0]; y = y - v[..., 1]
            else:
                x = x - v[..., 2]; y = y + v[..., 3]
            nb = cache[tt]
            acc += np.clip(MO.bilinear(nb["comb"], x, y), lo, hi) * W5[k]; wsum += W5[k]; prevf = nb
    tavg = acc / wsum
    w = (1 - cur["mirror"])[..., None]
    kk = max(1, int(round(3 * H / 1080)))
    hot = (cur["comb"].max(-1) > 1.5).astype(np.float32)
    p_ = np.pad(hot, kk, mode="edge"); hot = np.max(np.stack([p_[dy:dy + H, dx:dx + W] for dy in range(2 * kk + 1) for dx in range(2 * kk + 1)]), 0)
    sp = np.hypot(cur["vec"][..., 0], cur["vec"][..., 1]) / (H / 1080.0)
    w = w * (1 - hot * np.clip((sp - 0.25) / 0.5, 0, 1))[..., None]
    w = w * np.clip((VF[1] - sp) / (VF[1] - VF[0]), 0, 1)[..., None]
    return cur["comb"] + w * (tavg - cur["comb"])

src = {t: PS.read(os.path.join(SRC, f"f_{t:04d}.exr")) for t in range(EV[0] - 1, EV[-1] + 1)}
orig = {t: PS.rgb(PS.find(src[t], "BL_Final")) for t in src}
vec = {t: np.stack([PS.find(src[t], "BL_Vector")["channels"][c] for c in "XYZW"], -1) for t in src}
ours = {t: PS.rgb(PS.find(PS.read(os.path.join(DST, f"f_{t:04d}.exr")), "BL_Final")) for t in EV}
ours[EV[0] - 1] = orig[EV[0] - 1]                       # 第一对的上一帧：用原始（两种方法一样）
hz = {t: hazefix(t) for t in EV}; hz[EV[0] - 1] = orig[EV[0] - 1]
ref = {t: PS.rgb(PS.find(PS.read(os.path.join(REF, f"r_{t:04d}.exr")), "BL_Final")) for t in EV}

def mc(imgs):                                           # flicker_moving.py：|f_t − warp(f_{t−1})|，裁 8 px，整幅 + 最亮 2%
    T = {t: MO.tone(imgs[t]) for t in imgs}
    rs = [np.abs(T[t] - MO.warp_prev(T[t - 1], vec[t]))[8:-8, 8:-8] for t in EV[1:]]
    m = np.mean(rs, axis=0); A = MO.tone(orig[EV[1]])[8:-8, 8:-8]; br = A > np.percentile(A, 98)
    return float(m.mean()), float(m[br].mean())
lap = lambda x: float(np.abs(4 * x[1:-1, 1:-1] - x[:-2, 1:-1] - x[2:, 1:-1] - x[1:-1, :-2] - x[1:-1, 2:]).mean())
det = lambda imgs: np.mean([lap(MO.tone(imgs[t])) for t in EV])
err = lambda imgs: float(np.sqrt(np.mean([np.mean((MO.tone(imgs[t]) - MO.tone(ref[t])) ** 2) for t in EV])))
fo, fs, fh = mc(orig), mc(ours), mc(hz)
rep["flicker_whole_pct"] = dict(ours=round(100 * fs[0] / fo[0], 1), hazefix=round(100 * fh[0] / fo[0], 1))
rep["flicker_bright2_pct"] = dict(ours=round(100 * fs[1] / fo[1], 1), hazefix=round(100 * fh[1] / fo[1], 1))
rep["detail_pct"] = dict(ours=round(100 * det(ours) / det(orig), 1), hazefix=round(100 * det(hz) / det(orig), 1),
                         reference=round(100 * det(ref) / det(orig), 1))
def lapimg(x):
    return 4 * x[1:-1, 1:-1] - x[:-2, 1:-1] - x[2:, 1:-1] - x[1:-1, :-2] - x[1:-1, 2:]
def retention(imgs):                                    # 真实细节保留：高通与参考高通的投影（噪点与参考不相关，平均掉）
    num = sum(float((lapimg(MO.tone(imgs[t])) * lapimg(MO.tone(ref[t]))).sum()) for t in EV)
    den = sum(float((lapimg(MO.tone(ref[t])) ** 2).sum()) for t in EV)
    return 100.0 * num / den
rep["detail_retention_vs_ref_pct"] = dict(original=round(retention(orig), 1), ours=round(retention(ours), 1),
                                          hazefix=round(retention(hz), 1))
rep["err_vs_ref"] = dict(original=round(err(orig), 5), ours=round(err(ours), 5), hazefix=round(err(hz), 5))
rep["speed_median_px_1080"] = round(float(np.median(MO.speed(vec[EV[1]], orig[EV[1]].shape[0]))), 3)
fr = PS.read(os.path.join(DST, f"f_{EV[1]:04d}.exr"))
rep["frames_layer"] = dict(present=PS.find(fr, "BL_TemporalFrames") is not None,
                           mean=round(float(PS.find(fr, "BL_TemporalFrames")["channels"]["X"].mean()), 3))
rep["PASS"] = bool(rep["flicker_whole_pct"]["ours"] <= 1.05 * rep["flicker_whole_pct"]["hazefix"]
                   and rep["flicker_bright2_pct"]["ours"] <= 1.05 * rep["flicker_bright2_pct"]["hazefix"]
                   and rep["detail_retention_vs_ref_pct"]["ours"] >= 0.97 * rep["detail_retention_vs_ref_pct"]["original"]
                   and rep["err_vs_ref"]["ours"] < rep["err_vs_ref"]["original"]
                   and rep["frames_layer"]["present"] and rep["flicker_whole_pct"]["ours"] < 90)
print("TDENOISE", json.dumps(rep))
