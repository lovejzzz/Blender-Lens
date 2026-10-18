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
movers = {tuple(c["bl_move"]): c for c in cam_ob.children_recursive if "bl_move" in c}
d1, d2 = optics.solve_focus(rig.read_prescription(), 1000.0)
rep["focus_sync"] = dict(movers={f"{k}": round(v.location.z, 4) for k, v in movers.items()},
                         expect={f"{k}": round(k[0] * d1 + k[1] * d2, 4) for k in movers},
                         osl=(round(cam.cycles_custom["delta_unit"], 4), round(cam.cycles_custom["delta_front"], 4)))
cam.blenderlens.mode = 'GEOMETRY'
rep["geom_visible"] = not bpy.data.collections["BL_LensGeometry"].hide_render
rep["mode_param"] = cam.cycles_custom["mode"]
print("SMOKE " + json.dumps(rep, ensure_ascii=False))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT, "renders", "smoke.blend"))
