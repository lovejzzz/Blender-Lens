# Neil 2×（CN 111562632 B）的场曲：平面传感器上各视场的最佳聚焦面（X、Y 两向分别），
# 对照专利把像面设成的 X/Y 柱面（表 1–3 的 S36 / S34，FD1 无穷远）。
# 方法：从无穷远（极远的点光源）向整个前片发光线网格，追到像面（z = 0）；对每个视场，
# 求使 X（或 Y）方向光斑方差最小的轴向偏移 δ = −cov(X, tx) / var(tx)（tx = Dx/Dz），即该向的最佳聚焦位置。
# 专利像面的矢高：z = −(x²/(2Rx) + y²/(2Ry))（R < 0：边缘弯向物方，与本项目「R > 0 曲率中心在像方」约定一致）。
# 运行：python3 blenderlens/research/analysis/neil_field_curvature.py
import math, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
import blenderlens.optics as O
import blenderlens.flare as FL

PATENT_IMAGE_R = {50: (-744.936, -390.695), 65: (-828.990, -342.298), 85: (-732.441, -320.642)}   # FD1 的 (Rx, Ry)


def best_focus(lens, field_x, field_y, n=140):
    """视场点（传感器 mm）→ (δx, δy)：两向最佳聚焦相对传感器平面的轴向偏移（mm，+ = 朝物方）。"""
    # 先用主光线方向估计该视场在物方的角度：从传感器点射向出瞳中心反追
    S = lens.S
    z_stop = lens.z_stop
    d = O.aim_through_stop(S, (field_x, field_y), (0.0, 0.0), z_stop)
    pnt, dd = O.trace_reverse(S, (field_x, field_y, 0.0), d, clip=False)
    far = 1.0e7
    src = np.array(pnt) + far * np.array(dd)
    P, D, wt = FL._source_rays(lens, tuple(src), n)
    idx, X, Y, w, Dout = lens.trace(P, D, FL.Lens.seq_direct(len(S)), O.LAMBDA_D, True, 0.0, 1.38)
    tx, ty = Dout[:, 0] / Dout[:, 2], Dout[:, 1] / Dout[:, 2]
    # 像面上 z = δ 处的位置：X + δ·tx（光沿 −Z，z 增大 = 朝物方）
    def delta(V, t):
        tc, vc = t - t.mean(), V - V.mean()
        return -float((vc * tc).sum() / (tc * tc).sum())
    return delta(X, tx), delta(Y, ty), len(idx)


def main():
    rows = []
    for fl, (Rx, Ry) in PATENT_IMAGE_R.items():
        rx = O.load(os.path.join(O.PRESCRIPTION_DIR, f"neil_2x_{fl}.json"))
        lens = FL.Lens(rx, math.inf, 3.0)
        W, H = rx["sensor"]["width"], rx["sensor"]["height"]
        dx0, dy0, _ = best_focus(lens, 0.0, 0.0)
        print(f"=== {fl} mm（轴上最佳聚焦 δx {dx0:+.4f}  δy {dy0:+.4f} mm，以此为零点）")
        for fx, fy in ((0.5, 0.0), (1.0, 0.0), (0.0, 0.5), (0.0, 1.0), (0.7, 0.7), (1.0, 1.0)):
            x, y = fx * W / 2, fy * H / 2
            dx, dy, nr = best_focus(lens, x, y)
            dx -= dx0; dy -= dy0
            # 专利像面在该点相对顶点的位置（朝物方为正）
            pz = -(x * x / (2 * Rx) + y * y / (2 * Ry))
            rows.append((fl, x, y, dx, dy, pz))
            print(f"  视场 ({x:6.2f}, {y:5.2f}) mm: 最佳聚焦 δx {dx:+.4f}  δy {dy:+.4f} mm   专利像面 {pz:+.4f} mm   光线 {nr}")
    return rows


if __name__ == "__main__":
    main()
