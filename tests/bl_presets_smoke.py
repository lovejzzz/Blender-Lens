# 每个预设：搭建、OSL 编译、网格合法、法线方向、对焦同步、最近对焦边缘不相撞。
# Blender -b --factory-startup --python tests/bl_presets_smoke.py
import bpy, os, sys, math, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
from blenderlens import rig, optics as O

out = {}
for fn, name, _ in O.list_presets():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not hasattr(bpy.types.Camera, "blenderlens"):
        blenderlens.register()
    r = bpy.ops.blenderlens.build(preset=fn, height=270)
    cam_ob = bpy.context.scene.camera
    cam = cam_ob.data
    rx = rig.read_prescription()
    bad = []
    for ob in bpy.data.collections["BL_LensGeometry"].objects:
        if ob.data.validate(verbose=False):
            bad.append(("invalid", ob.name))
        if "bl_surface" in ob:
            nz = [ob["bl_normal_sign"] * p.normal.z for p in ob.data.polygons if p.material_index == 0]
            if nz and min(nz) <= 0:
                bad.append(("normal", ob.name, round(min(nz), 3)))
    mfd = cam_ob["bl_min_focus_mm"]
    cam.blenderlens.focus_distance = mfd
    d1, d2 = O.solve_focus(rx, mfd)
    movers = {tuple(c["bl_move"]): c for c in cam_ob.children_recursive if "bl_move" in c}
    sync = all(abs(v.location.z - (k[0] * d1 + k[1] * d2)) < 1e-6 for k, v in movers.items())
    osl = (abs(cam.cycles_custom["delta_unit"] - d1) < 1e-6) and (abs(cam.cycles_custom["delta_front"] - d2) < 1e-6)
    clash = [e["between"] for e in O.edge_report(rx, d1, d2) if e["min_gap"] < 0]
    out[fn] = dict(op=list(r), preset=cam_ob["bl_preset"], res=(bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y),
                   n_surfaces=sum(1 for o in bpy.data.objects if "bl_surface" in o), mesh_problems=bad,
                   f_number=round(cam.blenderlens.f_number, 2), mfd=mfd, focus_sync=sync and osl, clashes_at_mfd=clash,
                   squeeze_inf=round(O.squeeze_at(rx, math.inf), 4), squeeze_mfd=round(O.squeeze_at(rx, mfd), 4))
print("PRESETS " + json.dumps(out, ensure_ascii=False))
