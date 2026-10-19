# 把 refractiveindex.info 数据库（CC0）里的 Ohara 光学玻璃 YAML 汇编成一个紧凑的目录：
#   blenderlens/glass/ohara.json  {牌号: {code, nd, vd, formula, c, range}}
# formula 2（Sellmeier）：n² − 1 = Σ Bᵢ λ² / (λ² − Cᵢ)，c = [0, B1, C1, B2, C2, B3, C3]
# formula 3（多项式）：n² = c0 + Σ c₂ₖ₋₁ λ^c₂ₖ（c = [c0, c1, p1, c2, p2, …]）
# 运行：python3 tools/glass_catalog.py <YAML 目录>
import json, math, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "blenderlens", "glass", "ohara.json")


def parse(path):
    t = open(path, encoding="utf-8").read()
    m = re.search(r"type: formula (\d)\s+wavelength_range: ([\d.]+) ([\d.]+)\s+coefficients: ([^\n]+)", t)
    if not m:
        return None
    rec = dict(formula=int(m.group(1)), range=[float(m.group(2)), float(m.group(3))],
               c=[float(v) for v in m.group(4).split()])
    for key, pat in (("nd", r"\bnd: ([\d.]+)"), ("vd", r"\bVd: ([\d.]+)"), ("code", r"glass_code: (\w+)")):
        mm = re.search(pat, t)
        if mm:
            rec[key] = mm.group(1) if key == "code" else float(mm.group(1))
    return rec


def index(rec, lam_um):
    c, l2 = rec["c"], lam_um * lam_um
    if rec["formula"] == 2:
        s = 1.0
        for k in range(1, len(c) - 1, 2):
            s += c[k] * l2 / (l2 - c[k + 1])
        return math.sqrt(s)
    if rec["formula"] == 3:
        s = c[0]
        for k in range(1, len(c) - 1, 2):
            s += c[k] * lam_um ** c[k + 1]
        return math.sqrt(s)
    raise ValueError(rec["formula"])


def main(src):
    cat = {}
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".yml"):
            continue
        rec = parse(os.path.join(src, fn))
        if rec is None:
            continue
        name = fn[:-4]
        # 自检：公式算出的 nd / vd 与目录值一致
        nd = index(rec, 0.5875618); nF = index(rec, 0.4861327); nC = index(rec, 0.6562725)
        rec["check_nd"] = round(nd - rec.get("nd", nd), 7)
        rec["check_vd"] = round((nd - 1) / (nF - nC) - rec.get("vd", (nd - 1) / (nF - nC)), 4)
        cat[name] = rec
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"source": "refractiveindex.info database（CC0），OHARA Zemax catalog 2017-11-30", "glasses": cat},
              open(OUT, "w"), indent=0, separators=(",", ":"))
    worst_nd = max(abs(r["check_nd"]) for r in cat.values())
    worst_vd = max(abs(r["check_vd"]) for r in cat.values())
    print(f"{len(cat)} glasses → {OUT}；公式自检 |Δnd| ≤ {worst_nd:.1e}，|ΔVd| ≤ {worst_vd:.3f}")


if __name__ == "__main__":
    main(sys.argv[1])
