# Blender 内冒烟测试：Blender -b --factory-startup --python tests/bl_smoke.py
import bpy, os, sys, math, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
from blenderlens import rig, optics

bpy.ops.wm.read_factory_settings(use_empty=True)
blenderlens.register() if not hasattr(bpy.types.Camera, "blenderlens") else None
res = bpy.ops.blenderlens.build(height=540)
cam_ob = bpy.context.scene.camera
cam = cam_ob.data
rep = dict(op=list(res), camera=cam_ob.name, type=cam.type,
           params=sorted(cam.cycles_custom.keys()),
           n_objects=len(bpy.data.collections["BlenderLens"].all_objects),
           resolution=(bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y))
# 每个光学面网格合法、法线方向
bad = []
for ob in bpy.data.collections["BL_LensGeometry"].objects:
    me = ob.data
    if me.validate(verbose=False):
        bad.append(("invalid", ob.name))
    if "bl_surface" in ob:
        sgn = ob["bl_normal_sign"]
        nz = [sgn * p.normal.z for p in me.polygons if p.material_index == 0]
        if min(nz) <= 0:
            bad.append(("normal", ob.name, min(nz)))
        if ob.active_material["bl_ior"] < 1.0:
            bad.append(("ior<1", ob.name))
rep["mesh_problems"] = bad
# 对焦同步
cam.blenderlens.focus_distance = 1000.0
unit = next(c for c in cam_ob.children_recursive if c.name.startswith("BL_Unit"))
front = next(c for c in cam_ob.children_recursive if c.name.startswith("BL_Front"))
d1, d2 = optics.solve_focus(rig.read_prescription(), 1000.0)
rep["focus_sync"] = dict(unit_z=round(unit.location.z, 4), front_z=round(front.location.z, 4),
                         expect=(round(d1, 4), round(d1 + d2, 4)),
                         osl=(round(cam.cycles_custom["delta_unit"], 4), round(cam.cycles_custom["delta_front"], 4)))
cam.blenderlens.mode = 'GEOMETRY'
rep["geom_visible"] = not bpy.data.collections["BL_LensGeometry"].hide_render
rep["mode_param"] = cam.cycles_custom["mode"]
print("SMOKE " + json.dumps(rep, ensure_ascii=False))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT, "renders", "smoke.blend"))
