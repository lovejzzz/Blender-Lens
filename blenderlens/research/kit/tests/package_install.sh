#!/bin/zsh
# 打包扩展 zip，再在全新用户配置里安装、渲染、跑 GPU 帧（tests/run.py full 的最后一步）
set -e
ROOT="${0:A:h:h}"
BLENDER="${1:-${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}}"
cd "$ROOT"
mkdir -p dist
"$BLENDER" --command extension build --source-dir blenderlens --output-dir dist | tail -1
tests/install_test.sh "$BLENDER"
