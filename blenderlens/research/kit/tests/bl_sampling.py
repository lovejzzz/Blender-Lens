# 采样效率（B1 出瞳重要性采样、B2 连续波长色散）：无偏 + 降噪。
# 均匀白色环境下渲染：重要性采样开 / 关的各区域平均亮度应一致（< 1%；覆盖不全会让该区域变暗），
# 同 spp 下比较噪声（相对标准差，对 4×4 块均值做比较以去掉渐晕梯度）。
# 运行：Blender -b --factory-startup --python tests/bl_sampling.py -- [preset.json ...]
import os, sys, json, time
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig
OUT = os.path.join(ROOT, "renders")
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PRESETS = argv or ["c_series_style.json", "panavision_rear_134_tele.json"]
W_PX = 240

sc = bpy.context.scene
for ob in list(sc.objects):
    bpy.data.objects.remove(ob)
w = sc.world
if w.node_tree is None:
    w.use_nodes = True
bg = w.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (1, 1, 1, 1); bg.inputs["Strength"].default_value = 1.0


def render(cam, spp, importance, seed=1, dispersion=False):
    cc = cam.data.cycles_custom
    cc["pupil_importance"] = int(importance)
    cam.data.blenderlens.dispersion = dispersion
    rig.apply_settings(cam)
    sc.cycles.samples = spp; sc.cycles.seed = seed
    sc.cycles.use_denoising = False; sc.cycles.use_adaptive_sampling = False
    sc.view_settings.view_transform = 'Standard'
    sc.render.image_settings.file_format = 'OPEN_EXR'
    path = os.path.join(OUT, "sampling_tmp.exr")
    sc.render.filepath = path
    t = time.time()
    bpy.ops.render.render(write_still=True)
    dt = time.time() - t
    im = bpy.data.images.load(path, check_existing=False)
    H, W = sc.render.resolution_y, sc.render.resolution_x
    a = np.array(im.pixels[:], dtype=np.float64).reshape(H, W, 4)[::-1, :, :3]
    bpy.data.images.remove(im)
    return a, dt

def regions(a):
    H, W = a.shape[:2]
    m = lambda ys, xs: float(a[ys, xs].mean())
    h, w = H // 8, W // 8
    return dict(center=m(slice(H // 2 - h, H // 2 + h), slice(W // 2 - w, W // 2 + w)),
                edge_x=m(slice(H // 2 - h, H // 2 + h), slice(0, w)),
                edge_y=m(slice(0, h), slice(W // 2 - w, W // 2 + w)),
                corner=m(slice(0, h), slice(0, w)))

def noise(a, ref):
    """相对噪声：与高 spp 参考之差的 RMS / 参考均值（只看有光的像素）。"""
    g, r = a.mean(axis=2), ref.mean(axis=2)
    m = r > 0.02 * r.max()
    return float(np.sqrt(((g - r)[m] ** 2).mean()) / r[m].mean())

rep = {}
for preset in PRESETS:
    cam = bl.build(preset, scene=sc, height=int(W_PX / 2.37), units='MM')
    sc.render.resolution_x = W_PX
    sc.render.resolution_y = int(round(W_PX / rig.desqueezed_resolution(rig.read_prescription(), 1000)[0] * 1000))
    mfd = cam.get("bl_min_focus_mm", 1000.0)
    nf = cam.data.blenderlens.f_number
    for label, focus_m, fnum in (("inf_open", None, nf), ("mfd_open", mfd / 1000.0, nf), ("inf_f8", None, 8.0)):
        bl.set_focus(cam, metres=focus_m); bl.set_stop(cam, fnum)
        ref_on, t_ref = render(cam, 1024, True, seed=7)
        ref_off, _ = render(cam, 1024, False, seed=8)
        r_on, r_off = regions(ref_on), regions(ref_off)
        bias = {k: round(r_on[k] / r_off[k] - 1, 4) for k in r_on if r_off[k] > 1e-4}
        lo_on, t_on = render(cam, 32, True, seed=3)
        lo_off, t_off = render(cam, 32, False, seed=3)
        ref = 0.5 * (ref_on + ref_off)
        n_on, n_off = noise(lo_on, ref), noise(lo_off, ref)
        rep[f"{preset}:{label}"] = dict(bias=bias, regions_off={k: round(v, 4) for k, v in r_off.items()},
                                        noise_32spp_off=round(n_off, 4), noise_32spp_on=round(n_on, 4),
                                        spp_equiv_gain=round((n_off / n_on) ** 2, 2), t_off=round(t_off, 2), t_on=round(t_on, 2))
        print("SAMPLING_ROW", preset, label, json.dumps(rep[f"{preset}:{label}"]), flush=True)
    # B2：连续波长色散的色噪（同 spp，开色散；与旧的三选一相比只能看绝对值，这里记录供版本对比）
    bl.set_focus(cam, metres=None); bl.set_stop(cam, nf)
    d_ref, _ = render(cam, 1024, True, seed=9, dispersion=True)
    d_lo, _ = render(cam, 32, True, seed=3, dispersion=True)
    chroma = d_lo - d_lo.mean(axis=2, keepdims=True) - (d_ref - d_ref.mean(axis=2, keepdims=True))
    rep[f"{preset}:dispersion"] = dict(chroma_noise_32spp=round(float(np.sqrt((chroma ** 2).mean()) / d_ref.mean()), 4),
                                       white_balance=[round(float(v), 4) for v in d_ref.reshape(-1, 3).mean(0) / d_ref.mean()])
    print("SAMPLING_ROW", preset, "dispersion", json.dumps(rep[f"{preset}:dispersion"]), flush=True)
worst = max(abs(v) for k, r in rep.items() if "bias" in r for v in r["bias"].values())
rep["worst_bias"] = worst
rep["PASS"] = worst < 0.01
print("SAMPLING", json.dumps(rep))
