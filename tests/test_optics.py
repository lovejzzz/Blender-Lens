# 光学核心单测（Blender 外运行）：python3 tests/test_optics.py
import math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "blenderlens"))
import optics as O

def close(a, b, tol, msg):
    assert abs(a - b) <= tol, f"{msg}: {a} vs {b} (tol {tol})"
    print(f"ok  {msg}: {a:.6g}")

rx = O.load()

# 1. Cauchy：N-BK7 B ≈ 0.00422 μm²（文档第一节表）
A, B = O.cauchy(1.5168, 64.17)
close(B, 0.00422, 2e-5, "Cauchy B (N-BK7)")
close(A, 1.5046, 2e-4, "Cauchy A (N-BK7)")

# 2. 解析梯度 vs 数值梯度（含二次曲面常数）
for (x, y, cx, cy, kx, ky) in [(7, 3, 1/50, 1/50, 0, 0), (20, -5, -1/120, 0, 0, 0), (4, 9, 1/30, 1/80, -0.6, 0.3)]:
    s, gx, gy = O.sag_and_grad(x, y, cx, cy, kx, ky)
    h = 1e-6
    nx = (O.sag_and_grad(x + h, y, cx, cy, kx, ky)[0] - O.sag_and_grad(x - h, y, cx, cy, kx, ky)[0]) / (2 * h)
    ny = (O.sag_and_grad(x, y + h, cx, cy, kx, ky)[0] - O.sag_and_grad(x, y - h, cx, cy, kx, ky)[0]) / (2 * h)
    close(gx, nx, 1e-6, f"grad x @({x},{y})")
    close(gy, ny, 1e-6, f"grad y @({x},{y})")
# 球面 R=50 在 r=10 处法线倾角 = asin(10/50)（文档里的单测数值）
s, gx, gy = O.sag_and_grad(10, 0, 1/50, 1/50)
close(math.degrees(math.atan(gx)), math.degrees(math.asin(10 / 50)), 1e-9, "normal tilt R50 r10 (deg)")

# 3. 近轴：主镜 / 变形器 / 整支
S = O.flatten(rx)
prime = [s for s in S if s["group"] == "prime"]
ana = [s for s in S if s["group"] == "anamorph"]
close(O.paraxial_infinity(prime, "Ry")[0], 50.0, 1e-3, "prime EFL (mm)")
close(O.paraxial_infinity(prime, "Ry")[1], 74.837 / 2, 2e-3, "prime BFL (mm)")
fa = O.paraxial_infinity(ana[:5], "Rx")[0]
fb = O.paraxial_infinity(ana[5:], "Rx")[0]
close(fa, -74.74, 0.02, "anamorph front member f (patent -74.74)")
close(fb, 149.88, 0.02, "anamorph rear member f (patent 149.88)")
efl_x = O.paraxial_infinity(S, "Rx")[0]; efl_y = O.paraxial_infinity(S, "Ry")[0]
close(efl_y / efl_x, 2.011, 2e-3, "squeeze at infinity")

# 4. 真实光线 vs 近轴：小视场主光线落点 ≈ EFL·tanθ（反向追迹：从像面小高度出发）
L = O.layout(rx)
z_stop, r_stop = O.stop_geometry(rx, L)
for axis, efl in (("x", efl_x), ("y", efl_y)):
    h = 0.05
    pt = (h, 0.0) if axis == "x" else (0.0, h)
    d = O.aim_through_stop(L, pt, (0.0, 0.0), z_stop)
    p, dout = O.trace_reverse(L, (pt[0], pt[1], 0.0), d, clip=False)
    ang = math.atan2(dout[0] if axis == "x" else dout[1], dout[2])   # 物方主光线角
    close(-math.tan(ang) * efl, h, 2e-4, f"real vs paraxial image height ({axis})")

# 5. f/2：垂直截面轴上边缘光线出射到物方应平行，入射高度 = EFL/(2N)
d = O.aim_through_stop(L, (0.0, 0.0), (0.0, r_stop), z_stop)
p, dout = O.trace_reverse(L, (0.0, 0.0, 0.0), d, clip=False)
close(abs(dout[1] / dout[2]), 0.0, 2e-3, "axial marginal ray collimated in object space (y)")

# 6. 合焦：轴上 1/4 光瞳的等效像面弥散应很小（全孔径时的球差 ~0.02–0.03 mm 属真实像差）
def blur(field, frac):
    out = []
    for sx, sy in ((0, 0), (r_stop * frac, 0), (0, r_stop * frac)):
        d = O.aim_through_stop(L, field, (sx, sy), z_stop)
        q = O.trace_reverse(L, (field[0], field[1], 0.0), d, clip=False)[1]
        out.append((q[0] / q[2], q[1] / q[2]))
    return (out[1][0] - out[0][0]) * efl_x, (out[2][1] - out[0][1]) * efl_y
bx, by = blur((0, 0), 0.25)
close(abs(bx), 0.0, 0.005, "on-axis 1/4-pupil blur x (mm)")
close(abs(by), 0.0, 0.005, "on-axis 1/4-pupil blur y (mm)")
bx, by = blur((0, 0), 1.0)
print(f"    info: on-axis full-aperture f/2 transverse SA  x {bx:+.4f} mm, y {by:+.4f} mm")

# 7. 对焦：1 m 合焦后两截面像点都落在像面
d1, d2 = O.solve_focus(rx, 1000.0)
L1 = O.layout(rx, d1, d2)
close(O._image_z(L1, "Ry", 1000.0), 0.0, 1e-6, "focus 1 m: vertical image at sensor")
close(O._image_z(L1, "Rx", 1000.0), 0.0, 1e-6, "focus 1 m: horizontal image at sensor")
print(f"    Δ1 = {d1:.4f} mm, Δ2 = {d2:.4f} mm, squeeze(1 m) = {O.squeeze_at(rx, 1000.0):.4f}, squeeze(∞) = {O.squeeze_at(rx, math.inf):.4f}")
print("ALL OPTICS TESTS PASSED")
