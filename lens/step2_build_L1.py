# 第 2 步：单片柱面镜 Front_Cyl_L1 —— 生成 + 数值检查 + 形状图
# 运行：Blender -b --factory-startup --python lens/step2_build_L1.py
import bpy, sys, os, math, json
from mathutils import Vector
sys.path.insert(0, os.path.dirname(__file__))
import lensgen as lg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "renders")

# 空场景 + 毫米单位
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.unit_settings.system = 'METRIC'
sc.unit_settings.scale_length = 0.001
sc.unit_settings.length_unit = 'MILLIMETERS'

glass = bpy.data.materials.new("Glass");      glass.diffuse_color = (0.55, 0.75, 0.95, 1)
black = bpy.data.materials.new("EdgeBlack");  black.diffuse_color = (0.02, 0.02, 0.02, 1)

ob = lg.build_element("Front_Cyl_L1",
        s1=dict(Rx=-120.0, Ry=lg.INF), s2=dict(Rx=lg.INF, Ry=lg.INF),
        z1=91.5, z2=85.0, D=72.0, mat_glass=glass, mat_black=black)
me = ob.data

# —— 数值检查 ——
rings, sectors = 128, 192
nv_surf = 1 + rings * sectors
nf_surf = sectors + (rings - 1) * sectors
chk = {}
chk["invalid_geometry_fixed"] = me.validate(verbose=False)   # False = 原本就合法
chk["counts"] = dict(verts=len(me.vertices), faces=len(me.polygons),
                     expect_faces=2 * nf_surf + sectors)
# 按材质 / 面段统计几何面法线（绕序）方向
def face_stats(idx):
    zs = [me.polygons[i].normal.z for i in idx]
    return dict(n=len(zs), min_nz=round(min(zs), 4), max_nz=round(max(zs), 4))
front = range(0, nf_surf)
back = range(nf_surf, 2 * nf_surf)
side = range(2 * nf_surf, 2 * nf_surf + sectors)
chk["front_face_nz"] = face_stats(front)     # 期望全 > 0（朝物方）
chk["back_face_nz"] = face_stats(back)       # 期望全 < 0（实体外法线）
radial = [me.polygons[i].normal.x * me.polygons[i].center.x + me.polygons[i].normal.y * me.polygons[i].center.y
          for i in side]
chk["side_radial_outward_min"] = round(min(radial), 4)   # 期望 > 0
chk["material_index"] = dict(front=sorted({me.polygons[i].material_index for i in front}),
                             back=sorted({me.polygons[i].material_index for i in back}),
                             side=sorted({me.polygons[i].material_index for i in side}))
# 自定义法线 vs 几何面法线：每个角点点积应 > 0（否则 Cycles 着色法线与进出判定打架）
me.update()
cn = me.corner_normals
worst = 1.0
for p in me.polygons:
    for li in p.loop_indices:
        d = Vector(cn[li].vector).dot(p.normal)
        worst = min(worst, d)
chk["min_dot_custom_vs_face"] = round(worst, 4)
# 形状：中心与边缘 z（Rx<0 → 曲率中心在物方 → 前表面凹，边缘应更靠 +Z）
vz = lambda i: me.vertices[i].co.z
chk["front_center_z"] = round(vz(0), 4)
chk["front_edge_z_at_x=+36"] = round(vz(1 + (rings - 1) * sectors), 4)
chk["front_edge_z_at_y=+36"] = round(vz(1 + (rings - 1) * sectors + sectors // 4), 4)
chk["back_center_z"] = round(vz(nv_surf), 4)
chk["sag_theory_at_36"] = round(lg.sag(36, 0, -1/120, 0), 4)
chk["center_thickness"] = round(vz(0) - vz(nv_surf), 4)
chk["edge_thickness_x"] = round(vz(1 + (rings - 1) * sectors) - vz(nv_surf + 1 + (rings - 1) * sectors), 4)
print("CHECK " + json.dumps(chk, ensure_ascii=False))

# —— 形状图（Workbench，材质色，对象轮廓）——
sc.render.engine = 'BLENDER_WORKBENCH'
sh = sc.display.shading
sh.light = 'STUDIO'; sh.color_type = 'MATERIAL'
sh.show_cavity = True; sh.show_object_outline = True; sh.show_specular_highlight = True
sc.render.resolution_x, sc.render.resolution_y = 1600, 1000
sc.render.film_transparent = False
world = bpy.data.worlds.new("W"); sc.world = world; world.color = (0.18, 0.18, 0.2)

cam_data = bpy.data.cameras.new("Cam"); cam_data.clip_start = 1; cam_data.clip_end = 5000
cam = bpy.data.objects.new("Cam", cam_data); sc.collection.objects.link(cam); sc.camera = cam
target = Vector((0, 0, 88.25))
def shoot(loc, fname, ortho=None):
    cam.location = Vector(loc)
    cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
    if ortho:
        cam_data.type = 'ORTHO'; cam_data.ortho_scale = ortho
    else:
        cam_data.type = 'PERSP'; cam_data.lens = 50
    sc.render.filepath = os.path.join(OUT, fname)
    bpy.ops.render.render(write_still=True)

shoot((150, -170, 200), "L1_3q_objectside.png")          # 物方斜上：看凹面与侧壁
shoot((0, -400, 88.25), "L1_profile_XZ_from-Y.png", ortho=95)  # 侧视：x 方向弯、y 方向直
shoot((400, 0, 88.25), "L1_profile_YZ_from+X.png", ortho=95)   # 侧视：应为平行矩形

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT, "L1_test.blend"))
