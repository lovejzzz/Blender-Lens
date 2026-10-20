# 真实场景测试：外滩夜景广场 —— Poly Haven 实扫大理石胸像 + 街灯 + 消防栓 + 湿鹅卵石地面 + 外滩 HDRI。
# 所有预设统一到同一垂直焦距（默认 100 mm），同一机位、同一对焦距离，只换镜头。
# 资源（CC0，Poly Haven）放在 assets/，不随仓库提交。
# Blender -b --factory-startup --python tools/real_scene.py -- <preset.json> [focal_mm] [height] [samples] [hdri_rot_deg] [gate] [format]
#   focal_mm 0 = 预设原生焦距；gate 默认 FULL（0.9：README 图用 16:9_NATIVE）；format S35 / FF（0.8.8）
import bpy, os, sys, math
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
from blenderlens import rig, optics as O

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PRESET = args[0] if len(args) > 0 else "c_series_style.json"
FOCAL = float(args[1]) if len(args) > 1 else 100.0
HEIGHT = int(args[2]) if len(args) > 2 else 720
SAMPLES = int(args[3]) if len(args) > 3 else 256
HDRI_ROT = float(args[4]) if len(args) > 4 else 61.0
GATE = args[5] if len(args) > 5 else "FULL"
FMT = args[6] if len(args) > 6 else "S35"
A = os.path.join(ROOT, "assets")

EYE = 1400.0          # 机位高度（mm）
SUBJ = 4600.0         # 胸像到像面距离（mm），对焦于此
PLINTH_H = 1120.0     # 底座高度（胸像底部高度）

bpy.ops.wm.read_factory_settings(use_empty=True)
blenderlens.register()
GPU = os.environ.get("BL_GPU") == "1"                 # BL_GPU=1：用 BlenderLens GPU 路径（Metal）渲染
if GPU:
    import blenderlens.api as _bl
    print("GPU", _bl.enable_gpu(use_gpu=False))
bpy.ops.blenderlens.build(preset=PRESET, height=HEIGHT, focal_mm=FOCAL, gate=GATE, format=FMT)
sc = bpy.context.scene
cam_ob = sc.camera
cam_ob.location = (0.0, 0.0, EYE)
cam_ob.rotation_euler = (math.radians(90.0), 0.0, 0.0)        # 视线 = 世界 +Y、上 = +Z


def import_gltf(name, loc, rot_z_deg=0.0, scale=1.0, keep=lambda o: True):
    """glTF 导入器已按场景单位（mm）换算，scale 只用于额外缩放。"""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(A, name, f"{name}_2k.gltf"))
    new = [o for o in bpy.data.objects if o not in before]
    drop = [o for o in new if not keep(o)]
    new = [o for o in new if keep(o)]
    for o in drop:
        bpy.data.objects.remove(o)
    roots = [o for o in new if o.parent is None]
    for r in roots:
        r.scale = tuple(v * scale for v in r.scale)
        r.location = loc
        r.rotation_mode = 'XYZ'                       # glTF 导入为四元数，改成欧拉角再转向
        r.rotation_euler = (r.rotation_euler[0], r.rotation_euler[1], r.rotation_euler[2] + math.radians(rot_z_deg))
    return new


def principled(name, base=(0.5, 0.5, 0.5), rough=0.5, metallic=0.0):
    m = bpy.data.materials.new(name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*base, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metallic
    return m


def emission(name, color, strength):
    m = bpy.data.materials.new(name)
    nt = m.node_tree; nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission"); o = nt.nodes.new("ShaderNodeOutputMaterial")
    e.inputs["Color"].default_value = (*color, 1); e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs[0], o.inputs[0])
    return m


# ── 世界：外滩夜景 HDRI（绕 Z 旋转，把陆家嘴天际线转到镜头正后方）──
w = bpy.data.worlds.new("Bund"); sc.world = w
nt = w.node_tree; nt.nodes.clear()
tc = nt.nodes.new("ShaderNodeTexCoord"); mp = nt.nodes.new("ShaderNodeMapping")
env = nt.nodes.new("ShaderNodeTexEnvironment"); bg = nt.nodes.new("ShaderNodeBackground"); out = nt.nodes.new("ShaderNodeOutputWorld")
env.image = bpy.data.images.load(os.path.join(A, "shanghai_bund", "shanghai_bund_4k.hdr"))
mp.inputs["Rotation"].default_value = (0, 0, math.radians(HDRI_ROT))
bg.inputs["Strength"].default_value = 0.3
nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"]); nt.links.new(mp.outputs[0], env.inputs[0])
nt.links.new(env.outputs[0], bg.inputs[0]); nt.links.new(bg.outputs[0], out.inputs[0])

# ── 地面：湿鹅卵石（Poly Haven cobblestone_floor_04 贴图，2 m 一块，粗糙度压低模拟雨后）──
bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 60000, 0))
ground = bpy.context.object
ground.scale = (120000, 140000, 1)
bpy.ops.object.transform_apply(scale=True)
ground.location = (0, 60000, 0)                       # 从相机后方 10 m 延伸到前方 130 m
gm = bpy.data.materials.new("WetCobble"); gnt = gm.node_tree
p = gnt.nodes["Principled BSDF"]
tco = gnt.nodes.new("ShaderNodeTexCoord"); mpp = gnt.nodes.new("ShaderNodeMapping")
mpp.inputs["Scale"].default_value = (1 / 2000.0, 1 / 2000.0, 1)
gnt.links.new(tco.outputs["Object"], mpp.inputs["Vector"])
def tex(fn, noncolor=False):
    t = gnt.nodes.new("ShaderNodeTexImage")
    t.image = bpy.data.images.load(os.path.join(A, "cobblestone_floor_04", "textures", fn))
    if noncolor:
        t.image.colorspace_settings.name = "Non-Color"
    gnt.links.new(mpp.outputs[0], t.inputs[0])
    return t
diff = tex("cobblestone_floor_04_diff_2k.jpg"); rough = tex("cobblestone_floor_04_rough_2k.jpg", True); nor = tex("cobblestone_floor_04_nor_gl_2k.jpg", True)
dark = gnt.nodes.new("ShaderNodeMixRGB"); dark.blend_type = 'MULTIPLY'; dark.inputs[0].default_value = 1.0
dark.inputs[2].default_value = (0.45, 0.45, 0.47, 1)                      # 湿了更暗
gnt.links.new(diff.outputs[0], dark.inputs[1]); gnt.links.new(dark.outputs[0], p.inputs["Base Color"])
rmul = gnt.nodes.new("ShaderNodeMath"); rmul.operation = 'MULTIPLY'; rmul.inputs[1].default_value = 0.35   # 雨后低粗糙度
gnt.links.new(rough.outputs[0], rmul.inputs[0]); gnt.links.new(rmul.outputs[0], p.inputs["Roughness"])
nmap = gnt.nodes.new("ShaderNodeNormalMap"); nmap.inputs["Strength"].default_value = 0.8
gnt.links.new(nor.outputs[0], nmap.inputs["Color"]); gnt.links.new(nmap.outputs[0], p.inputs["Normal"])
p.inputs["Coat Weight"].default_value = 0.6; p.inputs["Coat Roughness"].default_value = 0.08
ground.data.materials.append(gm)

# ── 主体：实扫大理石胸像（Poly Haven marble_bust_01）放在石质底座上，略偏左 ──
BX = -330.0
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(BX, SUBJ, PLINTH_H / 2))
plinth = bpy.context.object; plinth.scale = (420, 420, PLINTH_H)
plinth.data.materials.append(principled("Plinth", (0.06, 0.055, 0.05), 0.55))
bust = import_gltf("marble_bust_01", (BX, SUBJ, PLINTH_H), rot_z_deg=18.0)

# ── 街灯：右后方 28 m，灯头进画面；灯泡里放一个发光小球作为炫光光源 ──
LX, LY = 2600.0, SUBJ + 23500.0
lamp = import_gltf("street_lamp_01", (LX, LY, 0.0), rot_z_deg=90.0)
for o in lamp:
    for slot in o.material_slots:
        if slot.material and slot.material.name.startswith("street_lamp_01_bulb"):
            slot.material = emission("LampBulb", (1.0, 0.72, 0.42), 60.0)
bpy.context.view_layer.update()
lamp_top = max((o.matrix_world @ __import__("mathutils").Vector(c)).z for o in lamp if o.type == 'MESH' for c in o.bound_box)
bulb_z = lamp_top - 420.0
bpy.ops.mesh.primitive_uv_sphere_add(radius=55.0, location=(LX, LY, bulb_z), segments=24, ring_count=12)
src = bpy.context.object; src.name = "FlareSource_Lamp"
src.data.materials.append(emission("LampSource", (1.0, 0.74, 0.46), 9000.0))
src[rig.FLARE_PROP] = 1
pl = bpy.data.lights.new("LampLight", 'POINT'); pl.energy = 3.0e9; pl.color = (1.0, 0.72, 0.45); pl.shadow_soft_size = 60
plo = bpy.data.objects.new("LampLight", pl); sc.collection.objects.link(plo); plo.location = (LX, LY, bulb_z - 150)

# ── 中景：做旧消防栓，右侧 9 m（100 mm 的垂直视场只有 ±5.3°，近处低矮物体进不了画面）──
hyd = import_gltf("fire_hydrant", (1050.0, 9000.0, 0.0), rot_z_deg=-25.0, keep=lambda o: o.name.endswith("_aged") or o.type != 'MESH')

# ── 真实纵深：两侧矮街灯（灯泡约 1.55 m，与机位同高，从近到远都在画面里）、灯串、长椅、汽车、前景灯笼 ──
import mathutils
def bulbify(objs, mat_prefix, color, strength):
    for o in objs:
        for slot in o.material_slots:
            if slot.material and slot.material.name.startswith(mat_prefix):
                slot.material = emission(mat_prefix + "_on", color, strength)

def link_copy(objs, loc, rot_z_deg):
    """链接复制（共享网格）一组导入物体到新位置。"""
    roots = [o for o in objs if o.parent is None]
    for r in roots:
        c = r.copy(); sc.collection.objects.link(c)
        c.location = loc; c.rotation_mode = 'XYZ'; c.rotation_euler = (r.rotation_euler[0], r.rotation_euler[1], math.radians(rot_z_deg))
        for ch in [o for o in objs if o.parent is r]:
            cc = ch.copy(); sc.collection.objects.link(cc); cc.parent = c

warm = (1.0, 0.70, 0.40)
row_y = [7000, 11000, 16000, 23000, 32000, 45000, 62000, 85000, 115000]
lamp2 = import_gltf("street_lamp_02", (-3500.0, row_y[0], 0.0), rot_z_deg=90.0)
bulbify(lamp2, "street_lamp_02_bulb", warm, 160.0)
for i, y in enumerate(row_y):
    for side in (-1, 1):
        if i == 0 and side == -1:
            continue
        link_copy(lamp2, (side * (3500.0 + 150.0 * (i % 2)), y, 0.0), 90.0 if side < 0 else -90.0)
# 远处两盏高街灯（60 m、95 m）
for (x, y) in ((-4200.0, 60000.0), (4600.0, 95000.0)):
    link_copy(lamp, (x, y, 0.0), 90.0)

# 灯串：三道横跨步道（12 m、25 m、45 m）+ 左上前景一小段（2.4 m）
bulb_mat = emission("StringBulb", (1.0, 0.76, 0.48), 60.0)
def string_lights(y, x0, x1, z_end, sag, spacing=350.0, r=16.0):
    n = max(2, int(abs(x1 - x0) / spacing))
    for k in range(n + 1):
        t = k / n
        x = x0 + (x1 - x0) * t
        z = z_end - sag * 4 * t * (1 - t)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=(x, y, z), segments=10, ring_count=6)
        bpy.context.object.data.materials.append(bulb_mat)
for (y, z_end, sag) in ((12000.0, 2350.0, 420.0), (25000.0, 2700.0, 600.0), (45000.0, 3200.0, 800.0)):
    string_lights(y, -5200.0, 5200.0, z_end, sag)
string_lights(2400.0, -1000.0, -330.0, 1630.0, 80.0, spacing=120.0, r=9.0)      # 前景

# 中景家具：长椅、汽车
bench = import_gltf("painted_wooden_bench", (-2300.0, 8200.0, 0.0), rot_z_deg=90.0)
link_copy(bench, (2400.0, 14500.0, 0.0), -90.0)
car = import_gltf("covered_car", (5000.0, 20000.0, 0.0), rot_z_deg=0.0)

# 前景灯笼：主体右前方 2.2 m、与视线同高，里面一团暖光（大的前景焦外）
lant = import_gltf("wooden_lantern_01", (360.0, 2100.0, 1180.0), rot_z_deg=20.0)
bpy.ops.mesh.primitive_uv_sphere_add(radius=38.0, location=(360.0, 2100.0, 1180.0 + 230.0), segments=16, ring_count=8)
bpy.context.object.data.materials.append(emission("LanternFlame", (1.0, 0.62, 0.30), 180.0))
ll = bpy.data.lights.new("LanternLight", 'POINT'); ll.energy = 2.5e7; ll.color = (1.0, 0.62, 0.3); ll.shadow_soft_size = 40
llo = bpy.data.objects.new("LanternLight", ll); sc.collection.objects.link(llo); llo.location = (360.0, 2100.0, 1180.0 + 230.0)

# 模型自带的「glass」材质导入后不透明，会挡住灯泡 → 统一换成磨砂玻璃
frosted = bpy.data.materials.new("FrostedGlass")
fn_ = frosted.node_tree; fn_.nodes.clear()
gb = fn_.nodes.new("ShaderNodeBsdfGlass"); go = fn_.nodes.new("ShaderNodeOutputMaterial")
gb.inputs["IOR"].default_value = 1.5; gb.inputs["Roughness"].default_value = 0.12
fn_.links.new(gb.outputs[0], go.inputs[0])
for o in bpy.data.objects:
    for slot in getattr(o, "material_slots", []):
        if slot.material and "glass" in slot.material.name.lower() and not slot.material.name.startswith("BL_"):
            slot.material = frosted

# ── 主体灯光：左前方暖色主光 + 右后方冷色轮廓光（与外滩夜色呼应）──
def area(name, loc, size, energy, color, target, spread=45.0):
    ld = bpy.data.lights.new(name, 'AREA'); ld.size = size; ld.energy = energy; ld.color = color; ld.spread = math.radians(spread)
    ob = bpy.data.objects.new(name, ld); sc.collection.objects.link(ob); ob.location = loc
    ob.rotation_euler = (mathutils.Vector(target) - mathutils.Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
head = (BX, SUBJ, PLINTH_H + 330.0)
area("Key", (BX - 1600, SUBJ - 1500, PLINTH_H + 1100), 900, 6.0e6, (1.0, 0.82, 0.64), head, 40)
area("Rim", (BX + 1300, SUBJ + 1400, PLINTH_H + 800), 500, 9.0e6, (0.55, 0.72, 1.0), head, 35)
area("Fill", (BX + 1500, SUBJ - 2500, PLINTH_H + 300), 1500, 6.0e5, (0.6, 0.7, 0.9), head, 90)

# ── 镜头设置：对焦主体，各预设自己的光圈 / 镀膜 / 炫光（look）──
p = cam_ob.data.blenderlens
p.focus_distance = SUBJ
p.dispersion = True
p.ghosts = False
rig.render_preset(sc)
sc.cycles.samples = SAMPLES
if GPU:
    _pr = bpy.context.preferences.addons["cycles"].preferences
    _pr.compute_device_type = 'METAL'; _pr.get_devices()
    for _d in _pr.devices:
        _d.use = _d.type == 'METAL'
    sc.cycles.device = 'GPU'
sc.cycles.use_denoising = True
sc.cycles.denoiser = 'OPENIMAGEDENOISE'
sc.cycles.denoising_prefilter = 'ACCURATE'
sc.cycles.denoising_input_passes = 'RGB_ALBEDO_NORMAL'
sc.cycles.diffuse_bounces = 3
sc.cycles.glossy_bounces = 4
sc.view_settings.view_transform = 'AgX'
sc.view_settings.look = 'AgX - Medium High Contrast'
sc.view_settings.exposure = 0.3

tag = PRESET.replace(".json", "") + ("" if GATE == "FULL" else "_" + GATE.replace(":", "x")) + ("" if FMT == "S35" else "_" + FMT)
os.makedirs(os.path.join(ROOT, "renders", "real"), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT, "renders", "real", f"real_{tag}_{FOCAL:g}mm.blend"))
out = os.path.join(ROOT, "renders", "real", f"real_{tag}_{FOCAL:g}mm_{HEIGHT}p.png")
img, used, layer = rig.render_with_flare(sc, out, n_grid=240)
print("REAL done", out, cam_ob["bl_preset"], used, "f/%g" % p.f_number)
