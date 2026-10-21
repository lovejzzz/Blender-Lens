# 相机输入指纹（0.9.2，MessyRoom Day 5：混用版本时一键确认「这个镜头的相机输入与另一版逐字节相同」）。不需要 bpy。
#   verify_inputs("c_series_style.json", gate="FULL", format="S35", focal_mm=0) → 每一项输入的 sha1（前 16 位）
#   命令行：python3 tools/blcli.py verify --preset c_series_style --gate FULL
# 对比的是渲染真正用到的东西：搭镜头时 prepare 之后的处方、lens_data（GPU 头文件与 OSL 共用的数组）、OSL 着色器源码、
# GPU 头文件（默认相机设置）、出瞳重要性采样表（随包的文件按字节、否则按表内容）、补丁文本。
import hashlib, json, os

try:
    from . import optics as O, oslgen as OG, gpukernel as GK
except ImportError:
    import optics as O, oslgen as OG, gpukernel as GK


def _h(x):
    if isinstance(x, (bytes, bytearray)):
        b = bytes(x)
    elif isinstance(x, str):
        b = x.encode()
    else:
        b = json.dumps(x, sort_keys=True, default=str).encode()
    return hashlib.sha1(b).hexdigest()[:16]


def _manifest_version():
    try:
        import re
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml")
        return re.search(r'^version\s*=\s*"([^"]+)"', open(p).read(), re.M).group(1)
    except (OSError, AttributeError):
        return None


def fingerprint(rx):
    """已经 prepare 过的处方 → 各项输入的 sha1。"""
    out = dict(prescription=_h(rx), lens_data=_h(OG.lens_data(rx)), osl=_h(OG.generate(rx)))
    try:
        out["gpu_header"] = _h(GK.generate_header(rx))
    except Exception as e:                                # 头文件生成依赖默认设置；失败时照实报
        out["gpu_header"] = f"error: {e!r}"[:80]
    key = OG._pupil_key(rx)
    f = os.path.join(OG.SHIPPED_PUPIL_DIR, key + ".npz") if OG.SHIPPED_PUPIL_DIR else None
    if f and os.path.exists(f):
        out["pupil_table"] = _h(open(f, "rb").read())
        out["pupil_table_source"] = "shipped"
    else:
        out["pupil_table"] = None
        out["pupil_table_source"] = "computed at build (not shipped)"
    out["pupil_key"] = key
    patches = "".join(str(getattr(GK, n, "")) for n in ("PATCHED", "PATCHED_SIGNATURE", "PATCHED_CALL", "PATCHED_INIT_CALL",
                                                        "PATCHED_PATH_INIT", "PATCHED_SD_WAVELENGTH"))
    out["kernel_patch"] = _h(patches + str(getattr(GK, "PATCH_VERSION", "")))
    out["all"] = _h({k: v for k, v in out.items() if k not in ("pupil_table_source",)})
    return out


def verify_inputs(preset="c_series_style.json", gate="FULL", format="S35", focal_mm=0.0, width=None, height=None):
    """→ dict(version, preset, gate, format, focal_mm, inputs={prescription, lens_data, osl, gpu_header, pupil_table, kernel_patch, all})。
    两个版本 / 两台机器上 inputs["all"] 相同 = 这个镜头的相机输入逐字节相同（渲染差异只可能来自场景、采样或 Cycles 本身）。"""
    path = preset if os.path.isabs(preset) or os.path.exists(preset) else os.path.join(O.PRESCRIPTION_DIR, preset if preset.endswith(".json") else preset + ".json")
    rx = O.prepare(O.load(path), format, focal_mm or 0.0, gate, width, height)
    return dict(version=_manifest_version(), preset=os.path.basename(path), gate=gate, format=format, focal_mm=focal_mm or 0.0,
                inputs=fingerprint(rx))
