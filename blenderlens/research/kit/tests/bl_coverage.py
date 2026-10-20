# 覆盖与畸变报告、build 警告（0.8.6，MessyRoom 广角需求 W2 / W3）
#   均匀白色环境，各预设按 MessyRoom 的用法缩到广角（16:9_NATIVE、f/2 → 夹到最大光圈），渲染的相对照度与
#   coverage.relative_illumination（与渲染同一估计器）在画面多处比：差 ≤ 0.10。
#   build 时的快速检查：不覆盖的给警告（存在相机属性、diagnose 带上）；覆盖的不警告。
#   W1：Laowa 2× 广角（CN 112285889 B）取景 25.5 mm 覆盖 16:9_NATIVE，4K 竖线弯曲 ≤ C 风格取景 21 mm 的 40%。
# 运行：Blender -b --factory-startup --python tests/bl_coverage.py
import os, sys, json, math
import bpy
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens
blenderlens.register()
import blenderlens.api as bl
from blenderlens import rig, coverage as CV
OUT = os.path.join(ROOT, "renders", "coverage")
os.makedirs(OUT, exist_ok=True)
GPU = bl.enable_gpu(use_gpu=True)[0]
CASES = [("c_series_style", 24.6), ("c_series_style", 32.8), ("e_series_style", 24.6), ("e_series_style", 46.9),
         ("laowa_2x_29", 30.88), ("panavision_rear_134", 24.6),          # Laowa 30.88 mm = MessyRoom 的「取景 25.5 mm」
         ("c_series_style", 0.0, "FF"), ("laowa_2x_29", 0.0, "FF")]      # 0.8.8 全画幅版本（原生焦距，16:9_NATIVE 收进 36 × 24）
PTS = [(0.9, 0.0), (0.0, 0.9), (0.9, 0.9), (0.6, 0.6), (-0.9, -0.6)]
rep = dict(cases={})
worst = 0.0
for pre, f, *fmt in CASES:
    fmt = fmt[0] if fmt else "S35"
    sc = bpy.context.scene
    for o in list(sc.objects):
        bpy.data.objects.remove(o)
    sc.world = bpy.data.worlds.new("W")
    if sc.world.node_tree is None:
        sc.world.use_nodes = True
    sc.world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
    sc.unit_settings.system = 'METRIC'; sc.unit_settings.scale_length = 1.0
    cam = bl.build(pre + ".json", sc, focal_mm=f, gate="16:9_NATIVE", units="SCENE", height=180, format=fmt)
    bl.configure(cam, focus_m=7.0, f_number=2.0, dispersion=False)
    sc.cycles.device = 'GPU' if GPU else 'CPU'; sc.cycles.samples = 384 if GPU else 128; sc.cycles.use_denoising = False
    sc.view_settings.view_transform = 'Standard'
    key = f"{pre}@{f}" + ("" if fmt == "S35" else f"_{fmt}")
    path = os.path.join(OUT, f"{key}.exr")
    bl.render_still(path, flare=False)
    im = bpy.data.images.load(path, check_existing=False)
    a = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)[::-1, :, :3].mean(-1)   # 行 0 = 顶部
    bpy.data.images.remove(im)
    H, W = a.shape
    px = lambda u, v: a[int(round((1 - v) / 2 * H - 0.5)) - 1:int(round((1 - v) / 2 * H - 0.5)) + 2,
                        int(round((u + 1) / 2 * W - 0.5)) - 1:int(round((u + 1) / 2 * W - 0.5)) + 2].mean()
    c = a[H // 2 - 3:H // 2 + 4, W // 2 - 3:W // 2 + 4].mean()
    rend = [float(px(u, v) / c) for u, v in PTS]
    model = CV.relative_illumination(rig.read_prescription(), PTS, rig.focus_mm(cam), cam.data.blenderlens.f_number)
    d = max(abs(x - y) for x, y in zip(rend, model))
    worst = max(worst, d)
    q = json.loads(cam["bl_coverage_quick"])
    if pre == "panavision_rear_134":
        full = bl.coverage(cam)                           # PV @24.6 的完整报告
    issues = [i["code"] for i in bl.diagnose(sc)]
    rep["cases"][key] = dict(sensor_mm=[round(rig.read_prescription()["sensor"][k_], 2) for k_ in ("width", "height")], render=[round(x, 3) for x in rend], model=[round(x, 3) for x in model], max_diff=round(d, 3),
                                      covers=q["covers"], warned=bool(q["warnings"]), diagnose_coverage="coverage" in issues)
# W1：Laowa 广角的竖线弯曲 vs C 风格（同样取景 21 mm，16:9_NATIVE；4K）
from blenderlens import optics as O
def bow(pre, framing):
    rx0 = O.load(os.path.join(ROOT, "blenderlens", "prescriptions", pre + ".json"))
    hg = O.apply_gate(rx0, "16:9_NATIVE")["sensor"]["height"]
    return CV.coverage_report(O.apply_gate(O.rescale_to_vertical_efl(rx0, framing * hg / 18.37), "16:9_NATIVE"), 8500.0, 4.0)
cb, lb21, lb = bow("c_series_style", 21), bow("laowa_2x_29", 21), bow("laowa_2x_29", 25.5)
rep["w1"] = dict(c21_bow=cb["edge_vertical_bow_px"], laowa21_bow=lb21["edge_vertical_bow_px"], laowa255_bow=lb["edge_vertical_bow_px"],
                 laowa255_covers=lb["covers"], laowa255_corner=lb["relative_illumination"]["corner"],
                 ratio_255=round(abs(lb["edge_vertical_bow_px"]) / abs(cb["edge_vertical_bow_px"]), 3),
                 distortion_edge_h=dict(c21=cb["distortion_pct"]["edge_h"], laowa255=lb["distortion_pct"]["edge_h"]))
rep["pv_full"] = {k: full[k] for k in ("image_circle_mm", "gate_diagonal_mm", "covers", "half_field_deg", "edge_vertical_bow_px")}
C = rep["cases"]
rep["worst_diff"] = round(worst, 3)
rep["PASS"] = bool(worst <= 0.10 and C["e_series_style@24.6"]["warned"] and C["e_series_style@24.6"]["diagnose_coverage"]
                   and not C["e_series_style@24.6"]["covers"] and C["c_series_style@24.6"]["warned"]
                   and C["panavision_rear_134@24.6"]["covers"] and not C["panavision_rear_134@24.6"]["warned"]
                   and rep["pv_full"]["covers"] and C["laowa_2x_29@30.88"]["covers"] and not C["laowa_2x_29@30.88"]["warned"]
                   and rep["w1"]["laowa255_covers"] and rep["w1"]["ratio_255"] <= 0.4
                   and all(C[k]["covers"] and not C[k]["warned"] for k in ("c_series_style@0.0_FF", "laowa_2x_29@0.0_FF")))
print("COVERAGE", json.dumps(rep, default=float))
