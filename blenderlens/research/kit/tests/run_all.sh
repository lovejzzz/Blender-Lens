#!/bin/zsh
# 全部测试：光学单测 → Blender 冒烟 → 映射验证 → 四项验收 → 打包 → 干净环境安装
set -e
ROOT="${0:A:h:h}"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
cd "$ROOT"
python3 tests/test_optics.py | tail -1
python3 tests/test_flare.py | tail -1
python3 tests/test_presets.py | tail -1
python3 tests/test_pupil.py | tail -1
python3 tests/test_gpu_lenspack.py | tail -1
python3 tests/test_coating.py | tail -1
python3 tests/test_glare.py | tail -1
python3 tests/test_reference.py | tail -1
"$BLENDER" -b --factory-startup --python tests/bl_smoke.py 2>&1 | grep -E "SMOKE|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_presets_smoke.py 2>&1 | grep -E "PRESETS|Traceback|rror:"
for p in c_series_style e_series_style g_series_style panavision_rear_134 panavision_rear_134_tele; do
  "$BLENDER" -b --factory-startup --python tests/bl_verify_mapping.py -- $p.json 2>&1 | grep -E "MAPPING|Traceback|rror:"
done
"$BLENDER" -b --factory-startup --python tests/bl_units.py 2>&1 | grep -E "UNITS|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_sampling.py 2>&1 | grep -E "^SAMPLING |Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_gpu.py 2>&1 | grep -E "^GPU |Traceback|rror:"
for p in c_series_style panavision_rear_134_tele; do
  "$BLENDER" -b --factory-startup --python tests/bl_verify_mapping.py -- $p.json --gpu 2>&1 | grep -E "MAPPING|Traceback|rror:"
done
"$BLENDER" -b --factory-startup --python tests/bl_dispersion.py 2>&1 | grep -E "^DISP|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_percentage.py 2>&1 | grep -E "^PCT |Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_gpu_ghosts.py 2>&1 | grep -E "^GHOSTS|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_gpu_ghosts.py -- BROADBAND 2>&1 | grep -E "^GHOSTS|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_spectral.py 2>&1 | grep -E "^SPECTRAL|Traceback|rror:"
for p in c_series_style e_series_style; do
  "$BLENDER" -b --factory-startup --python tests/bl_mumps.py -- $p.json 2>&1 | grep -E "^MUMPS|Traceback|rror:"
done
"$BLENDER" -b --factory-startup --python tests/bl_follow.py 2>&1 | grep -E "^FOLLOW|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_frames.py 2>&1 | grep -E "^FRAMES|Traceback|rror:"
"$BLENDER" -b --factory-startup --python tests/bl_acceptance.py -- all 2>&1 | grep -E "ACCEPT|Traceback|rror:"
mkdir -p dist
"$BLENDER" --command extension build --source-dir blenderlens --output-dir dist | tail -1
tests/install_test.sh "$BLENDER"
