# 预设的出瞳重要性采样表（随包分发，免去第一次搭镜头时的 2–10 秒计算）。
# 每个预设 × 常用画幅（FULL / 2.39 / 16:9 / 16:9_NATIVE）一份，文件名 = 处方内容指纹 + 表版本（oslgen._pupil_key）；
# 处方或表算法一变，指纹就变，旧文件自动失效（本脚本会删掉不再对应任何预设的旧文件）。
# 运行：python3 tools/build_pupil_tables.py   （改了 tools/build_presets.py 或 pupil.py 之后）
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.optics as O
import blenderlens.oslgen as G
import blenderlens.pupil as PU

GATES = ("FULL", "2.39", "16:9", "16:9_NATIVE")


def main():
    out = G.SHIPPED_PUPIL_DIR
    os.makedirs(out, exist_ok=True)
    want = set()
    for fn, _, _ in O.list_presets():
        for gate in GATES:
            rx = O.apply_gate(O.load(os.path.join(O.PRESCRIPTION_DIR, fn)), gate)
            key = G._pupil_key(rx)
            want.add(key + ".npz")
            path = os.path.join(out, key + ".npz")
            if os.path.exists(path):
                continue
            t = time.time()
            T = PU.table(rx)
            G.save_pupil_table(T, path)
            print(f"{fn:32s} {gate:12s} {time.time() - t:5.1f} s  带光占比 平均 {T['eff_box'].mean():.2f} 最低 {T['eff_box'].min():.2f}")
    for f in os.listdir(out):
        if f.endswith(".npz") and f not in want:
            os.remove(os.path.join(out, f))
            print("删除过期表", f)


if __name__ == "__main__":
    main()
