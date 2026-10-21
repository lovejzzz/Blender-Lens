# 多层 EXR 的渲染层处理（0.8.3）：保留视图层打开的所有渲染层，对颜色层统一做横向色差，加上插件自己的层。
# Blender 5.x 的多层 EXR 是多部分（multi-part）文件：每个渲染层一个部分，部分名 "<视图层>.<渲染层>"，
# 通道名 "<视图层>.<渲染层>.<R|G|B|A|X|Y|Z|W|…>"。读写用 Blender 自带的 OpenEXR 模块（5.3 起）。
# 数组约定：EXR 行 0 = 顶部（与 lenspack 的映射一致）；Blender 像素缓冲区行 0 = 底部。
import json, struct
import numpy as np

# 不做横向色差的渲染层：几何 / 数据类（深度、法线、位置、矢量、UV、索引、Cryptomatte、雾、去噪用的几何）
DATA_PREFIXES = ("Crypto", "Depth", "Normal", "Position", "Vector", "UV", "IndexOB", "IndexMA", "Mist",
                 "Denoising Normal", "Denoising Depth", "Denoising Roughness", "Sample Count", "Debug", "BL_Vector", "BL_Depth")   # BL_VectorConfidence 也算
PREFIX = "BL_"                                     # 插件加的层：BL_Flare、BL_Veil、BL_Final、BL_Vector


def read(path, want=None):
    """→ [dict(name, view_layer, pass_, header, channels={后缀: H×W float32 / float16（行 0 = 顶部）}, dtypes)]。
    want（0.8.4）：want(渲染层名) → bool，只读这些部分的像素（用 OpenImageIO 逐部分读，省内存）；None = 全读。"""
    import OpenEXR
    if want is None:
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
    import OpenImageIO as oiio
    heads = []
    with OpenEXR.File(path, header_only=True) as f:
        for pt in f.parts:
            heads.append((pt.name() or "", {k: v for k, v in dict(pt.header).items() if k not in ("channels", "chunkCount")}))
    out = []
    inp = oiio.ImageInput.open(path)
    if inp is None:
        raise OSError(f"读不了 {path}: {oiio.geterror()}")
    try:
        for i, (name, hdr) in enumerate(heads):
            vl, _, ps = name.partition(".")
            if not want(ps):
                continue
            inp.seek_subimage(i, 0)
            sp = inp.spec()
            px = inp.read_image(i, 0, 0, sp.nchannels, "unknown")
            if px is None:
                raise OSError(f"读不了 {path} 的 {name}: {inp.geterror()}")
            px = np.asarray(px).reshape(sp.height, sp.width, sp.nchannels)
            chans, dtypes = {}, {}
            for k, full in enumerate(sp.channelnames):
                suffix = full.rsplit(".", 1)[-1]
                chans[suffix] = np.ascontiguousarray(px[..., k])
                dtypes[suffix] = chans[suffix].dtype
            del px
            out.append(dict(name=name, view_layer=vl, pass_=ps, header=hdr, channels=chans, dtypes=dtypes))
    finally:
        inp.close()
    return out


LOSSY = ("DWAA", "DWAB", "B44", "B44A", "PXR24")            # 有损：只给颜色层用；数据层改用 ZIP
CODECS = ("NONE", "ZIP", "ZIPS", "PIZ", "RLE", "DWAA", "DWAB", "B44", "B44A", "PXR24", "HTJ2K")


def _compression(codec):
    import OpenEXR
    name = {"NONE": "NO", "HTJ2K": "HTJ2K256"}.get(codec, codec)
    return getattr(OpenEXR, f"{name}_COMPRESSION")


def write(path, parts, half_color=False, codec=None):
    """parts：read() 的格式。half_color：颜色层存 16 位半精度（数据层恒为 32 位）。
    codec（0.8.4）：EXR 压缩（CODECS 之一）；颜色层用它，数据层遇到有损压缩（DWAA / DWAB / B44 / PXR24）时改用无损 ZIP
    ——与 Blender 自己写多层 EXR 的做法相同。None = 保留各部分原来的压缩。"""
    import OpenEXR
    if codec is not None and codec not in CODECS:
        raise ValueError(f"exr_codec 必须是 {CODECS} 之一，收到 {codec!r}")
    eparts = []
    for p in parts:
        color = is_color(p)
        chans = {}
        for suffix, a in p["channels"].items():
            dt = np.float16 if (half_color and color) else np.float32
            chans[f"{p['name']}.{suffix}"] = OpenEXR.Channel(np.ascontiguousarray(a, dtype=dt))
        hdr = dict(p["header"]); hdr["name"] = p["name"]
        if codec is not None:
            hdr["compression"] = _compression(codec if (color or codec not in LOSSY) else "ZIP")
        eparts.append(OpenEXR.Part(hdr, chans, p["name"]))
    OpenEXR.File(eparts).write(path)


# ───────────────────────── 渲染层预设（0.8.4）─────────────────────────
# passes 可以是 "all"、预设名，或名字列表（列表里可以混用预设名）。
#   "temporal"：时间降噪 / 闪烁分析需要的层（MessyRoom R4）；BL_Vector、BL_Final 等插件层总是写。
#   "delivery"（0.9.2）：交付用的精简组合——temporal_denoise 只读这些（加插件层），文件约小一半。
#   "lightgroups"：全部光组（Combined_<名>）。
PRESETS = {
    "temporal": ("Combined", "Noisy Image", "Volume Direct", "Volume Indirect", "Denoising Albedo",
                 "Denoising Specular Albedo", "Denoising Normal", "Denoising Roughness", "Denoising Depth", "Depth"),
    # 0.9.2（MessyRoom Day 5：每帧 267 MB）：交付用的精简组合——只有 temporal_denoise 真正读的层（Combined + 镜面保护用的两层）；
    # 插件层（BL_Final、BL_Vector、BL_VectorConfidence、BL_Flare、BL_Veil、BL_Exclude…）总是写。不要 Depth / Noisy Image / 体积层。
    "delivery": ("Combined", "Denoising Specular Albedo", "Denoising Roughness"),
}
# 预设需要、渲染时临时打开的视图层开关（(对象路径, 属性)；渲完恢复）
PRESET_FLAGS = {
    "temporal": (("", "use_pass_z"), ("cycles", "denoising_store_passes"),
                 ("cycles", "use_pass_volume_direct"), ("cycles", "use_pass_volume_indirect")),
    "delivery": (("cycles", "denoising_store_passes"),),
}


# 视图层开关 → 它产生的渲染层（名字前缀）。passes 不是 "all" 时，没人要的开关在渲染期间临时关掉：Cycles 少算、少占内存。
FLAG_PASSES = {
    ("", "use_pass_z"): ("Depth",), ("", "use_pass_mist"): ("Mist",), ("", "use_pass_normal"): ("Normal",),
    ("", "use_pass_position"): ("Position",), ("", "use_pass_vector"): ("Vector",), ("", "use_pass_uv"): ("UV",),
    ("", "use_pass_object_index"): ("IndexOB",), ("", "use_pass_material_index"): ("IndexMA",),
    ("", "use_pass_diffuse_direct"): ("Diffuse Direct",), ("", "use_pass_diffuse_indirect"): ("Diffuse Indirect",),
    ("", "use_pass_diffuse_color"): ("Diffuse Color",), ("", "use_pass_glossy_direct"): ("Glossy Direct",),
    ("", "use_pass_glossy_indirect"): ("Glossy Indirect",), ("", "use_pass_glossy_color"): ("Glossy Color",),
    ("", "use_pass_transmission_direct"): ("Transmission Direct",),
    ("", "use_pass_transmission_indirect"): ("Transmission Indirect",),
    ("", "use_pass_transmission_color"): ("Transmission Color",), ("", "use_pass_emit"): ("Emit",),
    ("", "use_pass_environment"): ("Env",), ("", "use_pass_ambient_occlusion"): ("AO",), ("", "use_pass_shadow"): ("Shadow",),
    ("", "use_pass_cryptomatte_object"): ("CryptoObject",), ("", "use_pass_cryptomatte_material"): ("CryptoMaterial",),
    ("", "use_pass_cryptomatte_asset"): ("CryptoAsset",),
    ("cycles", "use_pass_volume_direct"): ("Volume Direct",), ("cycles", "use_pass_volume_indirect"): ("Volume Indirect",),
    ("cycles", "use_pass_shadow_catcher"): ("Shadow Catcher",),
    ("cycles", "denoising_store_passes"): ("Denoising", "Noisy Image"),
}


def flag_wanted(keep, names, prefixes):
    """视图层开关产生的渲染层（prefixes）里有没有要保留的。names：expand() 给出的显式名字集合。"""
    return any(keep(pr) or any(n.startswith(pr) for n in names) for pr in prefixes)


def expand(passes):
    """→ (keep(pass_名) → bool, [(对象路径, 属性)] 需要临时打开的开关, 是否用了预设, 显式名字集合)。"""
    if passes == "all":
        return (lambda n: True), [], False, set()
    items = [passes] if isinstance(passes, str) else list(passes)
    names, flags, lightgroups, preset = set(), [], False, False
    for it in items:
        if it in PRESETS:
            names.update(PRESETS[it]); flags += list(PRESET_FLAGS.get(it, ())); preset = True
        elif it == "lightgroups":
            lightgroups = True
        else:
            names.add(it)
    return (lambda n: n in names or (lightgroups and n.startswith("Combined_"))), flags, preset, names


# ───────────────────────── 流式读写（0.8.4，MessyRoom R4）─────────────────────────
# 一次只在内存里放一个渲染层：逐部分读 → 处理 → 写出 → 释放。用 Blender 自带的 OpenImageIO 写多部分 EXR。

_STD_KEYS = {"name", "channels", "chunkCount", "compression", "dataWindow", "displayWindow", "lineOrder",
             "pixelAspectRatio", "screenWindowCenter", "screenWindowWidth", "type", "version", "tiles"}


def heads(path):
    """→ [dict(index, name, view_layer, pass_, header, suffixes)]，只读头。"""
    import OpenEXR, OpenImageIO as oiio
    out = []
    with OpenEXR.File(path, header_only=True) as f:
        for i, pt in enumerate(f.parts):
            name = pt.name() or ""
            vl, _, ps = name.partition(".")
            out.append(dict(index=i, name=name, view_layer=vl, pass_=ps,
                            header={k: v for k, v in dict(pt.header).items() if k not in ("channels", "chunkCount")}))
    inp = oiio.ImageInput.open(path)
    try:
        for h in out:
            inp.seek_subimage(h["index"], 0)
            h["suffixes"] = [c.rsplit(".", 1)[-1] for c in inp.spec().channelnames]
    finally:
        inp.close()
    return out


def read_one(path, head):
    """读一个部分（heads() 的一项）→ read() 格式的 dict。"""
    import OpenImageIO as oiio
    inp = oiio.ImageInput.open(path)
    try:
        inp.seek_subimage(head["index"], 0)
        sp = inp.spec()
        px = np.asarray(inp.read_image(head["index"], 0, 0, sp.nchannels, "unknown")).reshape(sp.height, sp.width, sp.nchannels)
    finally:
        inp.close()
    chans = {c.rsplit(".", 1)[-1]: np.ascontiguousarray(px[..., k]) for k, c in enumerate(sp.channelnames)}
    return dict(name=head["name"], view_layer=head["view_layer"], pass_=head["pass_"], header=dict(head["header"]),
                channels=chans, dtypes={k: v.dtype for k, v in chans.items()})


class StreamWriter:
    """多部分 EXR 逐部分写出。plan：[dict(name, suffixes, header, color)]（全部部分事先给出——EXR 多部分文件头在最前）；
    之后按同样顺序调用 write(channels)。颜色层 half_color 时存 half，压缩 codec；数据层 float32，有损压缩改 ZIP。"""

    def __init__(self, path, plan, W, H, half_color=False, codec="ZIP"):
        import OpenImageIO as oiio
        if codec not in CODECS:
            raise ValueError(f"exr_codec 必须是 {CODECS} 之一，收到 {codec!r}")
        self.specs, self.dtypes = [], []
        for p in plan:
            half = half_color and p["color"]
            sp = oiio.ImageSpec(W, H, len(p["suffixes"]), oiio.TypeDesc("half" if half else "float"))
            sp.channelnames = tuple(f"{p['name']}.{c}" for c in p["suffixes"])
            sp.attribute("name", p["name"]); sp.attribute("oiio:subimagename", p["name"])
            c = codec if (p["color"] or codec not in LOSSY) else "ZIP"
            sp.attribute("compression", {"NONE": "none", "HTJ2K": "htj2k256"}.get(c, c.lower()))
            for k, v in p["header"].items():
                if k in _STD_KEYS:
                    continue
                if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    sp.attribute(k, v)
            self.specs.append(sp); self.dtypes.append(np.float16 if half else np.float32)
        self.path, self.k = path, 0
        self.out = oiio.ImageOutput.create(path)
        if self.out is None or not self.out.open(path, self.specs):
            raise OSError(f"写不了 {path}: {oiio.geterror()}")

    def write(self, channels):
        k, sp = self.k, self.specs[self.k]
        if k and not self.out.open(self.path, sp, "AppendSubimage"):
            raise OSError(self.out.geterror())
        data = np.stack([np.asarray(channels[c.rsplit(".", 1)[-1]], self.dtypes[k]) for c in sp.channelnames], axis=-1)
        if not self.out.write_image(data):
            raise OSError(self.out.geterror())
        self.k += 1

    def close(self):
        self.out.close()


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

_CRYPTO_CACHE = {}


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
    by_hex = {}
    for name, h in manifest.items():                    # 清单里是 32 位哈希；像素里的 float 是按 Cryptomatte 规范转过的：
        try:                                            # 指数位为 0 或 255 时翻转第 23 位（避免非规格化数 / NaN）。
            v = int(h, 16)                              # 0.8.3 直接比哈希 → 约 1/128 的物体认不出（矢量没有物体运动）
        except (TypeError, ValueError):
            continue
        if ((v >> 23) & 0xFF) in (0, 0xFF):
            v ^= 1 << 23
        by_hex[f"{v:08x}"] = name
    ch = p["channels"]
    ids = (ch["R"] if "R" in ch else ch["r"]).astype(np.float32)         # Cryptomatte 通道名是小写 r g b a
    key = (id(p), ids.shape)
    if _CRYPTO_CACHE.get("key") == key and _CRYPTO_CACHE.get("ids") is not None and np.array_equal(_CRYPTO_CACHE["ids"], ids):
        return _CRYPTO_CACHE["index"], list(_CRYPTO_CACHE["names"])
    # 0.9.2：一次排序（return_inverse）代替「每个物体在整幅图上比一遍」——3,000 个物体、4K 时 4.7 s → 约 0.5 s，结果相同
    uniq, inv = np.unique(ids.reshape(-1), return_inverse=True)
    names = []
    lut = np.full(len(uniq), -1, np.int32)
    for j, u in enumerate(uniq):
        nm = by_hex.get(struct.pack(">f", float(u)).hex())
        if nm is None:
            continue
        lut[j] = len(names)
        names.append(nm)
    index = lut[inv].reshape(ids.shape)
    _CRYPTO_CACHE.update(key=key, ids=ids, index=index, names=list(names))   # 同一帧里 fill 与 BL_Vector 各调一次
    return index, names
