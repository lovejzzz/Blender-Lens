# GPU 内核补丁漂移检查（0.8.2）：在本机 Blender 自带的 Cycles 内核源码里，每个补丁位置都必须逐字出现预期的次数；
# 在内存里打一遍补丁，结果必须含全部补丁文本、且不再含被替换的原文。Blender 升级后这里会明确失败并指出哪一处变了，
# 而不是在渲染时静默回退 CPU。纯 Python：python3 tests/test_kernel_patch.py（BLENDER 环境变量可指定 Blender）
import glob, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blenderlens.gpukernel as K

def ok(cond, msg):
    print(("ok  " if cond else "FAIL") + "  " + msg)
    if not cond:
        raise SystemExit(1)

blender = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
app = blender.split("/Contents/MacOS/")[0] if "/Contents/MacOS/" in blender else os.path.dirname(blender)
cands = sorted(glob.glob(os.path.join(app, "Contents", "Resources", "*", "scripts", "addons_core", "cycles"))) or \
        sorted(glob.glob(os.path.join(app, "*", "scripts", "addons_core", "cycles")))
ok(bool(cands), f"found Cycles add-on with kernel sources under {app}")
src = cands[-1]
version = src.split(os.sep)[-4]
for r in K.patch_report(src):
    ok(r["found"] == r["expected"], f"Blender {version}: {r['file']} {r['site']} found {r['found']}× (expected {r['expected']}×)")

# 内存里打补丁（与 prepare() 同样的替换）
def read(rel):
    return open(os.path.join(src, "source", "kernel", rel), encoding="utf-8").read()
cam = read(os.path.join("camera", "camera.h"))
cam = cam.replace(K.EXPECTED_ORIGINAL, K.PATCHED).replace(K.EXPECTED_SIGNATURE, K.PATCHED_SIGNATURE).replace(K.EXPECTED_CALL, K.PATCHED_CALL)
cam = cam.replace(K.ANCHOR, f'#include "kernel/camera/{K.HEADER_NAME}"\n\n' + K.ANCHOR)
init = read(K.INIT_FILE).replace(K.EXPECTED_INIT_CALL, K.PATCHED_INIT_CALL).replace(K.EXPECTED_PATH_INIT, K.PATCHED_PATH_INIT)
sd = read(K.SHADER_DATA_FILE)
sd = sd.replace(K.EXPECTED_SD_INCLUDE, f'#include "kernel/camera/{K.DEFS_NAME}"\n\n' + K.EXPECTED_SD_INCLUDE, 1).replace(K.EXPECTED_SD_WAVELENGTH, K.PATCHED_SD_WAVELENGTH)
ok(K.PATCHED in cam and K.PATCHED_SIGNATURE in cam and K.PATCHED_CALL in cam and K.HEADER_NAME in cam, "camera.h: all three patches applied")
ok(K.PATCHED_INIT_CALL in init and init.count("PATH_RAY_SPECTRAL; /* BlenderLens") == 2, "init_from_camera.h: wavelength + PATH_RAY_SPECTRAL patches applied")
ok(K.PATCHED_SD_WAVELENGTH in sd and K.DEFS_NAME in sd, "shader_data.h: shared wavelength patch applied")
ok(K.EXPECTED_CALL not in cam and K.EXPECTED_INIT_CALL not in init.replace(K.PATCHED_INIT_CALL, ""), "no unpatched originals left")
# 存根头文件（还没有镜头）下补丁必须是惰性的：所有改动都在 BLENDERLENS_GPU_CAMERA 宏里
for name in ("PATCHED_CALL", "PATCHED_INIT_CALL", "PATCHED_PATH_INIT", "PATCHED_SD_WAVELENGTH"):
    ok("BLENDERLENS_GPU_CAMERA" in getattr(K, name), f"{name} guarded by BLENDERLENS_GPU_CAMERA (inert with the stub header)")
print("ALL KERNEL PATCH TESTS PASSED")
