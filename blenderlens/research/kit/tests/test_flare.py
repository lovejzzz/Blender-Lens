# 正向炫光通道单测（Blender 外运行，约 1 分钟）：python3 tests/test_flare.py
#   1 自适应铺开不改变总能量
#   2 无镀膜鬼影比 100 nm 镀膜强得多，且颜色中性；100 nm 偏蓝紫
#   3 一字拉丝：鬼影能量集中在过光源的水平窄带内
# 与 Analytic 相机端鬼影的交叉验证（关闭自适应采样、8192 spp）见 README「验证记录」。
import math, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blenderlens"))
import optics as O, flare as FL

def ok(cond, msg):
    assert cond, msg
    print("ok ", msg)

rx = O.load()
W, H = 480, 203
src = (-2600.0, 700.0, 40000.0)                       # 镜头坐标：画面右上方偏中

# 1. 能量守恒：同一组光线，自适应铺开 vs 直接双线性
lens = FL.Lens(rx)
P, D, wt = FL._source_rays(lens, src, 120)
a, b = lens.pairs()[20]
idx, X, Y, w, Dout = lens.trace(P, D, FL.Lens.seq_ghost(len(lens.S), a, b), 0.55, False, 0.0, 1.38)
plain = np.zeros((H, W, 3)); FL._splat(plain, X, Y, w * wt[idx], lens.sensor, 1)
buckets = {}; FL._splat_adaptive(buckets, X, Y, w * wt[idx], idx, 120, lens.sensor, 1, (H, W))
adapt = sum(buf if kk == 0 else FL._blur_fft(buf, 0.5 * 2.0 ** (kk // 8) if kk // 8 else 0, 0.5 * 2.0 ** (kk % 8) if kk % 8 else 0)
            for kk, buf in buckets.items())
inside = (plain.sum() > 0)
ok(inside and abs(adapt.sum() / plain.sum() - 1) < 0.03, f"adaptive splat conserves energy ({adapt.sum() / plain.sum():.4f})")

# 2 + 3
res = {}
for label, coated, nm in (("c100", True, 100.0), ("unc", False, 0.0)):
    img, info = FL.flare_layer(rx, src, 4.0e6, 60.0, W, H, coated=coated, coating_nm=nm, n_grid=140)
    res[label] = (img, info)
g100 = res["c100"][0].sum(); gunc = res["unc"][0].sum()
ok(gunc / g100 > 10, f"uncoated ghosts ≫ coated ({gunc / g100:.1f}×)")
rgb = res["unc"][0].sum(axis=(0, 1)); rgb /= rgb.sum()
ok(np.abs(rgb - 1 / 3).max() < 0.01, f"uncoated ghosts neutral {rgb.round(3).tolist()}")
rgb = res["c100"][0].sum(axis=(0, 1)); rgb /= rgb.sum()
ok(rgb[2] > rgb[0] > rgb[1], f"100 nm coating ghosts blue-violet {rgb.round(3).tolist()}")
# 光源像点（近轴）
Sf = O.layout(rx)
fx = O.paraxial_infinity(Sf, "Rx")[0]; fy = O.paraxial_infinity(Sf, "Ry")[0]
sx = -fx * src[0] / src[2]; sy = -fy * src[1] / src[2]
cy = int(round((0.5 + sy / 18.6) * H)); cx = int(round((sx / 22.0 + 0.5) * W))
for label in ("c100", "unc"):
    lum = res[label][0].sum(axis=2)
    band = lum[max(0, cy - 3):cy + 4].sum() / lum.sum()
    frac = 7 / H
    ok(band / frac > 5, f"{label}: streak — {band:.2f} of ghost energy in ±3 px row band ({band / frac:.1f}× uniform)")
print("ALL FLARE TESTS PASSED")
