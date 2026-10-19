# BlenderLens：由公开专利处方驱动的变形镜头（Cycles OSL 自定义相机 + 真实镜片几何）
# 纯 Python 部分（optics、oslgen、flare、pupil、meshgen）不依赖 bpy，可以在 Blender 外按包导入：
#   import blenderlens.optics as O
# 在 Blender 里启用后，脚本用稳定入口，不必写安装仓库名：
#   import blenderlens.api as bl
import sys

try:
    import bpy  # noqa: F401
except ImportError:                       # Blender 外：只提供纯 Python 模块
    bpy = None

_ALIAS = "blenderlens"
_SUBMODULES = ("optics", "oslgen", "flare", "pupil", "meshgen", "lenspack", "gpukernel", "rig", "passes", "motion", "focus", "temporal", "shot", "denoise", "agent", "api", "ui")


def _install_alias():
    """把安装后的模块（如 bl_ext.user_default.blenderlens）同时登记为 blenderlens，让 `import blenderlens.api` 总能用。"""
    if __name__ == _ALIAS:
        return
    import importlib
    if _ALIAS in sys.modules and sys.modules[_ALIAS] is not sys.modules[__name__]:
        return                            # 已有另一份 blenderlens（开发副本）：不覆盖
    sys.modules[_ALIAS] = sys.modules[__name__]
    for name in _SUBMODULES:
        try:
            sys.modules[f"{_ALIAS}.{name}"] = importlib.import_module(f"{__name__}.{name}")
        except ImportError:
            pass


def _remove_alias():
    if sys.modules.get(_ALIAS) is sys.modules.get(__name__) and __name__ != _ALIAS:
        for name in (_ALIAS,) + tuple(f"{_ALIAS}.{n}" for n in _SUBMODULES):
            sys.modules.pop(name, None)


def register():
    from . import ui
    ui.register()
    _install_alias()


def unregister():
    from . import ui
    _remove_alias()
    ui.unregister()
