# 由专利转录数据生成预设处方 JSON（E / G 系列风格、Panavision 后置 1.34×）。
# 转录来源与核对见各预设 JSON 的 source 字段；生成后用 python3 tools/rebuild_apertures.py 估算口径。
# 运行：python3 tools/build_presets.py
import json, os, sys, copy
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


def isco_preset(fname, meta, look, prime_scale, att_scale):
    prime, glasses = nikon_prime(prime_scale, look["f_number"])
    glasses["I_LAFN21"] = {"nd": LAFN21[0], "vd": abbe(LAFN21), "note": "LaF N21（Isco 原表 n_d/n_C/n_F）"}
    glasses["I_SF11"] = {"nd": SF11[0], "vd": abbe(SF11), "note": "SF 11（Isco 原表 n_d/n_C/n_F）"}
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


def panavision_rear():
    glasses = {"air": {"nd": 1.0, "vd": None}}
    groups = {"zoom": [], "anamorph": []}
    stop = None
    elem_outer, cur = {}, []
    pending_d = 0.0
    rows = [r for r in PV_TABLE_I if r[1] not in ("S62",)]
    out = []
    for item, sid, shape, ry, rx_, d, code, rad, hy, hx in rows:
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
    zoom = [s for s in out if int(s["id"][1:]) <= 46]
    ana = [s for s in out if int(s["id"][1:]) >= 47]
    image_distance = ana[-1]["d"]
    meta = {"name": "Panavision 后置 1.34× 变形（US 7,085,066 原处方：变焦镜 + 8 片后置变形组）",
            "short_name": "Panavision 后置 1.34×（真实处方）", "order": 4,
            "tagline": "唯一公开的 Panavision 变形处方：后置变形组，16:9 画幅解挤压到 2.40:1；变焦镜取短焦端",
            "note": "Iain A. Neil 为 Panavision 设计，2004 申请、2006 授权。后置变形组位于变焦镜与反光镜之间约 26 mm 内；前 46 面是 US 6,122,111 的变焦镜（短焦端、无穷远）。",
            "units": "mm", "conventions": json.load(open(os.path.join(PDIR, "c_series_style.json"), encoding="utf-8"))["meta"]["conventions"]}
    rx = {"meta": meta, "glasses": glasses,
          "groups": [
              {"id": "zoom", "role": "US 6,122,111 变焦镜，短焦端 F1/Z1，无穷远", "scale": 1.0,
               "aperture_source": "patent", "f_number": 3.66, "stop": stop, "surfaces": zoom,
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
          "look": {"reference": "Panavision 2004 年后置变形组（专利实施例）；无公开的成品镜头规格",
                   "f_number": 3.66, "min_focus_mm": 1000.0, "coating_nm": 100.0, "flare_color": "PHYSICAL", "flare_gain": 0.6},
          "focus": {"mechanism": "整体移动（近似）：原变焦镜的对焦组在 US 6,122,111 中，未转录；这里整支镜头一起移动，只解垂直截面",
                    "groups": [{"ids": [s["id"] for s in out], "moves": [1, 0]}], "solve": ["Ry"], "min_distance_mm": 1000.0},
          "sensor": {"format": "16:9（3 齿孔 35 mm 或同尺寸数字传感器），像高半径 13.60 mm", "width": 23.71, "height": 13.34}}
    O.save(rx, os.path.join(PDIR, "panavision_rear_134.json"))
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
                 "f_number": 2.8, "min_focus_mm": 1143.0, "coating_nm": 125.0, "flare_color": "PHYSICAL", "flare_gain": 0.18, "corner_pupil_fraction": 0.55},
                prime_scale=1.0, att_scale=1.0)
    isco_preset("g_series_style.json",
                {"name": "G 系列风格 · 前置 2.09× 变形 100 mm（Isco 光学缩放 0.763 + Nikon f/2）", "short_name": "G 系列风格 100 mm（2007）",
                 "order": 3, "tagline": "同 E 系列风格的光学结构，缩到垂直 100 mm；T3、抗眩光（炫光弱、偏中性）",
                 "note": "Panavision G 系列处方未公开。光学结构与 E 系列风格相同，整体缩放到垂直焦距 100 mm（视场比原设计大约 30%）；按 Panavision 公布的 G 系列 100 mm 规格设定光圈与最近对焦，并按其「excellent glare resistance」把炫光调弱。",
                 "units": "mm", "conventions": conv},
                {"reference": "Panavision G Series 100 mm T3，最近对焦 91.4 cm（panavision.com）；页面描述：excellent glare resistance、minimal breathing",
                 "f_number": 3.0, "min_focus_mm": 914.0, "coating_nm": 100.0, "flare_color": "PHYSICAL", "flare_gain": 0.15, "corner_pupil_fraction": 0.75},
                prime_scale=0.763, att_scale=0.763)
    panavision_rear()
    for fn in ("e_series_style.json", "panavision_rear_134.json"):
        rx = O.load(os.path.join(PDIR, fn))
        S = O.layout(rx)
        fx = O.paraxial_infinity(S, "Rx")[0]; fy = O.paraxial_infinity(S, "Ry")[0]
        print(f"{fn}: EFL_x {fx:.3f}  EFL_y {fy:.3f}  squeeze {fy / fx:.4f}  BFL_y {O.paraxial_infinity(S, 'Ry')[1]:.3f}")
