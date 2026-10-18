#!/bin/zsh
# 全部测试：光学单测 → Blender 冒烟 → 映射验证 → 四项验收 → 打包 → 干净环境安装
set -e
ROOT="${0:A:h:h}"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
cd "$ROOT"
python3 tests/test_optics.py | tail -1
python3 tests/test_flare.py | tail -1
"$BLENDER" -b --factory-startup --python tests/bl_smoke.py 2>&1 | grep -E "SMOKE|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_verify_mapping.py 2>&1 | grep -E "MAPPING|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_acceptance.py -- all 2>&1 | grep -E "ACCEPT|Traceback|rror:"
mkdir -p dist
"$BLENDER" --command extension build --source-dir blenderlens --output-dir dist | tail -1
tests/install_test.sh "$BLENDER"
