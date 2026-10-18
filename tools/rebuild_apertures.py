# 改了处方的 R / d / 视场后重跑：重新估算各面口径与光阑，写回 JSON 并打印边缘间隙。
# python3 tools/rebuild_apertures.py [prescription.json]
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "blenderlens"))
import optics as O

path = sys.argv[1] if len(sys.argv) > 1 else O.DEFAULT_PRESCRIPTION
rx = O.load(path)
rx, info = O.apply_apertures(rx)
O.save(rx, path)
print(f"stop z = {info['z_stop']:.3f} mm, r = {info['r_stop']:.3f} mm, failed rays = {info['failed']}, clash fixed = {info['clash_fixed']}")
for r in O.edge_report(rx):
    flag = "  <-- 相撞" if r["min_gap"] < 0 else ""
    print(f"{r['between']:9s} {r['kind']:5s} 中心 {r['center']:7.3f}  最小 {r['min_gap']:7.3f} @ {r['at']}{flag}")
