# 多层 EXR（0.8.3，MessyRoom R1 + R4a）：render_still(passes="all") 的输出
#   1. 渲染层齐全（Combined、各光组、Depth、Cycles 的 Vector、BL_Flare / BL_Veil / BL_Final / BL_Vector），临时打开的 Position / Crypto 不写出；
#   2. Lateral in Post 之后各光组相加 ≈ Combined；BL_Final = Combined + BL_Flare + BL_Veil；
#   3. BL_Vector 与实测一致：运动物体上一个发光小点在第 1 / 2 / 3 帧的质心位移 = 该点处的矢量（相机也在动）；
#   4. Blender 能读回输出；
#   5. Render Region + Crop 与 Lateral in Post 一起用不报错，裁出来的一块与整幅对应区域一致。
# 运行：Blender -b --factory-startup --python tests/bl_layers.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, passes as PS
OUT = os.path.join(ROOT, "renders", "layers")
os.makedirs(OUT, exist_ok=True)
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
vl = sc.view_layers[0]
for g in ("key", "fill", "sky"):
    vl.lightgroups.add(name=g)
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None:
    w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.06, 0.08, 1)
w.lightgroup = "sky"
cam = bl.build("c_series_style.json", scene=sc, height=200, units='MM')

def emit(name, s, col=(1, 1, 1)):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial"); e = nt.nodes.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*col, 1); e.inputs[1].default_value = s; nt.links.new(e.outputs[0], o.inputs[0])
    return m

bpy.ops.mesh.primitive_plane_add(size=20000, location=(0, -600, 0), rotation=(math.radians(90), 0, 0))
ground = bpy.context.object
bpy.ops.object.light_add(type='AREA', location=(1500, 1500, -1500)); key = bpy.context.object
key.data.energy = 4.0e7; key.data.size = 800; key.lightgroup = "key"
bpy.ops.object.light_add(type='POINT', location=(-2000, 500, -800)); fill = bpy.context.object
fill.data.energy = 1.0e7; fill.lightgroup = "fill"
bpy.ops.mesh.primitive_cube_add(size=300, location=(0, -300, -3000)); cube = bpy.context.object
bpy.ops.mesh.primitive_uv_sphere_add(radius=10, location=(60, -120, -2840)); dot = bpy.context.object
dot.data.materials.append(emit("dot", 400.0)); dot.lightgroup = "key"; dot.parent = cube
dot.matrix_parent_inverse = cube.matrix_world.inverted()
bpy.ops.mesh.primitive_uv_sphere_add(radius=30, location=(2500, 900, -15000)); lamp = bpy.context.object
lamp.data.materials.append(emit("lamp", 4.0e4)); lamp.lightgroup = "key"; lamp[rig.FLARE_PROP] = 1
sc.frame_start, sc.frame_end = 1, 3
for f, cx, rz, camx in ((1, -150.0, 0.0, -40.0), (2, 0.0, 0.25, 0.0), (3, 120.0, 0.55, 30.0)):
    cube.location = (cx, -300, -3000); cube.rotation_euler = (0, rz, 0)
    cube.keyframe_insert("location", frame=f); cube.keyframe_insert("rotation_euler", frame=f)
    cam.location = (camx, 0, 0); cam.rotation_euler = (0, 0, 0)
    cam.keyframe_insert("location", frame=f)
sc.frame_set(2)
vl.use_pass_z = True; vl.use_pass_vector = True; vl.use_pass_diffuse_direct = True
bl.configure(cam, focus_m=3.0, f_number=5.6, dispersion=True, dispersion_mode='LATERAL_POST')
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 200)
W, H = sc.render.resolution_x, sc.render.resolution_y
sc.cycles.samples = 64; sc.cycles.use_denoising = False; sc.cycles.seed = 3
sc.view_settings.view_transform = 'Standard'
rep = {}

path = os.path.join(OUT, "f2_all.exr")
bl.render_still(path, flare=True, passes="all")
parts = PS.read(path)
names = [p["pass_"] for p in parts]
rep["passes"] = names
need = {"Combined", "Combined_key", "Combined_fill", "Combined_sky", "Depth", "Vector", "Diffuse Direct",
        "BL_Flare", "BL_Veil", "BL_Final", "BL_Vector"}
rep["missing"] = sorted(need - set(names))
rep["leaked_helpers"] = [n for n in names if n.startswith(("Position", "CryptoObject"))]

comb = PS.rgb(PS.find(parts, "Combined"))
lg = sum(PS.rgb(PS.find(parts, f"Combined_{g}")) for g in ("key", "fill", "sky"))
rep["lightgroups_sum_rel"] = round(float(abs(lg.sum() / comb.sum() - 1)), 5)
rep["lightgroups_pixel_rel_rms"] = round(float(np.sqrt(((lg - comb) ** 2).mean()) / comb.mean()), 5)
fin = PS.rgb(PS.find(parts, "BL_Final"))
recon = comb + PS.rgb(PS.find(parts, "BL_Flare")) + PS.rgb(PS.find(parts, "BL_Veil"))
rep["final_recon_rel"] = float(np.abs(fin - recon).max() / max(np.abs(recon).max(), 1e-9))
dep = PS.find(parts, "Depth")["channels"]["Z"]
rep["depth_holes_on_objects"] = round(float(((dep == 0)).mean()), 4)
rep["flare_energy"] = round(float(PS.rgb(PS.find(parts, "BL_Flare")).sum()), 3)

# 3. BL_Vector 对实测：三帧渲染（关色散、无炫光，点像清晰），点的质心（像素，y 向上）
def dot_centroid(frame):
    sc.frame_set(frame)
    bl.configure(cam, dispersion=False)
    p_ = os.path.join(OUT, f"dot_{frame}.exr")
    bl.render_still(p_, flare=False)
    im = bpy.data.images.load(p_, check_existing=False)
    a = np.array(im.pixels[:]).reshape(im.size[1], im.size[0], 4)[:, :, :3].mean(2)   # 行 0 = 底部（y 向上）
    bpy.data.images.remove(im)
    m = a > 0.5 * a.max()
    ys, xs = np.nonzero(m)
    wv = a[m]
    return float((xs * wv).sum() / wv.sum()), float((ys * wv).sum() / wv.sum())

c = {f: dot_centroid(f) for f in (1, 2, 3)}
sc.frame_set(2)
bl.configure(cam, dispersion=True, dispersion_mode='LATERAL_POST')
vec = PS.find(parts, "BL_Vector")["channels"]
cx2, cy2 = c[2]
row_top = H - 1 - int(round(cy2)); col = int(round(cx2))
win = (slice(row_top - 1, row_top + 2), slice(col - 1, col + 2))
v = [float(np.median(vec[k][win])) for k in "XYZW"]
expect = [c[1][0] - c[2][0], c[1][1] - c[2][1], c[2][0] - c[3][0], c[2][1] - c[3][1]]
rep["dot_centroids"] = {k: [round(x, 2) for x in v_] for k, v_ in c.items()}
rep["bl_vector_at_dot"] = [round(x, 3) for x in v]
rep["measured_motion"] = [round(x, 3) for x in expect]
rep["vector_err_px"] = round(max(abs(a - b) for a, b in zip(v, expect)), 3)
cyc = PS.find(parts, "Vector")["channels"]
rep["cycles_vector_at_dot"] = [round(float(np.median(cyc[k][win])), 3) for k in "XYZW"]

# 4. Blender 读回
im = bpy.data.images.load(path, check_existing=False)
im.reload()
_ = len(im.pixels)
rep["blender_reads"] = im.type == 'MULTILAYER'
rep["blender_type"] = im.type
bpy.data.images.remove(im)

# 5. Render Region + Crop + Lateral in Post
r = sc.render
r.use_border = True; r.use_crop_to_border = True
r.border_min_x, r.border_max_x, r.border_min_y, r.border_max_y = 0.55, 0.95, 0.10, 0.60
try:
    bl.render_still(os.path.join(OUT, "region.exr"), flare=True, passes="all")
    rp = PS.read(os.path.join(OUT, "region.exr"))
    rc = PS.rgb(PS.find(rp, "Combined"))
    reg = rig._region(sc, rc.shape[1], rc.shape[0])
    x0, y0 = reg[0], reg[1]
    full = comb[y0:y0 + rc.shape[0], x0:x0 + rc.shape[1]]        # 杂光是整幅卷积，裁切后只能近似 → 比 Combined
    inner = (slice(4, -4), slice(4, -4))                        # 边缘几个像素：裁切处色差取值夹边
    rep["region"] = dict(size=list(rc.shape[:2]), offset=[x0, y0],
                         rel_diff=round(float(np.abs(rc[inner] - full[inner]).mean() / full[inner].mean()), 4))
    bl.render_still(os.path.join(OUT, "region_single.png"), flare=True)          # 单层输出 + 裁切（0.8.2 及以前会报错）
    rep["region"]["single_layer_ok"] = os.path.exists(os.path.join(OUT, "region_single.png"))
except Exception as e:                                           # noqa: BLE001
    rep["region"] = dict(error=str(e)[:300])
r.use_border = False

rep["PASS"] = bool(not rep["missing"] and not rep["leaked_helpers"] and rep["lightgroups_sum_rel"] < 0.01
                   and rep["final_recon_rel"] < 1e-5 and rep["depth_holes_on_objects"] < 0.01 and rep["flare_energy"] > 0 and rep["vector_err_px"] < 0.35
                   and rep["blender_reads"] and "error" not in rep["region"] and rep["region"]["rel_diff"] < 0.05 and rep["region"].get("single_layer_ok"))
print("LAYERS", json.dumps(rep))
