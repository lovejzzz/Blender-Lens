# resolution_percentage ≠ 100% 时 render_frame（Lateral in Post + 炫光）尺寸正确（MessyRoom 报告的 0.5.0 bug）。
# Blender -b --factory-startup --python tests/bl_percentage.py
import bpy, os, sys, math, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "renders"); os.makedirs(OUT, exist_ok=True)
import blenderlens; blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
sc = bpy.context.scene
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
em = bpy.data.materials.new("e"); em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 50000; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
cam = bl.build("c_series_style.json", sc, height=270, units='MM')
cam.location = (0, 0, 0); cam.rotation_euler = (0, 0, 0)
bpy.ops.mesh.primitive_uv_sphere_add(radius=40, location=(600, 250, -5000)); bpy.context.object.data.materials.append(em)
bpy.context.object[rig.FLARE_PROP] = 1
cam.data.blenderlens.dispersion_mode = 'LATERAL_POST'
sc.cycles.samples = 8
res = []
for pct in (100, 50, 37):
    sc.render.resolution_percentage = pct
    fp = os.path.join(OUT, f"pct_{pct}.exr")
    out, used, layer = rig.render_frame(sc, fp, flare=True)
    exp = (int(639 * pct / 100), int(270 * pct / 100))
    good = tuple(out.size) == exp and layer.shape[:2] == (exp[1], exp[0]) and bool(used)
    res.append(good)
    print("PCT_ROW", pct, tuple(out.size), exp, good)
print("PCT", dict(PASS=all(res)))
