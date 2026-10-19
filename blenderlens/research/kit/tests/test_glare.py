# 杂光（0.8）：合成 ISO 9358 测试（均匀白场 + 中央黑斑）测出的 VGI = 设定值；不环绕；能量。纯 Python：python3 tests/test_glare.py
import math, os, sys, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.flare as FL

def ok(cond, msg):
    print(("ok  " if cond else "FAIL") + "  " + msg)
    if not cond:
        raise SystemExit(1)

def vgi_test(H, W, vgi):
    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.hypot(yy - (H - 1) / 2, xx - (W - 1) / 2)
    R = 0.5 * FL.VG_SPOT_FRAC * H
    img = np.repeat((rr > R).astype(float)[..., None], 3, axis=2)
    t = time.time()
    out = FL.veiling_glare(img, vgi)
    dt = time.time() - t
    spot = out[rr < 0.3 * R].mean()
    field = out[(rr > 1.5 * R) & (rr < 2.0 * R)].mean()
    return spot / field, dt

for H, W in ((135, 332), (1080, 2560), (2160, 5120)):
    for vgi in (0.005, 0.025, 0.08):
        m, dt = vgi_test(H, W, vgi)
        ok(abs(m / vgi - 1) < 0.10, f"{W}×{H}: target VGI {vgi:.3f} → measured {m:.4f} ({dt:.2f} s)")
# 不环绕：右边缘的亮点不应把核心光晕送到左边缘
H, W = 540, 1280
img = np.zeros((H, W, 3)); img[H // 2 - 1:H // 2 + 1, W - 3:W - 1] = 1000.0
out = FL.veiling_glare(img, 0.025)
near = out[H // 2, W - 12].mean(); far_left = out[H // 2, 2].mean()
ok(far_left < 0.02 * near, f"no wrap-around: left edge {far_left:.2e} ≪ 10 px from source {near:.2e}")
# 能量：画面中央的点光源，输出总能量 ≤ 输入（落到画面外的丢掉），且损失不大
img = np.zeros((H, W, 3)); img[H // 2, W // 2] = 1000.0
out = FL.veiling_glare(img, 0.025)
ratio = out.sum() / img.sum()
ok(0.97 < ratio <= 1.0 + 1e-9, f"energy: centre point source keeps {ratio:.4f} of its energy")
ok(np.allclose(FL.veiling_glare(img, 0.0), img), "vgi = 0 → unchanged")
print("ALL GLARE TESTS PASSED")
