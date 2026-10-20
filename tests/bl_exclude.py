# 排除层（0.8.5，MessyRoom 0.8.5 R2）：雨不进场景层、不参与时间平均，最后原样 over 回去
#   场景：地面 + 几个盒子，相机慢慢横移；集合 FX_rain 里三片「雨」（细长条，快速下落，Transmission 1、IOR 1.33、Alpha 0.35）。
#   R：passes="temporal", exclude_collections=["FX_rain"]；N：雨整个隐藏；O：只渲雨（其余 holdout、胶片透明）。
#   验收（MessyRoom）：BL_Exclude(R) 的颜色与 O 一致（0.8.7：alpha 不再用 Cycles 的，见 rig 里的自归一化）；
#   0.8.7：没有雨的像素 alpha 恰好 0、BL_Final = Combined + 炫光（不再被渐晕 / 截断偏差压暗）；R 的场景层运动补偿闪烁与 N 相差 ≤ 5%（hide 模式下逐像素相同）；
#   temporal_denoise(R) 的 BL_Final 与「temporal_denoise(N) 的场景层 + 雨层 over」逐像素一致；BL_Final(R) = 雨 over 场景 + 炫光。
#   0.8.7：exclude_spp=8 / exclude_bounces=2 时场景层逐像素不变、雨的覆盖（alpha 平均）差 < 15%，并报告排除层用时。
# 运行：Blender -b --factory-startup --python tests/bl_exclude.py
import os, sys, json, math, shutil
import bpy, bmesh
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS, motion as MO
OUT = os.path.join(ROOT, "renders", "exclude")
shutil.rmtree(OUT, ignore_errors=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.06, 0.09, 1)

def mat(name, scale=None, c1=(0.8, 0.8, 0.8), c2=(0.1, 0.1, 0.1)):
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
    return m

bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 10, 0)); bpy.context.object.data.materials.append(mat("ground", 20.0))
for i, x in enumerate((-2.5, 0.0, 2.5)):
    bpy.ops.mesh.primitive_cube_add(size=1.2, location=(x, 9 + i, 0.6)); bpy.context.object.data.materials.append(mat(f"b{i}", 4.0, (0.9, 0.7, 0.2), (0.2, 0.3, 0.6)))
bpy.ops.object.light_add(type='AREA', location=(0, 7, 5)); L = bpy.context.object; L.data.energy = 1500; L.data.size = 4
rain_col = bpy.data.collections.new("FX_rain"); sc.collection.children.link(rain_col)
rm = bpy.data.materials.new("rain")
if rm.node_tree is None:
    rm.use_nodes = True
b = rm.node_tree.nodes.get("Principled BSDF")
b.inputs["Transmission Weight"].default_value = 1.0; b.inputs["IOR"].default_value = 1.33; b.inputs["Alpha"].default_value = 0.35
b.inputs["Roughness"].default_value = 0.3
rng = np.random.default_rng(3)
for k, y in enumerate((4.0, 7.0, 11.0)):
    me = bpy.data.meshes.new(f"rain{k}"); bm = bmesh.new()
    for _ in range(400):
        x, z = rng.uniform(-6, 6), rng.uniform(0, 6)
        r = bmesh.ops.create_cube(bm, size=1.0)
        for v in r["verts"]:
            v.co.x = x + v.co.x * 0.012; v.co.y = y + v.co.y * 0.012; v.co.z = z + v.co.z * 0.25
    bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new(f"Rain{k}", me); rain_col.objects.link(ob); ob.data.materials.append(rm)
    for f in (1, 8):
        ob.location = (0, 0, -0.35 * (f - 1)); ob.keyframe_insert("location", frame=f)
cam = bl.build("c_series_style.json", sc, gate="16:9_NATIVE", units="SCENE", height=180)
cam.rotation_mode = 'XYZ'
for f in (1, 8):
    cam.location = (-0.2 + 0.03 * (f - 1), -2.0, 1.5); cam.rotation_euler = (math.radians(88), 0, 0)
    cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
for ob in [cam] + list(rain_col.objects):
    for fc in bl._action_fcurves(ob.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
bl.configure(cam, focus_m=10.0, f_number=5.6, dispersion=False)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 180)
sc.cycles.samples = 32; sc.cycles.use_denoising = True
sc.cycles.use_adaptive_sampling = False                  # 与排除层的渲染一致（它关自适应），便于逐像素比颜色
sc.view_settings.view_transform = 'Standard'
FR = list(range(1, 9)); EV = [4, 5]
dR, dN, dO = (os.path.join(OUT, k) for k in ("R", "N", "O"))
LOSSLESS = dict(exr_codec="PIZ", half_color=True)       # 逐像素比对：DWAB 有损（边缘几个百分点），用无损压缩
bl.render_frames(sc, frames=FR, out_dir=dR, name="f", passes="temporal", seed=2, exclude_collections=["FX_rain"], **LOSSLESS)
for ob in rain_col.objects:
    ob.hide_render = True
bl.render_frames(sc, frames=FR, out_dir=dN, name="f", passes="temporal", seed=2, **LOSSLESS)
for ob in rain_col.objects:
    ob.hide_render = False
others = [o for o in sc.objects if o.type == 'MESH' and o.name not in rain_col.all_objects and not o.name.startswith("BL_")]
for o in others:
    o.is_holdout = True
sc.render.film_transparent = True
mlb, mtb = sc.cycles.min_light_bounces, sc.cycles.min_transparent_bounces     # 排除层不用俄罗斯轮盘（见 rig._aux_render）
sc.cycles.min_light_bounces, sc.cycles.min_transparent_bounces = sc.cycles.max_bounces, sc.cycles.transparent_max_bounces
bl.render_frames(sc, frames=[4], out_dir=dO, name="f", passes=["Combined"], seed=2, vectors=False, **LOSSLESS)
sc.cycles.min_light_bounces, sc.cycles.min_transparent_bounces = mlb, mtb
for o in others:
    o.is_holdout = False
sc.render.film_transparent = False
rep = {}
R = {t: PS.read(os.path.join(dR, f"f_{t:04d}.exr")) for t in FR}
N = {t: PS.read(os.path.join(dN, f"f_{t:04d}.exr")) for t in FR}
O = PS.read(os.path.join(dO, "f_0004.exr"))
rgba = lambda P, n: np.stack([PS.find(P, n)["channels"][c] for c in "RGBA"], -1).astype(np.float32)
ex, onl = rgba(R[4], "BL_Exclude"), rgba(O, "Combined")
rep["exclude_vs_rain_only_rgb_maxdiff"] = float(np.abs(ex[..., :3] - onl[..., :3]).max() / max(float(np.abs(onl[..., :3]).max()), 1e-9))
# 降噪器（OIDN）对输入亮度不是严格线性：排除层是 ×1/4 渲、降噪后再除回去，个别像素差几个百分点；不降噪时逐像素相同
rep["exclude_vs_rain_only_rgb_meanrel"] = float(np.abs(ex[..., :3] - onl[..., :3]).mean() / max(float(np.abs(onl[..., :3]).mean()), 1e-9))
# 0.8.7：alpha 只表示雨——没有雨的像素恰好 0、BL_Final = Combined + 炫光（0.8.6 及以前：渐晕 + 截断偏差把画面压暗 5–11%）
norain = ex[..., :3].max(-1) == 0.0
fin4 = PS.rgb(PS.find(R[4], "BL_Final")); comb4 = PS.rgb(PS.find(R[4], "Combined"))
flv4 = PS.rgb(PS.find(R[4], "BL_Flare")) + PS.rgb(PS.find(R[4], "BL_Veil"))
rep["norain"] = dict(frac=round(float(norain.mean()), 4), alpha_max=float(ex[..., 3][norain].max()) if norain.any() else None,
                     final_vs_comb_maxrel=float(np.abs(fin4 - comb4 - flv4)[norain].max() / max(float(comb4.max()), 1e-9)) if norain.any() else None)
rep["raw_cycles_alpha_on_norain"] = round(float(onl[..., 3][norain].mean()), 4) if norain.any() else None
rep["exclude_alpha_mean"] = round(float(ex[..., 3].mean()), 4)
rep["exclude_header"] = json.loads(PS.find(R[4], "Combined")["header"]["blenderlens/exclude"])
# 场景层 = Combined + Flare + Veil；R 与 N 比
scene = lambda P: PS.rgb(PS.find(P, "Combined")) + PS.rgb(PS.find(P, "BL_Flare")) + PS.rgb(PS.find(P, "BL_Veil"))
vec = lambda P: np.stack([PS.find(P, "BL_Vector")["channels"][c] for c in "XYZW"], -1)
def mcf(D, img):
    T = {t: MO.tone(img(D[t])) for t in FR}
    return float(np.mean([np.abs(T[t] - MO.warp_prev(T[t - 1], vec(D[t])))[8:-8, 8:-8].mean() for t in FR[1:]]))
rep["scene_flicker"] = dict(R=round(mcf(R, scene), 6), N=round(mcf(N, scene), 6))
rep["scene_flicker_rel_diff"] = round(abs(rep["scene_flicker"]["R"] / rep["scene_flicker"]["N"] - 1), 5)
rep["final_with_rain_flicker"] = round(mcf(R, lambda P: PS.rgb(PS.find(P, "BL_Final"))), 6)
fin = PS.rgb(PS.find(R[4], "BL_Final"))
recon = ex[..., :3] + (1 - ex[..., 3:]) * PS.rgb(PS.find(R[4], "Combined")) + PS.rgb(PS.find(R[4], "BL_Flare")) + PS.rgb(PS.find(R[4], "BL_Veil"))
rep["final_recon_rel"] = float(np.abs(fin - recon).max() / max(float(np.abs(recon).max()), 1e-9))
sR = bl.temporal_denoise(dR, os.path.join(OUT, "R_stable"), frames=EV, half=False)
sN = bl.temporal_denoise(dN, os.path.join(OUT, "N_stable"), frames=EV, half=False)
errs = []
for t in EV:
    a = PS.rgb(PS.find(PS.read(os.path.join(OUT, "R_stable", f"f_{t:04d}.exr")), "BL_Final"))
    stableN = PS.rgb(PS.find(PS.read(os.path.join(OUT, "N_stable", f"f_{t:04d}.exr")), "BL_Final"))     # N 没有炫光层以外的东西
    exr_ = rgba(R[t], "BL_Exclude")
    flv = PS.rgb(PS.find(N[t], "BL_Flare")) + PS.rgb(PS.find(N[t], "BL_Veil"))
    b_ = exr_[..., :3] + (1 - exr_[..., 3:]) * (stableN - flv) + flv
    errs.append(float(np.abs(a - b_).max() / max(float(np.abs(b_).max()), 1e-9)))
rep["stable_vs_norain_plus_rain_rel"] = max(errs)
# 0.8.7：排除层只算 Combined（结果不变，上面的逐像素比对已覆盖）；可选的 exclude_spp / exclude_bounces
from blenderlens import rig as _rig
sc.frame_set(4)
dS = os.path.join(OUT, "S"); os.makedirs(dS, exist_ok=True)
sc.cycles.seed = 2 + 4
bl.render_still(os.path.join(dS, "full.exr"), flare=False, passes="temporal", exclude_collections=["FX_rain"], **LOSSLESS)
t_full = _rig.LAST_TIMING.get("exclude_render")
bl.render_still(os.path.join(dS, "cheap.exr"), flare=False, passes="temporal", exclude_collections=["FX_rain"],
                exclude_spp=8, **LOSSLESS)
t_cheap = _rig.LAST_TIMING.get("exclude_render")
F, Cp = PS.read(os.path.join(dS, "full.exr")), PS.read(os.path.join(dS, "cheap.exr"))
rep["exclude_spp_option"] = dict(
    scene_identical=float(np.abs(PS.rgb(PS.find(F, "Combined")) - PS.rgb(PS.find(Cp, "Combined"))).max()) <= 1e-3 * float(PS.rgb(PS.find(F, "Combined")).max()),   # GPU 有 ~3e-4 的不确定性
    alpha_mean=[round(float(rgba(F, "BL_Exclude")[..., 3].mean()), 4), round(float(rgba(Cp, "BL_Exclude")[..., 3].mean()), 4)],
    exclude_render_s=[t_full, t_cheap])
eo = rep["exclude_spp_option"]
nr = rep["norain"]
rep["PASS"] = bool(eo["scene_identical"] and abs(eo["alpha_mean"][1] / max(eo["alpha_mean"][0], 1e-9) - 1) < 0.15
                   and rep["exclude_vs_rain_only_rgb_maxdiff"] < 0.06 and rep["exclude_vs_rain_only_rgb_meanrel"] < 0.01 and rep["exclude_alpha_mean"] > 0.002
                   and nr["frac"] > 0.3 and nr["alpha_max"] < 1e-5 and nr["final_vs_comb_maxrel"] < 1e-3
                   and rep["scene_flicker_rel_diff"] <= 0.05 and rep["final_recon_rel"] < 1e-3
                   and rep["stable_vs_norain_plus_rain_rel"] < 2e-3 and rep["final_with_rain_flicker"] > rep["scene_flicker"]["R"])
print("EXCLUDE", json.dumps(rep))
