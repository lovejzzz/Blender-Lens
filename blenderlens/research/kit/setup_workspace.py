# 从已安装（或解压）的扩展还原研究用的工作区：
#   <dest>/blenderlens/   插件源码（不含 research/）
#   <dest>/tools/         处方生成器、口径重算
#   <dest>/tests/         全部测试
#   <dest>/research/      研究资料（RESEARCH.md、专利原件、玻璃数据）
# 用法：python3 setup_workspace.py <dest>
# 之后在 <dest> 里运行：python3 tests/test_presets.py、python3 tools/build_presets.py、zsh tests/run_all.sh
import os, shutil, sys

KIT = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(KIT)
PKG = os.path.dirname(RESEARCH)

if len(sys.argv) != 2:
    sys.exit("usage: python3 setup_workspace.py <dest>")
dest = os.path.abspath(sys.argv[1])
if os.path.exists(dest) and os.listdir(dest):
    sys.exit(f"{dest} 已存在且非空")
ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".*")
shutil.copytree(PKG, os.path.join(dest, "blenderlens"), ignore=shutil.ignore_patterns("research", "__pycache__", "*.pyc", ".*"))
shutil.copytree(os.path.join(KIT, "tools"), os.path.join(dest, "tools"), ignore=ignore)
shutil.copytree(os.path.join(KIT, "tests"), os.path.join(dest, "tests"), ignore=ignore)
shutil.copytree(RESEARCH, os.path.join(dest, "research"), ignore=shutil.ignore_patterns("kit", "__pycache__", "*.pyc", ".*"))
os.makedirs(os.path.join(dest, "renders"), exist_ok=True)
os.makedirs(os.path.join(dest, "dist"), exist_ok=True)
for f in ("run_all.sh", "install_test.sh"):
    os.chmod(os.path.join(dest, "tests", f), 0o755)
print(f"workspace ready: {dest}")
