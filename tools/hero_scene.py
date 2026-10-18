# 展示场景：夜景街道 —— 对焦主体 + 纵深暖色灯泡（椭圆焦外/猫眼）+ 远处车灯（一字拉丝）
# Blender -b --factory-startup --python tools/hero_scene.py -- [height] [samples] [coating_nm|look] [preset.json]
# 主体距离按预设的垂直焦距等比放置（50 mm → 2.2 m），使主体在画面里大小相近。
import bpy, os, sys, math, random
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
from blenderlens import rig

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HEIGHT = int(args[0]) if len(args) > 0 else 720
SAMPLES = int(args[1]) if len(args) > 1 else 768
COATING = args[2] if len(args) > 2 else "look"
PRESET = args[3] if len(args) > 3 else "c_series_style.json"
random.seed(7)

bpy.ops.wm.read_factory_settings(use_empty=True)
blenderlens.register()
bpy.ops.blenderlens.build(preset=PRESET, height=HEIGHT)
sc = bpy.context.scene
cam_ob = sc.camera
EYE = 1450.0                                   # 机位高度（mm）
from blenderlens import optics as _O
_rx = rig.read_prescription()
FY = _O.paraxial_infinity(_O.layout(_rx), "Ry")[0]
FX = _O.paraxial_infinity(_O.layout(_rx), "Rx")[0]
SW2, SH2 = _rx["sensor"]["width"] / 2, _rx["sensor"]["height"] / 2
# 以 C 系列风格（50 mm、S35 变形 18.6 mm 高、主体 2.2 m）为基准，保持主体在画面里的高度占比与横纵位置占比
SUBJ = max(2200.0 * (FY / 50.0) * (9.3 / SH2), _rx["focus"].get("min_distance_mm", 0.0))   # 主体到像面距离
XOFF = -0.431 * SW2 * SUBJ / FX                 # C 系列风格：−420 mm @ 2.2 m 对应画面半宽的 43%
ZOFF = 0.293 * SH2 * SUBJ / FY                  # C 系列风格：+120 mm 对应画面半高的 29%
DY = SUBJ - 2200.0         # 主体与它的灯沿视线平移：主体所在平面的物方视场宽度 ∝ 距离 / 焦距，基本不变
# 世界：Z 向上；相机绕 X 转 90° → 视线 = 世界 +Y、画面上方 = +Z
cam_ob.location = (0.0, 0.0, EYE)
cam_ob.rotation_euler = (math.radians(90.0), 0.0, 0.0)

def mat_emit(name, color, strength):
    m = bpy.data.materials.new(name)
    nt = m.node_tree if m.node_tree else None
    m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission"); o = nt.nodes.new("ShaderNodeOutputMaterial")
    e.inputs["Color"].default_value = (*color, 1); e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs[0], o.inputs[0])
    return m

def mat_surface(name, color, rough, metallic=0.0, coat=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Coat Weight"].default_value = coat
    return m

def sphere(loc, r, mat, seg=24):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc, segments=seg, ring_count=seg // 2)
    ob = bpy.context.object; ob.data.materials.append(mat); bpy.ops.object.shade_smooth()
    return ob

# 地面：湿沥青（低粗糙度 + 清漆）
bpy.ops.mesh.primitive_plane_add(size=200000, location=(0, 60000, 0))
bpy.context.object.data.materials.append(mat_surface("Asphalt", (0.006, 0.006, 0.007), 0.3, coat=0.3))

# 主体：Suzanne 立在细柱上，2.2 m
bpy.ops.mesh.primitive_monkey_add(size=380, location=(XOFF, SUBJ, EYE + ZOFF))
suz = bpy.context.object
suz.rotation_euler = (math.radians(6), 0, math.radians(24))           # 默认朝 −Y = 朝相机；略转向画面右侧的车灯
sub = suz.modifiers.new("sub", 'SUBSURF'); sub.levels = 2; sub.render_levels = 2
bpy.ops.object.shade_smooth()
suz.data.materials.append(mat_surface("Clay", (0.62, 0.5, 0.42), 0.48))
bpy.ops.mesh.primitive_cylinder_add(radius=18, depth=EYE + ZOFF - 190, location=(XOFF, SUBJ, (EYE + ZOFF - 190) / 2))
bpy.context.object.data.materials.append(mat_surface("Steel", (0.5, 0.5, 0.52), 0.25, metallic=1.0))

# 灯光：左前方暖色柔光主光 + 右后方冷色轮廓光
def area(loc, size, energy, color, target, spread=180.0):
    ld = bpy.data.lights.new("A", 'AREA'); ld.size = size; ld.energy = energy; ld.color = color
    ld.spread = math.radians(spread)
    ob = bpy.data.objects.new("A", ld); sc.collection.objects.link(ob); ob.location = loc
    d = (target[0] - loc[0], target[1] - loc[1], target[2] - loc[2])
    import mathutils
    ob.rotation_euler = mathutils.Vector(d).to_track_quat('-Z', 'Y').to_euler()
key = area((XOFF - 1280, SUBJ - 1200, EYE + ZOFF + 480), 700, 5.5e6, (1.0, 0.8, 0.62), (XOFF, SUBJ, EYE + ZOFF), spread=35.0)
rim = area((XOFF + 1120, SUBJ + 1300, EYE + ZOFF + 330), 400, 1.6e7, (0.55, 0.75, 1.0), (XOFF, SUBJ, EYE + ZOFF), spread=40.0)

# 背景灯：两侧路灯串（暖），纵深 8–45 m
warm = mat_emit("Bulb", (1.0, 0.5, 0.18), 50.0)
teal = mat_emit("Neon", (0.2, 0.8, 0.85), 45.0)
for side in (-1, 1):
    for k in range(14):
        y = 8000 + k * 2700 + random.uniform(-500, 500)
        x = side * (4200 + random.uniform(-400, 400))
        z = 3600 + random.uniform(-300, 600)
        sphere((x, y, z), 70, warm, 12)
for k in range(26):
    y = random.uniform(9000, 46000)
    x = random.uniform(-9000, 9000)
    if abs(x) < 1600 and y < 14000:
        continue
    z = random.uniform(600, 5200)
    sphere((x, y, z), random.uniform(35, 70), teal if random.random() < 0.22 else warm, 10)

# 车灯：远处一对，略偏右 → 一字拉丝
head = mat_emit("Headlight", (0.92, 0.95, 1.0), 2.5e4)
for dx in (-750, 750):
    sphere((2300 + dx, 30000, 750), 90, head, 16)[rig.FLARE_PROP] = 1

w = bpy.data.worlds.new("Night"); sc.world = w
rig.black_world(w)
for n in w.node_tree.nodes:
    if n.type == 'BACKGROUND':
        n.inputs["Color"].default_value = (0.004, 0.006, 0.012, 1); n.inputs["Strength"].default_value = 1.0

p = cam_ob.data.blenderlens
p.focus_distance = SUBJ
p.dispersion = True
p.ghosts = False                                # 炫光由正向通道合成（确定性、无噪点）
if COATING != "look":                           # 否则用预设 look 的光圈 / 镀膜 / 炫光
    p.coating_nm = float(COATING)
rig.render_preset(sc)
sc.cycles.samples = SAMPLES
sc.cycles.use_denoising = True
sc.cycles.denoiser = 'OPENIMAGEDENOISE'
sc.view_settings.view_transform = 'AgX'
sc.view_settings.look = 'AgX - Medium High Contrast'
sc.view_settings.exposure = 0.0
TAG = PRESET.replace(".json", "")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT, "renders", f"hero_scene_{TAG}.blend"))
out = os.path.join(ROOT, "renders", f"hero_{TAG}_{HEIGHT}p.png")
img, used, layer = rig.render_with_flare(sc, out, n_grid=300)
print("HERO done", out, used)
