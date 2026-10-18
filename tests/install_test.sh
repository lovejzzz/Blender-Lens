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
rm -rf "$TMP"
