# 画幅版本（0.8.8 全画幅；0.9.1 起用满片门、加 IMAX 15/70）：python3 tests/test_format.py
#   每个预设的全画幅 / IMAX 版本 = 整支镜头等比放大 k 倍（设计像圈刚好罩住整个传感器 / 片门），光窗 = 整个片门
#   （解挤压后的比例 = 片门宽高比 × 压缩比，每支镜头不同）。验证：
#   · 光窗 = 36 × 24 / 70.41 × 52.63；焦距 × k；压缩比、最大光圈不变；最近对焦 × k；
#   · 物理上对应的位置（原光窗坐标 × k）相对照度逐点相同（差 < 1e-6）；传感器四角照度不低于原设计光窗四角（− 0.08）；
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
    W0, H0 = rx["sensor"]["width"], rx["sensor"]["height"]
    ri_design = CV.coverage_report(rx, 1e7, 4.0, quick=True)["relative_illumination"]["corner"]
    fy0 = O.paraxial_infinity(O.layout(rx), "Ry")[0]
    for fmt in ("FF", "IMAX", "65MM"):
        W, H = O.FORMAT_SIZES[fmt]
        k = O.format_factor(rx, fmt)
        ff = O.to_format(rx, fmt)
        ok(ff["sensor"]["width"] == W and ff["sensor"]["height"] == H and ff["sensor"]["max_gate"] == [W, H],
           f"{name} {fmt}: 用满 {W:g} × {H:g}（解挤压后 {W * O.desqueeze(rx) / H:.2f}:1），k = {k:.4f}")
        if k == 1.0:                                     # 像圈已经罩住这个画幅（原生全画幅的 Sirui、或 65 mm 版用在全画幅上）：不放大，只换光窗
            fy1 = O.paraxial_infinity(O.layout(ff), "Ry")[0]
            ok(abs(fy1 - fy0) < 1e-9 and (abs(W0 - W) < 1e-6 or W0 * H0 > W * H),
               f"{name} {fmt}: 像圈已罩住 {W:g} × {H:g}，不放大（焦距 {fy1:.1f} mm 不变）")
            continue
        fy1 = O.paraxial_infinity(O.layout(ff), "Ry")[0]
        ok(abs(fy1 / (fy0 * k) - 1) < 1e-5 and O.desqueeze(ff) == O.desqueeze(rx)
           and abs(O.nominal_f_number(ff) - O.nominal_f_number(rx)) < 1e-9,
           f"{name} {fmt}: 焦距 {fy0:.1f} → {fy1:.1f} mm，压缩比 {O.desqueeze(ff)}、最大光圈 f/{O.nominal_f_number(ff):.2f} 不变")
        mf0 = rx["focus"]["min_distance_mm"]; mf1 = ff["focus"]["min_distance_mm"]
        ok(abs(mf1 / (mf0 * k) - 1) < 1e-3, f"{name} {fmt}: 最近对焦 {mf0:.0f} → {mf1:.0f} mm")
        # 物理上对应的位置（原光窗坐标 × k）相对照度逐点相同：整支等比放大，追迹只差一个比例
        pts2 = [(u * W0 * k / W, v * H0 * k / H) for u, v in PTS]
        for foc in (math.inf, 3.0 * mf0):
            a = CV.relative_illumination(rx, PTS, foc, None, m=40)
            b = CV.relative_illumination(ff, pts2, foc * k if math.isfinite(foc) else foc, None, m=40)
            ok(max(abs(x - y) for x, y in zip(a, b)) < 1e-6,
               f"{name} {fmt}: 对应位置相对照度逐点相同（对焦 {'∞' if math.isinf(foc) else f'{foc / 1000:.1f} m'}）")
        ri = CV.coverage_report(ff, 1e7 * k, 4.0, quick=True)["relative_illumination"]["corner"]
        ok(ri >= min(ri_design - 0.08, 0.35), f"{name} {fmt}: 传感器四角照度 {ri:.2f}（f/4；原设计光窗四角 {ri_design:.2f}）")
        if O.compensator(rx):
            s0, s1 = squeeze_mfd(rx, mf0), squeeze_mfd(ff, mf1)
            ok(abs(s1 - s0) < 1e-6, f"{name} {fmt}: 最近对焦压缩比 {s0:.4f} → {s1:.4f}（发腮补偿凸轮随缩放）")
        nat = O.prepare(rx, fmt, 0.0, "16:9_NATIVE")
        ok(nat["sensor"]["width"] <= W + 1e-6 and nat["sensor"]["height"] <= H + 1e-6,
           f"{name} {fmt}: 16:9_NATIVE 收进片门 {nat['sensor']['width']:.2f} × {nat['sensor']['height']:.2f} mm")
    if O.compensator(rx):
        half = O.rescale_to_vertical_efl(rx, fy0 * 0.5)
        ok(abs(squeeze_mfd(half, rx["focus"]["min_distance_mm"] * 0.5) - squeeze_mfd(rx, rx["focus"]["min_distance_mm"])) < 1e-6,
           f"{name}: 缩到一半焦距，最近对焦压缩比不变（0.8.8 修正）")
# 0.9.0 回归（MessyRoom 报的全画幅问题）：E / G 全画幅缩到取景 33 mm 时网格能生成（口径不超过曲面范围）；
# PV 长焦端缩过头时，S35 与全画幅的角落视场一致（以前全画幅显示 4.7°）
import meshgen as MG
for pre in ("e_series_style", "g_series_style"):
    r = O.prepare(O.load(os.path.join(O.PRESCRIPTION_DIR, pre + ".json")), "FF", 33 * 24 / 18.37, "16:9_NATIVE")
    for sf in O.layout(r):
        if sf.get("ap"):
            MG.surface_mesh(sf, rings=8, sectors=16, nx=9, ny=3)
    ok(True, f"{pre}: 全画幅版本缩到 43.1 mm 时网格能生成（口径收到曲面范围内）")
pv = O.load(os.path.join(O.PRESCRIPTION_DIR, "panavision_rear_134_tele.json"))
hs = [CV.coverage_report(O.prepare(pv, fm, 33 * gh / 18.37, "16:9_NATIVE"), 7000.0, None)["half_field_deg"]
      for fm, gh in (("S35", 16.4528), ("FF", 24.0))]
ok(abs(hs[0]["current_diag"] - hs[1]["current_diag"]) < 1.0 and hs[1]["current_diag"] > 20,
   f"PV 长焦端缩过头：角落视场 S35 {hs[0]['current_diag']}° / 全画幅 {hs[1]['current_diag']}°（估计值 {hs[1]['current_diag_estimated']}）")
ok(O.to_format(O.load(f), "S35") is not None and O.format_factor(O.load(f), "S35") == 1.0, "S35 = 原设计（不变）")
try:
    O.to_format(O.load(f), "VISTAVISION_X")
    ok(False, "未知画幅应报错")
except ValueError as e:
    ok("FF" in str(e), "未知画幅版本 → ValueError（列出可选值）")
print("FORMAT OK")
