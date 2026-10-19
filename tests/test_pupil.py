# 出瞳重要性采样表的覆盖性（无偏的前提）与光谱表的白平衡。纯 Python：python3 tests/test_pupil.py
# 随机传感器点 × 随机出瞳点 × 随机波长 × 随机对焦距离，真实追迹；所有能穿过镜头的光线，
# 其出瞳点都必须落在 OSL 同款插值 + 镜像得到的采样盒里。
import math, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.optics as O
import blenderlens.oslgen as G
import blenderlens.pupil as PU

def ok(cond, msg):
    print(("ok  " if cond else "FAIL") + "  " + msg)
    if not cond:
        raise SystemExit(1)

T = G.spectral_table()
w = np.array(T["w"]); p = np.diff([0.0] + T["cdf"])
ok(np.allclose((w * p).sum(axis=1), 1.0, atol=1e-9), f"spectral table: flat spectrum stays white {(w * p).sum(axis=1).round(6)}")
ok((w >= 0).all(), "spectral table: weights non-negative")

def box_at(Tb, sx, sy, W, H):
    """与 OSL 相同的插值与镜像。"""
    NX, NY = Tb["NX"], Tb["NY"]
    fx = abs(sx) / (0.5 * W) * (NX - 1); fy = abs(sy) / (0.5 * H) * (NY - 1)
    ix = min(int(math.floor(fx)), NX - 2); iy = min(int(math.floor(fy)), NY - 2)
    tx = min(max(fx - ix, 0.0), 1.0); ty = min(max(fy - iy, 0.0), 1.0)
    def bil(A):
        return (A[ix, iy] * (1 - tx) + A[ix + 1, iy] * tx) * (1 - ty) + (A[ix, iy + 1] * (1 - tx) + A[ix + 1, iy + 1] * tx) * ty
    x0, x1, y0, y1 = bil(Tb["X0"]), bil(Tb["X1"]), bil(Tb["Y0"]), bil(Tb["Y1"])
    if sx < 0: x0, x1 = -x1, -x0
    if sy < 0: y0, y1 = -y1, -y0
    return x0, x1, y0, y1

rng = np.random.default_rng(5)
for fn in [f for f, _, _ in O.list_presets()]:
    rx = O.load(os.path.join(O.PRESCRIPTION_DIR, fn))
    Tb = PU.table(rx)
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    z_exp0, r_exp, z_stop0, r_stop = G.exit_pupil(rx)
    r_samp = r_exp * 1.4
    st_id = O.stop_group(rx)["stop"]["after_surface"]
    mfd = rx.get("focus", {}).get("min_distance_mm") or 1000.0
    n_pass = n_out = 0
    import copy
    levels = (0.0, 0.5, 1.0) if O.compensator(rx) else (None,)      # 发腮补偿：表要覆盖所有补偿强度
    for trial, lv in [(tr, lv) for tr in range(6) for lv in levels]:
        fd = [math.inf, mfd, 1.6 * mfd, 3.3 * mfd, 7.0 * mfd, 30.0 * mfd][trial]
        rxl = rx
        if lv is not None:
            rxl = copy.deepcopy(rx); rxl["focus"]["compensator"]["level"] = lv
        d1, d2 = O.solve_focus(rxl, fd)
        S = O.layout(rxl, d1, d2)
        mv = next(sf["move"] for sf in S if sf["id"] == st_id)
        shift = mv[0] * d1 + mv[1] * d2
        n = 40000
        sx = rng.uniform(-W / 2, W / 2, n); sy = rng.uniform(-H / 2, H / 2, n)
        r = r_samp * np.sqrt(rng.uniform(0, 1, n)); th = rng.uniform(0, 2 * math.pi, n)
        qx, qy = r * np.cos(th), r * np.sin(th)
        P = np.column_stack([sx, sy, np.zeros(n)])
        D = np.column_stack([qx - sx, qy - sy, np.full(n, z_exp0 + shift)])
        D /= np.linalg.norm(D, axis=1)[:, None]
        lam = rng.uniform(0.40, 0.70)
        m = PU._passes(S, z_stop0 + shift, r_stop, P, D, lam)
        for i in np.nonzero(m)[0]:
            x0, x1, y0, y1 = box_at(Tb, sx[i], sy[i], W, H)
            n_pass += 1
            if not (x0 <= qx[i] <= x1 and y0 <= qy[i] <= y1):
                n_out += 1
    ok(n_out == 0, f"{fn}: all {n_pass} passing rays inside the sampling box (outside: {n_out}); "
                   f"efficiency centre {Tb['eff_disc'][0, 0]:.2f}→{Tb['eff_box'][0, 0]:.2f}, corner {Tb['eff_disc'][-1, -1]:.2f}→{Tb['eff_box'][-1, -1]:.2f}")
print("ALL PUPIL TESTS PASSED")
