# 同一画面、多种炫光颜色/强度对比：主画面只渲染一次，物理炫光层只算一次，之后只重新着色。
# Blender -b renders/hero_scene.blend --python tools/flare_variants.py
import bpy, os, sys, math
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
if not hasattr(bpy.types.Camera, "blenderlens"):
    blenderlens.register()
from blenderlens import rig, flare as FL, optics as O

sc = bpy.context.scene
cam_ob = sc.camera
p = cam_ob.data.blenderlens
W, H = sc.render.resolution_x, sc.render.resolution_y
OUT = os.path.join(ROOT, "renders")

# 1) 主画面
tmp = os.path.join(OUT, "variants_beauty.exr")
sc.render.image_settings.file_format = 'OPEN_EXR'
sc.render.filepath = tmp
bpy.ops.render.render(write_still=True)
img = bpy.data.images.load(tmp, check_existing=False)
beauty = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)
bpy.data.images.remove(img)

# 2) 每个光源的物理炫光层（未乘灯光颜色）
rx = rig._rx_cached()
dist = math.inf if p.focus_distance <= 0 else p.focus_distance
layers = []
for ob in rig.flare_sources(sc):
    strength, color = rig._emission_of(ob)
    lay, _ = FL.flare_layer(rx, rig._to_lens(cam_ob, ob.matrix_world.translation), strength, 0.5 * max(ob.dimensions),
                            W, H, focus_dist=dist, f_number=p.f_number, coated=p.coated, coating_nm=p.coating_nm, n_grid=300)
    layers.append((lay, color))

# 3) 变体
variants = [("physical", 'PHYSICAL', None, 1.0), ("light_color", 'SOURCE', None, 1.0), ("amber", 'AMBER', None, 1.0),
            ("custom_magenta", 'CUSTOM', (1.0, 0.3, 0.75), 1.0), ("amber_x3", 'AMBER', None, 3.0), ("physical_x0.3", 'PHYSICAL', None, 0.3)]
out = bpy.data.images.new("variant", W, H, float_buffer=True)
for name, mode, tint, gain in variants:
    flare = sum(rig.recolor(l, c, mode, tint or (0.25, 0.55, 1.0)) for l, c in layers) * gain
    px = beauty.copy()
    px[:, :, :3] += flare[::-1].astype(np.float32)
    out.pixels.foreach_set(px.ravel())
    sc.render.image_settings.file_format = 'PNG'
    sc.render.image_settings.color_depth = '16'
    path = os.path.join(OUT, f"hero_flare_{name}.png")
    out.save_render(path, scene=sc)
    print("VARIANT", name, path)
