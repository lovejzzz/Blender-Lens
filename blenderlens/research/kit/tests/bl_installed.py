# 在「干净配置 + 从 zip 安装的扩展」环境里验证：启用、搭建、渲染一张小图。
# 由 tests/install_test.sh 调用；不要在项目源码路径下 import blenderlens。
import bpy, sys, os, json, addon_utils
import numpy as np

mod = "bl_ext.user_default.blenderlens"
rep = {"enabled_before": mod in bpy.context.preferences.addons.keys()}
if not rep["enabled_before"]:
    bpy.ops.preferences.addon_enable(module=mod)
rep["enabled"] = mod in bpy.context.preferences.addons.keys()
pkg = sys.modules[mod]
rep["package_file"] = pkg.__file__
rep["not_from_source_tree"] = "ClaudeCodeProject" not in pkg.__file__

bpy.ops.wm.read_homefile(use_empty=True)
r = bpy.ops.blenderlens.build(height=120)
sc = bpy.context.scene
cam_ob = sc.camera
cam_ob.location = (0, 0, 0); cam_ob.rotation_euler = (0, 0, 0)
# 简单场景：一盏发光球在画面中心偏右上
bpy.ops.mesh.primitive_uv_sphere_add(radius=200, location=(600, 250, -5000))
m = bpy.data.materials.new("E"); nt = m.node_tree if m.node_tree else None
m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
e = nt.nodes.new("ShaderNodeEmission"); o = nt.nodes.new("ShaderNodeOutputMaterial"); nt.links.new(e.outputs[0], o.inputs[0])
bpy.context.object.data.materials.append(m)
sc.cycles.samples = 16
out = os.path.join(os.environ["BL_TEST_OUT"], "installed_render.png")
sc.render.filepath = out
bpy.ops.render.render(write_still=True)
img = bpy.data.images.load(out)
px = np.array(img.pixels[:]).reshape(img.size[1], img.size[0], 4)
ys, xs = np.nonzero(px[..., 0] > 0.5)
rep.update(build=list(r), resolution=list(img.size), lit_pixels=int(len(xs)),
           lit_centroid_uv_from_top_left=None if not len(xs) else (round(float(xs.mean()) / img.size[0], 3), round(1 - float(ys.mean()) / img.size[1], 3)),
           squeeze=round(pkg.rig.squeeze_info(cam_ob), 4))
import blenderlens.api as bl                      # 稳定入口：不写安装仓库名
rep["api_module"] = bl.__name__
rep["api_lens_info_squeeze"] = round(bl.lens_info(cam_ob)["squeeze"], 4)
rep["units"] = cam_ob.get("bl_units")
# 为命令行 GPU 测试存一个 .blend（install_test.sh 里用 -b … --python-expr "bl.enable_gpu()" -f 1 渲染）
sc.cycles.device = 'CPU'
sc.render.filepath = os.path.join(os.environ["BL_TEST_OUT"], "installed_cpu.png")
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.environ["BL_TEST_OUT"], "installed_gpu.blend"))
print("INSTALLED " + json.dumps(rep, ensure_ascii=False))
