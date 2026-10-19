# AGENTS.md — 给在这个仓库里工作的 AI

BlenderLens：Blender 5.x 扩展，按公开专利的真实处方逐面追迹，渲染变形（anamorphic）电影镜头。
用户说中文、在意画质与准确性；所有结论要能指回专利数字或公开实测。

## 先跑这三条（不用开 Blender 界面）

```bash
python3 tests/run.py quick                     # 约 2 分钟：纯 Python 全部 + Blender 冒烟 + AI 接口
python3 tools/blcli.py info c_series_style     # 某个预设的光学数字（JSON，不启动 Blender）
python3 tools/blcli.py render --preset c_series_style --focus-m 3 --gpu --out renders/cli/c.png   # 内置演示场景出图，然后用 Read 看图
```

- `tests/run.py {quick|standard|full}`：写 `renders/test_report.json`（每项 pass / 用时 / 指标 / 失败输出尾部），并对照 `tests/baseline.json` 报告指标漂移。退出码 0 = 通过，1 = 有失败，2 = 只有指标漂移。改动光学或采样后至少跑 standard；发版跑 full（含打包 + 干净环境安装）。确认新数值正确后才 `--update-baseline`。
- `tools/blcli.py`：`presets` / `info` / `describe` / `render`（`--variants` 出 A/B 对比图）/ `diagnose --blend` / `test`。每个命令输出一个 JSON；`render` 结果里带图像统计、`bl.diagnose()` 与当前全部设置。
- 交互式 Blender：用户开着 Blender 并启动了 MCP 插件时，可以用 Blender MCP 工具（`execute_blender_code`、截图）直接操作；连不上就用上面的命令行（无界面 Blender，结果一样）。

## Blender 里的 AI 接口（`import blenderlens.api as bl`）

| 调用 | 用途 |
|---|---|
| `bl.describe()` | 版本、预设（焦距 / 压缩比 / 最近对焦 / 是否真处方）、**全部相机设置的模式**（类型、范围、枚举选项、说明——直接从 Blender 属性定义读出）、API 签名 |
| `bl.state(cam)` | 当前全部设置 + `lens_info` + 渲染设置 + GPU 状态 |
| `bl.configure(cam, f_number=2.8, focus_m=3, coating_type='BROADBAND', mumps_correction=0.5)` | 先全部校验再写入；未知名字 / 越界 / 枚举错误 → `ValueError`（列出可选值），什么都不改；返回 `{名字: [旧, 新]}` |
| `bl.diagnose()` | 场景问题清单：`level`（error / warning / info）、`code`、`message`、`fix`（可执行的修法） |
| `bl.preview(path, quality='draft')` | 低清快速渲染 + 图像统计；渲完恢复分辨率与采样 |
| `bl.contact_sheet(path, [ {...}, {...} ])` | 多组设置并排成一张图（A/B），渲完恢复原设置 |
| `bl.build / set_focus / set_stop / set_mumps_correction / lens_info / render_still / render_frames / follow_camera / enable_gpu` | 原有稳定接口，见 `blenderlens/HANDOFF.md` §6 |

距离一律用米（`focus_m`）；镜头内部毫米；场景单位任意。GPU（Metal）必须在本进程第一次 GPU 渲染之前 `bl.enable_gpu()`。

## 代码地图

| 文件 | 作用 | bpy |
|---|---|---|
| `blenderlens/prescriptions/*.json` | **单一事实来源**：每支镜头的处方、对焦组、补偿凸轮表、观感参数（`look`） | – |
| `blenderlens/optics.py` | 读处方、近轴、真实追迹、对焦求解、发腮补偿、镀膜（特征矩阵）、口径、画幅 | 否 |
| `blenderlens/oslgen.py` | 由处方生成 OSL 相机源码；`lens_data()` 与 GPU 共用；出瞳表的缓存（随包 / 磁盘） | 否 |
| `blenderlens/gpukernel.py` | GPU 路径：拷贝 Cycles 内核源码、两处补丁、生成 `blenderlens_lens.h`（与 OSL 逐行对应） | 部分 |
| `blenderlens/pupil.py` | 出瞳重要性采样表、轴上透过率 | 否 |
| `blenderlens/flare.py` | 正向追迹炫光（鬼影）、衍射星芒、杂光（ISO 9358 标定） | 否 |
| `blenderlens/lenspack.py` | 后期横向色差、ST 图、渐晕图 | 否 |
| `blenderlens/meshgen.py` | Geometry 模式的镜片 / 光圈 / 镜筒网格 | 否 |
| `blenderlens/rig.py` | 在场景里搭镜头、每帧同步（`apply_settings`）、渲染后期 | 是 |
| `blenderlens/agent.py` / `api.py` / `ui.py` | AI 接口 / 稳定脚本接口 / 面板与属性 | 是 |
| `tools/build_presets.py` | 从专利转录数据生成 E/G/Panavision/Neil 处方，并给 C/E/G 加补偿板 | 否 |
| `tools/build_pupil_tables.py` | 预设出瞳表（改了处方的光学部分或 `pupil.py` 后必须重跑） | 否 |
| `blenderlens/research/` | `ROADMAP.md`（计划与验收）、`RESEARCH.md`（来源、核对、修正记录）、专利原件 | – |

## 不能破坏的约定

1. **处方 JSON 是唯一来源。** 网格、OSL、GPU 头文件、出瞳表、对焦都由它生成；不要手改生成物。E/G/PV/Neil 的 JSON 由 `tools/build_presets.py` 生成，改脚本不改 JSON（C 是手工维护）。
2. **同一算法有三份实现，必须一致**：OSL（`oslgen.py` 里的着色器字符串）、GPU（`gpukernel.py` 的 `KERNEL_BODY`）、Python（`optics.py` / `flare.py` / `pupil.py`）。改了任何一份，就要同时改另外两份，并跑 `bl_gpu`、`bl_gpu_ghosts`、`map_*`、`bl_mumps`（CPU / GPU / Geometry 一致性）。
3. 坐标：原点在像面，物方 +Z，光沿 −Z，单位 mm；R > 0 曲率中心在像方；柱面用 `R_x` 或 `R_y` 为 null。
4. 结论要有出处：新数字写进 `RESEARCH.md`，推翻旧结论写进 §5「修正记录」；计划与完成情况写进 `ROADMAP.md`。
5. 发版：改 `blenderlens/blender_manifest.toml` 的 version → `python3 tools/build_pupil_tables.py`（如需要）→ 同步 `blenderlens/research/kit/`（tests、tools 的拷贝）→ `python3 tests/run.py full` → 提交。提交作者必须是 `lovejzzz <120167983+lovejzzz@users.noreply.github.com>`（仓库本地 git config 已设）；只在用户要求时推送。

## 踩过的坑（别再踩）

- Metal 上 OSL 相机静默出黑图 → GPU 路径用 `_cycles.init` 读打过补丁的内核源码拷贝；**不要改 Blender.app**（签名坏了 macOS 会删掉它）。
- 自定义相机在 OSL 里只有两维随机数（第三维恒为 0）；GPU 路径用 Cycles 的 time 维做波长分层（开运动模糊时退回哈希）。
- `resolution_percentage ≠ 100` 时后期按 `img.size` 取尺寸；对焦关键帧在运动模糊子帧里不能写属性（`apply_settings` 只写变化的值）。
- Cycles 薄膜在 IOR < 1 时不对 → 界面法 IOR 恒 ≥ 1、必要时翻法线。
- Blender 自定义法线旧接口有 16 位量化；Cycles 顶点法线八面体量化里 0 不可表示 → `rig._set_normals` 已处理。
- 鬼影 / Geometry 模式必须关自适应采样（否则鬼影低估约 8 倍）。
- 美国专利公报可能印错（US 11,287,618）：一份对不上先找同族（CN 111562632 B）。
- Blender 5.3 的 Principled 色散每条路径一个波长：GPU 路径已与它共用（「共用模式」，见 RESEARCH §6.12）；CPU / OSL 做不到，`diagnose()` 会警告。
- 改了 `gpukernel.py` 里任何补丁文本：内核拷贝目录名含补丁哈希，会自动重新拷贝；Blender 升级先跑 `tests/test_kernel_patch.py`。
- 跑长任务（Blender 测试、整套测试）放后台；Geometry 模式 CPU 渲染很慢。

## 看图

渲染结果是 PNG：用 Read 工具直接看。比较参数时优先 `--variants` 出对比图，一次看完。`preview` / `contact_sheet` 返回的 `stats`（平均颜色、过曝比例、全黑比例）可以先用来判断是不是黑图 / 过曝。
