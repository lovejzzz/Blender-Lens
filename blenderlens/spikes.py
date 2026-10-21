# 孤立亮点（时间尖峰）的检测与报告（0.9.2，MessyRoom Day 5：降噪后残留的「每帧换位置的小亮点」、雨滴折射车灯）。纯 numpy。
#   定义（与 MessyRoom 的测法一致）：按 BL_Vector 把前一帧、后一帧对齐到当前帧，某像素的显示亮度比前后两帧都高出 delta 以上、
#   且自身显示亮度 > min_lum，记一个孤立亮点。正常的高光在前后帧也亮，不算；运动补偿不可信（遮挡 / 边缘）的像素不算。
#   显示亮度：Rec.709 亮度夹到 [0, 1] 后做 sRGB 编码（Standard 视图变换下屏幕上的值）。
#   bl.spike_report(in_dir, frames) → 每帧每层的个数 / 最大超出量 / 总计，另存整段的热图 PNG（每像素出现次数）。
import json, os, struct, zlib
import numpy as np

from . import motion as MO

DEFAULTS = dict(delta=0.06, min_lum=0.25, conf=0.8)


def display(rgb):
    """线性 RGB → 显示亮度（Rec.709 亮度、夹到 [0, 1]、sRGB 编码）。"""
    L = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    L = np.clip(L, 0.0, 1.0)
    return np.where(L <= 0.0031308, 12.92 * L, 1.055 * np.power(L, 1 / 2.4) - 0.055).astype(np.float32)


def aligned_neighbors(img_prev, img_next, vec, conf=None):
    """把前 / 后一帧（H×W×C，可为 None）对齐到当前帧（vec = 当前帧 BL_Vector）。→ (prev, next, ok_prev, ok_next)。
    ok：落在画面内（且 conf ≥ 阈值由调用方再乘）。"""
    H, W = vec.shape[:2]
    xx, yy = MO.grid(H, W)
    out = []
    for img, fn in ((img_prev, MO.prev_coords), (img_next, MO.next_coords)):
        if img is None:
            out.append((None, np.zeros((H, W), bool))); continue
        x, y = fn(vec, xx, yy)
        ok = (x >= 0) & (x <= W - 1) & (y >= 0) & (y <= H - 1)
        out.append((MO.bilinear(img, x, y), ok))
    (p, okp), (n, okn) = out
    return p, n, okp, okn


def spike_mask(cur, prev_al, next_al, valid, delta=0.06, min_lum=0.25):
    """cur / prev_al / next_al：线性 RGB（已对齐）；valid：前后都可信的像素。→ (mask, 超出量)。"""
    dc = display(cur)
    dn = np.maximum(display(prev_al), display(next_al))
    ex = dc - dn
    mask = valid & (ex > delta) & (dc > min_lum)
    return mask, np.where(mask, ex, 0.0).astype(np.float32)


def suppress(cur, prev_al, next_al, valid, delta=0.06, min_lum=0.25):
    """把孤立亮点压到前后两帧（对齐后）的较大值（逐通道取 min(当前, max(前, 后))）。→ (新图, 个数)。"""
    mask, _ = spike_mask(cur, prev_al, next_al, valid, delta, min_lum)
    cap = np.maximum(prev_al, next_al)
    out = np.where(mask[..., None], np.minimum(cur, cap), cur)
    return out.astype(np.float32), int(mask.sum())


def write_png(path, gray01):
    """单通道 0–1 → 8 位 PNG（黑 → 红 → 黄 → 白；不依赖 PIL / bpy）。"""
    v = np.clip(gray01, 0.0, 1.0)
    rgb = np.stack([np.clip(3 * v, 0, 1), np.clip(3 * v - 1, 0, 1), np.clip(3 * v - 2, 0, 1)], -1)
    a = (rgb * 255 + 0.5).astype(np.uint8)
    H, W = a.shape[:2]
    raw = b"".join(b"\x00" + a[r].tobytes() for r in range(H))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def spike_report(in_dir, frames, layers=("Combined", "BL_Exclude", "BL_Final"), name="f", file_ext=".exr",
                 delta=0.06, min_lum=0.25, conf=0.8, heatmap_dir=None, write_json=True):
    """读 render_frames / render_shot 写的多层 EXR（或 temporal_denoise 的输出），统计每帧每层的孤立亮点。
    需要每帧的 BL_Vector（temporal_denoise 的输出没有矢量时用输入目录的：in_dir 可给 (输出目录, 矢量目录)）；有 BL_VectorConfidence 时
    只在前后两帧链上都 ≥ conf 的像素上判断。第一帧 / 最后一帧（缺邻帧）不统计。
    → dict(per_frame=[{frame, <层>: {count, max_excess}}], total={<层>: 个数}, heatmaps={<层>: png}, settings)。"""
    from . import passes as PS
    vec_dir = None
    if isinstance(in_dir, (tuple, list)):
        in_dir, vec_dir = in_dir
    try:
        import bpy
        in_dir = bpy.path.abspath(in_dir); vec_dir = bpy.path.abspath(vec_dir) if vec_dir else None
    except ImportError:
        pass
    frames = list(frames)
    path = lambda d, t: os.path.join(d, f"{name}_{t:04d}{file_ext}")
    cache = {}

    def get(t):
        if t in cache:
            return cache[t]
        if not os.path.exists(path(in_dir, t)):
            cache[t] = None; return None
        want = set(layers) | {"BL_Vector", "BL_VectorConfidence"}
        parts = PS.read(path(in_dir, t), want=lambda ps: ps in want)
        if vec_dir and not any(p["pass_"] == "BL_Vector" for p in parts) and os.path.exists(path(vec_dir, t)):
            parts += PS.read(path(vec_dir, t), want=lambda ps: ps in ("BL_Vector", "BL_VectorConfidence"))
        d = {}
        for p in parts:
            ch = p["channels"]
            if p["pass_"] == "BL_Vector":
                d["vec"] = np.stack([ch[c] for c in "XYZW"], -1).astype(np.float32)
            elif p["pass_"] == "BL_VectorConfidence":
                d["conf"] = ch["X"].astype(np.float32)
            elif p["pass_"] in layers and {"R", "G", "B"} <= set(ch):
                d[p["pass_"]] = np.stack([ch[c] for c in "RGB"], -1).astype(np.float32)
        cache[t] = d
        for k in [k for k in cache if k < t - 1]:
            del cache[k]
        return d

    rep = dict(in_dir=in_dir, frames=frames, per_frame=[], total={l: 0 for l in layers}, heatmaps={},
               settings=dict(delta=delta, min_lum=min_lum, conf=conf, layers=list(layers),
                             definition="显示亮度（Rec.709、sRGB）比按 BL_Vector 对齐的前后两帧都高 delta 以上且 > min_lum"))
    heat = {}
    for t in frames:
        cur, pv, nx = get(t), get(t - 1), get(t + 1)
        if cur is None or pv is None or nx is None or "vec" not in cur:
            rep["per_frame"].append(dict(frame=t, skipped="缺本帧、邻帧或 BL_Vector")); continue
        row = dict(frame=t)
        for layer in layers:
            if layer not in cur or layer not in pv or layer not in nx:
                continue
            p_al, n_al, okp, okn = aligned_neighbors(pv[layer], nx[layer], cur["vec"])
            valid = okp & okn
            if "conf" in cur:
                cp, cn, _, _ = aligned_neighbors(pv.get("conf", np.ones_like(cur["conf"]))[..., None],
                                                 nx.get("conf", np.ones_like(cur["conf"]))[..., None], cur["vec"])
                valid &= (cur["conf"] >= conf) & (cp[..., 0] >= conf) & (cn[..., 0] >= conf)
            m, ex = spike_mask(cur[layer], p_al, n_al, valid, delta, min_lum)
            row[layer] = dict(count=int(m.sum()), max_excess=round(float(ex.max()), 4))
            rep["total"][layer] += int(m.sum())
            heat[layer] = heat.get(layer, 0) + m.astype(np.float32)
        rep["per_frame"].append(row)
    if heatmap_dir is None:
        heatmap_dir = in_dir
    os.makedirs(heatmap_dir, exist_ok=True)
    for layer, h in heat.items():
        pth = os.path.join(heatmap_dir, f"{name}_spikes_{layer}.png")
        hm = h
        # 亮点只有几个像素：先做 5×5 的盒式累加（看得见），再按最大值归一
        k = 5
        pad = np.pad(hm, k // 2)
        acc = np.zeros_like(hm)
        for dy in range(k):
            for dx in range(k):
                acc += pad[dy:dy + hm.shape[0], dx:dx + hm.shape[1]]
        write_png(pth, acc / max(float(acc.max()), 1e-9))
        rep["heatmaps"][layer] = pth
    if write_json:
        with open(os.path.join(heatmap_dir, f"{name}_spikes.json"), "w") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep
