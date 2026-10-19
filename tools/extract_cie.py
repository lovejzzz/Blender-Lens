# 从 Blender 自带的 Cycles 内核源码（kernel/tables.h）取出 CIE 1931 色匹配函数、D65 光谱与归一化常数，
# 写成 blenderlens/spectral/cie_d65.json —— 让 OSL（CPU）与 Python 用和 Cycles GPU 内核逐位相同的数据。
# CIE 数据本身是公开标准数据。运行：python3 tools/extract_cie.py [Blender.app 路径]
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = sys.argv[1] if len(sys.argv) > 1 else "/Applications/Blender.app"
src = sorted(glob.glob(os.path.join(app, "Contents/Resources/*/scripts/addons_core/cycles/source/kernel/tables.h")))[-1]
t = open(src, encoding="utf-8").read()

def num(name):
    return float(re.search(rf"#define {name} ([0-9.eE+-]+)f?", t).group(1))

def table(name):
    body = re.search(rf"{name}\[\](?:\[3\])? = \{{(.*?)\}};", t, re.S).group(1)
    return [float(v.rstrip("f")) for v in re.findall(r"[-+]?\d*\.\d+(?:[eE][-+]?\d+)?f?", body)]

cs = open(src.replace("tables.h", "util/colorspace.h"), encoding="utf-8").read()
def const(name):
    return float(re.search(rf"constexpr float {name} = ([0-9.eE+-]+)f;", cs).group(1))
cmf = table("cie_color_match")
out = dict(source=src.split("/Contents/Resources/")[-1], lambda_min_um=num("WAVELENGTH_CIE_MIN"),
           lambda_max_um=num("WAVELENGTH_CIE_MAX"), dlambda_um=num("WAVELENGTH_DLAMBDA"),
           resolution=int(num("WAVELENGTH_RESOLUTION")), d65_normalization=num("CIE_D65_NORMALIZATION"),
           cmf_xyz=[cmf[i:i + 3] for i in range(0, len(cmf), 3)], d65_spd=table("cie_d65_spd"),
           sampler=dict(note="Cycles sample_wavelength(): 逻辑斯蒂拟合 D65 亮度（intern/cycles/app/cie_d65_luminance_fit.py）",
                        a=const("a"), x0=const("x0"), y0=const("y0"), N=const("N")))
assert len(out["cmf_xyz"]) == out["resolution"] + 1 and len(out["d65_spd"]) >= out["resolution"] + 1, (len(out["cmf_xyz"]), len(out["d65_spd"]))
dst = os.path.join(ROOT, "blenderlens", "spectral", "cie_d65.json")
json.dump(out, open(dst, "w"), indent=0)
print(dst, len(out["cmf_xyz"]), "rows")
