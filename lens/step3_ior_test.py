# 第 3 步：最小测试 —— 透过单片柱面镜看刻度靶，实测水平压缩 vs 精确光线追迹
# 三种情况：none（无镜片对照）/ solid（封闭实体，IOR=n）/ iface（界面法，S1: n，S2: 1/n）
# 运行：Blender -b --factory-startup --python lens/step3_ior_test.py
import bpy, sys, os, math, json
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import lensgen as lg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "renders")

N_D = 1.5168                     # N-BK7
R1, Z1, Z2, D = -120.0, 91.5, 85.0, 72.0
F_THIN = R1 / (N_D - 1)          # ≈ −232 mm
ZT = Z1 - F_THIN                 # 靶面放在 |f| 外：薄透镜预测 ≈2× 水平压缩
PIX, RES = 0.1, 1100             # 0.1 mm/像素，视野 110 mm

# —— 精确追迹（camera 侧光线 +Z 入射，过平后表面不偏折，前表面 glass→air 折射）——
def trace(x0):
    c = 1 / R1
    zs = Z1 - lg.sag(x0, 0, c, 0)
    gx, _ = lg.sag_grad(x0, 0, c, 0)
    L = math.hypot(gx, 1.0); nx, nz = gx / L, 1.0 / L           # 面法线（+Z 侧）
    dx, dz = 0.0, 1.0
    eta = N_D / 1.0
    cosi = dx*nx + dz*nz
    k = 1 - eta*eta*(1 - cosi*cosi)
    tx = eta*dx + (math.sqrt(k) - eta*cosi)*nx                   # 出射方向（与 n 同侧）
    tz = eta*dz + (math.sqrt(k) - eta*cosi)*nz
    return x0 + (ZT - zs) * tx / tz

def inverse_trace(xt):           # 靶上 xt 在相机平面上对应的 x0（二分）
    lo, hi = 0.0, D / 2 - 1e-6
    if trace(hi) < xt:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if trace(mid) < xt else (lo, mid)
    return (lo + hi) / 2

# —— 场景 ——
def setup(variant):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.scale_length = 0.001; sc.unit_settings.length_unit = 'MILLIMETERS'
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.cycles.samples = 32; sc.cycles.use_denoising = False
    sc.cycles.max_bounces = 64; sc.cycles.transmission_bounces = 64
    sc.cycles.glossy_bounces = 64; sc.cycles.transparent_max_bounces = 64
    sc.cycles.blur_glossy = 0.0
    sc.cycles.caustics_reflective = True; sc.cycles.caustics_refractive = True
    sc.render.resolution_x = sc.render.resolution_y = RES
    sc.view_settings.view_transform = 'Standard'
    w = bpy.data.worlds.new("W"); sc.world = w; w.color = (0, 0, 0)

    # 刻度靶：自发光，竖线（每 10 mm，暖色）+ 横线（每 10 mm，冷色）
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, ZT))
    tgt = bpy.context.object; tgt.name = "Target"
    m = bpy.data.materials.new("TargetMat"); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    def line(axis_out, half_w=0.35):
        # |fract(v/10 + 0.5) − 0.5| · 10 < half_w
        a = nt.nodes.new("ShaderNodeMath"); a.operation = 'MULTIPLY_ADD'
        nt.links.new(axis_out, a.inputs[0]); a.inputs[1].default_value = 0.1; a.inputs[2].default_value = 0.5
        f = nt.nodes.new("ShaderNodeMath"); f.operation = 'FRACT'; nt.links.new(a.outputs[0], f.inputs[0])
        s = nt.nodes.new("ShaderNodeMath"); s.operation = 'SUBTRACT'; nt.links.new(f.outputs[0], s.inputs[0]); s.inputs[1].default_value = 0.5
        ab = nt.nodes.new("ShaderNodeMath"); ab.operation = 'ABSOLUTE'; nt.links.new(s.outputs[0], ab.inputs[0])
        lt = nt.nodes.new("ShaderNodeMath"); lt.operation = 'LESS_THAN'; nt.links.new(ab.outputs[0], lt.inputs[0]); lt.inputs[1].default_value = half_w / 10
        return lt.outputs[0]
    vx, hy = line(sep.outputs["X"]), line(sep.outputs["Y"])
    cmb = nt.nodes.new("ShaderNodeCombineColor")
    # R = 竖线，B = 横线，G = 两者都有一点（便于肉眼看）
    nt.links.new(vx, cmb.inputs[0]); nt.links.new(hy, cmb.inputs[2])
    g = nt.nodes.new("ShaderNodeMath"); g.operation = 'MAXIMUM'; nt.links.new(vx, g.inputs[0]); nt.links.new(hy, g.inputs[1])
    gm = nt.nodes.new("ShaderNodeMath"); gm.operation = 'MULTIPLY'; nt.links.new(g.outputs[0], gm.inputs[0]); gm.inputs[1].default_value = 0.35
    nt.links.new(gm.outputs[0], cmb.inputs[1])
    nt.links.new(cmb.outputs[0], em.inputs["Color"]); em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs[0], o.inputs["Surface"])
    tgt.data.materials.append(m)

    def glass(name, ior):
        mt = bpy.data.materials.new(name); mt.use_nodes = True
        b = mt.node_tree.nodes.get("Principled BSDF"); mt.node_tree.nodes.remove(b)
        gl = mt.node_tree.nodes.new("ShaderNodeBsdfGlass")
        gl.inputs["IOR"].default_value = ior; gl.inputs["Roughness"].default_value = 0.0
        mt.node_tree.links.new(gl.outputs[0], mt.node_tree.nodes["Material Output"].inputs["Surface"])
        return mt
    black = bpy.data.materials.new("EdgeBlack"); black.use_nodes = True
    black.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0, 0, 0, 1)
    black.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
    black.node_tree.nodes["Principled BSDF"].inputs["Specular IOR Level"].default_value = 0.0

    s1 = dict(Rx=R1, Ry=lg.INF); s2 = dict(Rx=lg.INF, Ry=lg.INF)
    if variant == "solid":
        lg.build_element("Front_Cyl_L1", s1, s2, Z1, Z2, D, glass("Glass_BK7", N_D), black)
    elif variant == "iface":
        lg.build_interface("L1_S1", s1, Z1, D, glass("IF_air_to_BK7", N_D / 1.0))
        lg.build_interface("L1_S2", s2, Z2, D, glass("IF_BK7_to_air", 1.0 / N_D))

    # 正交相机在像面（z=0），朝 +Z 看
    cd = bpy.data.cameras.new("Sensor"); cd.type = 'ORTHO'; cd.ortho_scale = RES * PIX
    cd.clip_start = 0.1; cd.clip_end = 2000
    cam = bpy.data.objects.new("Sensor", cd); sc.collection.objects.link(cam); sc.camera = cam
    cam.location = (0, 0, 0); cam.rotation_euler = (0, math.pi, 0)   # 朝 +Z；画面左右镜像（x→−u）
    return sc

def measure(png):
    img = bpy.data.images.load(png)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(RES, RES, 4)   # 行 0 = 底部
    row = RES // 2 + 50                       # y = +5 mm（两条横线之间）
    r = px[row, :, 0]
    cols = np.arange(RES)
    on = r > 0.25
    centers, i = [], 0
    while i < RES:
        if on[i]:
            j = i
            while j < RES and on[j]:
                j += 1
            w = r[i:j]; centers.append(float((cols[i:j] * w).sum() / w.sum()))
            i = j
        else:
            i += 1
    # 像素列 → 相机平面 x（相机绕 Y 转 180°，画面向右 = −X）
    xs = sorted([-(c + 0.5 - RES / 2) * PIX for c in centers])
    # 竖线在靶上的 x'：用图像最外两侧（镜片外，不折射）验证比例尺；镜片内与预测比
    return xs

results = {}
for variant in ("none", "solid", "iface"):
    sc = setup(variant)
    path = os.path.join(OUT, f"L1_ior_{variant}.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    results[variant] = measure(path)

# 预测：靶上 x' = ±10, ±20, … 对应相机平面 x0
pred = {}
for k in range(1, 6):
    x0 = inverse_trace(10.0 * k)
    if x0 is not None:
        pred[10 * k] = round(x0, 3)

def inside(xs):  # 镜片孔径内、x>0 的测量线位置
    return [round(x, 3) for x in xs if 0.5 < x < D / 2 - 0.5]

report = dict(
    ZT=round(ZT, 3), f_thin=round(F_THIN, 2),
    predicted_x0_for_target_line=pred,
    measured_inside_solid=inside(results["solid"]),
    measured_inside_iface=inside(results["iface"]),
    measured_inside_none=inside(results["none"]),
    squeeze_paraxial=round(trace(0.5) / 0.5, 4),
)
print("REPORT " + json.dumps(report, ensure_ascii=False))
