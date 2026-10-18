# 验证 OSL 相机：画面方位、视场映射（对照 Python 真实主光线追迹）、Analytic 与 Geometry 两种模式一致。
# Blender -b --factory-startup --python tests/bl_verify_mapping.py
import bpy, os, sys, math, json
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
from blenderlens import rig, optics as O

OUT = os.path.join(ROOT, "renders")
LT = 3000.0                       # 靶面到像面距离（mm）
H_PX = 540

bpy.ops.wm.read_factory_settings(use_empty=True)
blenderlens.register()
bpy.ops.blenderlens.build(height=H_PX)
sc = bpy.context.scene
cam_ob = sc.camera
cam = cam_ob.data
cam_ob.location = (0, 0, 0); cam_ob.rotation_euler = (0, 0, 0)          # 视线 = 世界 −Z
p = cam.blenderlens
p.focus_distance = LT
p.dispersion = False
W_PX = sc.render.resolution_x
sc.world = bpy.data.worlds.new("W"); rig.black_world(sc.world)
sc.view_settings.view_transform = 'Standard'
sc.cycles.use_denoising = False

# 刻度靶：竖线 x = 200k（橙），横线 y = 100 + 200k（蓝），右上象限红色方块（方位标记）
bpy.ops.mesh.primitive_plane_add(size=8000, location=(0, 0, -LT))
tgt = bpy.context.object
m = bpy.data.materials.new("Target"); m.use_nodes = True
nt = m.node_tree; nt.nodes.clear()
o = nt.nodes.new("ShaderNodeOutputMaterial"); em = nt.nodes.new("ShaderNodeEmission")
tc = nt.nodes.new("ShaderNodeTexCoord"); sep = nt.nodes.new("ShaderNodeSeparateXYZ")
nt.links.new(tc.outputs["Object"], sep.inputs[0])
def math_node(op, a, b=None):
    n = nt.nodes.new("ShaderNodeMath"); n.operation = op
    for k, v in enumerate((a, b)):
        if v is None: continue
        if isinstance(v, (int, float)): n.inputs[k].default_value = v
        else: nt.links.new(v, n.inputs[k])
    return n.outputs[0]
def lines(coord, period, offset, half_w):
    u = math_node('ADD', math_node('DIVIDE', math_node('SUBTRACT', coord, offset), period), 0.5)
    f = math_node('SUBTRACT', math_node('FRACT', u), 0.5)
    return math_node('LESS_THAN', math_node('MULTIPLY', math_node('ABSOLUTE', f), period), half_w)
vx = lines(sep.outputs["X"], 200.0, 0.0, 6.0)
hy = lines(sep.outputs["Y"], 200.0, 100.0, 6.0)
mark = math_node('MULTIPLY', math_node('MULTIPLY', math_node('GREATER_THAN', sep.outputs["X"], 420.0), math_node('LESS_THAN', sep.outputs["X"], 580.0)),
                 math_node('MULTIPLY', math_node('GREATER_THAN', sep.outputs["Y"], 220.0), math_node('LESS_THAN', sep.outputs["Y"], 380.0)))
cmb = nt.nodes.new("ShaderNodeCombineColor")
nt.links.new(math_node('MAXIMUM', vx, mark), cmb.inputs[0])
nt.links.new(math_node('MULTIPLY', vx, 0.45), cmb.inputs[1])
nt.links.new(hy, cmb.inputs[2])
nt.links.new(cmb.outputs[0], em.inputs["Color"]); nt.links.new(em.outputs[0], o.inputs["Surface"])
tgt.data.materials.append(m)

# —— 预测：真实主光线（过光阑中心）落到靶面上 → 反解传感器位置 ——
rx = rig.read_prescription()
d1, d2 = O.solve_focus(rx, LT)
S = O.layout(rx, d1, d2)
z_stop, _ = O.stop_geometry(rx, S)
def chief_hit(sx, sy):
    d = O.aim_through_stop(S, (sx, sy), (0.0, 0.0), z_stop)
    pnt, dd = O.trace_reverse(S, (sx, sy, 0.0), d, clip=False)
    t = (LT - pnt[2]) / dd[2]
    return pnt[0] + t * dd[0], pnt[1] + t * dd[1]
def solve(axis, target, lim):
    lo, hi = -lim, lim
    g = lambda s: (chief_hit(s, 0.0)[0] if axis == 0 else chief_hit(0.0, s)[1]) - target
    glo = g(lo)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        gm = g(mid)
        if (gm > 0) == (glo > 0): lo, glo = mid, gm
        else: hi = mid
    return 0.5 * (lo + hi)
SW, SH = rx["sensor"]["width"], rx["sensor"]["height"]
pred_cols = {}
for k in range(-4, 5):
    xw = 200.0 * k
    try:
        sx = solve(0, -xw, SW / 2)                    # 镜头 x = −世界 x
    except Exception:
        continue
    if abs(sx) < SW / 2 * 0.98:
        pred_cols[xw] = (sx / SW + 0.5) * W_PX - 0.5
pred_rows = {}
for k in range(-3, 3):
    yw = 100.0 + 200.0 * k
    sy = solve(1, yw, SH / 2)
    if abs(sy) < SH / 2 * 0.98:
        pred_rows[yw] = (-sy / SH + 0.5) * H_PX - 0.5

def centroids(sig, thr):
    on = sig > thr
    out, i = [], 0
    while i < len(sig):
        if on[i]:
            j = i
            while j < len(sig) and on[j]: j += 1
            w = sig[i:j]; out.append(float((np.arange(i, j) * w).sum() / w.sum())); i = j
        else:
            i += 1
    return out

def render_and_measure(mode, samples):
    p.mode = mode
    rig.render_preset(sc, mode)
    sc.cycles.samples = samples
    path = os.path.join(OUT, f"map_{mode.lower()}.png")
    sc.render.filepath = path
    sc.render.image_settings.file_format = 'PNG'
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H_PX, W_PX, 4)[::-1]   # 行 0 = 顶部
    # 竖线：沿中心行（世界 y=0 在两条横线之间）
    row = int(round(H_PX / 2))
    g = px[row - 2: row + 3, :, 1].mean(axis=0)            # 绿通道只有竖线
    cols = centroids(g, 0.12)
    b = px[:, W_PX // 2 + 7: W_PX // 2 + 12, 2].mean(axis=1)  # 蓝通道只有横线（避开 x=0 竖线）
    rows = centroids(b, 0.25)
    # 方位：红色方块（世界 +x、+y）应在画面右上
    r = px[:, :, 0] - px[:, :, 1] * 2.2
    ys, xs = np.nonzero(r > 0.5)
    marker = (float(xs.mean()) / W_PX, float(ys.mean()) / H_PX) if len(xs) else None
    mrgb = None
    if len(xs):
        cy, cx = int(ys.mean()), int(xs.mean())
        mrgb = [round(float(v), 4) for v in px[cy - 8:cy + 8, cx - 8:cx + 8, :3].mean(axis=(0, 1))]
    return cols, rows, marker, mrgb

def match(meas, pred):
    errs = {}
    for k, v in pred.items():
        if not meas: continue
        m = min(meas, key=lambda c: abs(c - v))
        errs[k] = round(m - v, 2)
    return errs

rep = dict(resolution=(W_PX, H_PX), focus=LT, delta=(round(d1, 3), round(d2, 3)))
for mode, spp in (('ANALYTIC', 32), ('GEOMETRY', 64)):
    cols, rows, marker, mrgb = render_and_measure(mode, spp)
    rep[mode] = dict(col_err_px=match(cols, pred_cols), row_err_px=match(rows, pred_rows),
                     n_cols=len(cols), n_rows=len(rows),
                     marker_uv_from_topleft=None if marker is None else tuple(round(v, 3) for v in marker),
                     marker_rgb=mrgb)
print("MAPPING " + json.dumps(rep, ensure_ascii=False))
