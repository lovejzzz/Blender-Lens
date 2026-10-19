# AI 原生接口（0.8.1）：describe / state / configure / diagnose / preview / contact_sheet 都能用、返回可 JSON 序列化、
# 校验会拒绝错误输入且不改任何东西、诊断能发现典型问题、预览与对比图写出。
# 运行：Blender -b --factory-startup --python tests/bl_agent.py
import os, sys, json, math
import bpy
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
OUT = os.path.join(ROOT, "renders", "agent")
os.makedirs(OUT, exist_ok=True)
checks = {}
def check(name, cond, info=None):
    checks[name] = bool(cond) if info is None else {"ok": bool(cond), "info": info}

d = bl.describe()
json.dumps(d, ensure_ascii=False)
check("describe_json", True)
check("describe_version", d["version"] and d["version"].count(".") == 2, d["version"])
check("describe_presets", len(d["presets"]) >= 8 and all("squeeze_inf" in p for p in d["presets"]))
check("describe_settings", {"f_number", "mumps_correction", "coating_type", "focus_m"} <= set(d["settings"]))
check("describe_enum_items", "BROADBAND" in d["settings"]["coating_type"]["items"])
check("describe_api", "configure" in d["api"] and "signature" in d["api"]["build"])

sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
check("diagnose_no_camera", [i["code"] for i in bl.diagnose()] == ["no_camera"])
cam = bl.build("c_series_style.json", scene=sc, height=135, units='MM')
s = bl.state(cam)
json.dumps(s)
check("state_json", s["settings"]["mumps_correction"] == 1.0 and s["lens"]["preset"], s["lens"]["preset"])

# configure：合法 → 生效；不合法 → 抛错且不改
ch = bl.configure(cam, f_number=4.0, focus_m=2.0, coating_type='BROADBAND', mumps_correction=0.5)
st = bl.state(cam)
check("configure_applies", st["settings"]["f_number"] == 4.0 and abs(st["lens"]["focus_mm"] - 2000.0) < 1e-6
      and st["settings"]["coating_type"] == 'BROADBAND' and abs(st["lens"]["mumps_correction"] - 0.5) < 1e-9, ch)
for bad in (dict(f_number=40.0), dict(coating_type="TRIPLE"), dict(fnumber=2.8), dict(f_number=2.8, ghosts="maybe_not_a_bool_but_ok", nope=1)):
    before = bl.state(cam)["settings"]
    try:
        bl.configure(cam, **bad); raised = False
    except ValueError as e:
        raised = True; msg = str(e)
    after = bl.state(cam)["settings"]
    check(f"configure_rejects_{'_'.join(bad)}", raised and before == after, msg[:120] if raised else None)
neil = None

# diagnose：故意制造问题
sc.render.resolution_x = 100
sc.cycles.samples = 16; sc.cycles.use_denoising = False
codes = [i["code"] for i in bl.diagnose()]
check("diagnose_resolution", "resolution_not_desqueezed" in codes, codes)
check("diagnose_dispersion_noise", "dispersion_noise" in codes, codes)
from blenderlens import rig
sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), 135)
codes = [i["code"] for i in bl.diagnose()]
check("diagnose_fixed", "resolution_not_desqueezed" not in codes, codes)

# 预览 + 对比图
rig.black_world(sc.world)
em = bpy.data.materials.new("e")
if em.node_tree is None:
    em.use_nodes = True
em.node_tree.nodes.clear()
o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
e.inputs[1].default_value = 30.0; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
for x in (-300, 0, 300):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(x, 0, -6000)); bpy.context.object.data.materials.append(em)
cam.location = (0, 0, 0); cam.rotation_euler = (0, 0, 0)
bl.configure(cam, focus_m=2.0, f_number=2.3)
pv = bl.preview(os.path.join(OUT, "preview.png"), samples=16, height=135, flare=False)
check("preview", os.path.exists(pv["path"]) and pv["stats"]["max"] > 0 and pv["stats"]["size"][1] == 135, pv["stats"])
before = bl.state(cam)["settings"]
cs = bl.contact_sheet(os.path.join(OUT, "sheet.png"), [dict(f_number=2.3), dict(f_number=8.0), dict(mumps_correction=0.0)],
                      samples=16, height=135, flare=False)
after = bl.state(cam)["settings"]
check("contact_sheet", os.path.exists(cs["path"]) and cs["grid"] == [1, 3] and len(cs["tiles"]) == 3, cs["grid"])
check("contact_sheet_restores", before == after)
m0, m1 = cs["tiles"][0]["stats"]["mean_rgb"][1], cs["tiles"][1]["stats"]["mean_rgb"][1]
check("contact_sheet_differs", abs(m0 - m1) > 1e-6, [m0, m1])
rep = dict(checks=checks, PASS=all((v if isinstance(v, bool) else v["ok"]) for v in checks.values()))
print("AGENT", json.dumps(rep, ensure_ascii=False))
