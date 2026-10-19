# 外部数据回归（0.8）：每个预设对照公开的厂方数据 / 文献描述（不只是对照专利自身）。纯 Python：python3 tests/test_reference.py
# 出处见 blenderlens/research/ROADMAP.md「调研摘要 B」。数值容差按来源的精度取（多数只是定性描述）。
import copy, math, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.optics as O

def ok(cond, msg):
    print(("ok  " if cond else "FAIL") + "  " + msg)
    if not cond:
        raise SystemExit(1)

P = lambda fn: O.load(os.path.join(O.PRESCRIPTION_DIR, fn))
FRONT = ("c_series_style.json", "e_series_style.json", "g_series_style.json", "neil_2x_50.json", "neil_2x_65.json", "neil_2x_85.json")
REAR = ("panavision_rear_134.json", "panavision_rear_134_tele.json")

def mfd(rx):
    return rx.get("focus", {}).get("min_distance_mm") or rx["look"]["min_focus_mm"]

def field_tan(rx, dist):
    """画面 90% 处主光线在物方的方向斜率（x、y）：对焦到 dist 时的视场。"""
    d1, d2 = O.solve_focus(rx, dist); S = O.layout(rx, d1, d2); zs, _ = O.stop_geometry(rx, S)
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    out = []
    for pt, ax in (((0.45 * W, 0.0), 0), ((0.0, 0.45 * H), 1)):
        p, q = O.trace_reverse(S, (pt[0], pt[1], 0.0), O.aim_through_stop(S, pt, (0.0, 0.0), zs), clip=False)
        out.append(abs(q[ax] / q[2]))
    return out

# 1. 最近对焦距离 = Panavision 公布值（C 50 mm 76.2 cm、E 135 mm 114.3 cm、G 100 mm 91.4 cm）
for fn, ref in (("c_series_style.json", 762.0), ("e_series_style.json", 1143.0), ("g_series_style.json", 914.0)):
    ok(abs(mfd(P(fn)) - ref) < 0.5, f"{fn}: close focus {mfd(P(fn)):.0f} mm = Panavision published {ref:.0f} mm")

# 2. 发腮：ARRI/ZEISS Master Anamorphic 数据表（2016-04）各焦距最近对焦时 H/V 放大率之比 1.97–2.01（校正过的现代镜头）。
#    默认（完全补偿）的 C/E/G 风格在最近对焦处压缩比与无穷远相差 < 1.5%；Neil（专利原样，未加补偿）6–7%
#    （介于 ARRI 的 ±1.5% 与 Laowa 示意的未校正 10% 之间）
for fn in FRONT:
    rx = P(fn)
    r = O.squeeze_at(rx, mfd(rx)) / O.squeeze_at(rx, math.inf)
    lim = 0.015 if O.compensator(rx) else 0.08
    ok(abs(r - 1) < lim, f"{fn}: squeeze at close focus / ∞ = {r:.4f} (|Δ| < {100 * lim:.1f}%)")
for fn in REAR:
    rx = P(fn)
    r = O.squeeze_at(rx, mfd(rx)) / O.squeeze_at(rx, math.inf)
    ok(abs(r - 1) < 1e-3, f"{fn}: rear anamorphic, no mumps ({r:.5f})")

# 3. 焦外形状：前置柱面（柱面在被摄体与光阑之间）→ 背景焦外是竖椭圆（Neil / FDTimes 2015；ASC Practical Optics）；
#    后置变形（Panavision 1.34×）→ 接近圆（「rear-anamorphic zooms do not have oval bokehs」）
for fn in FRONT:
    rx = P(fn)
    a = O.bokeh_aspect(rx, mfd(rx), 1e7)["aspect_desqueezed"]
    ok(a > 1.6, f"{fn}: background bokeh oval (desqueezed h/w {a:.2f} > 1.6)")
for fn in REAR:
    rx = P(fn)
    a = O.bokeh_aspect(rx, mfd(rx), 1e7)["aspect_desqueezed"]
    ok(0.8 < a < 1.25, f"{fn}: background bokeh near round (h/w {a:.2f})")

# 4. 呼吸方向：经典前置变形对焦时主要是垂直方向的视场变化（Panavision E 系列「vertical focus breathing」；
#    Hawk / Vantage：classic anamorphics breathe mostly vertically）。只对带补偿、模仿 Panavision 的 C/E/G 风格断言
for fn in ("c_series_style.json", "e_series_style.json", "g_series_style.json"):
    rx = P(fn)
    f0, f1 = field_tan(rx, math.inf), field_tan(rx, mfd(rx))
    bx, by = f1[0] / f0[0] - 1, f1[1] / f0[1] - 1
    ok(abs(by) > 2 * abs(bx), f"{fn}: breathing mostly vertical (x {100 * bx:+.1f}%, y {100 * by:+.1f}%)")
    r0 = copy.deepcopy(rx); r0["focus"]["compensator"]["level"] = 0.0
    g0, g1 = field_tan(r0, math.inf), field_tan(r0, mfd(r0))
    print(f"    info: 关掉补偿时 x {100 * (g1[0] / g0[0] - 1):+.1f}%、y {100 * (g1[1] / g0[1] - 1):+.1f}%（1950 年代式：水平为主）")
print("ALL REFERENCE TESTS PASSED")
