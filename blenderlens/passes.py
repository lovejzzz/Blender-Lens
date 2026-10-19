# 多层 EXR 的渲染层处理（0.8.3）：保留视图层打开的所有渲染层，对颜色层统一做横向色差，加上插件自己的层。
# Blender 5.x 的多层 EXR 是多部分（multi-part）文件：每个渲染层一个部分，部分名 "<视图层>.<渲染层>"，
# 通道名 "<视图层>.<渲染层>.<R|G|B|A|X|Y|Z|W|…>"。读写用 Blender 自带的 OpenEXR 模块（5.3 起）。
# 数组约定：EXR 行 0 = 顶部（与 lenspack 的映射一致）；Blender 像素缓冲区行 0 = 底部。
import json, struct
import numpy as np

# 不做横向色差的渲染层：几何 / 数据类（深度、法线、位置、矢量、UV、索引、Cryptomatte、雾、去噪用的几何）
DATA_PREFIXES = ("Crypto", "Depth", "Normal", "Position", "Vector", "UV", "IndexOB", "IndexMA", "Mist",
                 "Denoising Normal", "Denoising Depth", "Denoising Roughness", "Sample Count", "Debug", "BL_Vector")
PREFIX = "BL_"                                     # 插件加的层：BL_Flare、BL_Veil、BL_Final、BL_Vector


def read(path):
    """→ [dict(name, view_layer, pass_, header, channels={后缀: H×W float32（行 0 = 顶部）}, dtypes)]。"""
    import OpenEXR
    out = []
    with OpenEXR.File(path, separate_channels=True) as f:
        for pt in f.parts:
            name = pt.name() or ""
            vl, _, ps = name.partition(".")
            chans, dtypes = {}, {}
            for full, c in pt.channels.items():
                suffix = full.rsplit(".", 1)[-1]
                chans[suffix] = np.array(c.pixels)
                dtypes[suffix] = c.pixels.dtype
            hdr = {k: v for k, v in dict(pt.header).items() if k not in ("channels", "chunkCount")}
            out.append(dict(name=name, view_layer=vl, pass_=ps, header=hdr, channels=chans, dtypes=dtypes))
    return out


def write(path, parts, half_color=False):
    """parts：read() 的格式。half_color：颜色层存 16 位半精度（数据层恒为 32 位）。"""
    import OpenEXR
    eparts = []
    for p in parts:
        color = is_color(p)
        chans = {}
        for suffix, a in p["channels"].items():
            dt = np.float16 if (half_color and color) else np.float32
            chans[f"{p['name']}.{suffix}"] = OpenEXR.Channel(np.ascontiguousarray(a, dtype=dt))
        hdr = dict(p["header"]); hdr["name"] = p["name"]
        eparts.append(OpenEXR.Part(hdr, chans, p["name"]))
    OpenEXR.File(eparts).write(path)


def is_color(p):
    """颜色层（参与横向色差）：有 R/G/B 三个通道，且不是数据类。光组（Combined_<名>）、Emit、Diffuse/Glossy/
    Transmission/Volume Direct/Indirect/Color、AO、Shadow Catcher、颜色 AOV、Noisy Image、Denoising Albedo 都算。"""
    ps = p["pass_"]
    return {"R", "G", "B"} <= set(p["channels"]) and not ps.startswith(DATA_PREFIXES)


def rgb(p, dtype=np.float32):
    return np.stack([p["channels"]["R"], p["channels"]["G"], p["channels"]["B"]], axis=-1).astype(dtype)


def set_rgb(p, img):
    for k, c in enumerate("RGB"):
        p["channels"][c] = img[..., k].astype(np.float32)


def find(parts, pass_, view_layer=None):
    for p in parts:
        if p["pass_"] == pass_ and (view_layer is None or p["view_layer"] == view_layer):
            return p
    return None


def new_part(template, pass_, channels):
    return dict(name=f"{template['view_layer']}.{pass_}", view_layer=template["view_layer"], pass_=pass_,
                header=dict(template["header"]), channels={k: np.asarray(v, np.float32) for k, v in channels.items()},
                dtypes={k: np.float32 for k in channels})


# ───────────────────────── Cryptomatte → 物体名 ─────────────────────────

def crypto_object_ids(parts, view_layer):
    """每个像素覆盖最多的物体（CryptoObject00 的第 0 级）→ (H×W 的 int 索引, [物体名])；没有 Cryptomatte 时 (None, [])。"""
    p = find(parts, "CryptoObject00", view_layer)
    if p is None:
        return None, []
    manifest = {}
    for q in parts:                                     # 清单写在某一个部分的头里（Blender 5.3：Combined 那一部分）
        for k, v in q["header"].items():
            if k.startswith("cryptomatte/") and k.endswith("/manifest"):
                try:
                    manifest.update(json.loads(v if isinstance(v, str) else v.decode()))
                except (ValueError, AttributeError):
                    pass
    by_hex = {h.lower(): name for name, h in manifest.items()}
    ch = p["channels"]
    ids = (ch["R"] if "R" in ch else ch["r"]).astype(np.float32)         # Cryptomatte 通道名是小写 r g b a
    uniq = np.unique(ids)
    names = []
    index = np.full(ids.shape, -1, np.int32)
    for u in uniq:
        hx = struct.pack(">f", float(u)).hex()
        nm = by_hex.get(hx)
        if nm is None:
            continue
        index[ids == u] = len(names)
        names.append(nm)
    return index, names
