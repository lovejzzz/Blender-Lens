# 线性 EXR → 看图用 PNG（曝光倍数 + Reinhard + sRGB）。
# Blender -b --factory-startup --python tools/exr_to_png.py -- in.exr out.png exposure
import bpy, sys
import numpy as np
a = sys.argv[sys.argv.index("--") + 1:]
src, dst, ev = a[0], a[1], float(a[2])
im = bpy.data.images.load(src)
W, H = im.size
px = np.array(im.pixels[:], dtype=np.float32).reshape(H, W, 4)
x = px[..., :3] * ev
x = x / (1.0 + x)
x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.clip(x, 0, None), 1 / 2.4) - 0.055)
out = bpy.data.images.new("o", W, H)
out.pixels.foreach_set(np.concatenate([np.clip(x, 0, 1), np.ones((H, W, 1), np.float32)], axis=2).ravel())
out.filepath_raw = dst; out.file_format = 'PNG'; out.save()
