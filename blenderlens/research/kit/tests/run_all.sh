#!/bin/zsh
# 全部测试（兼容旧用法）。新入口是 tests/run.py：quick / standard / full、JSON 报告、指标基线。
ROOT="${0:A:h:h}"
exec python3 "$ROOT/tests/run.py" full "$@"
