# BlenderLens 0.9.0 交接说明（给接手的 AI）

> 本文件随安装包分发，对应扩展版本 **0.9.0**（`blender_manifest.toml` 中的 `version`）。如果版本号对不上，以 manifest 为准，并提醒用户本文件可能已过时。

## 1. 这是什么

一个 Blender 扩展，用**公开专利的真实镜片处方 + 真实光线追迹**模拟变形（anamorphic）电影镜头：

- Cycles **OSL 自定义相机**逐面追迹整支镜头，产生竖椭圆焦外、边缘猫眼、发腮、畸变和色散。0.5 起同一算法也编进 Cycles 的 GPU 内核，可以在 Metal 上渲染（第 6.5 节）。
- **正向追迹炫光通道**产生一字拉丝和鬼影（镀膜 0.8 起可选单层 MgF₂ 或三层宽带膜，特征矩阵法）。
- 处方 JSON 是**单一事实来源**：网格、OSL 着色器、对焦表都由它生成，不要手改生成物。

Panavision 的 C/E/G/T/Primo 量产处方从未公开。除「Panavision 后置」两个预设外，其余都是「同结构公开专利 + 按 Panavision 公布规格设定观感参数」，**不是复刻**，对用户描述时不要说成真品。

## 2. 安装

要求：Blender **5.0 及以上**（开发和测试都在 macOS / Apple M2 Max / Blender 5.3 beta 上完成；Windows、Linux、5.0–5.2 未实测）。不依赖第三方 Python 包。

- 图形界面：Edit → Preferences → Get Extensions → 右上角 ⌄ → **Install from Disk…** → 选 `blenderlens-0.8.3.zip`。
- 命令行：

```bash
blender --command extension install-file -r user_default -e blenderlens-0.8.3.zip
```

装好后的 Python 模块路径是 `bl_ext.user_default.blenderlens`（仓库名 `user_default` 取决于安装到哪个仓库）。启用后插件会把自己同时登记为 `blenderlens`，脚本里直接写 `import blenderlens.api as bl` 即可，不用管仓库名。

## 3. 必须知道的五件事（最容易出错）

1. **场景单位（0.4 起支持米制场景）。** 镜头内部恒为毫米，与场景之间只差一个统一比例（OSL 出射光线起点和 `BL_Rig` 都按它缩放）。Build 的 `units`：
   - `AUTO`（默认）：场景已是毫米，或场景里已有内容（默认立方体 / 灯 / 相机之外的物体）→ **保留场景单位**；空场景 → 改成毫米（旧行为）。
   - `SCENE`：保留场景单位（米制生产场景用这个）；`MM`：把场景改成毫米。
   - 对焦距离属性按场景单位存、按场景长度单位显示；Python API 一律用米（`bl.set_focus(cam, metres=8.5)`）。
   - 已验证：同一画面在毫米场景和米制场景（物体缩小 1000 倍）里渲染，平均亮度相同到 5 位小数。
   - 每个 .blend 只能有一支 BlenderLens 镜头（处方存在一个文本数据块里；再次 Build 会替换旧镜头）。
2. **相机朝向和普通相机一样**：相机物体本地 −Z 是视线、+Y 是上。只移动或旋转 `BL_Camera` 本身，不要动它下面的子物体（`BL_Rig`、`BL_Move_*`、镜片网格），对焦时插件会自动移动它们。
3. **渲染设备（0.5 起支持 Metal GPU，见第 6.5 节）**：Cycles 原生的 OSL 自定义相机只在 CPU 和 NVIDIA OptiX 上运行，Metal / HIP / oneAPI 上会静默出黑图。0.5 的 **GPU 路径**把镜头追迹编进 Cycles 的 GPU 内核：
   - 合成基准下 256 spp 1.23 s，比 0.5 的 CPU（23.0 s）快 19 倍、比 0.4 的 CPU（69.7 s）快 57 倍，与 CPU / OSL 画面一致。
   - **必须在本次启动后第一次 GPU 渲染之前启用。**
   - 没启用、Blender 版本不匹配、Geometry 模式时，每次渲染开始会自动改回 CPU，并在控制台和面板上写明原因，不会出黑图。0.7 起相机端鬼影（Ghosts / Flare）也能在 GPU 上跑（与 CPU 对照能量差 0.6%，快 19 倍）。
4. **采样**：色散和光瞳采样都会摊薄每个像素的采样。0.4 的出瞳重要性采样在最大光圈下把同等噪点所需的采样减到 1/1.4–1/1.8（无偏，实测）；0.8 把采样表加密，带光占比再提高约 1.25 倍。GPU 路径 0.8 起波长分层采样，色噪再降 2–2.7 倍（不开运动模糊时）。720p 仍建议 **≥ 512 spp + OIDN**（prefilter ACCURATE），过低会出现斑块状降噪痕迹。关闭色散可以进一步降低噪点。
5. **点光源炫光必须用「Render with Flare」**。相机里的「Ghosts / Flare」复选框对点光源几乎收敛不了，只适合窗户、天空这类大面积亮源。几何模式或开启鬼影时，插件会自动关闭自适应采样（否则鬼影会被低估约 8 倍）。

## 4. 镜头预设

| 文件 | 名称 | 光学来源 | 原生焦距（垂直）· 光圈 · 最近对焦 | 画幅 · 解挤压 |
|---|---|---|---|---|
| `c_series_style.json` | C 系列风格（1968，0.8 起默认带发腮补偿） | US 2,944,464（Rosin 1957，前置 2×）+ US 4,234,242（Nikon f/2 双高斯） | 50 mm · f/2.3 · 0.762 m | S35 变形 22.0×18.6 · 2.0 |
| `e_series_style.json` | E 系列风格（1980 年代） | US 5,184,880 实施例 3（Isco 1990，前置 2.09×，凸轮对焦）+ 同一主镜 | 131 mm · f/2.8 · 1.143 m | 同上 · 2.09 |
| `g_series_style.json` | G 系列风格（2007） | 同 E，整体缩放 0.763 | 100 mm · f/3 · 0.914 m | 同上 · 2.09 |
| `panavision_rear_134.json` | Panavision 后置 1.34×（真实处方） | US 7,085,066（Iain Neil / Panavision），变焦镜短焦端 Z1 + 8 片后置变形组 | 20.6 mm · f/3.66 · 0.476 m | 16:9 23.71×13.34 · 1.35 |
| `panavision_rear_134_tele.json` | 同上，长焦端 | 同上，变焦镜 Z4（间隔取自 US 6,122,111） | 66.4 mm · f/4.1 · 0.476 m | 同上 |
| `neil_2x_50.json` / `neil_2x_65.json` / `neil_2x_85.json` | Neil 2× 现代变形（真实处方，0.6） | CN 111562632 B（Iain Neil / 思锐 Sirui，2019）：同一前变形部分 + 三个可换后组 | 50 / 65 / 85 mm · f/3 · 1.08 m | 21.30×17.82 · 2.06 |

- 光圈数值取自 Panavision 公布的 **T 值**，模型里当作几何 f 值使用（真实镜头约亮 0.2 档）。
- 长焦端的变焦镜口径用 US 6,122,111 给出的各面最大通光直径（镜片实物尺寸）。0.3 误用 Z1 的光束高度，轴上光束被切掉一半以上，0.4 已修正。
- Neil 2× 三个预设（0.6）：
  - 不是 Panavision 镜头，是 Panavision 后置变形组的同一位设计者 Iain Neil 为思锐做的现代 2× 前置变形。
  - G1b 两片整体移动对焦，一个自由度同时对准两向；压缩比 2.06 → 1.93（发腮 −6.6%，比 C/E/G 温和得多）。
  - 数据必须用中国授权本 CN 111562632 B；美国本 US 11,287,618 的表格印刷错误很多（详见 research/RESEARCH.md §3.6、§5 第 12 条）。
- Panavision 后置两个预设：对焦按原变焦镜专利（US 6,122,111）的内对焦，前两组按 1 : 0.909 移动，所以两向同时合焦、没有发腮；最近对焦 0.48 m 取自该专利 F3（物面到第一面 133 mm），行程与专利表吻合到 0.01%（F3）/ 1%（F2）。

- **发腮补偿（0.8，C / E / G 风格）**：变形器最前面一块与对焦联动的补偿板（Auto Panatar 式反向旋转柱面对的一阶等效，`optics.compensator`）。面板 **Mumps Correction**（`p.mumps_correction`，0–1）默认 1：每个距离压缩比 = 无穷远，呼吸以垂直为主，焦外 1.9–2.3 竖椭圆——与 ARRI / Panavision 公开资料一致。设 0 = 1950 年代未校正（C 近摄 2.01 → 1.69×）。强度写进场景里的处方文本（`focus.compensator.level`），所有派生计算（炫光、后期色差、出瞳表）都按它算。
- **镀膜（0.8）**：`p.coating_type` = `'SINGLE'`（单层 MgF₂，C/E/G 默认）或 `'BROADBAND'`（三层宽带膜，Neil / Panavision 后置默认，`coating_nm` 94 ≈ 设计波长 520 nm）。

**统一焦段**：Build 时设 `focal_mm`，可把任意预设按比例缩放到同一垂直焦距（变形镜头按垂直方向命名焦距）。几个预设能同时物理成立的共同焦段是 **100 mm**：E/G 用的 Isco 附加镜是长焦设计，低于约 100 mm 会超出设计视场。Panavision 短焦端缩到 100 mm 要放大 4.85 倍，是假想的超大镜头；比较时优先用长焦端（放大 1.51 倍）。

## 5. 面板用法

3D 视图按 N，打开 **BlenderLens** 标签：

1. **Build Lens**：从下拉菜单选预设，搭建相机和镜头，并设为场景相机，同时设置输出分辨率（已按解挤压比换算）。按 F9（或左下角的操作面板）可以改：
   - **Vertical Focal Length**：缩放到这个垂直焦距（0 = 原生）。
   - **Gate**：画幅，镜头本身不变。
     - Full Aperture：处方原光窗。
     - 2.39 Extraction / 16:9 Extraction：从原光窗里**裁出**解挤压后为该比例的最大矩形。例如 C 系列 2× 的 16:9 抽取是 16.53 × 18.6 mm，丢掉两侧。
     - **16:9 Native (Full Circle)**：**原生 16:9、不裁切**。取镜头像圈（原光窗的半对角线）内、解挤压后为 16:9 的最大矩形，画面比原光窗更高：C 系列 19.14 × 21.53 mm，比 16:9 抽取多用 34% 的像面；Panavision 后置 21.67 × 16.45 mm，像圈半径 13.60 正是专利像高。各预设在这个画幅四角的光瞳通过率为 0.55–0.82，与原光窗四角相当。
     - Custom：自填宽高。
     - 超出像圈（四角或上边缘光瞳通过率 < 0.3）会警告。
   - **Scene Units**：见第 3 节第 1 条。
2. 相机设置（也在 Properties → 相机数据 → BlenderLens）：

| 属性 | 含义 |
|---|---|
| Mode | Analytic（OSL 逐面追迹，日常用）/ Geometry（Cycles 穿过真实镜片网格，验证或看反射用，噪点多） |
| Focus Distance | 从像面量起的对焦距离（场景单位，按场景长度单位显示），0 = 无穷远；低于预设最近对焦距离时按最近对焦算；可打关键帧 |
| Mumps Correction（0.8） | 只在 C / E / G 风格出现。发腮补偿强度 0–1，默认 1（Auto Panatar 式：各距离压缩比不变、呼吸以垂直为主）；0 = 1950 年代未校正。可打关键帧（属性动画按帧同步） |
| f-Number | 光圈（垂直截面 f 值），不能小于预设的最大光圈 |
| Blades / Rot | 光圈叶片数（0 = 圆形）和旋转角 |
| Dispersion | 连续波长色散（400–700 nm，按 R/G/B 传感器响应着色）；强度见 OSL 参数 `dispersion_strength`（0–1） |
| Compensate Exposure | 收光圈时补偿曝光；关闭则按物理变暗 |
| Ghosts / Flare | 相机端随机鬼影（只适合大面积亮源） |
| Coating / 膜系 / Coating (nm) | 镀膜开关、膜系（0.8：Single MgF₂ / Broadband 3-layer）与外层膜厚。单层 100 nm 为标准（残余反射蓝紫），125–140 nm 为风格化（更蓝）；宽带膜 94 nm ≈ 设计波长 520 nm，鬼影弱一个量级、偏品红 / 琥珀 |
| Starburst / Intensity（0.6） | 衍射星芒：光圈多边形的夫琅禾费衍射，按 R/G/B 与两向数值孔径计算，叶片数 6 → 6 芒，变形镜头的星芒会被拉伸；叶片数 0 时只有 Airy 光环。只加在 Render with Flare / API 输出里，只对标记的炫光光源 |
| Veiling Glare（0.6） | 杂光指数 VGI（0.8 起按 ISO 9358 标定：白场中央黑斑处测出的就是这个值；长尾 + 均匀雾化，不环绕）。搭建时取预设值（按年代 / 镀膜估计：C 2.5%、Panavision 1.2%、E / Neil 0.8%、G 0.5%），0 = 关。只加在 Render with Flare / API 输出里 |
| Flare Color / Tint / Intensity | Render with Flare 的着色：Physical（镀膜算出的颜色）/ Light Color（灯光本色）/ Amber（琥珀）/ Custom；Intensity 为倍数，1 = 物理正确。非 Physical 模式只换颜色，亮度不变 |

3. **Mark Flare Source**：把选中的发光物体标记为炫光光源。光源最好是一个**小的独立发光物体**（例如灯泡位置放一个发光小球），因为炫光通道用物体的原点作光源位置、最大尺寸的一半作半径。没有任何标记时，自动选取发光强度 ≥ 2×10⁴ 的物体。
4. **Render with Flare**：渲染当前帧，再叠加正向追迹的炫光层，按场景色彩管理写出（PNG 为 16 位）。只渲染单帧，动画需要逐帧调用。
5. **Recompute Apertures**：改了处方后重新估算口径。**Export JSON**：导出当前处方。

## 6. 用 Python 驱动（推荐给 AI 用）

**AI 原生接口（0.8.1）**——不看界面也能完整发现、设置、检查、预览：

```python
import blenderlens.api as bl
info = bl.describe()          # 版本、预设（焦距 / 压缩比 / 最近对焦）、全部设置的模式（类型 / 范围 / 枚举 / 说明）、API 签名
cam = bl.build("c_series_style.json", units="SCENE")
bl.configure(cam, focus_m=3, f_number=2.8, coating_type="BROADBAND", mumps_correction=1.0)   # 先校验再写；错了抛 ValueError 并列出可选值，什么都不改
print(bl.state(cam))          # 当前全部设置 + 光学信息 + 渲染设置 + GPU 状态（JSON 可序列化）
print(bl.diagnose())          # 问题清单：level / code / message / fix
bl.preview("//p.png", quality="draft")                       # 低清快速出图 + 图像统计（渲完恢复设置）
bl.contact_sheet("//ab.png", [dict(f_number=2.3), dict(f_number=8)])   # 多组设置并排（A/B）
```

稳定入口 `blenderlens.api`（0.4 起；距离用米，与场景单位无关）：

```python
import bpy, math
import blenderlens.api as bl                            # 启用扩展后可用，不依赖仓库名
from blenderlens import rig

cam = bl.build("c_series_style.json", focal_mm=40, gate="16:9_NATIVE", units="SCENE", height=1080)
cam.location = (0.0, -6.0, 1.4)                         # 场景单位（这里是米制场景）
cam.rotation_euler = (math.radians(90), 0, 0)           # 视线 = 世界 +Y
bl.set_focus(cam, metres=8.5)                           # 从像面量起；None = 无穷远
bl.set_stop(cam, 2.8)
bl.set_mumps_correction(cam, 1.0)                       # 0.8：发腮补偿 0–1（C / E / G 风格；默认 1）
cam.data.blenderlens.coating_type = 'BROADBAND'         # 0.8：镀膜 'SINGLE' / 'BROADBAND'
print(bl.lens_info(cam))                                # 焦距、压缩比、光窗、单位换算……

cam.data.blenderlens.flare_color = 'AMBER'
bpy.data.objects["MyBulb"][rig.FLARE_PROP] = 1          # 小的发光物体，标记为炫光光源
sc = bpy.context.scene
sc.cycles.samples = 512
sc.cycles.use_denoising = True
bl.render_still("//out.png", flare=True)                # 渲染 + 正向炫光层
```

- `bl.build` 的参数：`preset`（文件名或 JSON 路径）、`prescription`（直接给 dict）、`focal_mm`、`gate`（'FULL' / '2.39' / '16:9' 抽取 / '16:9_NATIVE' 原生满像圈 / 'CUSTOM' 配 `gate_size_mm=(w, h)`）、`units`（'AUTO' / 'SCENE' / 'MM'）、`height`、`set_render`。
- 老写法照样能用：`bpy.ops.blenderlens.build(...)`、`rig.apply_settings(cam)`、`rig.render_with_flare(sc, path)`。改相机属性会立即同步到 OSL 参数和镜片位置。
- 只要数字、不需要渲染：纯 Python 模块可以在 Blender 外按包导入（0.4 起 `__init__` 不再强制导入 bpy）。把扩展目录的上一级加进 `sys.path`，然后：

```python
import blenderlens.optics as optics
rx = optics.load(optics.PRESCRIPTION_DIR + "/e_series_style.json")
optics.squeeze_at(rx, 2500.0)               # 该对焦距离下的压缩比
optics.bokeh_aspect(rx, 1200.0, 20000.0)    # 焦外高宽比（解挤压后）
optics.solve_focus(rx, 2500.0)              # 两组对焦移动量 (Δ1, Δ2)
optics.rescale_to_vertical_efl(rx, 100.0)   # 缩放到垂直 100 mm
```

## 6.5 GPU 路径（0.5，Metal 实测）

**原理**：
- Cycles 的 Metal 内核在运行时从源码编译，源码随 Blender 分发在 `Resources/<版本>/scripts/addons_core/cycles/source/kernel/`。
- 没有 OSL 的设备上，`kernel/camera/camera.h` 的 `camera_sample_custom()` 直接返回 0，这就是 Metal 黑图的原因。
- GPU 路径的做法：
  - 把整个 Cycles 插件目录（7.6 MB）拷到本扩展的用户目录（`extension_path_user(..., "gpu_kernel")`；开发副本则在 `~/Library/Caches/blenderlens_gpu_kernel`）；
  - 只替换 `camera_sample_custom()` 的那一个分支，让它调用生成的 `kernel/camera/blenderlens_lens.h`：处方常量 + 逐面追迹，与 OSL 相机的 Analytic 直射光路同一份数据、同一算法；
  - 再用 `_cycles.init(<拷贝路径>, …)` 让 Cycles 改读这份源码。
- **不改 Blender.app、不重签名、不碰全局配置**；面板上的 Remove 或 `bl.remove_gpu_cache()` 会删除拷贝，重启 Blender 后完全恢复原状。

**用法**：
- 面板：BlenderLens → **Enable GPU Path**，然后把 Cycles 设成 GPU（Metal）。
- Python：`import blenderlens.api as bl; bl.enable_gpu()` 会同时把 Cycles 设成 Metal GPU，返回 (是否启用, 说明)。
- 命令行后台渲染（扩展已在偏好设置里启用）：

```bash
blender -b shot.blend --python-expr "import blenderlens.api as bl; print(bl.enable_gpu())" -a
```

- 分帧 / 断点续渲 / 多机：

```bash
blender -b shot.blend --python-expr "import blenderlens.api as bl; bl.enable_gpu(); bl.render_frames(frames=range(1, 145), out_dir='/共享目录/shot010')"
```

**动画**：对焦距离、f 值、叶片数可以打关键帧，GPU 路径同样生效。实测对焦关键帧 1.5 m → 6 m，GPU 与 CPU 逐帧平均亮度差 < 0.01%。

**改变用途的相机字段**：只对 BlenderLens 相机生效，普通相机不受影响。Blender 升级时要检查这些字段仍然传到内核：

| Blender 相机属性 | KernelCamera 字段 | 编码 | 新含义 |
|---|---|---|---|
| `stereo.convergence_distance` | `convergence_distance` | 1000 + Δ1 | 对焦位移 Δ1（mm） |
| `fisheye_fov` | `fisheye_fov` | 16 + Δ2 / 5 | 对焦位移 Δ2（mm） |
| `dof.aperture_ratio` | `inv_aperture_ratio` | 1 / f 值 | 光圈 f 值 |
| `dof.aperture_blades` | `blades` | 叶片数 | 光圈叶片数 |
| `dof.use_dof` | — | True | 让 Blender 同步上面两个 dof 字段 |

其余设置编进头文件，改了下一次 GPU 渲染会重新编译内核（约 10 秒）：色散开关与强度、出瞳重要性采样、曝光补偿、叶片旋转、场景单位。

**版本检查与回退**：
- `camera.h` 中被替换的那段必须与插件预期逐字一致（`gpukernel.EXPECTED_ORIGINAL`），否则拒绝启用并说明原因，渲染退回 CPU / OSL。
- 只在 Blender 5.3 beta（macOS、Metal、M2 Max）上实测；OptiX / HIP 原理相同，未测。

**Metal 编译器**：
- 首次内核编译约 2 分钟，之后有缓存；镜头或静态设置变了，重编约 10 秒。
- 处方数组用 `ccl_inline_constant`（与 Cycles 自带的 blackbody / CIE 表同一写法），逐面循环、牛顿迭代都能正常编译，没有改动算法，也没有精度损失。
- 平面 / 球面 / 纯柱面用解析求交（float32 下与 float64 牛顿相差 ≤ 0.0004 mm）。

**实测**（M2 Max 38 核 GPU，MessyRoom 合成基准 960×540）：

| | 64 spp | 256 spp | 256 spp 噪点 |
|---|---|---|---|
| CPU / OSL（0.4.0） | 21.6 s | 69.7 s | 0.0880 |
| CPU / OSL（0.5.0，解析求交） | 6.9 s | 22.9 s | 0.0880 |
| **GPU（0.5.0）** | **0.63 s** | **1.23 s** | 0.0880 |

- GPU 与 CPU / OSL：平均亮度差 0.001%，整体位移 0.000 px（相位相关），模糊后逐像素差 0.09%（噪点水平）。
- 画面映射：刻度靶误差 GPU 与 CPU 完全相同（C 系列 0.71 px、Panavision 长焦端 0.30 px）。
- 收敛：GPU 上 64 → 256 spp 噪点下降 2.02 倍。

**限制**：
- GPU 路径做 Analytic 光路（含相机端鬼影，0.7）。Geometry 模式仍走 CPU；Render with Flare 是后期叠加，不受影响。
- 鬼影设置（开关、增益、镀膜）编进 GPU 内核，改了会重编（约 10 秒）。
- 光线微分为 0。这只影响纹理过滤的 mip 级别，合成基准下画面差在噪点以内。

**MessyRoom 在 M4 Max（40 核 GPU）上的复测**（汽车旅馆第 110 帧，960×540，256 spp）：

| 配置 | 时间 | 原始噪点 | 64→256 收敛 |
|---|---|---|---|
| 0.4.0 CPU | 119 s | 0.581 | 1.79× |
| 0.5.0 CPU | 72 s | 0.575 | 1.83× |
| 0.5.0 GPU Full | 19 s | 0.521 | 1.93× |
| 0.5.0 GPU Lateral in Post | 19 s（后处理 0.6 s） | 0.274 | 2.28× |

- GPU 对 CPU：整体位移 0 px，平均亮度差 0.31%，逐像素差低于噪点地板。
- 4K 实测（GPU、Lateral in Post、16:9 Native、256 spp + OIDN）：每帧 180 s；0.4 CPU 外推约 75 分钟。

**0.5.1 修正**：`render_frame`（Render with Flare / Lateral in Post / render_frames）现在按实际输出尺寸处理，`resolution_percentage` 不是 100% 时也正确（MessyRoom 报告）。另外，开了 Crop to Render Region 时会明确报错，而不是出错图。

## 6.6 色散模式、曝光差、分帧渲染（0.5）

- **Dispersion Mode**（相机设置）：
  - **Full**：逐采样按波长追迹，横向 + 纵向色差都有，色噪最多。
  - **Lateral in Post**：渲染时关色散，横向色差（边缘彩边）由处方算出的 11 个波长映射在后期精确加上。纵向色差（焦外紫 / 绿边）没有。
    - 实测：彩边位置与 Full 相差 ≤ 0.11 px；64 spp 色噪从 3.55 降到 0.08（约 45 倍），亮度噪点相当。
    - 只在 Render with Flare / `bl.render_still` / `bl.render_frames` 的输出里生效；直接 F12 渲染没有彩边。
  - **Off**：无色散。
- 导出给 Nuke / Resolve：`blenderlens.lenspack.stmaps(rx, W, H, focus_mm)` 给出每波长与 R/G/B 的 ST map；`vignette_map(...)` 给出渐晕图；`lens_info(...)` 给出压缩比 / 焦距随对焦的变化。
- **`bl.exposure_vs_pinhole(cam)`**：相对同机位 Cycles 普通相机的曝光差（EV）。
  - 实测 C 系列 40 mm、f/2.3、16:9 抽取：预测整幅 −0.322 EV / 中心 −0.018 EV，渲染实测 −0.313 / −0.016。
  - 整幅偏暗来自渐晕 × cos⁴（普通相机没有）；普通相机要匹配，就把曝光加上 `frame_mean_ev`。
- **`bl.render_frames(scene, frames, out_dir, name, seed=0, flare=False)`**：
  - 种子 = seed + 帧号，同一帧在任何机器上结果逐像素相同；
  - 先写 `.partial` 再原子改名，已有帧跳过，所以可以断点续渲；
  - 每帧开始前原子创建 `.lock`，两台 Mac 对同一共享目录跑同一条命令就会自动分帧，过期锁（默认 12 小时）会被接手；
  - 输出按扩展名：EXR 为线性，PNG / TIFF 为 16 位并按场景色彩管理。
- **相机运动模糊（0.6.1，MessyRoom 反馈）**：
  - 每帧只拷贝源相机的 `matrix_world`，快门时间里相机不动，相机运动模糊会丢失。要给 BlenderLens 相机打关键帧。
  - 用 **`bl.follow_camera(cam, source, frame_start, frame_end, substeps=1, focus=True)`**：逐帧（`substeps` > 1 时含子帧）把源相机的位置 / 旋转烘焙成关键帧，`focus=True` 时同时烘焙源相机景深的对焦距离（或对焦物体的距离）。烘焙后再 `render_frames`。
  - 实测：横移镜头开运动模糊，烘焙后点光源拖影 28 px，只拷贝矩阵时 4 px。
  - **0.6.0 及以前的缺陷**：只要 BlenderLens 相机的对焦 / 光圈有关键帧，相机运动模糊就会丢失。原因是每帧同步的回调在运动模糊的子帧也会写相机参数，打断了相机运动的导出。0.6.1 起回调跳过子帧，`apply_settings` 只写变化了的值。

## 6.7 多层 EXR、对焦助手、闪烁检查（0.8.3，MessyRoom Day 4 的需求）

**多层 EXR**（`bl.render_still(path, passes="all")`、`bl.render_frames(..., passes="all")`；也可给渲染层名列表）：
- 写多部分 EXR（Blender 5.x 的多层格式：每个渲染层一个部分，名为「视图层.渲染层」），保留视图层打开的所有渲染层。
- **颜色层**（Combined、各光组 `Combined_<名>`、Emit、Diffuse / Glossy / Transmission / Volume、AO、颜色 AOV、Noisy Image、Denoising Albedo）做与 Combined 相同的横向色差（Lateral in Post 时）→ 各光组相加仍等于 Combined（实测相对差 0.0）。
- **数据层**（Depth、Normal、Position、UV、Index、Cryptomatte、数值 AOV、Denoising Normal / Depth）原样。
- 插件加的层：`BL_Flare`（正向炫光 + 星芒）、`BL_Veil`（杂光，差值层，可为负）、`BL_Final`（= Combined + BL_Flare + BL_Veil，即单层输出的成片）、`BL_Vector`。**Combined 本身不含炫光与杂光。**
- **运动矢量用 `BL_Vector`，不要用 Cycles 的 `Vector`**：Cycles 对自定义相机还没有反向映射（源码 `camera.h` 的 TODO，退回相机坐标），实测数值约 0.003 px、毫无意义（原样保留在文件里）。`BL_Vector` 按本镜头的真实成像算（主光线、每帧的对焦状态），约定与 Cycles 相同：像素单位、y 向上，**X,Y = 上一帧位置 − 当前位置，Z,W = 当前位置 − 下一帧位置**。计入相机运动与刚体物体运动（按 Cryptomatte 识别物体）；骨骼 / 形状键形变不计。实测：运动物体上一个点三帧的实际位移与矢量差 0.2 px。
- **Depth / Position 的洞已补**：Cycles 只在第 0 个采样写 Depth、Position、物体 / 材质索引；真实镜头的第 0 条光线常被光阑挡掉，约一半像素这些层是 0（纯发光表面则完全不写）。插件用同一物体的相邻像素补洞，补不上的沿主光线 ray_cast；Depth 按 Cycles 对自定义相机的定义（到相机原点的距离）重算这些像素。统计写在 Combined 头的 `blenderlens/fill`。
- 渲染时会临时关闭合成（输出原始渲染层）、临时打开 Position 与 Cryptomatte Object（用户没开的不写出）。
- Render Region + Crop 可以和 Lateral in Post / 炫光一起用了：按裁切区域在整幅里的坐标做（杂光是整幅卷积，裁切后只能近似）。

**对焦助手**（距离都与镜头对焦相同：从像面沿光轴的物距，米）：
```python
bl.focus_on(cam, "Room")                  # 物体（原点）、(x, y, z) 世界点、或 (u, v) 画面坐标（沿主光线 ray_cast）；返回米
bl.focus_pull(cam, "Room", "Sign", 100, 148)   # 逐帧关键帧：A → B（按 1/距离 插值，缓入缓出）；b=None 时一直跟住 A
bl.dof_range(cam, coc_px=1.0)             # 清晰范围 near_m / far_m、超焦距 hyperfocal_m（输出像素，解挤压后，两向取大）
bl.stop_for(cam, 19, 38, coc_px=1.0)      # 同时保住两个距离的 f 值与对焦距离（apply=True 写到相机）
cam["bl_subject"] = "Room"                # diagnose 会检查主体是否在清晰范围内（没设就看画面中心）
```
景深用每个截面的近轴离焦弥散（相对对焦面的像，与 DoF 计算器同一定义）；`astigmatic_floor_px` 另给出该设计在对焦面上两向焦点不重合造成的底（C 风格 4K f/2.8 约 2.5 px，Neil 约 5.5 px）。C 风格 3 m f/2.8 4K：清晰 2.90–3.11 m，超焦距 78.2 m（薄透镜估算 78.1 m）。

**闪烁检查**：
```python
bl.temporal_check(scene, frame=120, n=8, height=1080,
                  variants=[{"spp": 1024}, {"denoise": False}, {"hide": ["haze"]}])
```
相机锁在第 120 帧（动画暂时摘掉，测完恢复），时间照常走 8 帧，测 x/(1+x) 亮度的相邻帧差：整幅平均、最亮 2% 像素平均、p99；每个变体给出相对原设置的百分比，另存闪烁热力图 PNG。命令行：`python3 tools/blcli.py temporal --blend film.blend --frame 120 --variants v.json`。

**内存基线**（M2 Max 96 GB、Blender 5.3 beta、简单测试场景、3840×2160、GPU、Lateral in Post，`/usr/bin/time -l`）：

| 输出 | 用时 | 常驻内存峰值 | 内存占用峰值（含 Metal） |
|---|---|---|---|
| 只出成片（0.5.1 / Day 4 的用法） | 14 s（64 spp） | 2.6 GB | 7.0 GB |
| `passes="all"`（深度、法线、矢量、漫射 / 光泽、两个光组、去噪数据） | 41 s（64 spp） | 9.2 GB | 12.6 GB |

真实场景的几何、纹理、体积另外加上去；内存占用峰值出现在 Cycles 渲染阶段（4K 的每个渲染层都有胶片缓冲）。同时跑两个 4K 全渲染层的 Blender，按 2 × 12.6 GB 加场景本身估算。

**从 0.5.1 换到 0.8.3 的画面变化**（C 风格、16:9 Native、GPU、Lateral in Post、无炫光、对焦 19 m、f/2.8，同场景 512 spp 对比）：曝光 −0.0016 EV（0.1%）；几何整体平移 0.05 px、局部最大 0.18 px（噪点水平）；对焦面锐度 99.75%（不变）；逐像素差 6.7% 是采样噪点。0.8 的发腮补偿在 19 m 处几乎不起作用。

## 6.8 体积 / 内存、运镜闪烁、矢量可信度、时间降噪、一条命令渲一个镜头（0.8.4，MessyRoom Day 4 成片后的需求）

**体积与内存（R4）**：
```python
bl.render_still(path, passes="temporal")                 # 预设：Combined、Noisy Image、Volume Direct/Indirect、Denoising Albedo /
bl.render_frames(..., passes=["temporal", "lightgroups"])  #   Specular Albedo / Normal / Roughness / Depth、Depth + 插件层；可加光组
bl.render_still(path, passes="all", exr_codec="DWAB", half_color=True)
```
- 颜色层 half + 压缩（`exr_codec`），数据层（Depth、Position、BL_Vector、BL_VectorConfidence、去噪法线 / 粗糙度 / 深度）恒为 float32，遇到有损压缩（DWAA / DWAB / B44 / PXR24）自动改 ZIP——与 Blender 自己写多层 EXR 的做法相同。用预设时默认 DWAB + half；否则沿用场景的输出设置。
- passes 不是 "all" 时，视图层里开着但没人要的渲染层在渲染期间临时关掉（Cycles 少算、少占内存），预设需要的层临时打开，渲完全部恢复。
- 后期流式处理：Combined 与补洞 / 矢量要用的层先读，其余渲染层每次 4 个「读 → 横向色差 → 写」，用 Blender 自带的 OpenImageIO 逐部分写多部分 EXR。横向色差改为多线程、按行块放大映射。
- 4K 实测（简单测试场景、3840×2160、GPU、64 spp、Lateral in Post，`/usr/bin/time -l`）；`rig.LAST_TIMING` 有每一步的用时与到该步为止的峰值常驻内存（render_shot 写进日志）：

| 输出 | 用时 | 常驻内存峰值 | 内存占用峰值（含 Metal） | 文件 |
|---|---|---|---|---|
| 只出成片 | 14 s | 2.6 GB | 7.0 GB | 28 MB |
| 0.8.3 `passes="all"` | 41.7 s | 8.9 GB | 12.6 GB | 555 MB |
| 0.8.4 `passes="all"`（float32） | 29.8 s | 8.4 GB | 12.6 GB | 555 MB |
| 0.8.4 `passes="temporal"`（half + DWAB） | 27.8 s | 7.6 GB | 11.5 GB | 98 MB |

  内存占用峰值出现在 Cycles 渲染阶段，取决于视图层开了多少渲染层（每层一块 4K 胶片缓冲）；光组也算，`temporal` 预设不会关光组（关不了），不要的光组请在视图层里删掉。

**运镜中的闪烁（R1）**：
```python
bl.temporal_check(scene, frame=40, n=16, mode="moving", variants=[{"spp": 1024}, {"flare": True}])
```
相机照真实运动走，每帧写多层 EXR，用 BL_Vector 把 t−1 帧对齐到 t 帧再测残差（与 MessyRoom flicker_moving.py 同一定义），另给 `detail`（平均 |拉普拉斯|）与 `motion`：速度直方图（1080 行等效 px/帧，区间 0 / 0.25 / 0.5 / 1 / 2 / 4 / 8 / 16 / 32 / 64）、每个速度区间的平均闪烁、每帧中位速度。残差里也含对齐误差，所以主要用来比较变体与版本。命令行：`blcli.py temporal --mode moving`。

**BL_Vector 的可信度（R2）**：多层输出多了 `BL_VectorConfidence`（单通道 X，0–1），各项相乘：
- valid：矢量算出来了，上一帧位置在画面里；
- occlusion：当前帧各像素按 Depth 溅射到上一帧 / 下一帧的位置（z-buffer），自己的位置在那一帧被明显更近的东西占着 → 0（去遮挡 / 将被遮挡）；
- edge：深度不连续处的混合像素（相邻矢量差 > 0.5 px 开始降，2 px 为 0）；
- emitter：亮光源（> 1.5）外圈——那里其实是光源的散焦 / 辉光，跟着光源动，矢量却是背景的：光源与该像素矢量差 > 0.25 px 开始降，1 px 为 0；外圈宽度 = 光源深度处的散焦直径 / 2 + 2 px；
- specular：镜面（Denoising Specular Albedo 高、Roughness 低）倒影不跟表面走 → 1 − mirror（要有去噪数据层，`passes="temporal"` 有；没有时 `specular_assessed=false`）；
- speed：> 16 px/帧（1080 等效）开始降，48 px/帧为 0。
统计写在 Combined 头的 `blenderlens/vector_confidence`。另外两处矢量修正：**天空 / 环境**（没有几何的像素）当作无穷远，只随相机转动（0.8.3 是 0）；**Cryptomatte 识别物体**修了哈希 → float 的转换（0.8.3 约 1/128 的物体认不出，矢量里没有物体运动，见 RESEARCH §5 第 16 条）。发光体本身的矢量准（实测 0.1 px）；**移动的阴影**落在静止表面上（矢量是表面的）、主反射的镜面运动矢量目前没有，只在置信度里标低（镜面）或标不出（阴影）。

**时间降噪（R3）**：
```python
bl.temporal_denoise("//shots/heli", "//shots/heli_stable", frames=range(40, 57),
                    strength=1.0, protect=["specular", "emitters"], vfade_px=(2, 8), radius=3)
```
在 MessyRoom hazefix 的做法上：对齐的是 **Combined**（BL_Flare、BL_Veil 没有采样噪点，最后原样加回——鬼影绕画面中心反向运动，对不上矢量也不会被抹）；沿 BL_Vector 链式对齐 t−3…t+3（权重 1-2-3-4-3-2-1），样本按当前帧 5×5 邻域 min / max（外扩 25%）钳制；每个样本的权重再乘链上各帧的 BL_VectorConfidence 与前后一致性（t → t−1 的位置用 t−1 帧的 Z,W 走回来，偏差 > 0.5 px 就不用）；protect 与按速度淡出同 hazefix。输出每帧一个多层 EXR：`BL_Final`（稳定版）、`BL_TemporalFrames`（每像素实际用了几帧）、`BL_TemporalWeight`（混合强度）。命令行：`blcli.py denoise --in DIR --frames 40-56 --out DIR2`。

**一条命令渲一个镜头（R5）**：
```python
bl.render_shot(source_camera="CamHeli", frames=range(40, 57), out_dir="//shots/heli", preset="c_series_style",
               passes="temporal", spp=256, follow=True, guard=True, exposure="pinhole")
```
搭镜头（或用场景里已有的）→ follow_camera（位置、旋转、对焦、f 值都烘焙成关键帧）→ `exposure="pinhole"` 用 Cycles 胶片曝光（写进 EXR 数据）补偿整幅平均曝光差（按第一帧算，整镜一个常数）→ 逐帧渲染（确定性种子、跳过已有帧、.partial、多机锁）→ 每帧开始前看系统可用内存，低于 `guard_min_gb`（默认 4 GB）就等（每 15 秒看一次，最多 `guard_wait_s`），等不到就停下并写明原因 → `<out_dir>/<name>_log.json` 每帧写一次（用时、各步用时、峰值常驻内存、可用内存、文件大小）。再跑同一条命令就是续渲。命令行：`blcli.py shot --blend film.blend --source CamHeli --frames 40-56 --preset c_series_style --passes temporal --spp 256 --gpu --out renders/shots/heli`。

**炫光的时间稳定性（R6）**：正向鬼影本来就稳；**衍射星芒在 0.8.3 及以前会闪**——按像素中心点采样 PSF、主斑（约 0.15 像素）在像素上硬挖，光源挪半个像素星芒能量就跳 ±25%，相机 0.02°/帧漂移时整个炫光层的能量能跳到 4.7 倍（星芒能量是鬼影的几十倍）。0.8.4 改成按像素面积积分（RESEARCH §5 第 17 条），实测（两盏小灯、C 风格、6 叶光圈，炫光层能量 / 亮度质心的二阶差分，平滑运动下应为 0）：

| 相机运动 | 鬼影 | 星芒 | 合计 | 质心 |
|---|---|---|---|---|
| 0.02°/帧（亚像素漂移，最容易看出闪） | 0.28% | 0.27% | 0.19% | 0.15 px |
| 0.2°/帧（中速横摇） | 1.09% | 0.60% | 0.87% | 1.0 px |
| 0.8.3 的星芒，0.02°/帧 | — | — | 396% | 157 px |

中速时的二阶差是光源扫过渐晕 / 画面的平滑变化，不是闪。端到端（`temporal_check(mode="moving", variants=[{"flare": True}])`）的 `flare_stability`：能量 0.48%、质心 0.13 px。**结论：0.8.4 起运动镜头可以直接用插件的炫光（flare=True）**；光晕的形状与强度不变；0.8.3 的星芒还在光源那一两个像素上重复叠了约 100 倍于直射像的能量（画面上被高光截掉，但在 BL_Flare 数据里），0.8.4 去掉了。注意鬼影绕画面中心反向运动，BL_Vector 对不上它们 → 开炫光会抬高运动补偿残差，这是运动不是闪，看 `flare_stability`；时间降噪只对 Combined、炫光原样加回。正向炫光的代价：每个光源每帧几秒（与分辨率无关；0.8.4 快了 5–6 倍）。

## 6.9 透过玻璃的矢量、雨层、多镜头、曝光曲线、对焦跟随、倒影矢量（0.8.5，MessyRoom Day 5 便利店雨夜的需求）

**矢量与深度穿过透射面（R1）**：橱窗、门、冷柜门后面的东西原来拿到的是玻璃的运动。现在自动识别「薄透射面」——材质是 Principled（Transmission ≥ 0.9、Roughness ≤ 0.1）或 Glass BSDF（Roughness ≤ 0.1），或物体自定义属性 `bl_pass_through = True`（设 False 可以关掉自动识别）。有这样的物体时，主渲染之后再渲一次只要数据层的辅助渲染（透射面隐藏，8 采样、0 次弹射，只取 Position / Depth / Cryptomatte），于是：
- `BL_Vector`、`Depth`：透射面像素取它后面第一个非透射面（其余像素与不穿透时逐像素相同）；
- `BL_VectorSurface`、`BL_DepthSurface`：第一个命中面（玻璃本身）的矢量与深度，给玻璃上的反射、雨滴用；
- `BL_VectorConfidence` 多一项 glass：1 − 反射占比（光泽层 / Combined，即 Fresnel × 反射来的亮度 / 总亮度；为此临时打开光泽层）。
实测（店内 + 橱窗、相机横移）：运动补偿残差 = Transparent + Glossy + pass_alpha_threshold 绕法的 0.99 倍；玻璃后的区域是关掉穿透时的 0.71 倍。不用改材质，折射玻璃照常渲。辅助渲染的代价约为一次 8 采样、无弹射的渲染。

**排除层（R2）**：`render_shot(..., exclude_collections=["FX_rain"])`（`render_still`、`render_frames`、`render_sequence`、`temporal_check` 同名参数）：这些集合不进主渲染，另渲一次只有它们的 `BL_Exclude`（RGBA 预乘，其余物体 holdout → 被柜台等挡住的雨自动去掉，胶片透明）。Combined、矢量、置信度都是不含雨的场景层；`BL_Final = (BL_Exclude over Combined) + BL_Flare + BL_Veil`。`exclude_mode="hide"`（默认：主渲染里完全隐藏 → 场景层与「不渲雨」逐像素相同）或 `"camera"`（只对相机不可见，保留雨的阴影 / 反射）。`temporal_denoise` 只平均场景层，最后把 BL_Exclude 原样 over 回去；`temporal_check(mode="moving", layer="scene" | "exclude" | "final")`，`layer="exclude"` 用来检查雨是否帧间确定。实测：BL_Exclude 与「只渲雨」逐像素相同；场景层与不渲雨逐像素相同；降噪结果 = 「不渲雨降噪 + 雨 over」逐像素相同。**逐像素比对要用无损压缩**（`exr_codec="PIZ"`）：`temporal` 预设默认 DWAB，有损（平均 1e-4，雨的锐边几个百分点）。

**一个 .blend 多支镜头（R3）**：
```python
bl.render_sequence([dict(source="S1_EXT", frames=(1, 72), focal_mm=21), dict(source="S2_AISLE", frames=(73, 156), focal_mm=32),
                    dict(source="S3_COUNTER", frames=(157, 230), focal_mm=40, rack=[(190, "Oden"), (202, "CRT")])],
                   out_dir="//day5", preset="c_series_style", passes="temporal", spp=128, exclude_collections=["FX_rain"])
bl.shots_from_markers()     # 时间线上绑定相机的标记 → 镜头清单（每个标记到下一个标记的前一帧）
```
同一个进程依次搭镜头 → 跟随 → 渲染；GPU 内核随镜头改写，下一次渲染自动重新编译（每支镜头第一次约 10 秒，之后有缓存）。每个镜头写到 `<out_dir>/<name>/`（各自的 `f_log.json`），汇总在 `<out_dir>/sequence_log.json`。镜头项里可以给 `preset`、`focal_mm`、`settings`（同 `bl.configure`）、`focus_target`、`rack`、`spp`、`exposure` 等任何 `render_shot` 参数。实测（GPU，21 / 40 mm 两个镜头）：与每个镜头单独开进程渲平均差 < 1e-6、最大 3e-4——GPU 渲染本身就不逐位确定（Metal 原子累加的顺序每次不同），同一帧渲两次也是这个量级；CPU 逐位相同。命令行：`blcli.py sequence --blend day5.blend --shots shots.json | --markers ...`。**0.8.4 的 render_shot 搭镜头时会把场景的渲染设置改掉**（设备改 CPU、弹射数、自适应采样、焦散——`render_preset`）；0.8.5 起 render_shot 搭完镜头会把这些换回场景原来的。

**曝光曲线（R4）**：`exposure="pinhole"`（整幅平均）/ `"pinhole_centre"`（画面中心，中灰卡）把镜头相对针孔的补偿**乘到原有的 film_exposure 上**：有关键帧（按测光打的曝光、切点阶跃）就整条曲线乘，渲完恢复原样。实测（均匀中灰墙、−1.0 → −1.7 EV 阶跃）：与 Cycles 针孔相机差 0.011 EV（整幅）/ 0.002 EV（中心）；切点阶跃正好 −0.70 EV。

**对焦跟随（R5）**：
```python
bl.focus_track(cam, "Door", frames=(1, 72))                         # 每帧按轴向距离烘焙（相机、目标在动都对）
bl.focus_track(cam, frames=(157, 230), rack=[(190, "Oden"), (202, "CRT")])   # 拉焦：到 190 帧对在关东煮，202 帧对在 CRT
```
目标可以是物体、空物体或 (x, y, z)；两个 rack 点之间按 1/距离 缓入缓出，每帧两头都重新求距离。`render_shot` / `render_sequence` 的 `focus_target`、`rack` 参数直接用它。实测：主体处弥散 0.0 px（4K）；拉焦两端与沿射线实测的轴向距离相同。

**倒影的运动矢量（R6）**：平面反射体（物体属性 `bl_reflector = True`，或自动：平面网格 + Principled 粗糙度 ≤ 0.4 或接了纹理，如噪声控制的湿地面）上，按视线的镜面反射在场景里 ray_cast 找到倒影里的点，上一帧 / 下一帧时让它随所属物体运动、对当时的平面取镜像（虚像点），再用当时的相机与对焦投影 → `BL_VectorReflection`；`BL_ReflectionShare` = 光泽层 / Combined。下游（`temporal_check` moving、`temporal_denoise`）在倒影为主（占比 > 0.5）的像素用倒影矢量，其余用 BL_Vector（`motion.effective_vectors`）；置信度对这些像素不再按镜面扣分。代价：每个反射体像素一次 ray_cast（约 4.6 µs，4K 下多于 150 万像素时隔点算）。实测（湿街、招牌和路灯的倒影、相机横移）：倒影区运动补偿残差 = 不补偿的 0.28 倍（只用地面矢量是 3.0 倍——比不补偿还差）。限制：只算一次镜面反射、只对平面；粗糙倒影的模糊不影响矢量（虚像点运动相同）。

## 6.10 广角：覆盖与畸变报告、build 警告（0.8.6，MessyRoom Day 5 S1 的广角需求）

**覆盖与畸变报告（W2）**：`bl.coverage(cam)`（`bl.lens_info(cam, coverage=True)`、`blcli.py info PRESET --focal-mm 24.6 --gate 16:9_NATIVE --coverage`）：
- `image_circle_mm` vs `gate_diagonal_mm`：像圈（沿对角线相对照度降到 2% 处）与光窗对角线；
- `relative_illumination`：边缘（横 / 竖）与四角的相对照度（中心 = 1），**与 Analytic 渲染同一个估计器**（出瞳平面采样盘、cos⁴ 加权、同一追迹判据），渲染实测差 ≤ 0.015；`covers` = 四角 ≥ 35% 且像圈 ≥ 对角线；
- `half_field_deg`：当前对角线半视场 vs 这支镜头（照度 ≥ 35%）能覆盖的最大半视场；
- `distortion_pct`：边缘中点与四角的径向畸变（横向按水平焦距、竖向按垂直焦距分开算；负 = 桶形）；
- `edge_vertical_bow_px`：物方一条竖直线（95% 画宽处）从画面中间到 95% 画高，在 4K 输出上水平弯了多少像素；
- `warnings`：一句话结论。
例（16:9_NATIVE、f/2、对焦 7 m）：C 风格 @24.6 像圈 27.5 mm < 对角线 28.8 mm（四角舷窗）、横向边缘畸变 −58%、竖线弯 42 px；C @32.8 四角 19%、弯 45 px；E / G @24.6 像圈只有 14 mm；E @46.9 竖向边缘 7%、四角 1%；Panavision 后置 1.34× @24.6 覆盖（四角 78%）、畸变 −2%、弯 9 px。

**build 警告（W3）**：`bl.build` 搭好后做一次快速检查（中心、边缘、四角），不覆盖就在控制台打印警告，结论存在相机属性 `bl_coverage_quick`，`bl.lens_info` 带上、`bl.diagnose()` 报 `coverage`。不再静默生成「舷窗」。

**广角预设（W1）**：`laowa_2x_29.json`——CN 112285889 B 实施例 1（安徽长庚光学 / Venus Optics Laowa，2020 申请、2024 授权），真实的现代 2× 一体式广角变形：负前组（最后一片负透镜移动对焦）→ Y 向柱面组 → X 向柱面组 → 含光阑的后组。表是图片，逐位对照页面转录，近轴 Fx 14.593 / Fy 29.027（专利 14.59 / 29.03）、后截距 38.856（38.85），近摄 1500 mm 时对焦片行程比专利多约 5%（与 Neil 预设同类）。专利只给 nd / Vd（两项 Cauchy）、不给口径：口径按 apply_apertures 估算后，再按真实边厚收回（玻璃 ≥ 0.3 mm、空气不相撞）。原生像面 24.0 × 17.6 mm（按专利半画角追主光线得到）。
在 16:9_NATIVE 光窗上（MessyRoom 的「取景」= 焦距 × 18.37 / 光窗高；这支镜头 16:9_NATIVE 光窗高 22.24 mm）：

| 取景 | focal_mm | 覆盖（f/2.9 四角） | 4K 竖线弯曲 | 边缘横向畸变 |
|---|---|---|---|---|
| 21 mm | 25.4 | 否（四角舷窗，像圈 30.7 / 对角线 31.1 mm） | 43 px | −7% |
| 25 mm | 30.3 | 是（39%） | 19 px | −6% |
| 25.5 mm | 30.9 | 是（40%） | 18 px | −6% |
| 28 mm | 33.9 | 是（45%） | 11 px | −5% |
| C 风格 21 mm | 24.6 | 否 | −101 px | −58% |

16:9 原生光窗配 2× 解挤压时传感器几乎是方的（宽 / 高 0.89），垂直视场很大——取景 21 mm 时垂直半视场 23.6°，超过这支镜头的设计（16.7°，按 S35 3:2 设计）。所以 21 mm 还差一点（四角有很小的黑角）；25 mm 起覆盖。只要 2.39 之类的宽画幅，同样取景下余量大得多。

**不牺牲质量的提速（0.8.6）**——每一项都核对过与原来的结果相同或只差浮点舍入：
- **出瞳采样盒更贴**：出瞳表在每条边附近按 1/8 网格单元重新采样，余量从「1.5 个单元 + 1%」降到「1.5 / 8 个单元 + 0.2%」。采样仍然无偏（`test_pupil` 确认所有能穿过镜头的光线都在盒内），有效采样比例平均提高约 10%（C 0.64 → 0.70、Laowa 0.56 → 0.62、Neil 0.60 → 0.67），同样噪点约少用 10% 的采样。只改了表的数据，OSL / GPU 采样代码不变。出瞳表升到 v6（随包的已重建）。另：采样盘按镜头自动放大（见 RESEARCH §5 第 20 条）修正了 Panavision 长焦端 f/8 时边缘被误挡的光（边缘照度 0.71 → 0.79）。
- **运动矢量**：投影的牛顿迭代四张表一起插值、已收敛的点不再迭代（步长 < 1e-7 mm），2.2 倍快、结果相同（差 1e-14 mm）。
- **补洞**：只在缺的像素上按下标取邻居（原来整幅平移 8 次），4K 3.9 → 0.8 秒；邻居规则不变（顺带修了原来 np.roll 在画面边缘绕到对边取邻居的问题）。
- **倒影矢量**：光线多于 2 万条时用 BVH 树（同一份求值后的几何），每条约 1 µs（scene.ray_cast 约 4.6 µs），并取消了 150 万像素以上隔点算的近似。
- **正向炫光**：平面 / 球面 / 纯柱面改为解析求交（与渲染同一个二次式），1.07–1.22 倍快；与 60 次牛顿迭代的参考比，误差从 1.2% 降到 0.8%（Panavision；其余预设差 < 1e-12）。
- **倒影矢量只追需要的像素**：只对倒影占比（光泽层 / Combined）> 0.3 的反射体像素追反射光线（下游只在 > 0.5 处用，所以结果不变）；自动识别排除发光的物体（招牌、灯箱、发光卡片）。MessyRoom Day 5 实测：自动识别认进了所有平面卡片和招牌，矢量一步 125 秒 / 帧（4K），手动只指定 15 块湿地面后正常。
- **测过、不值得做的**：GPU 内核里的镜头追迹只占 1080p 64 spp 一帧约 0.1–0.2 秒（场景追迹约 2 秒）；EXR 写盘已经是多线程。
- 4K 实测（简单场景、GPU、`passes="temporal"`）：后期 17 → 12 秒，整帧 27.8 → 22 秒。

**E / G 缩到广角时为什么是舷窗**：不是渲染的问题。Isco 前组的 I1–I2 等镜片在估算的口径内两面相交（边缘厚度为负）：离轴光线要在镜片里「倒着走」才能通过。Analytic 渲染（OSL / GPU）正确地挡掉了这些光线；`optics.pupil_transmission` 与 Geometry 模式会放过它们（Geometry 网格在那里自相交），所以覆盖报告用的是与 Analytic 渲染相同的估计器。

## 6.11 排除层的 alpha 修正、排除层提速（0.8.7，MessyRoom Day 5 的反馈）

**问题（0.8.5 / 0.8.6 的 bug）**：`BL_Exclude` 的 alpha 直接用 Cycles 的 alpha，而 Cycles 的 alpha = 1 − Σ(相机光线权重 × 透过) / N。真实镜头的相机权重里带着渐晕（cos⁴、出瞳采样「过 / 不过」），所以**没有雨的地方 alpha 也不是 0**：中心约 0.06–0.12（随采样数变：8 spp 0.12、32 spp 0.08、128 spp 0.057——透明度 > 1 时被截断，偏差 ∝ 1/√spp），四角 0.4–0.7。`BL_Final = 雨 over 场景` 于是把整幅画面压暗（测试场景中心 −5%、四角 −11%；MessyRoom 的 S1 4K 128 spp Laowa：中心 0.924、四角 0.55–0.65）。

**修法**：排除层多渲一张「什么都不显示」的空画面（同一种子、同样采样数、关自适应 → 相机采样逐个相同，只有世界背景，很快），雨的透过率 τ = 雨层透明度 / 空画面透明度——镜头权重逐个约掉。两张都把相机光线权重乘 1/4（OSL 参数 `weight_scale`；GPU 经 `fisheye_polynomial_k0` 传入，见 gpukernel.FIELD_MAP），透明度不会到 1、不会被截断；样本截断（`sample_clamp_*`）随之乘 1/4、关俄罗斯轮盘（轮盘按路径通量决定终止，通量缩小会多杀路径：实测 RMSE +18%），颜色除回去后与不缩放、不用轮盘的渲染逐像素相同（RMSE 0.0623 vs 原来的 0.0624）。
- 结果：没有雨的像素 alpha = 0（CPU 恰好 0，GPU < 1e-6），BL_Final = Combined + 炫光；alpha 不再随采样数变（32 spp 0.0260 / 8 spp 0.0263）。
- 颜色：降噪器（OIDN）对输入亮度不是严格线性，排除层 ×1/4 渲、降噪后再除回去，与不缩放的平均差 0.4%（个别像素几个百分点）；不降噪时相同。
- 文件头 `blenderlens/exclude` 多了 `alpha_mean`、`raw_alpha_mean`（Cycles 原本的）、`empty_alpha_mean`、`weight_scale`。

**0.8.6 及以前已经渲好的帧**：雨层颜色（`BL_Exclude` 的 RGB，预乘）是对的，只有 alpha 有偏。不重渲的合成：`final = Combined + BL_Exclude.rgb + BL_Flare + BL_Veil`（加法，忽略雨挡住的那一点背景——雨的真实 alpha 很小，测试场景平均 0.026）。

**排除层提速**：
- 只算 Combined：排除层渲染时临时关掉其余渲染层（结果不变）。
- 可选（会改变结果，默认不开）：`exclude_spp=`（排除层单独的采样数）、`exclude_bounces=`（漫射 / 光泽 / 透射 / 体积弹射上限；透明弹射不限）。`render_still` / `render_frames` / `render_shot` / `render_sequence` 与 `blcli.py shot|sequence --exclude-spp N --exclude-bounces N` 都可用。注意：折射的雨不建议限弹射（雨丝要穿过多层，限了会变「实」、观感变）；`exclude_spp` 降低后雨的噪点变多，但时间降噪不会平均雨，所以只在雨本身干净时用。测试场景（180 p）：排除层 1.1 → 0.49 秒（8 spp）。
- `LAST_TIMING["exclude_render"]` 记录排除层用时（含空画面）。

## 6.12 全画幅版本、补偿凸轮缩放修正、空画面提速（0.8.8）

（0.9.1 起所有画幅版本改为用满整个片门、k 也随之变了，见 §6.14；下表是 0.8.8 时的数字。）

**全画幅版本**：现有预设都按 Super 35 级光窗设计（约 22 × 18.6 mm）。`bl.build(..., format="FF")`（面板：Build Lens (Full Frame)；命令行 `--format FF`）= 同一个设计整体做大一号：镜片、间隔、口径、光阑、对焦行程、补偿凸轮、最近对焦、光窗全部 × k，k = 把原光窗放进 36 × 24 mm 的最大倍数。光窗上每一点的物方角度、渐晕、畸变、焦外形状、炫光与原设计逐点相同（`tests/test_format.py`：相对照度逐点差 < 1e-6），不同的只有焦距 × k、同 f 值景深更浅（全画幅的本意）。厂商的全画幅变形镜头一般是重新设计；这里是等比放大，是「同一支镜头的全画幅版」最直接、可验证的做法。

| 预设 | k | 全画幅焦距（垂直） | 全画幅光窗（压缩前） | 最近对焦 |
|---|---|---|---|---|
| C 系列风格 | 1.290 | 64.5 mm | 28.39 × 24.00 | 0.98 m |
| E 系列风格 | 1.290 | 169.5 mm | 28.39 × 24.00 | 1.47 m |
| G 系列风格 | 1.290 | 129.4 mm | 28.39 × 24.00 | 1.18 m |
| Neil 2× 50 / 65 / 85 | 1.347 | 67.3 / 87.6 / 114.5 mm | 28.69 × 24.00 | 1.45 m |
| Laowa 2× 广角 | 1.364 | 39.6 mm | 32.73 × 24.00 | 2.29 m |
| Panavision 后置 1.34× 短 / 长焦端 | 1.518 | 31.3 / 100.8 mm | 36.00 × 20.25 | 0.72 m |

- 2× 镜头的全画幅光窗按高 24 mm（宽 36 mm 解挤压后是 3:1，没有 2× 镜头为它设计）；Panavision 1.34× 按宽 36 mm。
- `16:9_NATIVE`（原生满像圈）在全画幅版本上收进 36 × 24（原光窗算出来可能高过 24 mm，全画幅机身没有那么高的传感器），例：Laowa / C / Neil 21.3 × 24、Panavision 31.6 × 24，四角照度 41–65%，都覆盖。
- `focal_mm` 在全画幅版本上再缩放（光窗保持全画幅），`bl.coverage` / build 警告照常。
- 出瞳表随包多了全画幅的 36 份（共 72 份，288 KB），第一次搭全画幅镜头不用等。

**补偿凸轮随缩放（修正）**：`rescale_to_vertical_efl`（`focal_mm`）以前没有缩放发腮补偿的凸轮表（对焦行程 Δ1 是长度、补偿板光焦度 φ 是 1/长度），缩放后的 C / E / G 近摄压缩比不对：C 缩到一半焦距时最近对焦 1.80 而不是 2.01、放大两倍时 2.30。现在 Δ1 × k、φ ÷ k，任何焦距下最近对焦压缩比都与原设计相同（C 2.011、E / G 2.094）。

**排除层的空画面**：改在一个只有相机（共用同一份相机数据）、不带世界的临时场景里渲——不用同步 / 隐藏场景物体，也不用为 HDRI / 天空纹理建重要性采样图；与 0.8.7 的「全部隐藏」逐位相同（CPU 差 0，GPU 差 < 3e-4，GPU 本身不逐位确定）。实测（GPU、3,100 个物体 + 雨、1080p 128 spp）：空画面 1.8 → 1.45 秒。空画面的主要开销是相机采样本身（每个像素每个采样都要过一遍镜头），与像素数 × 采样数成正比、约为主渲染的 12%；俄罗斯轮盘关掉约多 5–10% 的雨层时间（密雨）。要再省只能降 `exclude_spp`（雨层和空画面一起降）。`LAST_TIMING["exclude_empty"]` 单独记录空画面用时。

**面板**：Build Lens 以前不做 0.8.6 的覆盖检查（只有 `bl.build` 做），现在一样会警告。

## 6.13 原生全画幅广角：Sirui 1.6× 35 mm（0.9.0）

**新预设 `sirui_ff_35.json`**：[CN 118169848 A](https://patents.google.com/patent/CN118169848A/zh)（广东思锐光学 / 中山亚众光电，2022-12-08 申请；同日实用新型 CN 218886282 U 是同一设计），专利原文「全画幅超广角大倍率变形镜头」：Y 向焦距 35 mm、变倍比 1.6×、全画幅出 2.4:1（= 36 × 1.6 / 24）。规格与市售 Sirui Saturn 35 mm T2.9 1.6× 全画幅逐项吻合（18 片 13 组、58 mm 滤镜、415 g / 专利 < 420 g、长 102 / < 103 mm）。这是第一个**按全画幅设计**的预设（0.8.8 的全画幅版本是 S35 设计等比放大）。

- **结构**：第一球面组（第 1–3 片，第 2–3 片内对焦）→ 第一柱面组（第 4–6 片，X 向负光焦度，水平压缩）→ 第二球面组（第 7–15 片，含三胶合与双胶合，光阑在第 10、11 片之间）→ 第二柱面组（第 16–17 片，Y 向）→ 第 18 片两面偶次非球面。
- **转录核对**（表 1 是图片，按 4 倍放大逐位转录；专利没给后截距）：fY 34.959 mm（专利 35，−0.12%）；X、Y 两向的近轴像面相差 0.03 mm（后截距 13.266 / 13.237）——没给后截距反而是很强的核对：只有每个半径、间隔都对时两向才会合焦在同一平面。权利要求里的 6 个焦距关系全部满足：fY 34.96 ∈ [33, 37]、fX 21.21 ∈ [20.6, 23.2]、fY/fX 1.648 ∈ [1.55, 1.65]、f(2–3)X / f(1–10)Y 1.194 ∈ [1.0, 1.3]、f(1–3)X / f(4–10)Y −2.616 ∈ [−3.0, −2.2]、f(1–10)X / f(11–18)Y 0.513 ∈ [0.50, 0.55]。
- **没给的**：口径（按 apply_apertures 估算，角落放行 0.6，不需要收回；估出的前片直径 48.5 mm < 58 mm 滤镜）、光阑孔径（按市售 T2.9 取 f/2.9）、玻璃牌号（nd 三位 / vd 一位小数 → 两项 Cauchy）。
- **发腮**：没有——近轴压缩比 ∞ / 2 m / 0.9 m 都是 1.648（专利称「各物距变倍比恒定」）。
- **最近对焦**：专利 0.7 m（物像距），但近轴求解在 0.7 m 要 4.60 mm 行程，D6 只有 4.554 mm（差 1%，与 Neil / Laowa 同类）→ 取市售公布的 0.9 m。
- **解挤压**：按市售标称 1.6。近轴 fY / fX = 1.648，物方 tan 比随视场增大（画宽 25% 1.66 → 边缘 1.78）：水平桶形约 −3%、垂直枕形约 +5%，4K 高度下画面边缘竖线弯 82 px（画面宽 5184 px）——设计本身的性质（专利称「畸变量很小」是按摄影镜头的标准）。
- **畸变复核**（用户对照实拍样张提出疑问后，2026-10-20）：① 两种独立方法——主光线反解（coverage.py）82 px、物方竖线上逐点正向整束追迹取光斑重心（flare.Lens）74 px（追到 y = 10.6 mm）——都约 3%；同法 Laowa 16 / 18 px。② 非球面按专利原样是唯一成立的解释：系数反号或去掉后光斑 300–800 µm、畸变 −10% ~ −12%；按原样在各视场最佳焦面上光斑 4–15 µm。③ 宽方向畸变与专利图 4 同号同量级（40.4° 处 −3.1% vs 图上约 −2.5%）。④ 实拍用户：EOSHD 论坛评这支 35 mm「Distortion is quite heavy - and pincushion」，可在 Resolve 校正——与这里竖线向内弯（枕形外观）一致。保留：模型的场曲与专利图 4 对不上——细光束（Coddington）场曲在 20–30° 处子午 −0.29 ~ −0.44 mm、弧矢 −0.13 ~ −0.23 mm（相对轴上，离开镜头为正），专利两条曲线都在 ±0.1 mm 内；40.4° 畸变 −3.1% vs 专利 −2.39%。逐项排除后判断**不是我们的数据或方法**，专利图 3 / 4 很可能不是按表 1 原样算的（排除过程见 RESEARCH §6.19「场曲差异核查」）。影响：画面边缘在平的传感器上比专利宣称的软（全开时离轴光斑 20–37 µm，最佳焦面上 4–15 µm）；畸变可能多算约 0.7 个百分点。竖线弯曲只在画面最左右的直线上明显：按 1300 px 宽显示约 19 px。
- **全画幅覆盖**（`bl.coverage`）：

| 光窗 | f 值 | 四角照度 | 边缘（横 / 竖） | 像圈 | covers |
|---|---|---|---|---|---|
| FULL 36 × 24（2.4:1） | f/2.9 | 26% | 37% / 65% | 51.8 mm（对角线 43.3） | 否（暗角，没有黑角） |
| FULL 36 × 24 | f/4 | 37% | 49% / 74% | | 是 |
| FULL 36 × 24 | f/5.6 | 42% | | | 是 |
| 16:9_NATIVE（收进 36 × 24：26.67 × 24） | f/2.9 | 39% | 57% / 65% | | 是 |

  全开时四角 26% 主要是设计本身：即使口径按「角落放行全部光瞳」估（没有机械渐晕），四角也只有 31%（对角线半视场 42.5°）。

- **对照**（同样垂直取景 35 mm、光窗高 24 mm、f/4，`renders/ff_compare/ff_compare.png`）：Laowa 全画幅版本（16:9_NATIVE 21.3 × 24）四角 55%、边缘横向畸变 −5.3%、竖线弯 14 px；Panavision 1.34× 全画幅版本（31.6 × 24）四角 76%、−2.1%、−9 px；Sirui 1.6×（36 × 24，2.4:1）四角 37%（f/4）、−3.4%、82 px。

**覆盖报告的画幅换算**（MessyRoom 的建议）：`bl.coverage` / `lens_info(coverage=True)` / build 的快速检查都带 `format`：`gate_diagonal_mm`、`crop_factor_vs_36x24`（全画幅对角线 / 光窗对角线）、`dof_vs_36x24_same_framing_and_stop`（同样取景、同样 f 值时景深相对全画幅的倍数）；全画幅版本另给 `vs_s35_design`（与原 S35 设计同一光窗方式的对角线比、景深系数、镜头放大倍数）。

**2× 镜头 + 16:9_NATIVE + 全画幅版本 ≈ S35**：2× 解挤压出 16:9 时压缩前光窗的宽高比是 0.89，全画幅上受 24 mm 高度限制（21.3 × 24），对角线只比 S35 的 16:9_NATIVE（19.8 × 22.2）大 8%：同取景时景深只浅约 7%，画面基本是 S35 像圈的中心部分（四角更亮）。要「全画幅的观感」（景深明显更浅），用低压缩比铺满 36 mm 宽：Panavision 1.34× 全画幅版本 FULL（36 × 20.25，对角线是 S35 的 1.52 倍、景深 × 0.66），或原生全画幅的 Sirui 1.6×（36 × 24 → 2.4:1）。

**全画幅版本「慢 1.9 倍」查过了**：同样取景（Laowa、16:9_NATIVE、GPU 1080p 128 spp），全画幅版本 3.9 秒 vs S35 4.5 秒（自适应采样开：4.7 vs 5.3），出瞳采样效率相同（0.727 / 0.729）。换镜头后第一帧会重编译 GPU 内核、建新焦距的出瞳表（一次性）；比较速度要看第二帧之后。

**MessyRoom 报的三个全画幅问题（0.8.8，0.9.0 修）**：
1. E / G 全画幅版本缩到短焦（如取景 33 mm → 43 mm）搭镜头时报 `cannot unpack non-iterable NoneType`：Isco 附加镜是长焦设计，缩到原设计的 0.3 倍再按全画幅视场重估口径，估出的口径超过曲面半径（比半球还大，曲面在那里没定义）→ 网格生成失败。现在口径估算会把口径收到曲面有定义的范围内（`optics._cap_to_surface_extent`，98% 的半径），多出来的是机械渐晕，覆盖报告照实显示「不覆盖」——这支镜头本来就缩不到那么短。
2. PV 长焦端全画幅的 `half_field_deg.current_diag` 显示 4.7°（S35 29.8°）：角落超出镜头能成像的范围时主光线追迹给出乱的角度（同一支镜头 S35 51.8°、全画幅 116.8°）。现在沿对角线逐点追，只用连续单调的点，角落不可信时按最后可信点的「真实 / 近轴」比外推并标 `current_diag_estimated: true`：两种画幅都是 29.0°。
3. （说明，不是 bug）同样取景时，`max_covered_diag` 对 Neil / PV 两种画幅相同，对 C、Laowa 不同（C 37.3° → 40.2°）：`focal_mm` 缩放时，估算口径的预设会按该版本的**整个原光窗**重估口径；全画幅版本的原光窗（28.4 × 24）相对 16:9_NATIVE 光窗（21.3 × 24）比 S35 时宽，估出的口径稍大、能覆盖的视场稍宽。专利给了口径的预设（Neil、PV）不重估，所以相同。全画幅版本不会让一支缩过头的镜头「变得能覆盖」——覆盖是角度问题，看 `bl.coverage`。

## 6.14 画幅用满片门、65 mm 5 齿孔、同焦段重新设计的 C 风格全画幅 / 65 mm 版（0.9.1）

**画幅版本改成用满整个片门**（用户：「解挤压后的画幅不应该是固定的…每个镜头挤压倍数不一样」「所有的画幅也应该是这个逻辑」）。0.8.8 的全画幅版本把光窗定成高 24 mm 的 2.39（2× 镜头），0.9.1 起所有画幅版本都用满整个传感器 / 片门，解挤压后的比例 = 片门宽高比 × 压缩比（2× 镜头在 36 × 24 上是 3:1，1.3× 在 65 mm 上是 2.97:1）。镜头放大倍数 k = 片门半对角线 / 设计像圈半径（`sensor.format_circle_mm` / 2 优先，Laowa 用专利像圈 27.52 mm）；`full_aperture` 记原设计光窗 × k；16:9_NATIVE 收进片门。

| 预设 | 原光窗 | FF k / 焦距 / 解挤压后 | IMAX 15/70 | 65 mm 5 齿孔 |
|---|---|---|---|---|
| C / E / G 系列风格 | 22.0 × 18.6 | 1.502 / 75.1 · 197.3 · 150.6 mm / 3.00 · 3.13:1 | 3.051 / 152.6 · 400.9 · 305.9 mm | 1.989 / 99.5 · 261.4 · 199.4 mm |
| Panavision 后置 1.34× 短 / 长焦端 | 23.7 × 13.3 | 1.590 / 32.8 · 105.6 mm / 2.02:1 | 3.231 / 66.7 · 214.5 mm / 1.81:1 | 2.106 / 43.4 · 139.8 mm / 3.08:1 |
| Neil 2× 50 / 65 / 85 | 21.3 × 17.8 | 1.558 / 77.9 · 101.3 · 132.4 mm / 3.09:1 | 3.165 / 158.3 · 205.8 · 269.1 mm / 2.76:1 | 2.063 / 103.2 · 134.2 · 175.4 mm / 4.70:1 |
| Laowa 2× 29 | 24.0 × 17.6 | 1.572 / 45.6 mm / 3.00:1 | 3.194 / 92.7 mm / 2.68:1 | 2.082 / 60.4 mm / 4.56:1 |
| Sirui 1.6× 全画幅 35 | 36 × 24 | 1（原生）/ 35.0 mm / 2.40:1 | 2.032 / 71.0 mm / 2.14:1 | 1.324 / 46.3 mm / 3.65:1 |

- **新画幅 `format="65MM"`**：65 mm 5 齿孔摄影片门 52.48 × 23.01 mm（Panavision System 65 / Ultra Panavision 70 的拍摄片门）。面板 Build Lens (65 mm 5-perf)、命令行 `--format 65MM`。2× 镜头放上去解挤压后 4.6:1——真实世界的 65 mm 变形镜头都是低压缩比（Ultra Panavision 70 1.25×，《八恶人》2.76:1 是放映片门；Hawk65 1.3×），所以下面另外设计了一支 1.3× 的。
- **IMAX 15/70**：真实世界里没有 IMAX 变形镜头（IMAX 本身就是 1.43:1 大画幅，只配球面镜头）。`format="IMAX"` 保留为「等比放大」版本；没有为它另做重新设计（1.3× 50 mm 在 70.4 mm 宽上水平视场约 85°，双高斯 + 柱面附加镜的结构撑不住，试算四角照度为 0）。
- 出瞳表随包只带 S35 / FF；IMAX 与 65 mm 版本第一次搭镜头时现算（约 10 秒，之后有磁盘缓存）。

**同焦段重新设计：`c_series_style_ff.json`（C 风格 50 mm 全画幅，1.65×）与 `c_series_style_65.json`（C 风格 50 mm 65 mm 5 齿孔，1.3×）**——都不是真实镜头，是「设想厂商自己出这支镜头的全画幅 / 65 mm 版会是什么样」（用户：「你觉得真实生活中的镜头会是什么样子，你就做什么样的镜头」）。
- **为什么不保持 2×**：C 2× 在全画幅 36 × 24 上、50 mm 时水平视场约 102°，原样放上去边缘 −42% 桶形、四角照度 0.23。真实厂商给大画幅降压缩比：Panavision Ultra Vista 1.65×、Cooke FF 1.8×、Hawk65 1.3×、Ultra Panavision 70 1.25×。
- **做法**（`tools/reoptimize.py`，配置 `c_ff2` / `c_65`）：
  1. 焦距 50 mm 不变，主镜（50 mm f/2 双高斯，本来就罩得住全画幅）不动作起点；
  2. 前置变形器按解析关系换压缩比：负组（A1–A5）曲率按比例缩放使 |f_负| = f_正 / 压缩比，再一维求解负 / 正组间隔使 X 向无焦；
  3. 全部 19 个曲率 + 全部间隔一起优化，玻璃与片数不变；
  4. 风格目标 = 原版在它**自己的 S35 光窗**上同一相对位置的：X / Y 复色 RMS 光斑 / 画高（中心 → 四角的软硬比例，双向匹配：不比原版锐也不比原版糊）、横 / 竖畸变 %（容差 1.5 个百分点：真实的全画幅版本畸变也不会完全相同）、f/4 相对照度；X / Y 近轴像面重合；后截距 ≥ 原版的 90%（卡口空间）；玻璃边厚 ≥ 0.8、空气 ≥ 0.3 mm；
  5. 角落与水平边缘加权（真实厂商的取舍：先保住原版的软硬比例，畸变让一点）；65 mm 版视场更宽，评价点加密到水平 / 竖直 0.7、0.85；
  6. 做成预设：口径按新视场估、角落放行比例选成四角照度最接近原版；补偿板沿用原版 5 mm 厚、口径按新视场；发腮补偿凸轮表按新设计重算。参数存 `tools/reopt_c_series_style_ff.json` / `reopt_c_series_style_65.json`，`build_presets.py` 生成预设。
- **结果（全画幅版，全开 f/2.3；‰ = 光斑或色差 / 画高）**：

| 相对位置 | 复色 RMS：原版 S35 → 全画幅版 | 单色（d）X / Y：原版 → 全画幅版 | 横向色差 F−C X / Y：原版 → 全画幅版 |
|---|---|---|---|
| 中心 | 1.22 → 1.18‰ | 0.97 / 0.65 → 0.78 / 0.64‰ | 0 → 0 |
| 横向 0.5 / 0.7 / 边缘 | 2.16 / – / 3.04 → 1.98 / – / 3.11‰ | 1.67 / 1.73 / 2.30 → 1.45 / 1.69 / 2.29（X） | −0.03 / 0.04 / 0.30 → −0.05 / 0.01 / 0.24（X） |
| 竖向 0.5 / 0.7 / 边缘 | 2.36 / – / 3.97 → 2.29 / – / 4.45‰ | 1.62 / 1.81 / 2.85 → 1.41 / 1.84 / 2.54（Y） | −0.07 / −0.09 / −0.15 → −0.09 / −0.12 / −0.17（Y） |
| 对角 0.5 / 0.7 | 2.55 / 3.45 → 2.72 / 3.41‰ | 1.86·1.67 / 2.40·2.41 → 2.03·1.70 / 2.46·2.24 | 相近 |
| 四角 | 5.30 → 5.47‰ | 3.44 / 4.01 → 3.40 / 4.12 | 0.25 / −0.20 → 0.33 / −0.16 |

  （原版 2× 直接放到 36 × 24 上：四角 6.95‰、横向边缘 4.16‰。）压缩比 1.649（标称 1.65，解挤压后 2.48:1），最近对焦 0.76 m 完全补偿时也是 1.649；畸变（`bl.coverage`）横向边缘 −11.9%（原 −11.5%）、角落 −13.1 / −12.2%（原 −12.9 / −8.9%），4K 竖线弯 −25.5 px（原 −27.3）；f/4 相对照度边缘 0.85、四角 0.80（原 0.93 / 0.88）；对角线半视场 36.3°（原 28.7°）。
- **结果（65 mm 版，同样口径）**：复色光斑 / 画高 中心 1.51‰（原 1.22）、横向 0.5 / 0.7 / 0.85 / 边缘 3.00 / 3.10 / 2.72 / 3.15‰（原 2.16 / 2.36 / 2.66 / 3.04）、竖向 0.5 / 边缘 2.14 / 3.71‰（原 2.36 / 3.97）、对角 0.7 3.47‰（原 3.45）、四角 5.40‰（原 5.30）；横向色差各点与原版相差 ≤ 0.1‰。弱点：水平 0.5–0.7 一带竖向的单色光斑比原版软约 50%（竖直方向的视场反而比原版锐）——这个结构（玻璃与片数不变）在 41° 对角半视场下的极限，真实厂商会加一片镜片。压缩比 1.295（标称 1.3，解挤压后 2.97:1）；畸变横向边缘 −17.6%、角落 −19.0 / −14.8%（原 −11.5 / −12.9 / −8.9%，按百分比更大，但 4K 竖线弯 −29 px 与原版 −27 px 相当——横向视场宽、同样的弯曲占的百分比就大）；f/4 相对照度边缘 0.75、四角 0.73（原 0.93 / 0.88）；最近对焦 0.76 m 时水平 0.7 处约比原版软一倍（`tests/test_presets.py` 记录了这一点）。
- 两支都按「单色清晰度」与「横向色差」分开对齐原版：只对齐复色光斑时，优化器把色边换成单色模糊（第一版全画幅横向边缘色差 65 µm，原版 7 µm——软的总量对了，软的样子变了）。
- 优化中修掉的坑：原版畸变目标要按**原版自己的压缩比**算（用目标压缩比算会把原版的 X 向焦距算错，把畸变当成 −20%）；补偿板在 ∞ 时是平行平板、厚度不影响成像，优化器会让它漂走——做预设时恢复原厚度；`add_compensator` 按 A1 口径 + 1 mm 估板的口径，全画幅视场下会挡角落——沿用按新视场估的口径；换光束后代价可能回升，优化器另存代价最低的一轮（`*_best.json`）。
- 之前的试点（Neil 2× 50 只优化后组、固定 2.39 光窗）已撤下：用户指出光窗不应固定，且改用 C 做「厂商会怎么做」的完整重新设计。

## 6.15 排除层（雨）的亮点控制（0.9.1，MessyRoom Day 5 便利店雨夜的反馈）

**现象**（MessyRoom 在 0.9.0 成片上量的）：雨丝（细圆柱、透射 1、IOR 1.33）经过强光源（车灯强度 40、亮窗）前面时，把光源按原强度折射过来；每滴每帧移动约 37 cm，成片里是「一帧一闪的白亮点」。BL_Exclude 亮度中位数 0.02、p99 0.16、最大 4.39；场景层每帧 0–20 个时间尖峰，雨层 200–860 个。

**新参数**（`bl.render_still` / `render_frames` / `render_shot` / `render_sequence`，命令行 `--exclude-clamp 直接,间接`、`--exclude-knee 起点,上限`）：
- `exclude_clamp=(直接, 间接)`：只对排除层那次渲染设 Cycles 的样本截断（0 = 不截断），主渲染不变。治本（折射路径在采样时就被截断），但截断是按样本的，会让被截的雨丝整体偏暗。
- `exclude_knee=(起点, 上限)`：渲完后按亮度对排除层（预乘 RGB）软压缩：起点以下逐像素不变，以上平滑趋向上限（一阶连续，色相不变）；BL_Final 用压缩后的排除层合成。与 MessyRoom 的 `rain_tame.py` 同一思路，但在渲染时做、写进 EXR 头。雨在时间上不连贯，所以是逐像素压缩、不是时间滤波。建议从 `exclude_knee=(0.05, 0.10)` 开始（MessyRoom 的取值）。
- 测试（`tests/bl_exclude.py`）：knee 后排除层亮度 ≤ 上限、起点以下不变、场景层不变、BL_Final = 雨 over 场景；clamp 压低峰值、雨的覆盖（alpha）不变；错误参数在渲染前报 ValueError。

**场景层的亮点**（湿路面凹凸 + 车灯反射，Noisy Image 最大 120）：不是镜头的问题，用 Cycles 自己的设置——`scene.cycles.blur_glossy`（Filter Glossy，0.5–1：把粗糙度很低的光泽反弹在间接光里放宽，专门压这类焦散亮点）、`sample_clamp_indirect` 降到 2–3（代价是间接高光略暗）、光源用 Light Tree（5.x 默认开）；Principled 的凹凸很细时把路面粗糙度从近 0 提到 0.05–0.1 也很有效。BlenderLens 的相机不改变这些路径。

## 7. 输出与解挤压

输出宽度 = 高 × 传感器宽高比 × 解挤压比。渲染结果是**已解挤压、方形像素**的正常画面，不需要后期再拉伸。压缩比随视场变化（水平畸变），例如 C 从中心 2.01 到边缘 2.27，属于镜头特征。

## 8. 改处方或加新镜头

- 处方在扩展目录的 `prescriptions/*.json`，往里放新的 JSON 就会出现在 Build Lens 菜单。字段约定见任一 JSON 的 `meta.conventions`：原点为像面，物方 +Z，R > 0 表示曲率中心在像方，柱面用 `R_x` 或 `R_y` 为 null 表示。
- 口径（`aperture`）可以由插件估算（Recompute Apertures），也可以写专利给定值（组上设 `"aperture_source": "patent"`）。
- 在源码仓库里，E/G/Panavision 三份 JSON 由 `tools/build_presets.py` 从专利转录数据生成，改它们要改生成脚本，不要直接改 JSON。

## 9. 已验证的精度（便于判断结果是否可信）

- 四份专利逐位对照扫描原件；Panavision 焦距 x/y 15.817 / 20.627（专利 15.81 / 20.60），整幅物方宽高比 2.401（专利 2.40）；Isco 用专利自带主镜复现其对焦表 11 行（凸轮量 ≤ 0.074 mm，压缩比 ≤ 0.03%）。
- 渲染映射（3 m 刻度靶）：Analytic 模式 5 个预设与 Python 真实主光线追迹相差 ≤ 0.8 px（成片用这个模式；画面边缘的残差是离轴彗差光斑质心与主光线之差，0.3 与 0.4 相同）；Geometry 模式（真实网格交叉校验）≤ 1.2 px。0.8 补上了 Geometry 模式一直缺的光圈网格（之前只靠 1.4 倍出瞳采样盘限光），并修了两处法线精度；单片球面镜 Geometry 与 Analytic 一致，Panavision 长焦端仍有全画面一致的约 (+0.27, −0.40) px（270p）平移，原因未查明（RESEARCH.md §5 第 14 条）。
- 外部数据（0.8，`tests/test_reference.py`）：最近对焦距离 = Panavision 公布值；近摄压缩比（补偿后 < 1.5%，ARRI Master Anamorphic 1.97–2.01×）；焦外形状（前置竖椭圆 > 1.6，后置 0.85–1.08）；C/E/G 呼吸以垂直为主。
- 杂光（0.8）：合成 ISO 9358 测试测出的 VGI 与设定值相差 < 1%。
- 焦外高宽比、猫眼渐晕、发腮压缩比的渲染实测，都与真实追迹预测吻合到约 1%（猫眼约 3%）。
- 炫光：正向通道与相机端鬼影（8192 spp、关闭自适应采样）交叉验证，颜色占比差 ≤ 0.03。

## 10. 已知限制

- Analytic 模式直射光路不计菲涅耳透射损失；Geometry 模式含镀膜损失，但宽带膜在 Geometry 模式里按单层近似（Cycles 薄膜只有一层）。
- 发腮补偿是一阶等效（一块马鞍面补偿板），不含两片分开转动的柱面之间的高阶交叉项；补偿关闭时它是一块平行平板（多两个镀膜反射面）。
- 曝光基准：开 Compensate Exposure 时，轴上、只受光阑限光的像点 = 场景亮度（相当于按 T 值对齐，各预设可以直接比较）。最大光圈下其他镜片的切光照常保留，例如 C 系列在 f/2.0 时轴上暗 16%。0.4 起 Panavision 两个预设比 0.3 亮 0.39 档（修正了椭圆出瞳的估计）。
- 每个 .blend 只能有一支 BlenderLens 镜头。
- Cycles 的 OSL 相机只有两维随机数（实测第三维恒为 0），CPU 路径色散的波长只能用随机数与像素位置的哈希，也无法与 Blender 5.3 的色散材质（Principled 的 Transmission Dispersion）共用路径波长——场景里有这种材质时 CPU 渲染会偏色，`bl.diagnose()` 警告；GPU 路径（0.8.2）分层采样并与它们共用同一个波长。
- 炫光：两次反射的序列鬼影 + 衍射星芒 + 杂光（0.6；0.8 按 ISO 9358 标定，预设值就是杂光指数，是按年代的估计——公开资料里没有电影变形镜头的实测）；不含镜筒内壁杂散光。
- 光阑轴向位置（尼康主镜）专利未给，取间隔中点；C 系列风格的变形器到主镜间隔 5 mm 为我方取值。
- 色散（0.6）：有目录数据的玻璃（Panavision、Neil 预设的 Ohara 玻璃）用 Ohara 目录公式的四项拟合，400–700 nm 误差 ≤ 2.6e-5，旧的两项 Cauchy 在重火石的紫端误差达 8.7e-3。老专利只给 nd / Vd 的玻璃（Rosin、Nikon）仍用两项 Cauchy；Isco 0.7 起用 Schott 目录曲线并通过专利谱线。Geometry 模式为单一 d 线折射率。
- Neil 2× 预设的像面在专利里是弯曲的（模拟弯曲物面），这里是平面传感器：画面边缘的最佳聚焦在传感器前 0.1–0.2 mm（有像散），f/3 时 4K 角落约 6–12 px 弥散，属于该设计本身的软角（research/RESEARCH.md §6.8）。
- Isco（E/G）玻璃 0.7 起用 Schott 目录曲线并精确通过专利的 d / C / F 三条谱线。
- Isco「低发腮」：同放大率下压缩比变化 −9.9%（Rosin −12.9%），没有专利正文说的那么低。

## 11. 出问题时先查

| 现象 | 原因 / 处理 |
|---|---|
| 画面全黑 | ① Cycles 设成了 Metal / 非 OptiX GPU（0.4 会自动改 CPU，旧版不会）；② 场景单位与 Build 时的 units 设置不符（例如米制场景却选了 MM）；③ 物体把相机包住了 |
| 焦外有斑块 | 采样不够，见第 3 节第 4 条 |
| 炫光层空白 | 没有标记光源，或光源太弱（< 2×10⁴ 且未标记），或光源在镜头后方 |
| 拉丝太强、发雾 | 调 Flare Intensity，或增大膜厚 / 改 Physical 以外的颜色 |
| Build 报 OSL 编译失败 | 该 Blender 构建没有 OSL，或版本低于 5.0 |
| 控制台出现「BlenderLens: … 本次渲染已改用 CPU」 | GPU 路径没启用（要在第一次 GPU 渲染前 `bl.enable_gpu()`），或 Geometry / Ghosts 模式，或 Blender 版本与补丁不符（`bl.gpu_status()` 看原因） |
| 启用 GPU 后第一次渲染很久才开始 | 正常：Metal 首次编译内核约 2 分钟；之后有缓存 |
| 想彻底恢复原状 | 面板 Remove 或 `bl.remove_gpu_cache()`，再重启 Blender |

## 继续研究

研究资料都在扩展目录的 `research/` 里：`ROADMAP.md`（下一步计划与验收标准，0.8 已完成项有标注）、`RESEARCH.md`（查过的专利、各预设的数据来源与核对、我方假设、修正记录）、专利扫描件与全文、玻璃色散数据、处方生成器与全部测试。先运行 `python3 research/kit/setup_workspace.py <新目录>` 还原成源码树布局，再按 `RESEARCH.md` §8 的流程修改与验证。
