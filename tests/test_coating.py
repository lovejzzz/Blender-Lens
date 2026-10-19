# 镀膜（0.8 多层膜）：特征矩阵法对照解析公式与教科书数值；宽带膜让鬼影变弱。纯 Python：python3 tests/test_coating.py
import math, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.optics as O
import blenderlens.flare as FL

def ok(cond, msg):
    print(("ok  " if cond else "FAIL") + "  " + msg)
    if not cond:
        raise SystemExit(1)

R = O.stack_reflectance
c = np.linspace(0.15, 1.0, 40)
# 单层：与 Airy 薄膜公式（0.7 及以前的实现）逐点一致，两个入射方向
err = max(float(np.abs(R(n1, n2, c, lam, O.coating_layers("SINGLE", 100, 1.38, 1.62)) - FL._fresnel_film(n1, 1.38, n2, c, 100, lam)).max())
          for lam in (0.45, 0.55, 0.65) for n1, n2 in ((1.0, 1.62), (1.62, 1.0)))
ok(err < 1e-12, f"single layer = Airy formula (max |ΔR| {err:.1e})")
r0 = float(R(1.0, 1.52, 1.0, 0.55, []))
ok(abs(r0 - 0.0426) < 5e-4, f"uncoated n=1.52 normal incidence {100 * r0:.2f}% (≈4.26%)")
r1 = float(R(1.0, 1.52, 1.0, 0.552, O.coating_layers("SINGLE", 100, 1.38, 1.52)))
ok(1.2e-2 < r1 < 1.32e-2, f"MgF₂ quarter-wave on 1.52 at design λ {100 * r1:.2f}% (Macleod ≈1.26%)")
lam = np.arange(0.420, 0.681, 0.005)
for ng in (1.52, 1.62, 1.75, 1.95):
    bb = R(1.0, ng, 1.0, lam, O.coating_layers("BROADBAND", 94, 1.38, ng))
    sg = R(1.0, ng, 1.0, lam, O.coating_layers("SINGLE", 100, 1.38, ng))
    mid = (lam >= 0.45) & (lam <= 0.65)
    # 高折射率玻璃上单层 MgF₂ 本来就接近理想（√n_glass ≈ 1.38 时零反射），宽带膜只要求更好
    ok(bb[mid].mean() < 0.003 and bb.max() < 0.016 and bb[mid].mean() < (0.35 if ng < 1.65 else 0.8) * sg[mid].mean(),
       f"broadband on n={ng}: mean 450–650 {100 * bb[mid].mean():.2f}% (single {100 * sg[mid].mean():.2f}%), max 420–680 {100 * bb.max():.2f}%")
# 互易：空气侧入射角 θa 与玻璃侧对应折射角 θg 的反射率相同
L = O.coating_layers("BROADBAND", 94, 1.38, 1.7)
for deg in (0, 20, 40, 60):
    ca = math.cos(math.radians(deg)); sg_ = math.sin(math.radians(deg)) / 1.7
    a, b = float(R(1.0, 1.7, ca, 0.5, L)), float(R(1.7, 1.0, math.sqrt(1 - sg_ * sg_), 0.5, L))
    ok(abs(a - b) < 1e-12, f"reciprocity at {deg}°: {a:.6f} = {b:.6f}")
ok(float(R(1.7, 1.0, math.cos(math.radians(40)), 0.5, L)) == 1.0, "total internal reflection from the glass side → 1")
# 鬼影：宽带膜比单层弱、颜色不同
rx = O.load(os.path.join(O.PRESCRIPTION_DIR, "neil_2x_50.json"))
src = (900.0, 300.0, 9000.0)
E = {}
for kind, nm in (("SINGLE", 100.0), ("BROADBAND", 94.0)):
    img, info = FL.flare_layer(rx, src, 4.0e6, 60.0, 240, 101, coated=True, coating_nm=nm, coating_type=kind, n_grid=70)
    E[kind] = np.array(info["ghost_energy"])
ratio = E["SINGLE"].sum() / max(E["BROADBAND"].sum(), 1e-30)
ok(ratio > 2.0, f"Neil 50: broadband ghosts {ratio:.1f}× weaker than single MgF₂")
rgb_s, rgb_b = E["SINGLE"] / E["SINGLE"].sum(), E["BROADBAND"] / E["BROADBAND"].sum()
ok(np.abs(rgb_s - rgb_b).max() > 0.03, f"ghost colour changes with the stack: single {rgb_s.round(3).tolist()} broadband {rgb_b.round(3).tolist()}")
print("ALL COATING TESTS PASSED")
