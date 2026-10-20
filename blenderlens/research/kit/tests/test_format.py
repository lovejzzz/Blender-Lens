# 全画幅版本（0.8.8）：python3 tests/test_format.py
#   每个预设的全画幅版本 = 整支镜头连同光窗等比放大 k 倍（光窗放进 36 × 24 mm 的最大倍数）。验证：
#   · 光窗在 36 × 24 以内且贴边（高 24 或宽 36）；焦距 × k；压缩比、最大光圈不变；最近对焦 × k；
#   · 光窗上同一相对位置的相对照度（与 Analytic 渲染同一估计器）与原设计相同（差 < 1e-6）——覆盖与渐晕逐点相同；
#   · 发腮补偿：最近对焦时的压缩比与原设计相同（0.8.8 修正：缩放时凸轮表 Δ1 × k、φ ÷ k；以前缩到一半焦距时 C 是 1.80 而不是 2.01）；
#   · 16:9_NATIVE 光窗在全画幅版本上收进 36 × 24（原生满像圈的光窗可能比处方光窗高）。
import glob, math, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blenderlens"))
import optics as O
import coverage as CV


def ok(cond, msg):
    assert cond, msg
    print("ok ", msg)


def squeeze_mfd(rx, obj):
    S = O.layout(rx, *O.solve_focus(rx, obj))
    L = obj - S[0]["z"]
    return O.paraxial_finite(S, "Ry", L)[1] / O.paraxial_finite(S, "Rx", L)[1]


PTS = [(0.0, 0.0), (0.95, 0.0), (0.0, 0.95), (0.95, 0.95), (-0.6, 0.5)]
for f in sorted(glob.glob(os.path.join(O.PRESCRIPTION_DIR, "*.json"))):
    name = os.path.basename(f)[:-5]
    rx = O.load(f)
    ff = O.to_format(rx, "FF")
    k = ff["meta"]["format"]["factor"]
    W, H = ff["sensor"]["width"], ff["sensor"]["height"]
    ok(W <= 36.0 + 1e-6 and H <= 24.0 + 1e-6 and (abs(W - 36.0) < 1e-3 or abs(H - 24.0) < 1e-3),
       f"{name}: 全画幅光窗 {W:.2f} × {H:.2f} mm（k = {k:.4f}）")
    fy0 = O.paraxial_infinity(O.layout(rx), "Ry")[0]
    fy1 = O.paraxial_infinity(O.layout(ff), "Ry")[0]
    ok(abs(fy1 / (fy0 * k) - 1) < 1e-5 and O.desqueeze(ff) == O.desqueeze(rx)
       and abs(O.nominal_f_number(ff) - O.nominal_f_number(rx)) < 1e-9,
       f"{name}: 焦距 {fy0:.1f} → {fy1:.1f} mm，压缩比 {O.desqueeze(ff)}、最大光圈 f/{O.nominal_f_number(ff):.2f} 不变")
    mf0 = rx["focus"]["min_distance_mm"]; mf1 = ff["focus"]["min_distance_mm"]
    ok(abs(mf1 / (mf0 * k) - 1) < 1e-3, f"{name}: 最近对焦 {mf0:.0f} → {mf1:.0f} mm")
    for foc in (math.inf, 3.0 * mf0):
        a = CV.relative_illumination(rx, PTS, foc, None, m=40)
        b = CV.relative_illumination(ff, PTS, foc * k if math.isfinite(foc) else foc, None, m=40)
        ok(max(abs(x - y) for x, y in zip(a, b)) < 1e-6,
           f"{name}: 相对照度逐点相同（对焦 {'∞' if math.isinf(foc) else f'{foc / 1000:.1f} m'}）：{[round(x, 3) for x in a]}")
    if O.compensator(rx):
        s0, s1 = squeeze_mfd(rx, mf0), squeeze_mfd(ff, mf1)
        ok(abs(s1 - s0) < 1e-6, f"{name}: 最近对焦压缩比 {s0:.4f} → {s1:.4f}（发腮补偿凸轮随缩放）")
        half = O.rescale_to_vertical_efl(rx, fy0 * 0.5)
        ok(abs(squeeze_mfd(half, mf0 * 0.5) - s0) < 1e-6, f"{name}: 缩到一半焦距，最近对焦压缩比不变（0.8.8 修正）")
    nat = O.prepare(rx, "FF", 0.0, "16:9_NATIVE")
    ok(nat["sensor"]["width"] <= 36.0 + 1e-6 and nat["sensor"]["height"] <= 24.0 + 1e-6,
       f"{name}: 全画幅 16:9_NATIVE 光窗 {nat['sensor']['width']:.2f} × {nat['sensor']['height']:.2f} mm")
ok(O.to_format(O.load(f), "S35") is not None and O.format_factor(O.load(f), "S35") == 1.0, "S35 = 原设计（不变）")
try:
    O.to_format(O.load(f), "IMAX")
    ok(False, "未知画幅应报错")
except ValueError as e:
    ok("FF" in str(e), "未知画幅版本 → ValueError（列出可选值）")
print("FORMAT OK")
