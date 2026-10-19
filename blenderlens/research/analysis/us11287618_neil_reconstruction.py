import sys, math, itertools, json, copy
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
import blenderlens.optics as O
cat = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "glass", "ohara.json")))["glasses"]
F = None
def G(name):
    r = cat[name]; return dict(nd=r["nd"], vd=r["vd"], note="Ohara " + name)
def C(code):
    return dict(nd=1 + int(code[:3]) / 1000, vd=int(code[3:]) / 10, note="code " + code)

# (id, Rx, Ry, d, glass-key or None)
FRONT = [
 ("S1", 700.562, 700.002, 4.429, "L1"), ("S2", -1525.0, -1525.0, 5.519, None),
 ("S3", -101.063, F, 4.000, "L2"), ("S4", 76.335, F, 15.511, None),
 ("S5", -422.684, -422.634, 3.000, "L3"), ("S6", 106.119, 106.119, 5.416, None),
 ("S7", F, "S7", 3.200, "L4"), ("S8", F, 258.236, 2.286, None),
 ("S9", F, 243.348, 10.640, "L5"), ("S10", F, "S10", 0.853, None),
 ("S11", F, -49.911, 7.066, "L6"), ("S12", F, -73.368, 10.492, None),
 ("S13", F, F, 3.000, "L7"), ("S14", 79.058, F, 2.000, None),
 ("S15", 70.533, 70.533, 18.758, "L8"), ("S16", 67.370, 67.370, 7.528, None),
 ("S17", 87.531, F, 8.543, "L9"), ("S18", -68.533, F, 2.000, None),
 ("S19", 50.729, 50.719, 4.582, "L10"), ("S20", 337.165, 337.165, 0.976, None),
 ("S21", 4393.823, 4393.823, 2.458, "L11"), ("S22", 43.481, 43.481, "S22d", None)]
FRONT_GLASS = dict(L1=G("S-TIH53"), L2=G("S-FPL51"), L3=G("S-FPL51"), L4=G("S-LAH65V"), L5=G("S-PHM52"), L6=G("S-TIH53"),
                   L7=G("S-BAM4"), L8=G("S-TIH6"), L9=G("S-FPL51"), L10=G("S-LAH55V"), L11=G("S-BAM4"))
REAR = {
 "T1": dict(s22=4.786, bfl=47.876, fly=50.0, rows=[
   ("S23", 84.664, 2.744, "R12"), ("S24", -195.007, 6.801, None), ("S25", -22.376, 1.500, "R13"), ("S26", 18.038, 2.794, None),
   ("S27", 1028.618, 3.116, "R14"), ("S28", -31.996, 2.268, None), ("STOP", None, 3.844, None),
   ("S30", 84.567, 7.600, "R15"), ("S31", -18.917, 0.500, None), ("S32", 34.942, 2.686, "R16"), ("S33", 34.085, 0.589, None),
   ("S34", 36.878, 1.800, "R17"), ("S35", 18.525, 47.876, None)],
   glass={"R12": [C("768528"), G("S-YGH51")], "R13": [G("S-TIM8")], "R14": [C("581359"), G("S-TIL25")], "R15": [G("S-FPL51")],
          "R16": [C("758623"), G("S-YGH51")], "R17": [C("726285"), G("S-TIH10")]}),
 "T3": dict(s22=4.720, bfl=42.000, fly=85.0, rows=[
   ("S23", 21.900, 6.511, "R12"), ("S24", 501.664, 0.490, None), ("S25", 20.222, 3.889, "R13"), ("S26", 42.462, 2.091, None),
   ("S27", 600.375, 1.600, "R14"), ("S28", 14.606, 4.723, None), ("STOP", None, 16.316, None),
   ("S30", -15.323, 1.598, "R15"), ("S31", -24.929, 2.235, None), ("S32", -191.227, 2.317, "R16"), ("S33", -28.256, 42.000, None)],
   glass={"R12": [C("497536"), G("S-FPL51")], "R13": [C("497536"), G("S-FPL51")], "R14": [C("848451"), G("S-TIL1")],
          "R15": [C("786276"), G("S-TIH4"), G("S-TIH23")], "R16": [C("786363"), G("S-LAM7")]}),
}

def build(t, s7, s10, gsel):
    surfs = []; glasses = {"air": {"nd": 1.0, "vd": None}}
    R = REAR[t]
    for sid, rx, ry, d, gk in FRONT:
        ry = {"S7": s7, "S10": s10}.get(ry, ry) if isinstance(ry, str) else ry
        d = R["s22"] if d == "S22d" else d
        med = "air"
        if gk:
            glasses[gk] = FRONT_GLASS[gk]; med = gk
        surfs.append({"id": sid, "R_x": rx, "R_y": ry, "d": d, "medium": med})
    for sid, r, d, gk in R["rows"]:
        if sid == "STOP":
            surfs[-1]["d"] += d; stop_after = surfs[-1]["id"]; stop_off = surfs[-1]["d"] - d
            continue
        med = "air"
        if gk:
            glasses[gk] = R["glass"][gk][gsel[gk]]; med = gk
        surfs.append({"id": sid, "R_x": r, "R_y": r, "d": d, "medium": med})
    image_d = surfs[-1]["d"]
    rx = {"meta": {"name": "neil", "conventions": {}}, "glasses": glasses,
          "groups": [{"id": "all", "scale": 1.0, "surfaces": surfs, "stop": {"after_surface": stop_after, "offset_mm": stop_off, "semi_diameter": 7.85, "f_number": 2.8}}],
          "sensor": {"width": 24.9, "height": 17.8}, "focus": {"groups": [], "solve": ["Ry"]}}
    return rx

def evaluate(rx):
    rxn = O.normalize(copy.deepcopy(rx))
    S = O.layout(rxn)
    fy, bfy = O.paraxial_infinity(S, "Ry")[:2]
    fx, bfx = O.paraxial_infinity(S, "Rx")[:2]
    return fy, fx, bfy, bfx

for t in ("T1", "T3"):
    R = REAR[t]
    keys = sorted(R["glass"])
    res = []
    for s7 in (-138.368, -139.348):
        for s10 in (41.527, -41.527):
            for combo in itertools.product(*[range(len(R["glass"][k])) for k in keys]):
                gsel = dict(zip(keys, combo))
                try:
                    fy, fx, bfy, bfx = evaluate(build(t, s7, s10, gsel))
                except Exception as e:
                    continue
                res.append((abs(fy - R["fly"]) / R["fly"] + abs(bfy - R["bfl"]) / R["bfl"], s7, s10,
                            {k: R["glass"][k][v]["note"] for k, v in gsel.items()}, round(fy, 3), round(fx, 3), round(bfy, 3), round(bfx, 3)))
    res.sort(key=lambda r: r[0])
    print(f"=== {t}: target FLY {R['fly']} FLX {R['fly'] / 2} BFL {R['bfl']}")
    for r in res[:8]:
        print(f"  score {r[0]:.4f} S7 {r[1]} S10 {r[2]} FLY {r[4]} FLX {r[5]} BFLy {r[6]} BFLx {r[7]} {r[3]}")
