#!/bin/zsh
# 在全新的用户配置目录里从 zip 安装扩展并运行 bl_installed.py（模拟另一台机器）。
# 用法：tests/install_test.sh [Blender 可执行文件] [zip]
set -e
ROOT="${0:A:h:h}"
BLENDER="${1:-/Applications/Blender.app/Contents/MacOS/Blender}"
ZIP="${2:-$(ls -t "$ROOT"/dist/blenderlens-*.zip | head -1)}"
TMP="$(mktemp -d)"
export BLENDER_USER_RESOURCES="$TMP/user"
export BL_TEST_OUT="$ROOT/renders"
echo "clean user dir: $BLENDER_USER_RESOURCES"
"$BLENDER" --factory-startup --command extension install-file -r user_default -e "$ZIP"
"$BLENDER" -b --python "$ROOT/tests/bl_installed.py" 2>&1 | grep -E "INSTALLED|Error|Traceback|rror:"
# GPU 路径（命令行用法，与 HANDOFF 第 6.5 节一致）：扩展里的 enable_gpu → Metal 渲染第 1 帧，与 CPU 结果对比
"$BLENDER" -b "$BL_TEST_OUT/installed_gpu.blend" --python-expr "import blenderlens.api as bl; print('GPUEN', bl.enable_gpu())" \
  -o "$BL_TEST_OUT/installed_gpu_####" -F PNG -f 1 2>&1 | grep -E "GPUEN|BlenderLens:|rror"
"$BLENDER" -b --factory-startup --python-expr "
import bpy, numpy as np, os
o = os.environ['BL_TEST_OUT']
m = lambda f: float(np.array(bpy.data.images.load(os.path.join(o, f)).pixels[:]).reshape(-1, 4)[:, :3].mean())
g, c = m('installed_gpu_0001.png'), m('installed_cpu.png')
print('INSTALLED_GPU', dict(gpu_mean=round(g, 4), cpu_mean=round(c, 4), rel=round(g / max(c, 1e-9) - 1, 4), PASS=abs(g / max(c, 1e-9) - 1) < 0.05 and g > 0.001))
" 2>&1 | grep INSTALLED_GPU
rm -rf "$TMP"
