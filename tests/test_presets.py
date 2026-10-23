# 预设处方单测（Blender 外运行）：python3 tests/test_presets.py
#   E/G 风格（Isco US 5,184,880 实施例 3）：凸轮对焦与压缩比变化 vs 专利对焦表
#   Panavision 后置 1.34×（US 7,085,066）：焦距 vs 专利；非球面梯度
import math, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blenderlens")); sys.path.insert(0, os.path.join(ROOT, "tools"))
import optics as O
import build_presets as BP

def ok(cond, msg):
    assert cond, msg
    print("ok ", msg)

P = lambda fn: O.load(os.path.join(O.PRESCRIPTION_DIR, fn))

# ── Isco：对焦表（用专利自己的 f=100 BK7 主镜，距离从焦面量起）──
import copy
rx = copy.deepcopy(P("e_series_style.json"))
rx["focus"]["compensator"]["level"] = 0.0             # 专利对焦表是未补偿的 Isco 凸轮
rx["glasses"]["BK7"] = {"nd": 1.5168, "vd": 64.17}
rx["groups"][next(i for i, g in enumerate(rx["groups"]) if g["id"] == "prime")] = {"id": "prime", "scale": 1.0, "stop": {"after_surface": "B1", "offset_fraction": 0.5, "f_number": 2.8},
                   "surfaces": [{"id": "B1", "R_x": 51.68, "R_y": 51.68, "d": 18.9, "medium": "BK7"},
                                {"id": "B2", "R_x": None, "R_y": None, "d": None, "medium": "air"}]}
rx["focus"]["groups"][0]["ids"] = ["I10", "I11", "I12", "B1", "B2"]
S = O.layout(rx)
fx, fy = O.paraxial_infinity(S, "Rx")[0], O.paraxial_infinity(S, "Ry")[0]
ok(abs(fx / 62.71 - 1) < 0.005 and abs(fy / 131.00 - 1) < 0.001, f"Isco: Fx {fx:.2f} / Fy {fy:.2f} vs patent 62.71 / 131.00")
sq_inf = O.squeeze_at(rx, math.inf)
E3_inf, E9_inf, f_inf = BP.ISCO_FOCUS_TABLE[0][1:]
worst = (0, 0, 0)
for dist_m, E3, E9, dpct in BP.ISCO_FOCUS_TABLE[1:]:
    d1, d2 = O.solve_focus(rx, dist_m * 1000.0)
    dE3, dE9 = -d2, d2 - d1                    # I4–I9 移动 Δ2、I10 起移动 Δ1
    pred = sq_inf * (1 + dpct / 100) / (1 + f_inf / 100)
    errs = (abs(dE3 - (E3 - E3_inf)), abs(dE9 - (E9 - E9_inf)), abs(O.squeeze_at(rx, dist_m * 1000.0) / pred - 1))
    worst = tuple(max(a, b) for a, b in zip(worst, errs))
ok(worst[0] < 0.1 and worst[1] < 0.1, f"Isco: cam motion reproduces focus table (11 rows), worst {worst[0]:.3f} / {worst[1]:.3f} mm")
ok(worst[2] < 0.001, f"Isco: squeeze change reproduces focus table, worst {100 * worst[2]:.3f}%")
_e0 = copy.deepcopy(P("e_series_style.json")); _e0["focus"]["compensator"]["level"] = 0.0
sq = O.squeeze_at(_e0, 2500.0) / O.squeeze_at(_e0, math.inf)
print(f"    info: E 系列风格（Nikon 主镜）2.5 m / ∞ 压缩比 = {sq:.4f}；专利表 = {(1 - 0.0632) / 1.0445:.4f}")

# ── Panavision：焦距 ──
rx = P("panavision_rear_134.json")
S = O.layout(rx)
fx, fy = O.paraxial_infinity(S, "Rx")[0], O.paraxial_infinity(S, "Ry")[0]
ok(abs(fx / 15.81 - 1) < 0.001 and abs(fy / 20.60 - 1) < 0.002, f"PV: EFL x {fx:.3f} / y {fy:.3f} vs patent 15.81 / 20.60（Ohara 目录值）")
ok(abs(O.paraxial_infinity(S, "Ry")[1] - 35.754) < 0.1, f"PV: paraxial BFL {O.paraxial_infinity(S, 'Ry')[1]:.3f} vs patent image distance 35.754")
# 物方半视场：像高 13.60 mm 沿 X（专利 42.34°）；整幅：画面边缘张角比 = 2.40
zs0, _ = O.stop_geometry(rx, S)
def obj_tan(pt):
    d = O.aim_through_stop(S, pt, (0, 0), zs0); p, q = O.trace_reverse(S, (pt[0], pt[1], 0), d, clip=False)
    return abs(q[0] / q[2]), abs(q[1] / q[2])
ang = math.degrees(math.atan(obj_tan((13.6, 0))[0]))
ok(abs(ang - 42.34) < 0.5, f"PV: object half-field at 13.6 mm image height {ang:.2f}° vs patent 42.34°")
asp = obj_tan((rx["sensor"]["width"] / 2, 0))[0] / obj_tan((0, rx["sensor"]["height"] / 2))[1]
ok(abs(asp - 2.40) < 0.01, f"PV: frame object-space aspect {asp:.3f} vs patent 2.40:1")
zoom = [s for s in S if s["group"] == "zoom"]
fz = O.paraxial_infinity(zoom, "Ry")[0]
ok(abs(fz / 15.3 - 1) < 0.01, f"PV: zoom alone EFL {fz:.3f} vs patent ≈15.3")
ok(O.stop_geometry(rx, S)[1] == 10.51, "PV: iris semi-diameter 10.51 at f/3.66")

# ── Panavision 内对焦：两向同时合焦、无发腮（短焦与长焦）──
for fn in ("panavision_rear_134.json", "panavision_rear_134_tele.json"):
    r = P(fn)
    for dist in (3000.0, 1000.0):
        ry, rxx = O.focus_residual(r, dist)
        ok(abs(ry) < 1e-6 and abs(rxx) < 1e-3, f"{fn}: focus {dist / 1000:.0f} m residual v {ry:+.5f} h {rxx:+.5f} mm")
    ok(abs(O.squeeze_at(r, 1000.0) / O.squeeze_at(r, math.inf) - 1) < 1e-3, f"{fn}: no mumps (squeeze 1 m / ∞ = {O.squeeze_at(r, 1000.0) / O.squeeze_at(r, math.inf):.5f})")
# 专利 US 6,122,111 TABLE 1：物距从物面量到 S1（S2 前 1 mm，随前组移动），前组行程 F2 11.099、F3 21.684 mm
r = P("panavision_rear_134.json"); z2 = O.layout(r)[0]["z"]
for od, pat, tol in ((330.0, 11.099, 0.02), (133.0, 21.684, 0.002)):
    d = 0.0
    for _ in range(30):
        d = O.solve_focus(r, od + 1.0 + z2 + d)[0]
    ok(abs(d / pat - 1) < tol, f"PV internal focus: travel at {od:.0f} mm from S1 {d:.3f} vs patent {pat}")
for fn in ("panavision_rear_134.json", "panavision_rear_134_tele.json"):
    ry, rxx = O.focus_residual(P(fn), 476.0)
    ok(abs(ry) < 1e-6 and abs(rxx) < 1e-3, f"{fn}: focus at MFD 476 mm residual v {ry:+.5f} h {rxx:+.5f}")
tele = P("panavision_rear_134_tele.json")
import copy as _c
rz = _c.deepcopy(tele); rz["groups"] = [rz["groups"][0]]; rz.pop("image_distance_mm"); rz["focus"]["groups"] = []
fz = O.paraxial_infinity(O.layout(rz), "Ry")[0]
ok(abs(fz / 49.2 - 1) < 0.005, f"PV tele: zoom alone at Z4 EFL {fz:.2f} vs patent 49.2")

# ── Neil / Sirui 2×（CN 111562632 B 表 1–3）：焦距、像距、对焦行程、单自由度两向合焦 ──
for fl, bfl in ((50, 47.576), (65, 43.071), (85, 42.000)):
    r = P(f"neil_2x_{fl}.json")
    S = O.layout(r)
    fy, by = O.paraxial_infinity(S, "Ry")[:2]; fx, bx = O.paraxial_infinity(S, "Rx")[:2]
    ok(abs(fy / fl - 1) < 0.001, f"Neil {fl}: FLY {fy:.3f} vs patent {fl}")
    ok(abs(fx / (fl / 2) - 1) < 0.04, f"Neil {fl}: FLX {fx:.3f} vs patent ≈{fl / 2}")
    ok(abs(by - bfl) < 0.15 and abs(bx - bfl) < 0.15, f"Neil {fl}: paraxial BFL y/x {by:.3f}/{bx:.3f} vs printed {bfl}")
    z1 = S[0]["z"]
    d_fd1 = O.solve_focus(r, 1000002.0 + z1)[0]
    for od, pat in ((1843.0, 3.172), (868.0, 6.391)):
        t = O.solve_focus(r, od + z1)[0] - d_fd1
        ry, rxx = O.focus_residual(r, od + z1)
        ok(abs(t / pat - 1) < 0.05 and abs(rxx) < 0.1, f"Neil {fl}: G1b travel to {od:.0f} mm {t:.3f} vs patent {pat} (X residual {rxx:+.3f})")

# ── 非球面梯度（S3）──
s3 = next(s for s in S if s["id"] == "S3")
for x, y in ((12.0, 5.0), (25.0, -18.0), (-30.0, 20.0)):
    s, gx, gy = O.surf_sag(s3, x, y)
    h = 1e-5
    nx = (O.surf_sag(s3, x + h, y)[0] - O.surf_sag(s3, x - h, y)[0]) / (2 * h)
    ny = (O.surf_sag(s3, x, y + h)[0] - O.surf_sag(s3, x, y - h)[0]) / (2 * h)
    ok(abs(gx - nx) < 1e-6 and abs(gy - ny) < 1e-6, f"PV: asphere S3 gradient @({x},{y})")

# ── 所有预设：边缘不相撞（∞ 与最近对焦）──
for fn, name, _ in O.list_presets():
    rx = P(fn)
    for dist in (math.inf, rx["focus"]["min_distance_mm"]):
        d1, d2 = O.solve_focus(rx, dist)
        bad = [e["between"] for e in O.edge_report(rx, d1, d2) if e["min_gap"] < 0]
        ok(not bad, f"{name} @ {'∞' if math.isinf(dist) else f'{dist / 1000:.2f} m'}: no edge clash")
# ── 发腮补偿（0.8，Auto Panatar 式一阶等效）──
import numpy as np
sys.path.insert(0, ROOT)
import blenderlens.flare as FL
def rms_spot(rx, dist, fx, fy):
    lens = FL.Lens(rx, dist, None); S = lens.S
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    sx, sy = fx * W / 2, fy * H / 2
    pnt, dv = O.trace_reverse(S, (sx, sy, 0.0), O.aim_through_stop(S, (sx, sy), (0.0, 0.0), lens.z_stop), clip=False)
    t = (dist - pnt[2]) / dv[2]
    P_, D_, wt = FL._source_rays(lens, (pnt[0] + t * dv[0], pnt[1] + t * dv[1], dist), 100)
    idx, X, Y, w, Dout = lens.trace(P_, D_, FL.Lens.seq_direct(len(S)), O.LAMBDA_D, True, 0.0, 1.38)
    return math.hypot(X.std(), Y.std())
MFD_TOL = {"c_series_style_ff.json": 1.25, "c_series_style_65.json": 2.1}   # 最近对焦时补偿后最差光斑 / 画高 ≤ 原版 C × 这个倍数（65 mm 版水平视场宽，近摄时水平 0.7 处约软一倍，HANDOFF §6.14）
def lev_c(v):
    r = copy.deepcopy(P("c_series_style.json")); r["focus"]["compensator"]["level"] = v
    return r
for fn in ("c_series_style.json", "e_series_style.json", "g_series_style.json", "c_series_style_ff.json", "c_series_style_65.json"):
    base = P(fn)
    mfd = base["focus"]["min_distance_mm"]
    def lev(v):
        r = copy.deepcopy(base); r["focus"]["compensator"]["level"] = v
        return r
    s_inf = O.squeeze_at(base, math.inf)
    s0, s5, s1 = (O.squeeze_at(lev(v), mfd) for v in (0.0, 0.5, 1.0))
    s1b = O.squeeze_at(lev(1.0), 2.0 * mfd)
    ok(abs(s1 / s_inf - 1) < 0.002 and abs(s1b / s_inf - 1) < 0.002 and s0 < s5 < s1,
       f"{fn}: mumps correction — squeeze at MFD {s0:.3f} (0) → {s5:.3f} (0.5) → {s1:.3f} (1), ∞ {s_inf:.3f}")
    ok(max(abs(v) for v in O.focus_residual(lev(1.0), mfd)) < 1e-6, f"{fn}: both axes in focus at MFD with full correction")
    clash = [e for e in O.edge_report(lev(1.0), *O.solve_focus(lev(1.0), mfd)) if e["min_gap"] < 0]
    ok(not clash, f"{fn}: no edge clash at MFD with full correction")
    a0 = max(rms_spot(lev(0.0), mfd, f, g) for f, g in ((0, 0), (0.7, 0), (0, 0.7)))
    a1 = max(rms_spot(lev(1.0), mfd, f, g) for f, g in ((0, 0), (0.7, 0), (0, 0.7)))
    if fn in ("c_series_style_ff.json", "c_series_style_65.json"):   # 重新设计的全画幅 / 65 mm 版：与原版在它自己光窗上比（光斑 / 画高），补偿后不比原版糊
        a_o = max(rms_spot(lev_c(1.0), lev_c(1.0)["focus"]["min_distance_mm"], f, g) for f, g in ((0, 0), (0.7, 0), (0, 0.7)))
        ok(a1 / base["sensor"]["height"] < MFD_TOL[fn] * a_o / P("c_series_style.json")["sensor"]["height"],
           f"{fn}: sharpness at MFD (worst RMS spot / frame height {1000 * a1 / base['sensor']['height']:.2f}‰ vs original C {1000 * a_o / P('c_series_style.json')['sensor']['height']:.2f}‰)")
    else:
        ok(a1 < 1.35 * a0, f"{fn}: sharpness at MFD kept (worst RMS spot {1000 * a0:.0f} → {1000 * a1:.0f} µm)")
    ok(O.comp_phi(lev(1.0), 0.0) == 0.0, f"{fn}: compensator plate is flat at infinity")
# ── Sirui 1.6× 全画幅 35 mm（CN 118169848 A）：专利只给 fY 35 与权利要求里的 6 个比值；后截距没给 → X / Y 两向近轴像面必须重合 ──
rx = P("sirui_ff_35.json"); S = O.layout(rx)
fx, bx = O.paraxial_infinity(S, "Rx"); fy, by = O.paraxial_infinity(S, "Ry")
ok(abs(fy / 35.0 - 1) < 0.005, f"Sirui FF 35: fY {fy:.3f} vs patent 35 mm ({100 * (fy / 35 - 1):+.2f}%)")
ok(abs(bx - by) < 0.05, f"Sirui FF 35: X / Y image planes coincide (BFL {bx:.3f} / {by:.3f}) — transcription check")
idx = {sf["id"]: i for i, sf in enumerate(S)}
def sub_efl(a, b, axis):
    M = [[1.0, 0.0], [0.0, 1.0]]; n_prev = 1.0
    for i in range(idx[a], idx[b] + 1):
        sf = S[i]; R = sf[axis]; n_next = sf["n"] if i < idx[b] else 1.0
        Pw = 0.0 if math.isinf(R) else (n_next - n_prev) / R
        M = [[M[0][0], M[0][1]], [M[1][0] - Pw * M[0][0], M[1][1] - Pw * M[0][1]]]
        if i < idx[b]:
            t = sf["d"] / n_next
            M = [[M[0][0] + t * M[1][0], M[0][1] + t * M[1][1]], M[1]]
        n_prev = n_next
    return -1.0 / M[1][0]
vals = {"fY": fy, "fX": fx, "fY/fX": fy / fx,
        "f(2-3)X/f(1-10)Y": sub_efl("S3", "S6", "Rx") / sub_efl("S1", "S17", "Ry"),
        "f(1-3)X/f(4-10)Y": sub_efl("S1", "S6", "Rx") / sub_efl("S7", "S17", "Ry"),
        "f(1-10)X/f(11-18)Y": sub_efl("S1", "S17", "Rx") / sub_efl("S18", "S31", "Ry")}
claims = rx["groups"][0]["source"]["patent_values"]["claims"]
for k, v in vals.items():
    ok(claims[k][0] < v < claims[k][1], f"Sirui FF 35: claim {k} = {v:.4f} in {claims[k]}")
d1, d2 = O.solve_focus(rx, rx["focus"]["min_distance_mm"])
ok(not [e for e in O.edge_report(rx, d1, d2) if e["min_gap"] < 0], "Sirui FF 35: no clash at MFD (0.9 m)")
ok(rx["groups"][0]["surfaces"][0]["aperture"]["clear"] * 2 < 58.0, "Sirui FF 35: estimated front element fits the 58 mm filter thread")
# ── C 风格 50 mm 全画幅版（0.9.1，tools/reoptimize.py c_ff2）：同焦段、1.65×、用满 36 × 24，风格 = 原版在 S35 上的 ──
import coverage as CV
import reoptimize as RO
rx, orig = P("c_series_style_ff.json"), P("c_series_style.json")
S, So = O.layout(rx), O.layout(orig)
(fx, bx), (fy, by) = O.paraxial_infinity(S, "Rx"), O.paraxial_infinity(S, "Ry")
ok(abs(fy - 50.0) < 0.05 and abs(fy / fx - 1.65) < 0.01 and O.desqueeze(rx) == 1.65, f"C FF: fY {fy:.3f} mm, squeeze {fy / fx:.4f}, desqueeze {O.desqueeze(rx)}")
ok(abs(bx - by) < 0.05 and by >= 0.9 * O.paraxial_infinity(So, "Ry")[1] - 0.01, f"C FF: X / Y image planes coincide, BFL {by:.2f} mm ≥ 90% of original")
ok((rx["sensor"]["width"], rx["sensor"]["height"]) == (36.0, 24.0) and O.format_factor(rx, "FF") == 1.0, "C FF: native full-frame gate, FF version = itself")
cn, co = CV.coverage_report(rx, 1e7, 4.0), CV.coverage_report(orig, 1e7, 4.0)
dd = max(abs(cn["distortion_pct"][k] - co["distortion_pct"][k]) for k in cn["distortion_pct"])
ok(dd < 3.5, f"C FF: distortion within {dd:.2f} points of the original on its own gate")
ok(cn["relative_illumination"]["corner"] > 0.75, f"C FF: corner RI at f/4 {cn['relative_illumination']['corner']:.2f} (original {co['relative_illumination']['corner']:.2f})")
qn = RO.quality(RO.open_apertures(rx), 36.0, 24.0)
qo = RO.quality(RO.open_apertures(orig), orig["sensor"]["width"], orig["sensor"]["height"])
ratio = max(a["per_mille_of_height"] / b["per_mille_of_height"] for a, b in zip(qn, qo))
ok(ratio < 1.3, f"C FF: sharpness profile (spot / frame height) within ×{ratio:.2f} of the original at every field point")
# ── C 风格 50 mm 65 mm 5 齿孔版（0.9.1，tools/reoptimize.py c_65）：1.3×、用满 52.48 × 23.01 ──
rx = P("c_series_style_65.json"); S = O.layout(rx)
(fx, bx), (fy, by) = O.paraxial_infinity(S, "Rx"), O.paraxial_infinity(S, "Ry")
ok(abs(fy - 50.0) < 0.05 and abs(fy / fx - 1.3) < 0.02 and O.desqueeze(rx) == 1.3, f"C 65: fY {fy:.3f} mm, squeeze {fy / fx:.4f}, desqueeze {O.desqueeze(rx)}")
ok(abs(bx - by) < 0.05 and by >= 0.9 * O.paraxial_infinity(So, "Ry")[1] - 0.01, f"C 65: X / Y image planes coincide, BFL {by:.2f} mm ≥ 90% of original")
ok((rx["sensor"]["width"], rx["sensor"]["height"]) == O.FORMAT_SIZES["65MM"], "C 65: native 65 mm 5-perf gate")
d1, d2 = O.solve_focus(rx, rx["focus"]["min_distance_mm"])
ok(not [e for e in O.edge_report(rx, d1, d2) if e["min_gap"] < 0], "C 65: no clash at MFD")
c6 = CV.coverage_report(rx, 1e7, 4.0)
ok(abs(c6["edge_vertical_bow_px"] - co["edge_vertical_bow_px"]) < 5, f"C 65: vertical-line bow at 4K {c6['edge_vertical_bow_px']:.1f} px (original {co['edge_vertical_bow_px']:.1f})")
ok(c6["relative_illumination"]["corner"] > 0.65, f"C 65: corner RI at f/4 {c6['relative_illumination']['corner']:.2f}")
q6 = RO.quality(RO.open_apertures(rx), *O.FORMAT_SIZES["65MM"])
ratio = max(a["per_mille_of_height"] / b["per_mille_of_height"] for a, b in zip(q6, qo))
ok(ratio < 1.4, f"C 65: sharpness profile within ×{ratio:.2f} of the original at every field point")
# ── 0.9.3：E / G（1.65×）与 Neil 50 / 65 / 85（1.6×）的全画幅同焦段重新设计：与原版在它自己光窗上的风格对齐 ──
REDESIGNS = (("e_series_style_ff.json", "e_series_style.json", 1.65, 3.5, 1.15), ("g_series_style_ff.json", "g_series_style.json", 1.65, 3.5, 1.15),
             ("neil_2x_50_ff.json", "neil_2x_50.json", 1.6, 3.5, 1.25), ("neil_2x_65_ff.json", "neil_2x_65.json", 1.6, 4.0, 1.3),
             ("neil_2x_85_ff.json", "neil_2x_85.json", 1.6, 3.5, 1.25))
for fn, ofn, sq_t, dist_tol, sharp_tol in REDESIGNS:
    if not os.path.exists(os.path.join(O.PRESCRIPTION_DIR, fn)):
        continue
    rx, orig = P(fn), P(ofn)
    S, So = O.layout(rx), O.layout(orig)
    (fx, bx), (fy, by) = O.paraxial_infinity(S, "Rx"), O.paraxial_infinity(S, "Ry")
    fy0 = O.paraxial_infinity(So, "Ry")[0]
    ok(abs(fy / fy0 - 1) < 1e-3 and abs(fy / fx - sq_t) < 0.01 and O.desqueeze(rx) == sq_t,
       f"{fn}: fY {fy:.2f} mm (original {fy0:.2f}), squeeze {fy / fx:.4f}")
    ok(abs(bx - by) < 0.05 and by >= 0.9 * O.paraxial_infinity(So, "Ry")[1] - 0.01 and (rx["sensor"]["width"], rx["sensor"]["height"]) == (36.0, 24.0),
       f"{fn}: X / Y image planes coincide, BFL {by:.2f} mm, full 36 × 24 gate")
    d1, d2 = O.solve_focus(rx, rx["focus"]["min_distance_mm"])
    ok(not [e for e in O.edge_report(rx, d1, d2) if e["min_gap"] < 0], f"{fn}: no edge clash at MFD")
    cn, co = CV.coverage_report(rx, 1e7, 4.0), CV.coverage_report(orig, 1e7, 4.0)
    dd = max(abs(cn["distortion_pct"][k] - co["distortion_pct"][k]) for k in cn["distortion_pct"])
    ok(dd < dist_tol and cn["relative_illumination"]["corner"] > co["relative_illumination"]["corner"] - 0.1,
       f"{fn}: distortion within {dd:.2f} points; corner RI {cn['relative_illumination']['corner']:.2f} (original {co['relative_illumination']['corner']:.2f})")
    qn = RO.quality(RO.open_apertures(rx), 36.0, 24.0)
    qo = RO.quality(RO.open_apertures(orig), orig["sensor"]["width"], orig["sensor"]["height"])
    ratio = max(a["per_mille_of_height"] / b["per_mille_of_height"] for a, b in zip(qn, qo))
    ok(ratio < sharp_tol, f"{fn}: sharpness profile within ×{ratio:.2f} of the original at every field point")
    if O.compensator(rx):
        mfd = rx["focus"]["min_distance_mm"]
        ok(abs(O.squeeze_at(rx, mfd) / O.squeeze_at(rx, math.inf) - 1) < 0.002, f"{fn}: compensated squeeze at MFD {O.squeeze_at(rx, mfd):.4f}")
# ── 0.9.5：G 风格全画幅成套定焦（观感目标 = G 原版；广角端换结构，畸变按实测上限）──
GSET = ((35, 0.025, 1.35, 0.45), (40, 0.03, 1.35, 0.6), (50, 0.015, 1.45, 0.7), (75, 0.008, 1.15, 0.75), (100, None, 1.15, 0.8))
g_orig = P("g_series_style.json"); g_look = g_orig.get("look", {})
qg = RO.quality(RO.open_apertures(g_orig), g_orig["sensor"]["width"], g_orig["sensor"]["height"])
for f, dist_frac, sharp_tol, ri_min in GSET:
    fn = "g_series_style_ff.json" if f == 100 else f"g_series_style_ff_{f}.json"
    rx = P(fn); S = O.layout(rx)
    (fx, bx), (fy, by) = O.paraxial_infinity(S, "Rx"), O.paraxial_infinity(S, "Ry")
    ok(abs(fy - f) < 0.5 and abs(fy / fx - 1.65) < 0.02 and abs(bx - by) < 0.1 and (rx["sensor"]["width"], rx["sensor"]["height"]) == (36.0, 24.0),
       f"G set {f} mm: fY {fy:.2f}, squeeze {fy / fx:.4f}, X / Y image planes {bx:.2f} / {by:.2f}, 36 × 24")
    ok(f == 100 or all(rx["look"].get(k) == g_look.get(k) for k in ("coating_nm", "flare_color", "flare_gain", "veiling_glare", "f_number")),
       f"G set {f} mm: look parameters (coating, flare, veiling glare, stop) match the G original")
    d1, d2 = O.solve_focus(rx, rx["focus"]["min_distance_mm"])
    ok(not [e for e in O.edge_report(rx, d1, d2) if e["min_gap"] < 0], f"G set {f} mm: no clash at MFD {rx['focus']['min_distance_mm'] / 1000:.2f} m")
    cv = CV.coverage_report(rx, 3000.0, 4.0)
    ok(cv["relative_illumination"]["corner"] >= ri_min, f"G set {f} mm: corner RI at f/4 {cv['relative_illumination']['corner']:.2f} (≥ {ri_min})")
    qn = RO.quality(RO.open_apertures(rx), 36.0, 24.0)
    ratio = max(a["per_mille_of_height"] / b["per_mille_of_height"] for a, b in zip(qn, qg))
    ok(ratio < sharp_tol, f"G set {f} mm: sharpness profile within ×{ratio:.2f} of the G original; distortion edge {cv['distortion_pct']['edge_h']:.1f}%, 4K bow {cv['edge_vertical_bow_px']} px")
# ── 0.9.2：横向色差进覆盖报告（光斑重心，4K 像素）；相机输入指纹稳定、随处方变化 ──
import verify as VF
cr = CV.coverage_report(O.prepare(P("c_series_style.json"), "S35", 0, "FULL"), 3000.0, 4.0)["lateral_color_px"]
ok(cr["corner"]["F_C"] is not None and 0.3 < cr["corner"]["F_C"][2] < 6.0 and cr["edge_h"]["F_C"][2] > cr["edge_v"]["F_C"][2],
   f"C: lateral colour at 4K corner {cr['corner']['F_C'][2]} px (F−C, spot centroids), edge h/v {cr['edge_h']['F_C'][2]} / {cr['edge_v']['F_C'][2]} px")
v1, v2 = VF.verify_inputs("c_series_style.json", "FULL"), VF.verify_inputs("c_series_style.json", "FULL")
v3 = VF.verify_inputs("c_series_style.json", "16:9_NATIVE")
ok(v1["inputs"] == v2["inputs"] and v1["inputs"]["all"] != v3["inputs"]["all"] and v1["inputs"]["pupil_table_source"] == "shipped",
   f"verify_inputs: stable ({v1['inputs']['all']}), changes with the gate, shipped pupil table")
print("ALL PRESET TESTS PASSED")
