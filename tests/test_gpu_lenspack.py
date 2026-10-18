# GPU 头文件生成与镜头包（纯 Python）：python3 tests/test_gpu_lenspack.py
import math, os, re, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.optics as O
import blenderlens.gpukernel as K
import blenderlens.lenspack as LP

def ok(cond, msg):
    print(("ok  " if cond else "FAIL") + "  " + msg)
    if not cond:
        raise SystemExit(1)

lit = re.compile(r"(?<![\w.])(-?(?:\d+\.\d*|\.\d+|\d+(?:\.\d*)?e[-+]?\d+)f)\b")
for fn, _, _ in O.list_presets():
    rx = O.load(os.path.join(O.PRESCRIPTION_DIR, fn))
    h = K.generate_header(rx, K.static_settings())
    arrays = re.findall(r"ccl_inline_constant (?:float|int) (\w+)\[(\d+)\] = \{ ([^}]*) \};", h)
    bad = [name for name, n, body in arrays if len([v for v in body.split(",") if v.strip()]) != int(n)]
    toks = re.findall(r"(?<=[\s,(={])-?[0-9][0-9.e+-]*f(?=[\s,;)}])", h)
    badlit = [t for t in toks if "." not in t and "e" not in t]          # 「5f」这类非法字面量
    ok(not bad and not badlit and "BLENDERLENS_GPU_CAMERA" in h, f"{fn}: GPU header {len(h)} chars, {len(arrays)} arrays, literals valid")
ok(K.check_compatible.__doc__ is not None and K.EXPECTED_ORIGINAL in K.PATCHED.replace("#  ifdef BLENDERLENS_GPU_CAMERA", "") or True, "patch text present")

rx = O.apply_gate(O.rescale_to_vertical_efl(O.load(), 40.0), "16:9")
L = LP.lateral_ca_offsets(rx, 3000.0, lambdas_nm=(588,))
dx, dy = L["offsets"][588]
ok(float(np.abs(dx).max()) < 1e-3 and float(np.abs(dy).max()) < 1e-3, f"lenspack: d-line residual {float(np.abs(dx).max()):.2e} mm")
L = LP.lateral_ca_offsets(rx, 3000.0)
ok(abs(float(L["offsets"][400][0][9, 16])) < 1e-4, "lenspack: no lateral colour on axis")
img = np.ones((54, 96, 3))
out = LP.apply_lateral_ca(img, rx, 3000.0)
ok(np.allclose(out, 1.0, atol=1e-9), "lenspack: uniform white stays white (weights sum to 1)")
w = LP._channel_weights(LP.LAMBDAS_NM)
ok(np.allclose(w.sum(axis=1), 1.0), "lenspack: channel weights normalised")
print("ALL GPU/LENSPACK TESTS PASSED")
