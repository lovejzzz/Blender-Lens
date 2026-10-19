# 多层 EXR 的体积与内存（0.8.4，MessyRoom R4）：
#   1. passes="temporal"：只写时间降噪需要的层（+ 插件层），颜色层 half + DWAB，数据层（Depth、BL_Vector…）float32 + ZIP；
#      视图层里开着但没人要的层（Normal、Diffuse…）不写出，渲染时临时关掉，渲完全部恢复；
#   2. 与 passes="all"（float32）对比：Combined 相对差 < 1%（half + DWAB 有损），BL_Vector 完全一致（无损）；
#   3. ["temporal", "lightgroups"] 带上光组；exr_codec 写错 → ValueError；
#   4. 流式写出的文件 Blender 能读（MULTILAYER），Cryptomatte 清单与 blenderlens/fill 头还在；体积明显变小。
# 运行：Blender -b --factory-startup --python tests/bl_passes_r4.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS
import OpenEXR
OUT = os.path.join(ROOT, "renders", "passes_r4")
os.makedirs(OUT, exist_ok=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
vl = sc.view_layers[0]
for g in ("key", "sky"):
    vl.lightgroups.add(name=g)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.06, 0.08, 1)
w.lightgroup = "sky"
cam = bl.build("c_series_style.json", scene=sc, height=160, units='MM')
bpy.ops.mesh.primitive_plane_add(size=20000, location=(0, -600, 0), rotation=(math.radians(90), 0, 0))
bpy.ops.object.light_add(type='AREA', location=(1500, 1500, -1500)); key = bpy.context.object
key.data.energy = 4.0e7; key.data.size = 800; key.lightgroup = "key"
bpy.ops.mesh.primitive_cube_add(size=300, location=(0, -300, -3000)); cube = bpy.context.object
sc.frame_start, sc.frame_end = 1, 3
for f, cx in ((1, -150.0), (2, 0.0), (3, 120.0)):
    cube.location = (cx, -300, -3000); cube.keyframe_insert("location", frame=f)
sc.frame_set(2)
vl.use_pass_normal = True; vl.use_pass_diffuse_direct = True; vl.use_pass_vector = True    # 开着但 temporal 不要
bl.configure(cam, focus_m=3.0, f_number=5.6, dispersion=True, dispersion_mode='LATERAL_POST')
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 160)
sc.cycles.samples = 32; sc.cycles.use_denoising = True; sc.cycles.seed = 3
sc.view_settings.view_transform = 'Standard'
flags_before = dict(z=vl.use_pass_z, normal=vl.use_pass_normal, diffuse=vl.use_pass_diffuse_direct, vector=vl.use_pass_vector,
                    store=vl.cycles.denoising_store_passes, vol=vl.cycles.use_pass_volume_direct, pos=vl.use_pass_position)
rep = {}

def info(path):
    out = {}
    with OpenEXR.File(path, header_only=True) as f:
        for pt in f.parts:
            h = dict(pt.header)
            out[(pt.name() or "").partition(".")[2]] = dict(compression=str(h.get("compression")).split(".")[-1],
                                                            keys=[k for k in h if "/" in k])
    with OpenEXR.File(path, separate_channels=True) as f:
        for pt in f.parts:
            out[(pt.name() or "").partition(".")[2]]["dtype"] = sorted({str(c.pixels.dtype) for c in pt.channels.values()})
    return out

p_all = os.path.join(OUT, "all.exr"); p_tmp = os.path.join(OUT, "temporal.exr"); p_lg = os.path.join(OUT, "temporal_lg.exr")
bl.render_still(p_all, flare=False, passes="all")
bl.render_still(p_tmp, flare=False, passes="temporal")
rep["flags_restored"] = flags_before == dict(z=vl.use_pass_z, normal=vl.use_pass_normal, diffuse=vl.use_pass_diffuse_direct,
                                             vector=vl.use_pass_vector, store=vl.cycles.denoising_store_passes,
                                             vol=vl.cycles.use_pass_volume_direct, pos=vl.use_pass_position)
bl.render_still(p_lg, flare=False, passes=["temporal", "lightgroups"])
I = info(p_tmp)
rep["temporal_passes"] = sorted(I)
want = {"Combined", "Noisy Image", "Volume Direct", "Volume Indirect", "Denoising Albedo", "Denoising Specular Albedo",
        "Denoising Normal", "Denoising Roughness", "Denoising Depth", "Depth", "BL_Flare", "BL_Veil", "BL_Final", "BL_Vector",
        "BL_VectorConfidence"}
rep["temporal_missing"] = sorted(want - set(I))
rep["temporal_extra"] = sorted(set(I) - want)
color = [n for n in I if n in ("Combined", "Noisy Image", "Volume Direct", "Denoising Albedo", "BL_Final", "BL_Flare")]
data = [n for n in I if n in ("Depth", "BL_Vector", "BL_VectorConfidence", "Denoising Normal", "Denoising Depth", "Denoising Roughness")]
rep["color_half_dwab"] = all(I[n]["dtype"] == ["float16"] and I[n]["compression"] == "DWAB_COMPRESSION" for n in color)
rep["data_float_zip"] = all(I[n]["dtype"] == ["float32"] and I[n]["compression"] == "ZIP_COMPRESSION" for n in data)
rep["lightgroups"] = sorted(n for n in info(p_lg) if n.startswith("Combined_"))
A = PS.read(p_all); B = PS.read(p_tmp)
ca, cb = PS.rgb(PS.find(A, "Combined")), PS.rgb(PS.find(B, "Combined"))
rep["combined_rel_diff"] = round(float(np.abs(ca - cb).mean() / ca.mean()), 5)
va = np.stack([PS.find(A, "BL_Vector")["channels"][c] for c in "XYZW"], -1)
vb = np.stack([PS.find(B, "BL_Vector")["channels"][c] for c in "XYZW"], -1)
rep["vector_max_diff"] = float(np.abs(va - vb).max())
rep["size_all_kb"] = os.path.getsize(p_all) // 1024; rep["size_temporal_kb"] = os.path.getsize(p_tmp) // 1024
Ia = info(p_all)
rep["all_float32"] = all(v["dtype"] == ["float32"] for v in Ia.values())
rep["fill_header"] = "blenderlens/fill" in Ia["Combined"]["keys"] and "blenderlens/fill" in I["Combined"]["keys"]
im = bpy.data.images.load(p_tmp, check_existing=False); im.reload(); _ = len(im.pixels)
rep["blender_reads"] = im.type == 'MULTILAYER'
bpy.data.images.remove(im)
try:
    bl.render_still(os.path.join(OUT, "bad.exr"), flare=False, passes="temporal", exr_codec="JPEG")
    rep["bad_codec_raises"] = False
except ValueError:
    rep["bad_codec_raises"] = True
rep["timing"] = rig.LAST_TIMING
rep["PASS"] = bool(rep["flags_restored"] and not rep["temporal_missing"] and not rep["temporal_extra"] and rep["color_half_dwab"]
                   and rep["data_float_zip"] and rep["lightgroups"] == ["Combined_key", "Combined_sky"]
                   and rep["combined_rel_diff"] < 0.01 and rep["vector_max_diff"] == 0.0 and rep["all_float32"]
                   and rep["fill_header"] and rep["blender_reads"] and rep["bad_codec_raises"]
                   and rep["size_temporal_kb"] < 0.5 * rep["size_all_kb"])
print("PASSES_R4", json.dumps(rep))
