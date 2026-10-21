# 由专利转录数据生成预设处方 JSON（E / G 系列风格、Panavision 后置 1.34×）。
# 转录来源与核对见各预设 JSON 的 source 字段；生成后用 python3 tools/rebuild_apertures.py 估算口径。
# 运行：python3 tools/build_presets.py
import json, math, os, sys, copy
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blenderlens"))
import optics as O

PDIR = O.PRESCRIPTION_DIR

# ───────── US 7,085,066 B2（Iain A. Neil，Panavision International，2004）TABLE I ─────────
# 前 46 面为 US 6,122,111 变焦镜（短焦端 F1/Z1、无穷远），S47–S60 为后置变形组。
# Y/X 半径列按专利原样；通光高度为专利给出的最大高度（mm，546.1 nm，f/3.66，像高 13.60）。
PV_TABLE_I = [
    # (item, surface, shape, R_y, R_x, d, glass code, 通光高度 radial, Y, X)
    ('Dummy', 'S1', 'Flat', None, None, 1.0, None, None, None, None),
    ('1', 'S2', 'Sphere', 218.818, 218.818, 5.0, '438950', 50.12, None, None),
    ('', 'S3', 'Asphere', 38.506, 38.506, 23.534, None, 38.15, None, None),
    ('2', 'S4', 'Sphere', 222.116, 222.116, 3.8, '618634', 35.31, None, None),
    ('', 'S5', 'Sphere', 43.238, 43.238, 5.3, None, 30.67, None, None),
    ('3', 'S6', 'Sphere', 51.336, 51.336, 15.592, '805254', 30.72, None, None),
    ('', 'S7', 'Sphere', 77.033, 77.033, 0.1, None, 27.53, None, None),
    ('4', 'S8', 'Sphere', 43.19, 43.19, 7.697, '438950', 26.53, None, None),
    ('', 'S9', 'Sphere', 55.573, 55.573, 4.431, None, 24.8, None, None),
    ('Dummy', 'S10', 'Flat', None, None, 0.109, None, None, None, None),
    ('5', 'S11', 'Sphere', 129.546, 129.546, 5.181, '636354', 24.45, None, None),
    ('6', 'S12', 'Sphere', -318.127, -318.127, 2.2, '804396', 23.9, None, None),
    ('', 'S13', 'Sphere', 97.742, 97.742, 3.87, None, 22.48, None, None),
    ('7', 'S14', 'Sphere', -515.815, -515.815, 2.17, '762401', 22.29, None, None),
    ('', 'S15', 'Sphere', 86.505, 86.505, 0.7, None, 21.63, None, None),
    ('8', 'S16', 'Sphere', 44.464, 44.464, 3.56, '847239', 21.57, None, None),
    ('9', 'S17', 'Sphere', 56.665, 56.665, 2.0, '806410', 21.05, None, None),
    ('', 'S18', 'Sphere', 38.814, 38.814, 1.38, None, 20.14, None, None),
    ('10', 'S19', 'Sphere', 47.058, 47.058, 3.798, '805254', 20.15, None, None),
    ('', 'S20', 'Sphere', 70.812, 70.812, 81.386, None, 19.74, None, None),
    ('11', 'S21', 'Sphere', 73.576, 73.576, 5.284, '618634', 11.34, None, None),
    ('', 'S22', 'Sphere', -467.06, -467.06, 1.3, None, 10.82, None, None),
    ('Iris', 'S23', 'Flat', None, None, 1.32, None, 10.51, None, None),
    ('12', 'S24', 'Sphere', 64.719, 64.719, 4.599, '618634', 10.62, None, None),
    ('', 'S25', 'Sphere', 280.545, 280.545, 6.476, None, 10.48, None, None),
    ('13', 'S26', 'Sphere', -44.031, -44.031, 2.15, '804466', 10.31, None, None),
    ('', 'S27', 'Sphere', -276.492, -276.492, 0.1, None, 10.59, None, None),
    ('14', 'S28', 'Sphere', 104.569, 104.569, 11.912, '438950', 10.7, None, None),
    ('', 'S29', 'Sphere', -45.864, -45.864, 0.1, None, 11.27, None, None),
    ('15', 'S30', 'Sphere', -125.907, -125.907, 2.2, '734515', 11.24, None, None),
    ('', 'S31', 'Sphere', 63.152, 63.152, 4.979, None, 11.32, None, None),
    ('16', 'S32', 'Sphere', 14624.0, 14624.0, 2.25, '720347', 12.02, None, None),
    ('17', 'S33', 'Sphere', 84.356, 84.356, 9.972, '603655', 12.34, None, None),
    ('', 'S34', 'Sphere', -87.065, -87.065, 0.1, None, 13.37, None, None),
    ('18', 'S35', 'Sphere', 88.905, 88.905, 2.55, '521526', 13.7, None, None),
    ('19', 'S36', 'Sphere', 56.43, 56.43, 14.288, '438950', 13.84, None, None),
    ('', 'S37', 'Sphere', -114.09, -114.09, 0.1, None, 14.68, None, None),
    ('20', 'S38', 'Sphere', 84.019, 84.019, 6.824, '720460', 14.83, None, None),
    ('', 'S39', 'Sphere', 391.268, 391.268, 0.6, None, 14.6, None, None),
    ('Dummy', 'S40', 'Flat', None, None, 0.1, None, None, None, None),
    ('21', 'S41', 'Sphere', 1395.656, 1395.656, 2.527, '785257', 14.58, None, None),
    ('', 'S42', 'Sphere', -105.898, -105.898, 0.1, None, 14.54, None, None),
    ('22', 'S43', 'Sphere', -220.862, -220.862, 1.8, '901315', 14.44, None, None),
    ('', 'S44', 'Asphere', 72.073, 72.073, 0.911, None, 14.25, None, None),
    ('23', 'S45', 'Sphere', 128.877, 128.877, 3.169, '720460', 14.27, None, None),
    ('', 'S46', 'Sphere', -118.373, -118.373, 1.432, None, 14.29, None, None),
    ('24', 'S47', 'Y Cylinder', 136.373, None, 2.444, '816466', 14.12, None, None),
    ('', 'S48', 'Y Cylinder', -176.528, None, 0.125, None, 14.01, None, None),
    ('25', 'S49', 'Y Cylinder', 19.172, None, 4.09, '816466', 13.48, 11.1, None),
    ('', 'S50', 'Y Cylinder', 18.137, None, 3.765, None, 13.22, 9.6, None),
    ('26', 'S51', 'Y Cylinder', -192.507, None, 1.047, '670473', 12.84, 9.3, None),
    ('', 'S52', 'Y Cylinder', 24.412, None, 2.982, None, 12.77, 8.8, None),
    ('27', 'S53', 'X Cylinder', None, 385.928, 1.039, '816466', 12.48, None, None),
    ('', 'S54', 'X Cylinder', None, 31.262, 0.793, None, 12.27, None, None),
    ('28', 'S55', 'X Cylinder', None, 35.527, 0.978, '626357', 12.39, None, None),
    ('29', 'S56', 'X Cylinder', None, 29.063, 2.085, '847238', 12.38, None, None),
    ('', 'S57', 'X Cylinder', None, 36.17, 0.076, None, 12.37, None, None),
    ('30', 'S58', 'X Cylinder', None, 32.52, 5.371, '788474', 12.47, None, None),
    ('31', 'S59', 'X Cylinder', None, -182.118, 1.037, '805254', 12.45, None, None),
    ('', 'S60', 'X Cylinder', None, 261.747, 15.231, None, None, 8.4, 11.8),
    ('Dummy', 'S61', 'Flat', None, None, 20.523, None, None, None, None),
    ('Image', 'S62', 'Flat', None, None, 0.0, None, 13.6, None, None),
]

# Ohara 目录值（2026-10-18 核实）：老牌号取 OHARA 2017 Zemax 目录（refractiveindex.info），S- 牌号取 oharacorp.com。
# 专利只给六位代码（n_d 3 位、V_d 1 位），且 FPL53 印成 438950（目录为 439950）——用目录值替代代码换算。
PV_GLASS_NAME = {'S2': 'FPL53', 'S4': 'PHM52', 'S6': 'PBH6W', 'S8': 'FPL53', 'S11': 'PBM6', 'S12': 'LAH63', 'S14': 'LAM55', 'S16': 'PBH53W', 'S17': 'LAH53', 'S19': 'PBH6W', 'S21': 'PHM52', 'S24': 'PHM52', 'S26': 'LAH65', 'S28': 'FPL53', 'S30': 'LAL59', 'S32': 'BPH8', 'S33': 'PHM53', 'S35': 'SSL5', 'S36': 'FPL53', 'S38': 'LAM61', 'S41': 'PBH11W', 'S43': 'LAH78', 'S45': 'LAM61', 'S47': 'SLAH59', 'S49': 'SLAH59', 'S51': 'SBAH10', 'S53': 'SLAH59', 'S55': 'STIM1', 'S56': 'STIH53', 'S58': 'SLAH64', 'S59': 'STIH6'}
OHARA_CATALOG = {   # 牌号: (n_d, V_d)
    "FPL53": (1.43875, 94.96), "PHM52": (1.61800, 63.39), "PBH6W": (1.80518, 25.44), "PBM6": (1.63636, 35.38),
    "LAH63": (1.80440, 39.58), "LAM55": (1.76200, 40.10), "PBH53W": (1.84666, 23.89), "LAH53": (1.80610, 40.95),
    "LAH65": (1.80400, 46.58), "LAL59": (1.73400, 51.49), "BPH8": (1.72047, 34.72), "PHM53": (1.60300, 65.48),
    "SSL5": (1.52130, 52.55), "LAM61": (1.72000, 46.04), "PBH11W": (1.78472, 25.71), "LAH78": (1.90135, 31.55),
    "SLAH59": (1.81600, 46.62), "SBAH10": (1.67003, 47.23), "STIM1": (1.62588, 35.70), "STIH53": (1.84666, 23.78),
    "SLAH64": (1.78800, 47.37), "STIH6": (1.80518, 25.42),
}

# US 6,122,111（Neil & Betensky，Panavision）TABLE 1 的变焦间隔（F1 无穷远对焦）：S10、S20、S39 后的空气间隔
# 专利：Z1 15.3 mm、Z2 23.9 mm、Z3 42.5 mm、Z4 49.2 mm（变焦镜单独）
PV_ZOOM = {"Z1": (0.109, 81.386, 0.100), "Z2": (27.982, 26.018, 27.592),
           "Z3": (9.423, 3.205, 68.959), "Z4": (0.100, 0.971, 80.521)}
PV_ZOOM_EFL = {"Z1": 15.3, "Z2": 23.9, "Z3": 42.5, "Z4": 49.2}
# US 6,122,111 权利要求 35 / TABLE 1 末列：变焦镜各面最大通光直径（mm，546.1 nm，像高 14.45，f/2.2，
# 「for all Focus and Zoom Positions」= 镜片实物尺寸）。US 7,085,066 TABLE I 只给 Z1 / f/3.66 下的光束高度，
# 用在长焦端会把轴上光束切掉一半以上（实测轴上通过率 0.34 → 应为 0.77），所以 Z1 以外的变焦位置改用这张表。
PV_ZOOM_MAX_DIA = {
    "S2": 124.80, "S3": 90.57, "S4": 90.24, "S5": 73.22, "S6": 73.95, "S7": 68.33, "S8": 64.89, "S9": 62.41,
    "S11": 48.90, "S12": 47.92, "S13": 45.43, "S14": 45.15, "S15": 44.15, "S16": 44.47, "S17": 43.55, "S18": 41.81,
    "S19": 41.84, "S20": 41.17, "S21": 41.12, "S22": 41.10, "S24": 42.20, "S25": 41.99, "S26": 41.96, "S27": 44.73,
    "S28": 47.12, "S29": 47.81, "S30": 47.23, "S31": 48.06, "S32": 48.29, "S33": 51.41, "S34": 52.55, "S35": 57.81,
    "S36": 58.48, "S37": 58.93, "S38": 59.41, "S39": 58.70, "S41": 29.97, "S42": 29.72, "S43": 29.34, "S44": 28.50,
    "S45": 28.58, "S46": 28.75,
}
# 对焦间隔（S5、S9）：F1 无穷远、F2 约 330 mm、F3 约 133 mm
PV_FOCUS = {"F1": (5.300, 4.431), "F2": (6.322, 14.508), "F3": (7.260, 24.155)}
# 两组位移：前组 = ΔS5 + ΔS9，第二组 = ΔS9 → 比值（F2、F3 平均）
_r = [(PV_FOCUS[f][1] - PV_FOCUS["F1"][1]) / ((PV_FOCUS[f][0] - PV_FOCUS["F1"][0]) + (PV_FOCUS[f][1] - PV_FOCUS["F1"][1])) for f in ("F2", "F3")]
PV_FOCUS_RATIO = round(sum(_r) / 2, 4)

PV_ASPH = {   # Z = c r² / (1 + √(1 − (1+K) c² r²)) + A r⁴ + B r⁶ + C r⁸ + D r¹⁰ + E r¹²
    "S3":  (-1.5066, [2.0962e-6, -7.9395e-10, 6.1324e-13, -2.8537e-16, 3.1072e-20]),
    "S44": (-2.2286, [2.2871e-6, -2.1575e-9, 9.2167e-12, -1.2856e-14, 0.0]),
}

# ───────── US 5,184,880（A. Lisziewicz，Isco Optische Werke，1990）实施例 3 ─────────
# 前 6 面在垂直方向起作用（伽利略式：正→负），后 6 面在水平方向起作用（反伽利略式：负→正）。
# 间隔取对焦表「infinite」一行：E(3) = 96.425、E(9) = 57.780（主表 100.224 / 51.328 对应约 6 m 合焦）。
LAFN21 = (1.78831, 1.78332, 1.79992)      # n_d, n_C, n_F（专利原表）
SF11 = (1.78472, 1.77599, 1.80645)
ISCO_EMB3 = [  # (id, R, d, glass 或 None=空气, 作用方向)
    ("I1", 394.285, 15.291, "LAFN21", "y"),
    ("I2", -331.901, 6.116, "SF11", "y"),
    ("I3", None, 96.425, None, "y"),
    ("I4", None, 10.920, "SF11", "y"),
    ("I5", -257.733, 4.160, "LAFN21", "y"),
    ("I6", 313.350, 4.600, None, "y"),
    ("I7", -140.753, 4.255, "LAFN21", "x"),
    ("I8", 60.706, 10.500, "SF11", "x"),
    ("I9", 285.948, 57.780, None, "x"),
    ("I10", None, 3.255, "SF11", "x"),
    ("I11", 107.283, 7.440, "LAFN21", "x"),
    ("I12", -145.001, 49.800, None, "x"),
]
ISCO_FOCUS_TABLE = [  # 距离 m, E(3), E(9), d(Fy/Fx) %（专利对焦表，对照用）
    ("inf", 96.425, 57.780, 4.45), (50.0, 96.918, 56.975, 3.91), (30.0, 97.244, 56.439, 3.55),
    (20.0, 97.648, 55.771, 3.10), (15.0, 98.047, 55.104, 2.65), (10.0, 98.830, 53.773, 1.75),
    (7.5, 99.593, 52.449, 0.84), (5.0, 101.057, 49.823, -0.96), (4.0, 102.095, 47.876, -2.31),
    (3.5, 102.803, 46.499, -3.27), (3.0, 103.702, 44.682, -4.54), (2.5, 104.868, 42.179, -6.32),
]


def abbe(n):
    nd, nC, nF = n
    return round((nd - 1) / (nF - nC), 2)


def nikon_prime(scale, f_number):
    """US 4,234,242 实施例 1（与 C 系列风格同一份转录，取自 c_series_style.json）。"""
    c = json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))
    g = copy.deepcopy(next(g for g in c["groups"] if g["id"] == "prime"))
    g["scale"] = scale
    g["target_f"] = round(100.0 * scale, 3)
    g["f_number"] = f_number
    for sf in g["surfaces"]:
        sf.pop("aperture", None); sf.pop("clear_semi", None)
    st = g["stop"]
    for k in ("semi_diameter_at_f_number", "z_at_infinity"):
        st.pop(k, None)
    st["f_number"] = f_number
    glasses = {k: v for k, v in c["glasses"].items() if k.startswith("N_") or k == "air"}
    return g, glasses


# ───────────────────────── 发腮补偿板（0.8）─────────────────────────
# 一阶等效的 Auto Panatar 补偿（见 optics.compensator）：变形器最前面加一块 N-BK7 薄平板（不随对焦移动），
# 前表面的马鞍面曲率由凸轮表 φ(Δ1) 决定。这里按「每个距离的压缩比 = 无穷远压缩比」逐点解出凸轮表。
COMP_GLASS = {"nd": 1.5168, "vd": 64.17, "catalog": "SCHOTT:N-BK7",
              "note": "补偿板（一阶等效的反向旋转柱面对，见 optics.compensator）"}

def add_compensator(rx, gap=6.0, thickness=5.0, n_table=13, level=0.0):
    rx = json.loads(json.dumps(rx))
    rx["groups"] = [g for g in rx["groups"] if g["id"] != "compensator"]
    rx["focus"].pop("compensator", None)
    a0 = rx["groups"][0]["surfaces"][0]["aperture"]
    r = math.hypot(a0["semi_x"], a0["semi_y"]) if a0["shape"] == "rect" else a0["outer"]
    r = math.ceil(r + 1.0)
    rx["glasses"]["BL_COMP"] = COMP_GLASS
    ap = {"shape": "circle", "clear": r, "outer": r + 1.0}
    rx["groups"].insert(0, {"id": "compensator", "scale": 1.0,
        "note": "0.8：发腮补偿（Auto Panatar 式，一阶等效）。补偿强度为 0 时是一块平行平板，对成像无影响（只多两个反射面）",
        "surfaces": [
            {"id": "K1", "R_x": None, "R_y": None, "d": thickness, "medium": "BL_COMP", "aperture": ap,
             "note": "马鞍面：曲率随对焦（凸轮表 focus.compensator）"},
            {"id": "K2", "R_x": None, "R_y": None, "d": gap, "medium": "air", "aperture": ap}]})
    rx["focus"]["compensator"] = {"surface": "K1", "level": 0.0, "d1": [0.0, 1.0], "phi": [0.0, 0.0]}
    mfd = rx["focus"]["min_distance_mm"]
    d1s, phis = [], []
    for j in range(n_table):
        u = j / (n_table - 1)
        if u == 0:
            d1s.append(0.0); phis.append(0.0); continue
        d1, d2, phi = O.solve_compensated(rx, mfd / u)
        d1s.append(round(d1, 6)); phis.append(phi)
    order = sorted(range(n_table), key=lambda k: d1s[k])
    if order != list(range(n_table)) and order != list(range(n_table))[::-1]:
        raise RuntimeError("补偿凸轮表：Δ1 不随距离单调")
    if d1s[-1] < d1s[0]:
        d1s, phis = d1s[::-1], phis[::-1]
    rx["focus"]["compensator"] = {
        "surface": "K1", "level": float(level), "d1": d1s, "phi": [float(f"{v:.9g}") for v in phis],
        "note": "凸轮表：对焦环行程 Δ1（mm）→ 补偿板光焦度 φ（1/mm，水平 −φ / 垂直 +φ），完全补偿时每个距离的压缩比 = 无穷远；"
                "level（0–1）是补偿强度，界面 Mumps Correction 写入"}
    return rx

def add_compensator_file(fname):
    path = os.path.join(PDIR, fname)
    rx0 = O.load(path)
    rx = add_compensator(rx0, level=rx0.get("look", {}).get("mumps_correction", 0.0))
    O.save(rx, path)
    c = rx["focus"]["compensator"]
    print(f"{fname}: 补偿凸轮表 Δ1 {c['d1'][0]:.3f}…{c['d1'][-1]:.3f} mm，φ 最大 {1000 * max(map(abs, c['phi'])):.3f} D")


def isco_preset(fname, meta, look, prime_scale, att_scale):
    prime, glasses = nikon_prime(prime_scale, look["f_number"])
    # 色散：目录曲线（Schott，refractiveindex.info CC0）的形状 + 精确通过专利给的 C / F 线（optics.dispersion_coeffs）
    glasses["I_LAFN21"] = {"nd": LAFN21[0], "vd": abbe(LAFN21), "n_C": LAFN21[1], "n_F": LAFN21[2], "catalog": "SCHOTT:N-LAF21",
                           "note": "LaF N21（Isco 原表 n_d/n_C/n_F）；色散曲线形状取其无铅后继 N-LAF21，并校正到专利的 C / F 线"}
    glasses["I_SF11"] = {"nd": SF11[0], "vd": abbe(SF11), "n_C": SF11[1], "n_F": SF11[2], "catalog": "SCHOTT:SF11",
                         "note": "SF 11（Isco 原表 n_d/n_C/n_F；与 Schott 目录 SF11 相差 ≤ 5e-6）"}
    surfs = []
    for sid, R, d, gl, ax in ISCO_EMB3:
        surfs.append({"id": sid, "R_x": R if ax == "x" else None, "R_y": R if ax == "y" else None,
                      "d": d, "medium": "I_" + gl if gl else "air",
                      "acts": "水平" if ax == "x" else "垂直"})
    att = {"id": "anamorph",
           "role": "前置无焦变形器：前组在垂直方向放大 1.31×（伽利略），后组在水平方向缩小到 0.627×（反伽利略），合计约 2.09×",
           "source": {"patent": "US 5,184,880 — A. Lisziewicz, Anamorphic attachment（Isco Optische Werke），1990 申请 / 1993 授权，实施例 3",
                      "url": "https://patents.google.com/patent/US5184880A/en",
                      "table": "第 11–12 栏主表 + 对焦表（已对照扫描件）；间隔取对焦表 infinite 行",
                      "patent_values": {"Fg": 100.0, "Fx": 62.71, "Fy": 131.00, "anamorphic_factor": 2.089,
                                        "entrance_to_focal_plane_mm": 375.012},
                      "focus_table": ISCO_FOCUS_TABLE,
                      "verified": "以专利自带 f=100 BK7 主镜近轴追迹：Fx 62.55、Fy 131.01、比 2.0946"},
           "scale": att_scale, "surfaces": surfs}
    rx = {"meta": meta, "glasses": glasses, "groups": [att, prime],
          "anamorphic": {"desqueeze": 2.09, "type": "front",
                         "note": "Isco 实施例 3 实际压缩比 2.09（专利 Fy/Fx），按此解挤压使无穷远处圆形不变形；按 Panavision 名义 2.0 解挤压会窄 4.5%"},
          "look": look,
          "focus": {"mechanism": "按专利：中间两组（I4–I9）一起移动 Δ2；后组 I10–I12 与主镜由凸轮跟随移动 Δ1；前组 I1–I3 相对焦面固定。两截面同时求解",
                    "groups": [{"ids": [s["id"] for s in surfs if s["id"] in ("I10", "I11", "I12")] + [s["id"] for s in prime["surfaces"]], "moves": [1, 0]},
                               {"ids": ["I4", "I5", "I6", "I7", "I8", "I9"], "moves": [0, 1]}],
                    "solve": ["Ry", "Rx"], "min_distance_mm": look["min_focus_mm"]},
          "sensor": {"format": "35 mm 变形光窗（SMPTE 59）", "width": 22.0, "height": 18.6}}
    rx = O.normalize(rx)
    rx, info = O.apply_apertures(rx)            # 口径：真实追迹估算（见 optics.apply_apertures）
    O.save(rx, os.path.join(PDIR, fname))
    print(f"{fname}: 光阑 z {info['z_stop']:.3f} r {info['r_stop']:.3f}，回收余量 {info['clash_fixed']}")
    return rx


def panavision_rear(zoom_pos="Z1"):
    glasses = {"air": {"nd": 1.0, "vd": None}}
    groups = {"zoom": [], "anamorph": []}
    stop = None
    elem_outer, cur = {}, []
    pending_d = 0.0
    rows = [r for r in PV_TABLE_I if r[1] not in ("S62",)]
    out = []
    # 以 US 7,085,066 TABLE I（Z1，已逐行核实）为基准，只叠加 US 6,122,111 中该变焦位置相对 Z1 的间隔变化
    # （两份专利的虚拟面安排不同：S39 在 7,085,066 为 0.600，在 6,122,111 为 0.100）
    dz = {sid: PV_ZOOM[zoom_pos][k] - PV_ZOOM["Z1"][k] for k, sid in enumerate(("S10", "S20", "S39"))}
    for item, sid, shape, ry, rx_, d, code, rad, hy, hx in rows:
        d = round(d + dz.get(sid, 0.0), 4)
        if item == "Dummy":
            if out:
                out[-1]["d"] = round(out[-1]["d"] + d, 4)
            continue
        if item == "Iris":
            stop = {"after_surface": out[-1]["id"], "offset_mm": out[-1]["d"],
                    "semi_diameter": rad, "f_number": 3.66, "note": "Iris S23（专利：f/3.66 时半径 10.51）"}
            out[-1]["d"] = round(out[-1]["d"] + d, 4)
            continue
        key = None
        if code:
            name = PV_GLASS_NAME[sid]
            key = "PV_" + name
            nd, vd = OHARA_CATALOG[name]
            c_nd, c_vd = O.glass_from_code(code)
            glasses[key] = {"nd": nd, "vd": vd, "code": code, "name": name,
                            "code_values": [c_nd, c_vd], "note": "Ohara 目录值；专利只给代码"}
        sf = {"id": sid, "R_x": rx_, "R_y": ry, "d": d, "medium": key or "air",
              "shape": shape, "patent_heights": {"radial": rad, "Y": hy, "X": hx}}
        if sid in PV_ASPH:
            K, A = PV_ASPH[sid]
            sf["k"] = K
            sf["asph"] = A
        if item:
            sf["element"] = item
        out.append(sf)
    if zoom_pos != "Z1":                         # 变焦镜部分改用 6,122,111 的实物最大口径（见 PV_ZOOM_MAX_DIA）
        for sf in out:
            if sf["id"] in PV_ZOOM_MAX_DIA:
                sf["patent_heights"] = {"radial": PV_ZOOM_MAX_DIA[sf["id"]] / 2, "Y": None, "X": None,
                                        "source": "US 6,122,111 claim 35（最大通光直径 / 2）"}
    # 口径：用专利通光高度。镜片外径 = 该片各面 radial 最大值；Y/X 高度作遮罩（切边或矩形视场光阑）
    els = []
    cur = []
    for sf in out:
        cur.append(sf)
        if sf["medium"] == "air":
            els.append(cur); cur = []
    for el in els:
        outer = max(s["patent_heights"]["radial"] or 0 for s in el)
        for s in el:
            h = s["patent_heights"]
            ap = {"shape": "circle", "clear": h["radial"] or outer, "outer": outer}
            if h["Y"]:
                ap["semi_y"] = h["Y"]
            if h["X"]:
                ap["semi_x"] = h["X"]
            s["aperture"] = ap
    if zoom_pos != "Z1":
        # 原变焦镜全焦段恒定 f/2.2 → 长焦端光阑开大。保持专利在 Z1 给出的系统垂直 f 值（f/4.10），由近轴推算光阑半径
        stop = {"after_surface": stop["after_surface"], "offset_mm": stop["offset_mm"], "f_number": 4.10,
                "note": "长焦端：保持系统垂直 f/4.10（专利 Z1 值），光阑半径近轴推算；Z1 时为 10.51 mm（f/3.66 综合）"}
    zoom = [s for s in out if int(s["id"][1:]) <= 46]
    ana = [s for s in out if int(s["id"][1:]) >= 47]
    image_distance = ana[-1]["d"]
    tele = zoom_pos == "Z4"
    pos = f"{zoom_pos}（变焦镜单独 {PV_ZOOM_EFL[zoom_pos]} mm）"
    meta = {"name": f"Panavision 后置 1.34× 变形（US 7,085,066 原处方：变焦镜 + 8 片后置变形组），变焦 {pos}",
            "short_name": "Panavision 后置 1.34× 长焦端（真实处方）" if tele else "Panavision 后置 1.34×（真实处方）",
            "order": 5 if tele else 4,
            "tagline": "唯一公开的 Panavision 变形处方：后置变形组，16:9 画幅解挤压到 2.40:1；变焦镜取" + ("长焦端 Z4" if tele else "短焦端 Z1"),
            "note": f"Iain A. Neil 为 Panavision 设计，2004 申请、2006 授权。后置变形组位于变焦镜与反光镜之间约 26 mm 内；前 46 面是 US 6,122,111 的变焦镜（{pos}、无穷远；变焦间隔取自该专利 TABLE 1）。" + ("变焦镜口径取 US 6,122,111 给出的各面最大通光直径（全变焦、全对焦范围的实物尺寸）；后置组口径为 7,085,066 在 Z1 下给出的值。" if zoom_pos != "Z1" else "口径为专利在 Z1 下给出的光束高度。"),
            "units": "mm", "conventions": json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))["meta"]["conventions"]}
    rx = {"meta": meta, "glasses": glasses,
          "groups": [
              {"id": "zoom", "role": f"US 6,122,111 变焦镜，F1/{zoom_pos}，无穷远", "scale": 1.0, "zoom_position": zoom_pos,
               "aperture_source": "patent", "f_number": stop["f_number"], "stop": stop, "surfaces": zoom,
               "source": {"patent": "US 7,085,066 B2 — I. A. Neil, Anamorphic imaging system（Panavision International），2004 申请 / 2006 授权",
                          "url": "https://patents.google.com/patent/US7085066B2/en",
                          "table": "TABLE I（CODE V 导出，20 °C；玻璃为 Ohara 代码）",
                          "patent_values": {"efl_x": 15.81, "efl_y": 20.60, "f_number_x": 3.15, "f_number_y": 4.10,
                                            "f_number_effective": 3.66, "squeeze_nominal": 1.34,
                                            "zoom_efl_alone": 15.3, "corner_relative_illumination": 0.5}}},
              {"id": "anamorph", "role": "后置变形组：Y 向柱面 3 片（挤压）+ X 向柱面 5 片（像差控制），垂直拉伸 1.34×",
               "scale": 1.0, "aperture_source": "patent", "surfaces": ana}],
          "image_distance_mm": image_distance,
          "anamorphic": {"desqueeze": 1.35, "type": "rear",
                         "note": "专利：把 2.40:1 物方画面垂直拉伸到 16:9 像面（正文称压缩比约 1.34）。真实追迹：画面边缘物方张角比 2.401，整幅解挤压比 1.350；压缩比从中心 1.304 增到边缘 1.351（水平畸变），所以中心物体解挤压后略宽约 3.5%"},
          "look": {"reference": "Panavision 2004 年后置变形组（专利实施例）；无公开的成品镜头规格。最近对焦取变焦镜专利 F3：物面到 S1 133 mm = 距像面 476 mm",
                   "f_number": 3.66 if zoom_pos == "Z1" else 4.10, "min_focus_mm": 476.0, "coating": "BROADBAND", "coating_nm": 94.0, "flare_color": "PHYSICAL", "flare_gain": 0.6,
                   "veiling_glare": 0.012},
          "focus": {"mechanism": "按 US 6,122,111 的内对焦：前组（第 1–2 片，S2–S5）移动 Δ，第二组（第 3–4 片，S6–S9）移动 0.909·Δ"
                                 "（专利 TABLE 1：F2 时 S5/S9 间隔 +1.022/+10.077、F3 时 +1.960/+19.724 → 两组位移比 0.908/0.910）；变焦组与后置变形组不动。"
                                 "中间像位置不变 → 两向同时合焦、无发腮。专利物距从物面量到 S1（第一面前 1 mm 的虚面，随前组移动）；按此定义，前组行程模型/专利：F2（330 mm）11.221 / 11.099 mm，F3（133 mm）21.686 / 21.684 mm",
                    "groups": [{"ids": ["S2", "S3", "S4", "S5"], "moves": [1, 0]},
                               {"ids": ["S6", "S7", "S8", "S9"], "moves": [PV_FOCUS_RATIO, 0]}],
                    "solve": ["Ry"], "min_distance_mm": 476.0},
          "sensor": {"format": "16:9（3 齿孔 35 mm 或同尺寸数字传感器），像高半径 13.60 mm", "width": 23.71, "height": 13.34}}
    O.save(rx, os.path.join(PDIR, "panavision_rear_134_tele.json" if tele else "panavision_rear_134.json"))
    return rx


# ───────── US 11,287,618 B2 / CN 111562632 B（Iain A. Neil，广东思锐光学 Sirui，优先权 2019-02-14）─────────
# 一个前变形部分（G1，2.06×）+ 可换的后非变形部分（G2）→ 垂直焦距 50 / 65 / 85 mm，全开 f/3（像方）。
# 数据取自中国授权本 CN 111562632 B 第 28–30 页的表 1–3（逐位核对）。美国本 US 11,287,618 B2 的表格印刷有大量
# 错误（玻璃代码 / 名称不存在、表 2 后组间隔照抄表 1、S10 / S24 半径错、S8 FD3 5.677 应为 8.677 等），不可用；
# 核对过程与证据见 research/RESEARCH.md 与 research/analysis/。
NEIL_GLASS = {   # 牌号: 中国本印的玻璃代码（均与 Ohara 目录一致）
    "S-TIH53": "847238", "S-FPL51": "497816", "S-LAH65": "804466", "S-PHM52": "618634", "S-BAM4": "606437",
    "S-TIH6": "805254", "S-LAH55V": "835427", "S-YGH51": "755523", "S-TIM8": "596392", "S-LAM66": "801350",
    "S-TIH10": "728285", "S-TIM28": "689311", "S-TIL1": "548458", "S-TIH4": "755275", "S-LAM7": "750353",
}
# 前变形部分（三张表相同）：(面, 形状, R_x, R_y, 间隔 FD1, 玻璃, 孔径半直径)；FD2/FD3 间隔见 NEIL_FOCUS
NEIL_FRONT = [
    ("S1", "Sph", 700.502, 700.502, 4.429, "S-TIH53", 32.82), ("S2", "Sph", -1525.000, -1525.000, 5.519, None, 31.92),
    ("S3", "XCyl", -101.043, None, 4.000, "S-FPL51", 29.72), ("S4", "XCyl", 76.315, None, 15.511, None, 27.33),
    ("S5", "Sph", -422.634, -422.634, 3.000, "S-FPL51", 23.73), ("S6", "Sph", 108.119, 108.119, 5.446, None, 22.52),
    ("S7", "YCyl", None, -139.340, 3.200, "S-LAH65", 22.09), ("S8", "YCyl", None, 258.136, 2.285, None, 21.77),
    ("S9", "YCyl", None, 243.346, 10.640, "S-PHM52", 21.17), ("S10", "YCyl", None, -51.827, 0.883, None, 21.06),
    ("S11", "YCyl", None, -49.911, 7.056, "S-TIH53", 20.88), ("S12", "YCyl", None, -73.368, 10.402, None, 21.11),
    ("S13", "Sph", None, None, 3.000, "S-BAM4", 19.57), ("S14", "XCyl", 79.055, None, 2.000, None, 19.40),
    ("S15", "Sph", 70.533, 70.533, 18.750, "S-TIH6", 19.14), ("S16", "Sph", 67.370, 67.370, 7.528, None, 16.35),
    ("S17", "XCyl", 87.532, None, 8.843, "S-FPL51", 16.01), ("S18", "XCyl", -65.833, None, 2.000, None, 15.71),
    ("S19", "Sph", 50.719, 50.719, 4.582, "S-LAH55V", 14.97), ("S20", "Sph", 337.165, 337.165, 0.976, None, 14.41),
    ("S21", "Sph", 4393.823, 4393.823, 2.450, "S-BAM4", 14.16), ("S22", "Sph", 43.481, 43.481, None, None, 13.17),
]
# 对焦：G1b（L3–L4，S5–S8）整体移动；S4 / S8 后的间隔，FD1 ∞（1000 m）、FD2 1843 mm、FD3 868 mm（物面到 S1）
NEIL_FOCUS = {"FD1": (15.511, 2.285), "FD2": (12.339, 5.458), "FD3": (9.120, 8.677)}
# 后非变形部分：(S22 后间隔, 后组面 [(面, R, 间隔, 玻璃, 半径)]，光阑在 S28 后的间隔与半径, 像距, FLY)
NEIL_REAR = {
    50: (4.700, [("S23", 84.664, 2.744, "S-YGH51", 7.14), ("S24", -105.007, 6.401, None, 7.06), ("S25", -22.376, 1.500, "S-TIM8", 6.39),
                 ("S26", 18.038, 2.794, None, 6.54), ("S27", 1028.618, 3.116, "S-LAM66", 7.07), ("S28", -31.995, 2.250, None, 7.46),
                 ("STOP", None, 3.844, None, 7.85), ("S30", 84.857, 7.500, "S-FPL51", 8.38), ("S31", -18.917, 0.500, None, 8.62),
                 ("S32", 24.942, 2.686, "S-YGH51", 7.88), ("S33", 34.085, 0.589, None, 7.32), ("S34", 36.878, 1.500, "S-TIH10", 7.18),
                 ("S35", 18.525, 47.576, None, 6.68)]),
    65: (12.789, [("S23", 38.182, 2.940, "S-YGH51", 9.30), ("S24", 198.850, 0.400, None, 9.08), ("S25", 18.392, 3.613, "S-PHM52", 8.71),
                  ("S26", 57.246, 1.176, None, 8.12), ("S27", 74.274, 1.500, "S-TIM8", 7.72), ("S28", 14.668, 3.990, None, 6.93),
                  ("STOP", None, 2.584, None, 6.62), ("S30", -59.736, 1.500, "S-TIM28", 6.31), ("S31", 29.355, 2.795, None, 6.21),
                  ("S32", 46.904, 11.342, "S-LAH55V", 6.44), ("S33", -46.999, 43.071, None, 6.28)]),
    85: (4.720, [("S23", 21.990, 5.511, "S-FPL51", 12.13), ("S24", 501.664, 0.400, None, 11.71), ("S25", 20.222, 3.889, "S-FPL51", 10.68),
                 ("S26", 42.462, 2.091, None, 9.87), ("S27", 600.875, 1.600, "S-TIL1", 9.37), ("S28", 14.606, 4.723, None, 8.13),
                 ("STOP", None, 16.315, None, 7.84), ("S30", -15.123, 1.500, "S-TIH4", 5.89), ("S31", -24.929, 2.335, None, 6.11),
                 ("S32", -191.227, 2.617, "S-LAM7", 6.17), ("S33", -28.256, 42.000, None, 6.30)]),
}


def neil_sirui(fl):
    cat = json.load(open(os.path.join(ROOT, "blenderlens", "glass", "ohara.json"), encoding="utf-8"))["glasses"]
    glasses = {"air": {"nd": 1.0, "vd": None}}
    def glass(name):
        key = "OH_" + name.replace("-", "")
        r = cat[name]
        assert r.get("code", NEIL_GLASS[name]) == NEIL_GLASS[name], (name, r.get("code"))
        glasses[key] = {"nd": round(r["nd"], 6), "vd": round(r["vd"], 3), "code": NEIL_GLASS[name], "name": name,
                        "catalog": "OHARA:" + name, "note": "Ohara 目录值（refractiveindex.info / OHARA 2017）"}
        return key
    s22, rear = NEIL_REAR[fl]
    def surf(sid, rx_, ry, d, g, ap, shape):
        return {"id": sid, "R_x": rx_, "R_y": ry, "d": d, "medium": glass(g) if g else "air", "shape": shape,
                "patent_semi": ap}
    front = [surf(sid, rx_, ry, s22 if d is None else d, g, ap, shape) for sid, shape, rx_, ry, d, g, ap in NEIL_FRONT]
    back, stop = [], None
    for sid, R, d, g, ap in rear:
        if sid == "STOP":
            stop = {"after_surface": back[-1]["id"], "offset_mm": back[-1]["d"], "semi_diameter": ap, "f_number": 3.0,
                    "note": f"光阑 S29（专利：全开 f/3 时孔径半直径 {ap}）"}
            back[-1]["d"] = round(back[-1]["d"] + d, 4)
            continue
        back.append(surf(sid, R, R, d, g, ap, "Sph"))
    image_distance = back[-1]["d"]
    # 口径：专利给的圆形孔径半直径；镜片外径取该片两面的较大值
    for grp in (front, back):
        els, cur = [], []
        for sf in grp:
            cur.append(sf)
            if sf["medium"] == "air":
                els.append(cur); cur = []
        for el in els:
            outer = max(s["patent_semi"] for s in el)
            for s in el:
                s["aperture"] = {"shape": "circle", "clear": s["patent_semi"], "outer": outer}
    for sf in front + back:
        sf.pop("patent_semi")
    src = {"patent": "CN 111562632 B / US 11,287,618 B2 — I. A. Neil, Anamorphic objective lens system（广东思锐光学 Sirui），2019 优先权",
           "url": "https://patents.google.com/patent/CN111562632B/en",
           "url_us": "https://patents.google.com/patent/US11287618B2/en",
           "table": f"中国授权本表 {[50, 65, 85].index(fl) + 1}（第 {28 + [50, 65, 85].index(fl)} 页，逐位核对）；美国本同号表格印刷错误多，不采用",
           "patent_values": {"fly": float(fl), "flx_approx": fl / 2, "f_number": 3.0, "image_half_x": 10.65, "image_half_y": 8.91}}
    meta = {"name": f"Neil 2× 现代前置变形 {fl} mm（Sirui 专利 CN 111562632 B，f/3）",
            "short_name": f"Neil 2× 现代变形 {fl} mm（真实处方）",
            "order": 6 + [50, 65, 85].index(fl),
            "tagline": "Iain Neil（Panavision 后置变形组的设计者）为思锐设计的现代 2× 前置变形：同一前组配三个可换后组；内对焦、无呼吸式凸轮",
            "note": "专利实施例原样收录（不是 Panavision 镜头）。前变形部分 G1：X 柱面 3 片、Y 柱面 3 片加球面；G1b 两片整体移动对焦。"
                    "像面在专利里取 X/Y 柱面以模拟弯曲物面，这里用平面传感器，所以边缘有该设计本身的场曲。",
            "units": "mm", "conventions": json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))["meta"]["conventions"]}
    rx = {"meta": meta, "glasses": glasses,
          "groups": [{"id": "front", "role": "前变形部分 G1（2.06×）：G1a / G1b（对焦）/ G1c", "scale": 1.0, "aperture_source": "patent",
                      "surfaces": front, "source": src},
                     {"id": "rear", "role": f"后非变形部分 G2（{fl} mm 版），含可调光阑", "scale": 1.0, "aperture_source": "patent",
                      "f_number": 3.0, "stop": stop, "surfaces": back}],
          "image_distance_mm": image_distance,
          "anamorphic": {"desqueeze": 2.06, "type": "front",
                         "note": "专利称约 2×；近轴压缩比 FLY/FLX = 2.06（三个焦段相同，由前变形部分决定），按光学实际值解挤压（与 E/G 一致）"},
          "look": {"reference": "思锐专利实施例；成品镜头规格未公开。最近对焦取专利 FD3：物面到第一面 868 mm",
                   "f_number": 3.0, "coating": "BROADBAND", "coating_nm": 94.0, "flare_color": "PHYSICAL", "flare_gain": 1.0, "veiling_glare": 0.008},
          "focus": {"mechanism": "G1b（L3–L4，S5–S8）整体移动：S4 后间隔减小、S8 后间隔增大同样多（专利 FD1/FD2/FD3：15.511/12.339/9.120 与 2.285/5.458/8.677）。"
                                 "一个自由度同时对准两向（X 残差 ≤ 0.07 mm）。近轴求解的行程比专利多 2–4%（FD2 3.31 vs 3.17、FD3 6.54 vs 6.39 mm），"
                                 "三个后组相同，来自前组：专利按弯曲像面上的最佳聚焦定位，像方相差约 0.05 mm，在 f/3 景深以内",
                    "groups": [{"ids": ["S5", "S6", "S7", "S8"], "moves": [1, 0]}],
                    "solve": ["Ry"], "min_distance_mm": None},
          "sensor": {"format": "专利像面：X ±10.65、Y ±8.91 mm", "width": 21.30, "height": 17.82}}
    # 最近对焦：物面到 S1 868 mm → 从像面量起
    z1 = O.layout(rx)[0]["z"]
    rx["focus"]["min_distance_mm"] = round(868.0 + z1, 1)
    rx["look"]["min_focus_mm"] = rx["focus"]["min_distance_mm"]
    O.save(rx, os.path.join(PDIR, f"neil_2x_{fl}.json"))
    return rx


# ───────── CN 112285889 B（安徽长庚光学 / Venus Optics Laowa，李大勇，2020 申请、2024 授权）实施例 1 ─────────
# 2× 一体式变形广角：负屈光度第一组 G1（最后一片负透镜移动对焦）→ Y 向柱面组 G2 → X 向柱面组 G3 → 含光阑的正第四组 G4。
# 专利：Fx 14.59 / Fy 29.03 mm、Fno 2.9、半画角 ωx 41.5° / ωy 16.7°（真实主光线：像面 24.0 × 17.6 mm，S35 级）。
# 表是图片（说明书 [0057]–[0059]，PDF 第 6–7 页），逐位对照页面图像转录；表头列序为 Ns、Ry、Rx、D、Nd、ABV（Ry 在前）。
# 只给 Nd / Vd（无牌号、无口径、无非球面）→ 两项 Cauchy 色散、口径由 optics.apply_apertures 估算。
LAOWA_EX1 = [   # (面, Ry, Rx, D, Nd, Vd)；Nd 为该面之后的介质
    (1, 93.899, 93.899, 1.800, 1.92103, 21.34), (2, 53.423, 53.423, 8.144, None, None),
    (3, 142.045, 142.045, 7.629, 1.91241, 25.00), (4, -469.695, -469.695, 0.150, None, None),
    (5, 95.141, 95.141, 1.500, 1.89598, 30.91), (6, 39.132, 39.132, "D6", None, None),
    (7, -477.362, -477.362, 1.500, 1.90300, 40.80), (8, 77.321, 77.321, "D8", None, None),
    (9, 40.196, None, 17.000, 1.88255, 38.93), (10, -47.909, None, 1.500, 1.84126, 24.37), (11, 176.465, None, 27.920, None, None),
    (12, -21.485, None, 1.200, 1.49700, 81.61), (13, -34.203, None, 3.000, None, None),
    (14, None, -20.480, 7.563, 1.90864, 21.13), (15, None, -35.748, 0.150, None, None),
    (16, None, -47.914, 2.286, 1.49700, 81.61), (17, None, -26.179, 0.500, None, None),
    (18, -80.348, -80.348, 1.244, 1.60342, 38.01), (19, 40.098, 40.098, 1.138, None, None),
    (20, -244.499, -244.499, 2.272, 1.81799, 23.81), (21, -49.219, -49.219, 0.150, None, None),
    ("STOP", None, None, 3.525, None, None),
    (23, 22.183, 22.183, 4.000, 1.90366, 31.31), (24, 392.186, 392.186, 12.645, None, None),
    (25, -20.218, -20.218, 2.471, 1.69895, 30.05), (26, 33.179, 33.179, 1.155, None, None),
    (27, -86.126, -86.126, 1.200, 1.80610, 33.27), (28, 18.735, 18.735, 5.026, 1.69680, 55.46), (29, -21.152, -21.152, 0.150, None, None),
    (30, 31.919, 31.919, 5.667, 1.72916, 54.67), (31, -17.131, -17.131, 1.172, 1.68893, 31.16), (32, 201.032, 201.032, 38.850, None, None),
]
LAOWA_FOCUS = {"inf": (16.279, 4.000), 1500.0: (15.414, 4.865)}     # D(6)、D(8)；物体距离（物面到第一面，mm）


def _cap_apertures_to_edges(rx, min_glass=0.3, min_air=0.0, step=0.97, iters=400):
    """专利没给口径时，apply_apertures 按光线估的口径可能超出镜片能做到的范围（两面在口径内相交 = 边厚为负）。
    把这样的镜片（含胶合件）口径按比例收回，直到 ∞ 与最近对焦时玻璃边厚 ≥ min_glass、空气间隙 ≥ min_air。
    收回的口径会让角落多一点机械渐晕——这是这支镜头真实的样子。→ {镜片: 收回后的比例}。"""
    by_id = {sf["id"]: sf for g in rx["groups"] for sf in g["surfaces"]}
    el_of = {}
    for ids in O.element_groups(rx):
        for i in ids:
            el_of[i] = ids
    states = [(0.0, 0.0)]
    md = rx.get("focus", {}).get("min_distance_mm")
    if md:
        states.append(O.solve_focus(rx, md))
    scale = {}
    def shrink(sid):
        ids = el_of.get(sid, [sid])
        for i in ids:
            a = by_id[i].get("aperture")
            if not a:
                continue
            for k in ("semi_x", "semi_y", "clear", "outer"):
                if a.get(k):
                    a[k] = round(a[k] * step, 4)
        key = "+".join(ids); scale[key] = round(scale.get(key, 1.0) * step, 4)
    for _ in range(iters):
        bad = []
        for d1, d2 in states:
            for e in O.edge_report(rx, d1, d2):
                lim = min_glass if e["kind"] == "glass" else min_air
                if e["min_gap"] < lim:
                    bad.append((e["min_gap"] - lim, e["between"]))
        if not bad:
            break
        _, between = min(bad)
        a_id, b_id = between.split("→")
        ra = max(v or 0 for v in O._outer_extent(next(sf for sf in O.layout(rx) if sf["id"] == a_id)))
        rb = max(v or 0 for v in O._outer_extent(next(sf for sf in O.layout(rx) if sf["id"] == b_id)))
        shrink(a_id if ra >= rb else b_id)               # 收回口径较大的那一侧
    return scale


def laowa_2x():
    glasses = {"air": {"nd": 1.0, "vd": None}}
    def glass(nd, vd):
        key = f"LW_{int(round(nd * 1e5))}_{int(round(vd * 100))}"
        glasses[key] = {"nd": nd, "vd": vd, "note": "专利只给 Nd / Vd（两项 Cauchy）"}
        return key
    front, rear, stop, cur = [], [], None, None
    cur = front
    d6, d8 = LAOWA_FOCUS["inf"]
    for sid, ry, rx_, d, nd, vd in LAOWA_EX1:
        if sid == "STOP":
            stop = {"after_surface": cur[-1]["id"], "offset_mm": cur[-1]["d"], "f_number": 2.9,
                    "note": "光阑（专利第 22 面）；专利没给孔径，按 Fno 2.9（垂直截面近轴）定"}
            cur[-1]["d"] = round(cur[-1]["d"] + d, 4)
            cur = rear
            continue
        dd = {"D6": d6, "D8": d8}.get(d, d)
        cyl = "YCyl" if rx_ is None else ("XCyl" if ry is None else "Sph")
        cur.append({"id": f"L{sid}", "R_x": rx_, "R_y": ry, "d": dd, "medium": glass(nd, vd) if nd else "air", "shape": cyl})
    src = {"patent": "CN 112285889 B — 李大勇，一种变形宽荧幕镜头及具有其的数码摄影机（安徽长庚光学科技 / Venus Optics Laowa），2020 申请、2024 授权，实施例 1",
           "url": "https://patents.google.com/patent/CN112285889B/zh",
           "pdf": "https://patentimages.storage.googleapis.com/23/f1/bc/d576b5493f6c1a/CN112285889B.pdf",
           "table": "说明书 [0057]–[0059]（PDF 第 6–7 页，表为图片，逐位对照转录）",
           "patent_values": {"Fx": 14.59, "Fy": 29.03, "Fno": 2.9, "omega_x_deg": 41.5, "omega_y_deg": 16.7, "bfl": 38.85,
                             "focus_table": {"inf": list(LAOWA_FOCUS["inf"]), "1500": list(LAOWA_FOCUS[1500.0])}}}
    conv = json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))["meta"]["conventions"]
    meta = {"name": "Laowa 风格 2× 广角变形 29 mm（CN 112285889 B 实施例 1，f/2.9）",
            "short_name": "Laowa 2× 广角 29 mm（真实处方）", "order": 9,
            "tagline": "现代一体式 2× 广角变形：负前组 + 中间 Y / X 柱面组 + 后组光阑；广角下畸变小、覆盖 S35",
            "note": "专利实施例原样收录（是否为某款市售镜头未知）。柱面在中间（不是前置附加镜），广角时比 C 风格的前置变形器畸变小得多。"
                    "专利未给口径与玻璃牌号：口径按 apply_apertures 估算（角落放行 0.6），色散按 Nd / Vd 两项 Cauchy。",
            "units": "mm", "conventions": conv}
    rx = {"meta": meta, "glasses": glasses,
          "groups": [{"id": "front", "role": "G1 负前组（L7–L8 移动对焦）+ G2 Y 向柱面组 + G3 X 向柱面组 + G4 前半", "scale": 1.0,
                      "surfaces": front, "source": src},
                     {"id": "rear", "role": "G4 后半（光阑之后）", "scale": 1.0, "f_number": 2.9, "stop": stop, "surfaces": rear}],
          "image_distance_mm": rear[-1]["d"],
          "anamorphic": {"desqueeze": 2.0, "type": "integrated",
                         "note": "近轴 Fy/Fx = 1.99（专利 29.03 / 14.59）；按 2.0 解挤压"},
          "look": {"reference": "专利实施例 1；成品镜头规格未公开", "f_number": 2.9, "coating": "BROADBAND", "coating_nm": 94.0,
                   "flare_color": "PHYSICAL", "flare_gain": 1.0, "veiling_glare": 0.01, "corner_pupil_fraction": 0.6},
          "focus": {"mechanism": "G1 最靠像面的一片负透镜（L7–L8）移动：D(6) 减小、D(8) 增大同样多（专利 ∞ 16.279 / 4.000，1500 mm 15.414 / 4.865）",
                    "groups": [{"ids": ["L7", "L8"], "moves": [1, 0]}], "solve": ["Ry"], "min_distance_mm": None},
          "sensor": {"format": "专利像面：按主光线追迹，半画角 ωx 41.5° / ωy 16.7° 落在 ±12.0 / ±8.8 mm → 24.0 × 17.6 mm（S35 级）",
                     "width": 24.0, "height": 17.6,
                     "format_circle_mm": 27.52,
                     "format_circle_note": "0.9.1：原光窗 24.0 × 17.6 的四角本身照不到（f/4 时 0）；画幅版本（全画幅 / IMAX）按「四角 ≥ 35%（f/4）」"
                                           "的有效像圈直径 27.52 mm 放大（全画幅 × 1.573 时四角 35%）"}}
    rx = O.normalize(rx)
    z1 = O.layout(rx)[0]["z"]
    rx["focus"]["min_distance_mm"] = round(1500.0 + z1, 1)          # 专利近摄表：物面到第一面 1500 mm
    rx["look"]["min_focus_mm"] = rx["focus"]["min_distance_mm"]
    rx, info = O.apply_apertures(rx)
    shrunk = _cap_apertures_to_edges(rx, min_glass=0.3, min_air=0.0)
    if shrunk:
        print("laowa_2x_29: 口径按边厚收回：", shrunk)
    O.save(rx, os.path.join(PDIR, "laowa_2x_29.json"))
    S = O.layout(rx)
    print(f"laowa_2x_29.json: EFL_x {O.paraxial_infinity(S, 'Rx')[0]:.3f}  EFL_y {O.paraxial_infinity(S, 'Ry')[0]:.3f}  "
          f"BFL_y {O.paraxial_infinity(S, 'Ry')[1]:.3f}  光阑 r {info['r_stop']:.3f}")
    return rx


# ───────── CN 118169848 A（广东思锐光学 / 中山亚众光电，2022-12-08 申请）：全画幅超广角大倍率变形镜头 ─────────
# 同日的实用新型 CN 218886282 U 是同一设计。Y 向焦距 35 mm、变倍比 1.6×、全画幅、2.4:1 画面（= 36 × 1.6 / 24）、
# 18 片 13 组、58 mm 滤镜、< 420 g、长 < 103 mm、内对焦（第 2–3 片）0.7 m–∞——与市售 Sirui Saturn 35 mm T2.9 1.6× 全画幅
# （18 片 13 组、58 mm、415 g、102 mm）逐项吻合。表 1 是图片（说明书 [0066]，PDF 第 9 页），按 4 倍放大的页面逐位转录；
# 列序：透镜、面型、Y 半径、X 半径、厚度、折射率、阿贝数（Y 在前）。只给 nd / vd（三位 / 一位小数），没有口径、没有后截距。
# 第 4–6 片是 X 向柱面（第一柱面组，水平压缩），第 16–17 片是 Y 向柱面；第 18 片两面偶次非球面（表 2）。
SIRUI_FF35 = [   # (面, Ry, Rx, D, nd, vd)；nd 为该面之后的介质；None = 平面（Inf）
    (1, 99.430, 99.430, 2.400, 1.787, 47.7), (2, 28.754, 28.754, "D2", None, None),
    (3, -308.34, -308.34, 2.000, 1.886, 19.4), (4, 70.370, 70.370, 0.200, None, None),
    (5, 45.955, 45.955, 6.441, 1.919, 28.9), (6, -137.35, -137.35, "D6", None, None),
    (7, None, -61.867, 2.400, 1.516, 67.1), (8, None, 20.821, 7.88, None, None),
    (9, None, -65.393, 5.01, 1.730, 23.4), (10, None, 24.333, 7.866, 1.915, 31.3), (11, None, -42.382, 0.300, None, None),
    (12, 46.577, 46.577, 3.11, 1.921, 27.5), (13, None, None, 1.382, None, None),
    (14, -49.587, -49.587, 1.300, 1.561, 39.3), (15, 29.534, 29.534, 6.995, 1.804, 46.6), (16, -15.095, -15.095, 1.300, 1.851, 28.2),
    (17, None, None, 3.00, None, None),
    ("STOP", None, None, 1.176, None, None),
    (18, 22.109, 22.109, 3.544, 1.946, 18.0), (19, -87.010, -87.010, 0.640, None, None),
    (20, 48.705, 48.705, 1.300, 1.676, 26.3), (21, 14.131, 14.131, 3.180, None, None),
    (22, -18.318, -18.318, 1.300, 1.905, 18.7), (23, 13.228, 13.228, 5.528, 1.804, 46.6), (24, -50.491, -50.491, 0.200, None, None),
    (25, 34.084, 34.084, 3.184, 1.946, 17.9), (26, 164.133, 164.133, 0.502, None, None),
    (27, 38.661, None, 5.61, 1.670, 33.2), (28, -24.310, None, 2.20, 1.760, 50.0), (29, 33.557, None, 3.614, None, None),
    (30, 80.037, 80.037, 2.413, 1.807, 41.0), (31, 108.986, 108.986, "BFL", None, None),
]
SIRUI_FF35_ASPH = {30: (-7.8997, [-1.0e-4, -7.424e-7, 8.484e-10, -3.4404e-13]),     # 表 2：面 1 / 面 2（第十八透镜）
                   31: (7.9691, [-6.0433e-5, -7.6463e-7, 3.8716e-9, -6.1160e-12])}
SIRUI_FF35_FOCUS_IDS = ["S3", "S4", "S5", "S6"]                                     # 第 2–3 片（内对焦组）


def sirui_ff35():
    glasses = {"air": {"nd": 1.0, "vd": None}}
    def glass(nd, vd):
        key = f"SR_{int(round(nd * 1e3))}_{int(round(vd * 10))}"
        glasses[key] = {"nd": nd, "vd": vd, "note": "专利只给 nd / vd（两项 Cauchy）"}
        return key
    front, rear, stop, cur = [], [], None, None
    cur = front
    for sid, ry, rx_, d, nd, vd in SIRUI_FF35:
        if sid == "STOP":
            stop = {"after_surface": cur[-1]["id"], "offset_mm": cur[-1]["d"], "f_number": 2.9,
                    "note": "光阑（专利表 1「光阑」行：第十透镜后 3.00 mm、距第十一透镜 1.176 mm）；专利没给孔径，"
                            "按市售 Sirui Saturn 35 mm 的 T2.9 取 f/2.9（垂直截面近轴）"}
            cur[-1]["d"] = round(cur[-1]["d"] + d, 4)
            cur = rear
            continue
        dd = {"D2": 8.490, "D6": 4.554, "BFL": 30.0}.get(d, d)        # BFL 先占位，下面按 Y 向近轴像面求出
        cyl = "YCyl" if rx_ is None and ry is not None else ("XCyl" if ry is None and rx_ is not None else "Sph")
        sf = {"id": f"S{sid}", "R_x": rx_, "R_y": ry, "d": dd, "medium": glass(nd, vd) if nd else "air", "shape": cyl}
        if sid in SIRUI_FF35_ASPH:
            K, A = SIRUI_FF35_ASPH[sid]
            sf["k"] = K; sf["asph"] = A; sf["shape"] = "Asph"
        cur.append(sf)
    src = {"patent": "CN 118169848 A — 全画幅超广角大倍率变形镜头（广东思锐光学 / 中山亚众光电，2022-12-08 申请，2024-06-11 公开）；"
                     "同日实用新型 CN 218886282 U（2023-04-18 授权）为同一设计",
           "url": "https://patents.google.com/patent/CN118169848A/zh",
           "pdf": "https://patentimages.storage.googleapis.com/16/50/78/11e8f07caa79eb/CN118169848A.pdf",
           "table": "说明书 [0066] 表 1、[0068] 表 2（PDF 第 9 页，表为图片，按 4 倍放大逐位转录）",
           "patent_values": {"Fy": 35.0, "squeeze": 1.6, "picture": "2.4:1", "format": "全画幅",
                             "claims": {"fY": [33.0, 37.0], "fX": [20.6, 23.2], "fY/fX": [1.55, 1.65],
                                        "f(2-3)X/f(1-10)Y": [1.00, 1.30], "f(1-3)X/f(4-10)Y": [-3.0, -2.2],
                                        "f(1-10)X/f(11-18)Y": [0.50, 0.55]},
                             "min_object_image_mm": 700.0, "filter_mm": 58, "mass_g": "< 420", "length_mm": "< 103"},
           "product": "Sirui Saturn 35 mm T2.9 1.6× 全画幅（18 片 13 组、58 mm 滤镜、E 口 415 g、长 102 mm、最近对焦 0.9 m）——规格与专利逐项吻合"}
    conv = json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))["meta"]["conventions"]
    meta = {"name": "Sirui 风格 1.6× 全画幅广角变形 35 mm（CN 118169848 A，T2.9）",
            "short_name": "Sirui 1.6× 全画幅 35 mm（真实处方）", "order": 10,
            "tagline": "原生全画幅（36 × 24）的 1.6× 一体式广角变形：前球面组 + X 向柱面组 + 光阑 + Y 向柱面组 + 非球面；全画幅出 2.4:1",
            "note": "专利实施例原样收录；与市售 Sirui Saturn 35 mm T2.9 1.6× 规格逐项吻合（是否完全相同未经厂方确认）。"
                    "专利未给口径、后截距与玻璃牌号：口径按 apply_apertures 估算（角落放行 0.6）并按真实边厚收回，后截距取 Y 向近轴像面，色散按 nd / vd 两项 Cauchy。",
            "units": "mm", "conventions": conv}
    rx = {"meta": meta, "glasses": glasses,
          "groups": [{"id": "front", "role": "第一球面组（S1–S6，第 2–3 片内对焦）+ 第一柱面组 X 向（S7–S11）+ 第二球面组前半（S12–S17）",
                      "scale": 1.0, "surfaces": front, "source": src},
                     {"id": "rear", "role": "第二球面组后半（光阑之后）+ 第二柱面组 Y 向（S27–S29）+ 非球面第 18 片", "scale": 1.0,
                      "f_number": 2.9, "stop": stop, "surfaces": rear}],
          "image_distance_mm": 30.0,
          "anamorphic": {"desqueeze": 1.6, "type": "integrated",
                         "note": "专利：变倍比 1.6×、全画幅出 2.4:1；按 1.6 解挤压（市售标称）。实际：近轴 fY / fX = 1.648；物方 tan 比随视场增大"
                                 "（画宽 25% 1.66、50% 1.69、75% 1.73、边缘 1.78）——水平桶形约 −3%、垂直枕形约 +4%"},
          "look": {"reference": "专利实施例；市售对应 Sirui Saturn 35 mm T2.9 1.6×（最近对焦 0.9 m）", "f_number": 2.9,
                   "coating": "BROADBAND", "coating_nm": 94.0, "flare_color": "PHYSICAL", "flare_gain": 1.0, "veiling_glare": 0.01,
                   "corner_pupil_fraction": 0.6},
          "focus": {"mechanism": "第 2–3 片（S3–S6）整体移动的内对焦：D2（专利 8.490）与 D6（4.554）此消彼长，镜头总长不变；0.7 m（物像距）到 ∞",
                    "groups": [{"ids": SIRUI_FF35_FOCUS_IDS, "moves": [1, 0]}], "solve": ["Ry"], "min_distance_mm": 900.0,
                    "note": "专利说 0.7 m（物像距）；近轴求解在 0.7 m 要 4.60 mm 行程，而 D6 只有 4.554 mm（差 1%，与 Neil / Laowa 同类：专利按最佳像面、"
                            "这里按近轴）→ 最近对焦取市售 Sirui Saturn 35 mm 公布的 0.9 m（此时 D6 剩约 0.9 mm）"},
          "sensor": {"format": "原生全画幅 36 × 24 mm（专利：全画幅、1.6× 出 2.4:1 = 36 × 1.6 / 24）", "width": 36.0, "height": 24.0,
                     "max_gate": [36.0, 24.0]}}
    rx = O.normalize(rx)
    S = O.layout(rx)
    bfl_y = O.paraxial_infinity(S, "Ry")[1]
    rear[-1]["d"] = round(bfl_y, 4)
    rx["groups"][1]["surfaces"][-1]["d"] = round(bfl_y, 4)
    rx["image_distance_mm"] = round(bfl_y, 4)
    rx["look"]["min_focus_mm"] = rx["focus"]["min_distance_mm"]
    rx, info = O.apply_apertures(rx)
    shrunk = _cap_apertures_to_edges(rx, min_glass=0.3, min_air=0.0)
    if shrunk:
        print("sirui_ff_35: 口径按边厚收回：", shrunk)
    O.save(rx, os.path.join(PDIR, "sirui_ff_35.json"))
    S = O.layout(rx)
    print(f"sirui_ff_35.json: EFL_x {O.paraxial_infinity(S, 'Rx')[0]:.3f}  EFL_y {O.paraxial_infinity(S, 'Ry')[0]:.3f}  "
          f"BFL_x {O.paraxial_infinity(S, 'Rx')[1]:.3f}  BFL_y {O.paraxial_infinity(S, 'Ry')[1]:.3f}  光阑 r {info['r_stop']:.3f}")
    return rx


if __name__ == "__main__":
    conv = json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))["meta"]["conventions"]
    # Isco 附加镜为长焦设计（原尺寸配 f=100 主镜：垂直 131 mm、水平 62.7 mm，S35 变形画幅垂直半视场约 4°）。
    # 缩到 50 mm 时视场角是设计值的 2.6 倍，口径与边厚都不成立，所以 E/G 风格取与之匹配的长焦段。
    isco_preset("e_series_style.json",
                {"name": "E 系列风格 · 前置 2.09× 变形 135 mm（Isco 1990 + Nikon f/2，原尺寸）", "short_name": "E 系列风格 135 mm（1980 年代）",
                 "order": 2, "tagline": "现代双子系统前置变形器（两向分别矫正）、凸轮联动对焦；干净的蓝色水平炫光、少雾化",
                 "note": "Panavision E 系列处方未公开。本选项用同年代的 Isco 公开专利前置变形器（实施例 3，原尺寸）+ 公开的 f/2 双高斯主镜（f=100），垂直焦距 131 mm，观感参数按 Panavision 公布的 E 系列 135 mm 规格设定。",
                 "units": "mm", "conventions": conv},
                {"reference": "Panavision E Series 135 mm T2.8，最近对焦 114.3 cm（panavision.com）；页面原文：Clean blue horizontal flares without additional artifacts such as veiling glare or edge reflections",
                 "f_number": 2.8, "min_focus_mm": 1143.0, "mumps_correction": 1.0, "coating_nm": 125.0, "flare_color": "PHYSICAL", "flare_gain": 0.18, "corner_pupil_fraction": 0.55,
                 "veiling_glare": 0.008},
                prime_scale=1.0, att_scale=1.0)
    isco_preset("g_series_style.json",
                {"name": "G 系列风格 · 前置 2.09× 变形 100 mm（Isco 光学缩放 0.763 + Nikon f/2）", "short_name": "G 系列风格 100 mm（2007）",
                 "order": 3, "tagline": "同 E 系列风格的光学结构，缩到垂直 100 mm；T3、抗眩光（炫光弱、偏中性）",
                 "note": "Panavision G 系列处方未公开。光学结构与 E 系列风格相同，整体缩放到垂直焦距 100 mm（视场比原设计大约 30%）；按 Panavision 公布的 G 系列 100 mm 规格设定光圈与最近对焦，并按其「excellent glare resistance」把炫光调弱。",
                 "units": "mm", "conventions": conv},
                {"reference": "Panavision G Series 100 mm T3，最近对焦 91.4 cm（panavision.com）；页面描述：excellent glare resistance、minimal breathing",
                 "f_number": 3.0, "min_focus_mm": 914.0, "mumps_correction": 1.0, "coating_nm": 100.0, "flare_color": "PHYSICAL", "flare_gain": 0.15, "corner_pupil_fraction": 0.75,
                 "veiling_glare": 0.005},
                prime_scale=0.763, att_scale=0.763)
    panavision_rear("Z1")
    panavision_rear("Z4")
    for fl in (50, 65, 85):
        neil_sirui(fl)
    laowa_2x()
    sirui_ff35()
    for fn in ("c_series_style.json", "e_series_style.json", "g_series_style.json"):   # C 是手工维护的 JSON，这一步幂等
        add_compensator_file(fn)
    # 0.9.1：C 风格 50 mm 全画幅版（同焦段、变形器重配成 1.65×、整支重新优化；参数在 tools/reopt_c_series_style_ff.json）
    import reoptimize as RO
    RO.use("c_ff2")
    RO.build_preset(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reopt_c_series_style_ff.json"))
    # 0.9.1：C 风格 50 mm 65 mm 5 齿孔版（1.3×，参照 Ultra Panavision 70 / Hawk65；参数在 tools/reopt_c_series_style_65.json）
    RO.use("c_65")
    RO.build_preset(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reopt_c_series_style_65.json"))
    for fn in ("e_series_style.json", "panavision_rear_134.json", "panavision_rear_134_tele.json",
               "neil_2x_50.json", "neil_2x_65.json", "neil_2x_85.json"):
        rx = O.load(os.path.join(PDIR, fn))
        S = O.layout(rx)
        fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
        print(f"{fn}: EFL_x {fx:.3f}  EFL_y {fy:.3f}  squeeze {fy / fx:.4f}  BFL_y {O.paraxial_infinity(S, 'Ry')[1]:.3f}")
