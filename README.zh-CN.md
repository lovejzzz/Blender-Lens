# BlenderLens — Panavision 风格变形镜头（Blender / Cycles）

[English](README.md) | **中文**

用**公开专利的真实镜片处方 + 真实光线追迹**，在 Blender 里模拟变形（anamorphic）电影镜头。效果都由处方算出，不是滤镜：
- 竖椭圆焦外、边缘猫眼、发腮、水平畸变；
- 连续波长色散；
- 一字拉丝与鬼影炫光。

| C 系列风格 · 100 mm · f/2.3 | E 系列风格 · 100 mm · f/2.8 | G 系列风格 · 100 mm · f/3 |
|---|---|---|
| ![C](docs/images/v0.9_c_100mm.jpg) | ![E](docs/images/v0.9_e_100mm.jpg) | ![G](docs/images/v0.9_g_100mm.jpg) |

| Neil 2× 现代变形 · 100 mm · f/3 | Panavision 后置 1.34× 长焦端 · 100 mm · f/4.1 |
|---|---|
| ![Neil](docs/images/v0.9_neil_100mm.jpg) | ![Panavision](docs/images/v0.9_pv_tele_100mm.jpg) |

| Laowa 2× 广角 · 30.9 mm · f/2.9（16:9） | Sirui 1.6× **原生全画幅** 35 mm · f/2.9（36 × 24 → 2.4:1） |
|---|---|
| ![Laowa](docs/images/v0.9_laowa_31mm.jpg) | ![Sirui](docs/images/v0.9_sirui_ff_35mm.jpg) |

用 0.9.0 渲染：同一个真实 3D 场景（Poly Haven CC0 模型与 HDRI）、同一机位；前五张同为垂直 100 mm，两支广角用各自的焦段。原生 16:9 光窗（Sirui 用整个 36 × 24 传感器），768 spp + OIDN，带正向追迹炫光，全部走 Metal GPU 路径（M2 Max，720p 每张 40–100 秒）。0.6 的旧图在 [0.6 新增](#06-新增) 里。

> Panavision 的 C / E / G / T / Primo 变形系列量产处方从未公开。C / E / G 三个选项是「同年代、同结构的公开专利 + 按 Panavision 公布的规格设定观感参数」，**不是复刻**。唯一公开的真 Panavision 处方是 US 7,085,066（后置 1.34× 变形组），原样收录为两个预设。

---

## 0.9.5 新增：G 风格全画幅成套定焦（35–100 mm）

- **G 风格全画幅成套定焦**：新增 35、40、50、75 mm，和 0.9.3 的 100 mm 凑成一整套。全部是 1.65×、f/3，镀膜、炫光、杂光都沿用 G 原版。每支都按 G 原版的清晰度、色彩和暗角来对齐（比较的是画面上相同相对位置的表现）。和真实厂商的成套镜头一样，广角端换用了另一种光学结构（C 风格全画幅设计），因为 G 的附加镜覆盖不了广角。
- 清晰度在各个位置都是 G 原版的 1.05–1.39 倍以内，f/4 时四角亮度 0.59–0.88。**广角端桶形畸变明显**：35 / 40 mm 在 4K 画面里竖线会弯 80–95 px。28 mm 试过，四角是黑的，覆盖不了。详细数据见 `blenderlens/HANDOFF.md` §6.19。

## 0.9.4 新增：直接按交付比例设传感器

- **`aspect=2.37`**：`bl.build`、`render_shot`、`render_sequence` 都能用，会在整个传感器里取解挤压后正好是这个比例的最大光窗（例如 1.65× 全画幅 C 风格是 34.47 × 24 mm）。不用事后裁切，也没有黑边。`gate_size_mm=(宽, 高)` 现在在渲镜头时也能用，实际用到的光窗会写进日志和每帧 EXR 头。

## 0.9.3 新增：E、G、Neil 的全画幅重新设计

- **又多了五个全画幅预设**，做法和 C 风格全画幅版相同：焦距保持原值，变形器重配成全画幅用的压缩比，整支镜头重新优化，让它在 36 × 24 上的表现逐点对齐原版在它自己 S35 光窗上的样子。对齐的项目包括单色清晰度、横向和轴向色差、畸变、暗角。
  - E 风格 131 mm、G 风格 100 mm：1.65×（2.48:1）。最差的位置模糊度分别是原版的 1.06 倍、1.02 倍。
  - Neil 50 / 65 / 85 mm：1.6×（2.4:1，和思锐自家的全画幅产品一致）。最差的位置分别是原版的 1.17 倍、1.27 倍、1.01 倍；近摄时压缩比变小的特点保留。
- 这些是 BlenderLens 自己设计的镜头，不是真实镜头；详细数据见 `blenderlens/HANDOFF.md` §6.17。

## 0.9.2 新增：渲镜头更快、单帧亮点、内置时间质检

- **更快**：`bl.enable_gpu(denoise_gpu=True)` 把 OIDN 降噪放到 GPU 上（Blender 默认在 CPU 上做）。3842 × 1624 一帧从 53 秒降到 38 秒。结果与 CPU 降噪不逐位相同，所以不默认打开，`bl.diagnose()` 会提示。Cryptomatte 解码快了 20 倍，物体多的场景里 fill 和运动矢量这两步明显变快。
- **`passes="delivery"`**：只写时间降噪真正要读的层，加上 BlenderLens 自己的层。3842 × 1624 时每帧 EXR 从 71 MB 降到 20 MB。
- **单帧亮点**：`temporal_denoise(..., spike_suppress=True)` 会把孤立的亮点压到按运动对齐后的前后帧水平，正常高光不动。`bl.spike_report()` 按层、按帧统计这些亮点并出热图，`render_shot` 每个镜头渲完会自动做一次。
- **镜头级临时覆盖**：`render_shot` 和 `render_sequence` 的每个镜头可以设 `visibility={"墙": {"visible_camera": False}}`，以及渲染前 / 后的 `pre` / `post` 回调，渲完自动还原。
- **`bl.verify_inputs()`**：给相机的每一项输入算指纹（处方、镜头数据、OSL、GPU 头文件、出瞳表、补丁）。用来确认两个版本对同一支镜头的渲染输入完全相同；每帧 EXR 头里也写同样的指纹。
- **`exclude_motion_blur=True`**：只给雨层开运动模糊，雨滴拉成线，主渲染不变。
- **`bl.coverage` 新增横向色差**：按 4K 像素给出。C 风格在画面边缘和四角约 2.4–2.5 px。

## 0.9.1 新增：画幅用满片门、65 mm、C 风格的全画幅与 65 mm 重新设计

- **画幅用满整个片门**：所有画幅版本都用满整个传感器 / 片门，解挤压后的比例随每支镜头的压缩比（2× 镜头在 36 × 24 上是 3:1，1.3× 在 65 mm 上是 2.97:1）；整支镜头按设计像圈刚好罩住片门来放大（全画幅时 C / E / G × 1.502、Neil × 1.558、Panavision × 1.590）。
- **新画幅：65 mm 5 齿孔**（`format="65MM"`，52.48 × 23.01 mm，Panavision System 65 / Ultra Panavision 70 的摄影片门），与全画幅、IMAX 15/70 并列。真实的 IMAX 15/70 没用过变形镜头，所以 IMAX 只保留等比放大版本。
- **两个新预设（BlenderLens 自己设计，不是专利）**：设想 C 风格 50 mm 的厂商自己出全画幅版和 65 mm 版。焦距 50 mm 不变；变形器像真实的大画幅变形镜头那样降压缩比（Ultra Vista 1.65×、Hawk65 1.3×、Ultra Panavision 70 1.25×）；玻璃和片数不变，整支重新优化。目标是新片门上的表现逐点对齐原版在它自己 S35 光窗上的：单色清晰度、横向色差、畸变、暗角。
  - **C 风格 50 mm 全画幅 1.65×**（36 × 24 → 2.48:1）：光斑 / 画高在每个位置都是原版的 ×0.9–1.12，横向色差基本相同，4K 竖线弯 −25.5 px（原 −27.3），f/4 四角 0.80（原 0.88）；补偿凸轮表按新设计重算，最近对焦压缩比保持 1.65。
  - **C 风格 50 mm 65 mm 5 齿孔 1.3×**（52.48 × 23.01 → 2.97:1）：原版的 ×0.9–1.39，f/4 四角 0.73。弱点在水平 0.5–0.7 一带，竖向软约 50%：这是这个结构在 41° 对角半视场下的极限，真实厂商会加一片镜片。
- **雨层的亮点（MessyRoom 反馈）**：`exclude_clamp=(直接, 间接)` 只截断排除层（雨）那次渲染的样本，`exclude_knee=(起点, 上限)` 对排除层亮度软压缩。雨滴折射车灯造成的单帧白亮点会被压下去，BL_Final 用压缩后的排除层合成。命令行用 `--exclude-clamp`、`--exclude-knee`。
- 优化器是 `tools/reoptimize.py`，参数存在 `tools/reopt_*.json`，由 `tools/build_presets.py` 重新生成两个预设。

## 0.9.0 新增：原生全画幅广角变形

- **新预设：Sirui 风格 1.6× 全画幅 35 mm（真实处方）**，出自 [CN 118169848 A](https://patents.google.com/patent/CN118169848A/zh)（广东思锐光学，2022），与市售 Sirui Saturn 35 mm T2.9 1.6× 逐项吻合（18 片 13 组、58 mm 滤镜、415 g）。第一个**按全画幅设计**的预设：整个 36 × 24 mm 传感器解挤压后是 2.4:1。逐位转录核对：垂直焦距 34.96 mm（专利 35），X / Y 两向像面相差 0.03 mm，权利要求里 6 个焦距关系全部满足。全开四角是中心的 26%（设计本身，对角线半视场 42.5°），f/4 起 37%；水平桶形约 −3%、垂直枕形约 +5%。
- **覆盖报告显示画幅**：`bl.coverage` 给出相对 36 × 24 的 crop factor 和对应的景深系数；全画幅版本另给比 Super 35 原设计实际大多少。2× 镜头用 16:9_NATIVE 时在全画幅上受高度限制（21.3 × 24 mm），只比 S35 大 8%；要真正的全画幅观感，用低压缩比铺满宽度：Panavision 1.34× 全画幅版本（对角线 1.52 倍）或新的 Sirui 1.6×。
- **演示图换成 0.9 渲染**（0.6 的旧图放在下面 0.6 的介绍里）。

## 0.8.8 新增：全画幅版本

- **每支镜头都有全画幅版本**：`bl.build(..., format="FF")`，面板里 Build Lens (Full Frame)，命令行 `--format FF`。原来的预设都是按 Super 35 设计的；全画幅版本是同一个设计整体做大一号（焦距 × 1.29–1.52），覆盖、渐晕、畸变、焦外形状、炫光与原设计逐点相同，同 f 值下景深更浅。例：C 风格 64.5 mm、Neil 65 → 87.6 mm、Laowa 39.6 mm、Panavision 1.34× 31.3–100.8 mm。
- **修正**：用 `focal_mm` 缩放 C / E / G 时，近摄的发腮补偿以前没跟着缩放（压缩比偏差），现在任何焦距下都与原设计相同。
- **排除层更快一点**：空画面在一个只有相机的临时场景里渲。

## 0.8.7 修正：雨层不再把画面压暗

- **`BL_Exclude` 的 alpha 修正**：0.8.5 / 0.8.6 里排除层的 alpha 混进了镜头渐晕和采样截断偏差，`BL_Final` 在没有雨的地方也被压暗（中心约 5–8%、四角更多）。现在 alpha 与空画面的透明度相除，没有雨的像素 alpha 恰好 0，`BL_Final` = 场景 + 雨 + 炫光。已经渲好的帧：雨层颜色是对的，用加法合成 `Combined + BL_Exclude.rgb + BL_Flare + BL_Veil`。
- **排除层更省**：只算 Combined；可选 `exclude_spp=` / `exclude_bounces=`（`blcli.py shot --exclude-spp`），默认不开。

## 0.8.6 新增：广角变形（MessyRoom Day 5）

- **Laowa 风格 2× 广角 29 mm**：CN 112285889 B 的真实现代 2× 广角变形（柱面在中间，不是前置附加镜）。在 16:9 原生光窗上缩到取景 21–28 mm 时，边缘畸变约 −6%（C 风格 −58%），4K 下竖直线弯 17–43 px（C 风格 101 px）。
- **覆盖与畸变报告**：`bl.coverage(cam)` / `bl.lens_info(cam, coverage=True)` / `blcli.py info --coverage`：像圈 vs 光窗对角线、四角相对照度（与渲染同一估计器，差 ≤ 0.02）、横 / 竖畸变、画面边缘竖直线在 4K 下的弯曲、当前视场 vs 能覆盖的视场。
- **不牺牲质量的提速**：出瞳采样盒更贴（有效采样约 +10%，仍然无偏）、运动矢量投影 2.2 倍、补洞 5 倍、倒影矢量用 BVH 树、炫光解析求交。4K 每帧后期 17 → 12 秒。
- **build 警告**：缩放后覆盖不了光窗时，`bl.build` 会警告（控制台、`lens_info`、`diagnose`），不再静默生成「舷窗」。

## 0.8.5 新增：便利店雨夜（MessyRoom Day 5）

- **矢量和深度穿过玻璃**：橱窗、门、冷柜门（Principled 透射 ≥ 0.9 且粗糙度 ≤ 0.1、Glass BSDF，或 `bl_pass_through`）后面的东西不再拿到玻璃的运动。`BL_Vector` / `Depth` 取第一个非透射面；玻璃自己的进 `BL_VectorSurface` / `BL_DepthSurface`；玻璃后的置信度按反射占比降。
- **雨、粒子的排除层**：`exclude_collections=["FX_rain"]` 把这些集合单独渲成预乘的 `BL_Exclude`（被场景挡住的部分自动去掉）。矢量、闪烁检查、时间降噪都在不含雨的场景层上做，最后把雨原样叠回。
- **一个 .blend 多支镜头**：`bl.render_sequence([...])` / `bl.shots_from_markers()` / `blcli.py sequence`，每个镜头焦距可以不同，同一个进程渲完。`render_shot` 搭镜头时不再改掉场景的渲染设置。
- **保留曝光关键帧**：`exposure="pinhole"` / `"pinhole_centre"` 乘到原有的 `film_exposure` 曲线上。
- **对焦跟随**：`bl.focus_track(cam, 目标, 帧, rack=[(帧, 目标), …])`。
- **倒影的运动矢量**：平面反射体（湿地面、水洼）上的倒影有自己的矢量（`BL_VectorReflection`、`BL_ReflectionShare`），倒影为主的像素自动用它。

## 0.8.4 新增：运镜与时间稳定性（MessyRoom Day 4 成片后的反馈）

- **多层输出更小、更省内存**：`passes="temporal"` 只写时间降噪需要的层（Combined、Noisy、体积、去噪 albedo / 镜面 / 法线 / 粗糙度 / 深度、Depth 和插件层）；颜色层 half + DWAB，数据层 float32 + ZIP（`exr_codec`、`half_color`）。没人要的渲染层渲染时关掉，后期逐层流式处理。4K 测试帧 555 MB → 97 MB。
- **真实运镜中的闪烁**：`bl.temporal_check(..., mode="moving")` 按真实运动渲染，用 `BL_Vector` 把 t−1 帧对齐到 t 帧测残差，另给速度直方图与各速度区间的闪烁。
- **矢量可信度**：新层 `BL_VectorConfidence`（遮挡 / 去遮挡、深度边缘、发光体外圈、镜面、速度）；天空 / 环境像素有了随相机转动的矢量；修了 Cryptomatte 哈希转换的错误（约 1/128 的物体没有物体运动）。
- **时间降噪**：`bl.temporal_denoise(in_dir, out_dir, frames)`——运动补偿平均，按矢量置信度和前后一致性加权，炫光与杂光原样加回；输出稳定版 `BL_Final` 和「每像素用了几帧」的图。
- **一条命令渲一个镜头**：`bl.render_shot(source_camera, frames, out_dir, preset, passes, spp)` / `blcli.py shot`——跟随源相机（含对焦与光圈）、续渲、内存不够时等待、逐帧记录用时与内存。
- **炫光在视频里稳定了**：衍射星芒原来按像素中心点采样，亚像素移动时能量跳 ±25%；现在按像素面积积分（0.3%）。正向鬼影追迹快 5–6 倍。

## 0.8.3 新增：成片输出（MessyRoom 片子里提出的需求）

- **保留所有渲染层的多层 EXR**（`bl.render_still(path, passes="all")`、`bl.render_frames(..., passes="all")`）：视图层打开的渲染层全部保留；Lateral in Post 对每个颜色层做同样的色差（各光组相加仍等于 Combined），数据层不动；炫光与杂光是单独的层（`BL_Flare`、`BL_Veil`、`BL_Final`）。
- **`BL_Vector` 运动矢量**：按真实镜头成像算（Cycles 自己的 Vector 对自定义相机没有意义）；约定同 Cycles，与实测位移差 0.2 px。Depth / Position 的洞（Cycles 只在第 0 个采样写，真实镜头的第 0 条光线常被挡）已补。
- **对焦助手**：`bl.focus_on(cam, 物体 | 点 | (u, v))`、`bl.focus_pull(cam, A, B, 起, 止)`、`bl.dof_range(cam, coc_px)`、`bl.stop_for(cam, near_m, far_m)`；主体不在清晰范围时 `diagnose()` 会警告并说差多少米。
- **闪烁检查**：`bl.temporal_check(scene, frame, n, variants=[...])` 锁住相机让时间走，测相邻帧差（整幅、最亮 2%、p99）并出热力图，变体给出相对百分比。
- Render Region 可与 Lateral in Post 一起用；4K 内存基线写在 HANDOFF；横向色差后期快约 7 倍。

## 0.8.2 新增：Blender 5.3 兼容

- **与 5.3 色散玻璃共用波长（GPU）**：Blender 5.3 给 Principled BSDF 加了色散（每条路径一个波长）。之前我们的镜头和这种玻璃各抽一个波长、颜色加权两次——透过色散玻璃看白天空会明显偏黄（偏色 14%）。现在 GPU 路径让两者用同一个随机数和映射，共用一个波长（偏色 0.2%）；场景里没有色散材质时仍用 0.8 的低噪采样。CPU / OSL 路径做不到，`bl.diagnose()` 会警告并给出修法。
- **工作色彩空间**：镜头色散的颜色按场景工作色彩空间（如 ACEScg）换算，GPU 与 CPU 都是。
- **内核补丁漂移检查**：`tests/test_kernel_patch.py`（quick 级）对照本机 Blender 检查所有 GPU 补丁位置，升级后明确指出哪一处变了。

## 0.8.1 新增：AI 原生

让 AI（以及脚本）不用看界面就能使用、测试、改进 BlenderLens：
- **自描述接口**：`bl.describe()`（全部设置的模式直接从 Blender 属性读出）、`bl.state()`、带校验的 `bl.configure()`、`bl.diagnose()`（问题 + 可执行的修法）、`bl.preview()`、`bl.contact_sheet()`（A/B 对比图）。
- **命令行** `tools/blcli.py`：`presets` / `info`（不启动 Blender 的光学数字）/ `describe` / `render`（内置演示场景，`--variants` 出对比图）/ `diagnose` / `test`，输出都是 JSON。
- **分级测试** `tests/run.py quick | standard | full`：并行、JSON 报告（`renders/test_report.json`）、指标基线对比（`tests/baseline.json`），能发现「测试仍通过但数值变差」。
- **`AGENTS.md`**：给接手的 AI 的代码地图、不能破坏的约定、发版流程、踩过的坑。

## 0.8 新增

按 [`blenderlens/research/ROADMAP.md`](blenderlens/research/ROADMAP.md) 的 0.8「准确性与采样」：
- **发腮补偿（Mumps Correction，C / E / G 风格）**：Auto Panatar 式（Panavision 1958，Wallin 反向旋转柱面对）的一阶等效补偿板，与对焦环联动。
  - 默认完全补偿：最近对焦处压缩比 = 无穷远（C 2.011、E/G 2.094），与 ARRI Master Anamorphic 数据表（近摄 1.97–2.01×）一致。
  - 补偿后对焦呼吸以垂直为主（E：水平 −6%、垂直 −32%），与 Panavision 对 E 系列的描述「vertical focus breathing」一致；背景焦外是经典的 1.9–2.3 竖椭圆。
  - 滑块拉到 0 = 1950 年代 CinemaScope 式未校正（C 近摄 1.69×、人脸变胖）。CPU / GPU / Geometry 三条路径实测一致。
- **多层镀膜**：特征矩阵法（Abelès），单层 MgF₂ 或三层 1/4–1/2–1/4 宽带膜（贴玻璃层按每面玻璃配）。宽带膜的鬼影弱一个量级、偏品红 / 琥珀；Neil 与 Panavision 后置预设默认宽带膜。CPU / GPU 鬼影能量差 0.7%。
- **GPU 波长分层采样**：波长取 Cycles 的 time 维（与出瞳同一个三维 Sobol 点）。同样采样数下色噪低 2.1–2.7 倍（约等于 4.5–7 倍采样），平均颜色不变（0.04%）。开运动模糊时自动退回旧方式。
- **出瞳采样表加密**：带光采样占比平均从约 0.54 提到 0.62–0.74（无额外每采样开销），预设的表随包分发，搭镜头不再等待。
- **杂光按 ISO 9358 标定**：预设值就是合成测试（白场中央黑斑）实测的杂光指数（误差 < 1%）；去掉了 FFT 环绕（右边的亮光不再漏到左边），4K 0.6 秒。0.7 实测只有设定值的约 1/4。
- **Geometry 模式补上光圈**：之前几何模式没有光圈网格，只靠 1.4 倍出瞳采样盘限光，f 值与叶片几乎无效；现在有随对焦移动、随 f 值 / 叶片重建的光圈。网格法线也改成浮点「自由法线」，并绕开 Cycles 法线压缩对 0 分量的偏置。
- **外部数据回归测试** `tests/test_reference.py`：最近对焦距离、近摄压缩比、焦外形状（前置椭圆 / 后置近圆）、呼吸方向，逐项对照厂方数据与文献。

## 0.7 新增

- **GPU 相机端鬼影**：开 Ghosts / Flare 也能在 Metal 上渲染（与 CPU 能量差 0.6%，快 19 倍）。
- **Schott 目录**：E/G 的 Isco 玻璃用 Schott 目录曲线，并精确通过专利给的 d / C / F 三条谱线（SF11 紫端折射率修正 0.007）。
- **Neil 场曲研究**：模型算出的最佳聚焦面与专利设定的弯曲像面一致；平面传感器上 4K 角落约 6–12 px 弥散，属该设计本身的软角。
- `bl.follow_camera`（0.6.1）：跟随场景相机并烘焙关键帧，保留相机运动模糊。

## 0.6 新增

0.6 渲染的图（原光窗，CPU / 最早的 GPU 路径，720p 每张约 3–5 分钟）：

| C 系列风格 · 100 mm · f/2.3 | E 系列风格 · 100 mm · f/2.8 | G 系列风格 · 100 mm · f/3 |
|---|---|---|
| ![C](docs/images/v0.6/real_c_100mm.jpg) | ![E](docs/images/v0.6/real_e_100mm.jpg) | ![G](docs/images/v0.6/real_g_100mm.jpg) |

| Neil 2× 现代变形 · 100 mm · f/3 | Panavision 后置 1.34× 长焦端 · 100 mm · f/4.1 |
|---|---|
| ![Neil](docs/images/v0.6/real_neil_2x_85_100mm.jpg) | ![Panavision](docs/images/v0.6/real_panavision_rear_134_tele_100mm.jpg) |


- **Neil 2× 现代变形三个预设**：
  - 中国授权本表格逐位读出；近轴焦距 49.998 / 65.028 / 85.012 mm（专利 50 / 65 / 85），后焦与印刷值相差 ≤ 0.1 mm。
  - G1b 内对焦，一个自由度同时对准两向。
- **目录色散**：Panavision 与 Neil 预设的 Ohara 玻璃改用目录公式（refractiveindex.info，CC0），400–700 nm 误差 ≤ 2.6×10⁻⁵。旧的两项近似在重火石紫端偏差达 8.7×10⁻³，紫边位置因此不准。
- **衍射星芒**：光圈多边形的夫琅禾费衍射，按 R/G/B 与两向数值孔径计算，变形镜头的星芒会被拉伸。
- **杂光（veiling glare）**：保能量的长尾散射，按镜头年代 / 镀膜取 ISO 9358 杂光指数典型值。
- **GPU 光线微分**：纹理过滤与 CPU / OSL 版一致。

## 1. 安装

要求：Blender **5.0 及以上**，不依赖第三方 Python 包。在 macOS / Apple M2 Max / Blender 5.3 上开发与实测，另有 M4 Max 的第三方复测；Windows / Linux 未实测。

1. 下载本仓库的 [`dist/blenderlens-0.9.5.zip`](dist/blenderlens-0.9.5.zip)。
2. Blender → Edit → Preferences → **Get Extensions** → 右上角 ⌄ → **Install from Disk…** → 选这个 zip。
   也可以用命令行：

   ```bash
   blender --command extension install-file -r user_default -e blenderlens-0.9.5.zip
   ```

3. 3D 视图按 **N**，出现 **BlenderLens** 标签。

从源码打包：

```bash
blender --command extension build --source-dir blenderlens --output-dir dist
```

## 2. 快速上手（界面）

1. **Build Lens** → 从下拉菜单选镜头。插件会做四件事：搭好相机和整支镜头、设为场景相机、把渲染设成 Cycles、按解挤压比设置输出分辨率。
   按 **F9**（或左下角的操作面板）可以改：
   - **Vertical Focal Length**：把镜头等比缩放到这个垂直焦距，0 = 原生。几个预设能同时物理成立的共同焦段是 100 mm。
   - **Gate**（画幅）：
     - Full Aperture：处方原光窗。
     - 2.39 Extraction / 16:9 Extraction：从原光窗里裁切。
     - **16:9 Native (Full Circle)**：原生 16:9、不裁切，用满整个像圈。
     - Custom：自填宽高。
   - **Scene Units**：默认 Auto。已有内容的场景（例如米制生产场景）保留原单位，空场景改成毫米。镜头内部恒为毫米，二者只差一个比例。
2. 像普通相机一样移动、旋转 `BL_Camera`：本地 −Z 是视线。不要动它下面的子物体，对焦时插件会自动移动镜片组。
3. 在面板（或 Properties → 相机数据 → BlenderLens）里设置：
   - **Focus Distance**：从像面量起，0 = 无穷远，可以打关键帧。
   - **f-Number**、**Blades**：光圈与叶片数，0 = 圆形。
   - **Dispersion / Dispersion Mode**：
     - Full：逐采样按波长追迹，横向与纵向色差都有。
     - Lateral in Post：渲染时关色散，横向彩边在输出时由处方映射精确加上，色噪低约 45 倍。
     - Off：无色散。
   - **Mode**：Analytic（日常用）/ Geometry（Cycles 穿过真实镜片网格，用于校验或看反射）。
4. 正常渲染（F12），或用 **Render with Flare**：渲染完再叠加正向追迹的一字拉丝与鬼影。
   - 先选中小的发光物体（例如灯泡位置放一个发光小球），点 **Mark Flare Source** 标记为炫光光源。
   - 炫光的颜色（物理镀膜色 / 灯光本色 / 琥珀 / 自定义）和强度在面板里调。

## 3. GPU 渲染（Mac Metal）

Cycles 原生的 OSL 自定义相机只在 CPU 和 NVIDIA OptiX 上运行，Metal 上会出黑图。0.5 起插件提供 **GPU 路径**：
- 把同一套逐面追迹编进 Cycles 运行时编译的 GPU 内核。
- 内核源码拷贝放在扩展自己的用户目录，**不改 Blender 本身**。

- 界面：BlenderLens 面板 → **Enable GPU Path**，再把 Cycles 设备设成 GPU（Metal）。
- 必须在本次启动 Blender 后**第一次 GPU 渲染之前**启用。首次编译内核约 2 分钟，之后有缓存。
- 没启用、Blender 版本与补丁不符、Geometry 模式、开了相机端鬼影时，渲染会自动改回 CPU，并在控制台写明原因，不会出黑图。
- 面板上的 **Remove** 删除内核拷贝，重启后完全恢复原状。

实测（960×540，256 spp，不降噪）：

| | M2 Max · 合成基准 | M4 Max · 真实夜景帧（MessyRoom 复测） |
|---|---|---|
| 0.4 CPU | 69.7 s | 119 s |
| 0.5 CPU | 22.9 s | 72 s |
| **0.5 GPU** | **1.23 s** | **19 s** |

- GPU 与 CPU / OSL 参考实现：平均亮度差 0.001%，整体位移 0.000 px。
- 4K 一帧（GPU、Lateral in Post、16:9 Native、256 spp + OIDN）在 M4 Max 上约 3 分钟。

## 4. 用 Python 与命令行

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

命令行（源码树里，输出 JSON；见 `AGENTS.md`）：

```bash
python3 tools/blcli.py info c_series_style
```

```bash
python3 tools/blcli.py render --preset c_series_style --focus-m 3 --gpu --variants '[{"f_number":2.3},{"f_number":8}]' --out renders/cli/ab.png
```

启用扩展后，脚本里直接用稳定入口（不用管安装仓库名）。距离一律用米：

```python
import math
import blenderlens.api as bl

bl.enable_gpu()                                    # Mac：启用 GPU 路径（第一次 GPU 渲染之前）
cam = bl.build("c_series_style.json",              # 预设文件名，或任意处方 JSON 路径
               focal_mm=40, gate="16:9_NATIVE", units="SCENE", height=2160)
cam.location = (0.0, -6.0, 1.4)
cam.rotation_euler = (math.radians(90), 0, 0)
bl.set_focus(cam, metres=8.5)
bl.set_stop(cam, 2.8)
bl.set_mumps_correction(cam, 1.0)                       # 0.8：发腮补偿 0–1（C / E / G 风格；默认 1）
cam.data.blenderlens.coating_type = 'BROADBAND'         # 0.8：镀膜 'SINGLE' / 'BROADBAND'
cam.data.blenderlens.dispersion_mode = 'LATERAL_POST'

print(bl.lens_info(cam))                           # 焦距、压缩比、光窗、单位换算……
print(bl.exposure_vs_pinhole(cam))                 # 相对普通相机的曝光差（EV）
bl.render_still("//shot.exr", flare=True)          # 渲染 + 插件后期（横向色差、炫光）
```

命令行后台渲染整段动画（对焦、f 值关键帧在 GPU 上同样生效）：

```bash
blender -b shot.blend --python-expr "import blenderlens.api as bl; bl.enable_gpu(); bl.render_frames(frames=range(1, 145), out_dir='/shared/shot010')"
```

跟随已有的场景相机（保留相机运动模糊）：

```python
bl.follow_camera(cam, bpy.data.objects["ShotCam"], 1, 144, substeps=2)   # 位置 / 旋转 / 对焦距离烘焙成关键帧
```

`render_frames` 的行为：
- **确定性**：种子 = seed + 帧号，同一帧在任何机器上结果相同。
- **可断点续渲**：先写 `.partial` 再改名，已有帧跳过。
- **多机分帧**：两台机器对同一个共享目录跑同一条命令，会用 `.lock` 文件自动分帧。

纯光学计算（不需要 Blender）：把仓库根目录加进 `sys.path`，然后：

```python
import blenderlens.optics as O
rx = O.load(O.PRESCRIPTION_DIR + "/e_series_style.json")
O.squeeze_at(rx, 2500.0)                 # 2.5 m 对焦时的压缩比
O.bokeh_aspect(rx, 1200.0, 20000.0)      # 焦外高宽比（解挤压后）
```

更完整的说明（每个参数、常见问题、已知限制）见 [`blenderlens/HANDOFF.md`](blenderlens/HANDOFF.md)。

## 5. 镜头选项

| 选项 | 光学来源 | 焦距 · 光圈 · 最近对焦 | 压缩比 ∞ → 最近对焦 | 依据的 Panavision 公开规格 |
|---|---|---|---|---|
| **C 系列风格（1968）** | 前置 2×：[US 2,944,464](https://patents.google.com/patent/US2944464)（Rosin / Scanoptic 1957）+ 主镜 [US 4,234,242](https://patents.google.com/patent/US4234242A)（Nikon f/2 双高斯） | 垂直 50 mm · f/2.3 · 0.76 m | 2.011 → 2.011（补偿，默认）；关补偿 1.687（−16%） | C Series 50 mm T2.3、最近对焦 76.2 cm、「明显的变形炫光」 |
| **E 系列风格 135 mm（1980 年代）** | 前置 2.09×：[US 5,184,880](https://patents.google.com/patent/US5184880A/en) 实施例 3（Isco 1990，两向分别矫正、凸轮对焦）+ 同一主镜 | 垂直 131 mm · f/2.8 · 1.14 m | 2.094 → 2.094（补偿，默认）；关补偿 1.632（−22%） | E Series 135 mm T2.8、最近对焦 114.3 cm、「干净的蓝色水平炫光、无雾化」 |
| **G 系列风格 100 mm（2007）** | 同 E 系列风格光学，整体缩放 0.763 | 垂直 100 mm · f/3 · 0.91 m | 2.094 → 2.094（补偿，默认）；关补偿 1.652（−21%） | G Series 100 mm T3、最近对焦 91.4 cm、「极佳的抗眩光」 |
| **Panavision 后置 1.34×（真实处方）** | [US 7,085,066](https://patents.google.com/patent/US7085066B2/en)（Iain A. Neil / Panavision 2004）原样：变焦镜短焦端 + 8 片后置变形组（3 片 Y 向柱面 + 5 片 X 向柱面），2 个非球面 | 水平 15.8 / 垂直 20.7 mm · f/3.66 · 0.48 m | 1.304 → 1.304（无发腮） | 专利实施例：16:9 画幅解挤压到 2.40:1 |
| **Neil 2× 现代变形 50 / 65 / 85 mm（真实处方，0.6）** | [CN 111562632 B](https://patents.google.com/patent/CN111562632B/en)（Iain A. Neil / 广东思锐光学 Sirui，2019）原样：同一前变形部分（X 柱面 3 片、Y 柱面 3 片，G1b 两片内对焦）+ 三个可换后组。**美国同族 US 11,287,618 的表格印刷错误多，不能用**（见 `blenderlens/research/RESEARCH.md`） | 垂直 50 / 65 / 85 mm · f/3 · 1.08 m | 2.06 → 1.93（−6.6%） | 专利实施例（不是 Panavision 镜头；设计者与 Panavision 后置组相同） |
| **Laowa 风格 2× 广角 29 mm（真实处方，0.8.6）** | [CN 112285889 B](https://patents.google.com/patent/CN112285889B/zh) 实施例 1（安徽长庚光学 / Laowa，2020）：负前组（一片移动对焦）→ Y 向柱面组 → X 向柱面组 → 含光阑的后组；专利只给 nd / Vd、没有口径（口径估算后按真实边厚收回） | 水平 14.6 / 垂直 29.0 mm · f/2.9 · 1.68 m | 1.99 → 1.99（内对焦） | 专利实施例；广角选项：16:9_NATIVE 下取景约 25 mm 起覆盖，边缘畸变 −6% |
| **Sirui 风格 1.6× 全画幅 35 mm（真实处方，0.9.0）** | [CN 118169848 A](https://patents.google.com/patent/CN118169848A/zh)（广东思锐光学，2022；= Sirui Saturn 35 mm T2.9 1.6×）：前球面组（第 2–3 片对焦）→ X 向柱面组 → 含光阑的球面组 → Y 向柱面组 → 非球面；按全画幅设计（36 × 24 → 2.4:1）；专利只给 nd / vd、没有口径（口径估算） | 水平 21.2 / 垂直 35.0 mm · f/2.9 · 0.9 m | 1.648 → 1.648（无发腮；按标称 1.6 解挤压） | 专利实施例；原生全画幅：全开四角 26%，f/4 时 37% |
| **C 风格 50 mm 全画幅 1.65×（BlenderLens 设计，0.9.1）** | C 风格的变形器重配成 1.65×、整支重新优化（玻璃与片数不变），在全画幅上对齐原版的观感；`tools/reoptimize.py` | 垂直 50 mm · f/2.3 · 0.76 m | 1.65 → 1.65（补偿） | 不是真实镜头：设想的全画幅版；36 × 24 → 2.48:1 |
| **C 风格 50 mm 65 mm 5 齿孔 1.3×（BlenderLens 设计，0.9.1）** | 同一做法，1.3×（Hawk65 / Ultra Panavision 70 一级），片门 52.48 × 23.01 | 垂直 50 mm · f/2.3 · 0.76 m | 1.30 → 1.30（补偿） | 不是真实镜头；2.97:1；水平 0.5–0.7 一带偏软 |
| **Panavision 后置 1.34× 长焦端（真实处方）** | 同上，变焦镜取长焦端 Z4（变焦间隔取自 [US 6,122,111](https://patents.google.com/patent/US6122111A/en) TABLE 1，叠加到已核实的 Z1 表上） | 水平 50.9 / 垂直 66.4 mm · f/4.1（垂直）· 0.48 m | 1.305 → 1.305（无发腮） | 变焦镜单独 49.22 mm（专利 49.2） |

- **统一焦段**：Build 时填「Vertical Focal Length」，可把任意预设按比例缩放到同一垂直焦距（压缩比、f 值、像差形态不变；估算口径的预设按新视场重算口径）。Isco 附加镜（E/G）低于约 100 mm 会超出设计视场，所以几个预设能同时物理成立的共同焦段是 100 mm。
- 观感参数（光圈、最近对焦、镀膜膜厚、炫光强度、角落光瞳通过比例）写在每个处方 JSON 的 `look` 里。前四个搭建时写入相机，之后可以在面板里改；角落光瞳比例决定镜片口径（猫眼强弱），改了要重算口径。
- 猫眼强弱（f/N 时角落 95%/90% 视场的光瞳通过率，真实追迹）：C 0.64、E 0.84、G 0.90、Panavision 后置 0.68（专利原口径；专利称角落相对照度约 50%）。收两档后 C/E/G 恢复到 1.00。
- E/G 系列风格用的 Isco 附加镜是长焦设计：配 f=100 主镜时 S35 变形画幅垂直半视场只有约 4°。缩到 50 mm 会超出设计视场 2.6 倍、镜片边厚为负，所以这两个选项取 135 mm 和 100 mm。
- 解挤压比（输出分辨率宽度 = 高 × 传感器宽高比 × 解挤压比）：C 用名义 2.0（中心实测 2.010）；E/G 用光学实际值 2.09（按名义 2.0 会窄 4.5%）；Panavision 后置用 1.35，因为专利规定整幅 2.40:1 装进 16:9，真实追迹画面边缘张角比正好 2.401。压缩比都随视场变化（水平畸变）：C 从中心 2.01 到边缘 2.27，E 2.09→2.13，G 2.09→2.16，Panavision 后置 1.30→1.35。
- 发腮（关掉补偿时，同一放大率下比较，物距 = 20 × 垂直焦距）：C −12.9%、E/G −9.9%、Panavision 后置 0（内对焦）。0.8 起 C/E/G 默认带 Auto Panatar 式补偿（Mumps Correction = 1），各距离压缩比不变。Isco 设计比 Rosin 少约四分之一，但专利正文「约 4%」是相对其对焦表中段参考距离（约 6 m）的偏差，从无穷远到 2.5 m 实际变化约 10%。
- 后置变形组没有发腮：按 US 6,122,111 的内对焦（前两组按 1 : 0.909 移动），变焦镜输出的中间像位置不变，后置变形组看到的物像关系始终一样，两向同时合焦、压缩比不随对焦变化。这是它与前置设计最大的画面差别之一。

## 6. 改处方（单一事实来源）与继续研究

每个镜头选项只有一份处方：`blenderlens/prescriptions/*.json`（E/G 系列风格和 Panavision 后置预设由 `tools/build_presets.py` 从专利转录数据生成）。场景里的 `BL_prescription.json` 文本块是当前镜头处方的副本。往这个目录放新的 JSON，就会出现在 Build Lens 菜单里。

1. 改 JSON 里的 R / d / 玻璃 / 缩放。
2. `python3 tools/rebuild_apertures.py`：重新追迹估算各面口径和光阑位置，检查边缘是否相撞。
3. 在 Blender 里 **Build**，或 **Recompute Apertures**。网格、OSL 相机、对焦表都由 JSON 重新生成，不要手改网格或 OSL。

研究资料随扩展分发在 `blenderlens/research/`：
- `RESEARCH.md`：查过的专利、各预设的数据来源与逐位核对、我方假设、修正记录；
- `ROADMAP.md`：下一步计划（0.8 准确性与采样、0.9 新镜头、1.0 管线），每项有做法和验收标准；
- 候选专利原件（未打包进扩展）在仓库根目录 `research_candidates/patents/`；
- 专利扫描件与全文、玻璃色散数据；
- 处方生成器与全部测试。

## 7. 测试

```bash
python3 tests/run.py quick
```

```bash
python3 tests/run.py full
```

`quick` 约 2 分钟、`standard` 约 15–20 分钟、`full`（含打包与干净环境安装）约 1 小时；报告写到 `renders/test_report.json`，数值指标对照 `tests/baseline.json`。`tests/run_all.sh` 等于 `full`。

| 测试 | 内容 |
|---|---|
| `tests/bl_agent.py`（0.8.1） | AI 接口：describe / state / configure（含拒绝错误输入且不改动）/ diagnose / preview / contact_sheet |
| `tests/test_optics.py` | Cauchy、解析梯度、近轴对照专利、真实 vs 近轴、对焦求解 |
| `tests/test_flare.py` | 炫光通道：能量守恒、镀膜强度与颜色、一字拉丝集中度 |
| `tests/test_pupil.py` | 出瞳采样表覆盖所有能穿过镜头的光线（随机视场 × 波长 × 对焦距离，无偏的前提）；光谱表白平衡 |
| `tests/bl_units.py` | 毫米场景与米制场景渲染一致；GPU 非 OptiX 自动回退 CPU；稳定 API；画幅 |
| `tests/bl_sampling.py` | 出瞳重要性采样开 / 关：各区域亮度一致（无偏），同 spp 噪点对比 |
| `tests/bl_convergence.py` | 64 → 256 spp 的噪点下降（理想 2×）：色散开 / 关 × 自适应采样开 / 关 |
| `tests/test_presets.py` | Isco 凸轮对焦复现专利对焦表（11 行，≤ 0.074 mm、≤ 0.03%）；Panavision 焦距与非球面；所有预设边缘不相撞；发腮补偿（各强度压缩比、合焦、不相撞、锐度不变） |
| `tests/bl_presets_smoke.py` | 每个预设在 Blender 里搭建、网格、法线、对焦同步 |
| `tests/bl_smoke.py` | 插件注册、搭建、网格合法、法线方向、对焦同步 |
| `tests/bl_verify_mapping.py` | 渲染刻度靶：两种模式的画面位置与 Python 真实追迹对比（Analytic < 1 px，Geometry < 1.5 px）、方位正确 |
| `tests/bl_acceptance.py` | 文档第四节四项现象的定量验收 |
| `tests/test_gpu_lenspack.py` | GPU 内核头文件生成（所有预设、字面量合法）；横向色差映射（d 线残差、轴上为零、白色守恒） |
| `tests/bl_gpu.py` | GPU 路径对 CPU / OSL：时间、噪点、亮度、整体位移、对焦关键帧、Geometry 回退 |
| `tests/bl_dispersion.py` | Lateral in Post 与 Full 的彩边位置对比、三种色散模式的噪点 |
| `tests/bl_frames.py` | 曝光差 API 对照实测；render_frames 的跳过、断点续渲、确定性、锁 |
| `tests/bl_gpu_ghosts.py` | GPU 相机端鬼影对照 CPU / OSL：鬼影能量、模糊后逐像素差、时间（`-- BROADBAND` 测宽带膜） |
| `tests/test_coating.py`（0.8） | 镀膜：单层 = Airy 公式、教科书数值（裸玻璃 4.26%、MgF₂ 1.26%）、宽带膜残余反射、互易、全反射、鬼影变弱 |
| `tests/test_glare.py`（0.8） | 杂光：合成 ISO 9358 测试测出的 VGI = 设定值（3 种分辨率 × 3 个值）、不环绕、能量 |
| `tests/test_reference.py`（0.8） | 外部数据：最近对焦距离、近摄压缩比、焦外形状、呼吸方向 |
| `tests/bl_spectral.py`（0.8） | GPU 波长分层采样：色噪对照参考图（16 / 64 / 256 spp）、无偏 |
| `tests/bl_mumps.py`（0.8） | 发腮补偿：最近对焦处圆盘的宽高比，补偿 0 / 1，CPU / GPU / Geometry 一致 |
| `tests/bl_follow.py` | follow_camera：子帧位置在两帧之间、对焦距离有关键帧、开运动模糊时拖影存在（且对焦关键帧不再打断相机运动模糊） |
| `tests/bl_percentage.py` | resolution_percentage ≠ 100% 时后期步骤尺寸正确 |
| `tests/bl_bench.py` | MessyRoom 合成基准（时间、三种噪点，结果追加进 bench.json） |
| `tests/install_test.sh` | 在全新用户配置里从 zip 安装、渲染，并按 HANDOFF 的命令行用法跑 GPU 帧（模拟另一台机器） |

## 8. 验证记录（2026-10-18 / 19，Blender 5.3 beta，M2 Max）

| 项目 | 结果 |
|---|---|
| 画面映射 | 3 m 刻度靶，5 个预设 × Analytic / Geometry 两种模式，对照 Python 真实主光线追迹：Analytic ≤ 0.8 px；Geometry ≤ 1.2 px（主要是全画面一致的亚像素平移，长焦预设最大）；方位正确（不镜像、不倒置） |
| Geometry 透过率 | 红通道 0.875，按 16 个镀膜面估算 0.866 |
| ① 竖向椭圆焦外 | C 系列风格，对焦 1.2 m、点光源在 20 m：解挤压后高宽比实测 1.450，真实追迹预测 1.452 |
| ② 边缘猫眼 | C 系列风格：f/2.3 相对 f/4.6 的渐晕比（能量指标，消掉视场相关的光斑大小与 cos⁴），水平边缘 0.79 / 角落 0.68 / 远角 0.64，真实追迹预测 0.77 / 0.68 / 0.66；收两档后猫眼消失 |
| ③ 一字拉丝 | 鬼影能量 32–44% 落在过光源的 ±6 px 水平带内（该带只占画面 3.2%）。颜色：100 nm 蓝紫，135 nm 蓝，无镀膜中性，形状不变 |
| ④ 发腮 | C 系列风格压缩比（0.8.1 复测，默认补偿）：无穷远实测 2.010、1 m 实测 2.008（预测均为 2.011）。关掉补偿（0.7 及以前的行为）：1 m 实测 1.759（预测 1.752），近摄解挤压后画面偏宽约 13% |
| Panavision 处方 | 62 行 Table I 与非球面系数逐行对照扫描件无误；玻璃用 Ohara 目录值（专利只给六位代码，且 FPL53 印作 438950，目录为 439950）。焦距 x/y 15.817 / 20.627（专利 15.81 / 20.60），后焦距 35.80（专利像距 35.754），13.6 mm 像高物方半视场 42.14°（专利 42.34°），整幅物方宽高比 2.401（专利 2.40） |
| Isco 处方 | 主表、对焦表逐位对照扫描件无误；用专利自带主镜复现对焦表 11 行（凸轮量 ≤ 0.074 mm，压缩比 ≤ 0.03%）；估算口径与专利设计口径相差几个百分点 |
| Rosin / Nikon 处方 | 逐位对照扫描件无误 |
| 炫光交叉验证 | 正向通道与相机端鬼影（关闭自适应采样，8192 spp）的光晕/光源比：0.085 vs 0.078（后者仍随采样数上升）；颜色占比两者相差 ≤ 0.03 |

### 更正

早先报告的「f/2 时角落光斑面积只剩中心的 62%」是错的。那是按光斑面积阈值量出来的，被噪点和离轴像差（一侧亮边 + 彗差状拖尾）干扰，当时没有用独立预测核对。按真实追迹，当时的口径设计在角落只挡掉约 7–14% 的光瞳，猫眼很弱。现在口径按每个预设的「角落光瞳通过比例」（look.corner_pupil_fraction）设计：C 0.40 / E 0.55 / G 0.75。验收改用能量指标，并与真实追迹预测逐项对照。

0.4 的三处更正：
- **Panavision 长焦端口径**：0.3 误用了 Z1 下的光束高度，轴上光束被切掉 55%。现在改用 US 6,122,111 权利要求 35 的各面最大通光直径（镜片实物尺寸）。
- **统一焦段缩放**：0.3 没有同时缩放最近对焦距离，Panavision 短焦端放大到 100 mm 时会对焦到镜头里面。
- **曝光基准**：光阑后有柱面的镜头，近轴出瞳估计偏大，Panavision 两个预设因此暗了 0.39 档。现在 Build 时实测并归一化。

### 与原方案文档不同的三点发现

1. **「焦外高宽比 ≈ 压缩比」只是近似。** 这支「靠改变柱面组间距对焦」的镜头，背景焦外解挤压后约 1.5，前景焦外约 3.1。渲染与真实追迹吻合到 1% 以内，属于该结构本身的性质。`optics.bokeh_aspect()` 可以算任意对焦/物距组合。
2. **Cycles 薄膜在 IOR < 1 时不正确。** 同一层 100 nm MgF₂：IOR 1.6 时透过率 0.991（正确），IOR 1/1.6 时只有 0.76。所以界面法改为 IOR 恒 ≥ 1，玻璃→空气面的法线翻到像方。物理上与「法线朝 +Z、IOR = n后/n前」等价。
3. **鬼影/几何模式必须关闭自适应采样。** 这类像素绝大多数采样为黑、偶尔一次极亮，自适应采样会提前停掉，实测鬼影被低估约 8 倍。插件在这两种模式下自动关闭。

## 9. 已知限制

- Analytic 模式直射光路不计菲涅耳透过损失（曝光按轴上透过率归一）；开鬼影时按镀膜计。Geometry 模式含镀膜损失，但 Cycles 薄膜只有单层，宽带膜在 Geometry 模式里按单层近似。
- 炫光：两次反射的序列鬼影 + 衍射星芒 + 杂光；不含镜筒内壁的杂散光、鬼影本身的光圈衍射振铃。
- 发腮补偿是反向旋转柱面对的一阶等效（一块马鞍面补偿板），不含两片分开、各自转动的柱面之间的高阶交叉项。补偿板在补偿关闭时是平行平板，多两个（镀膜的）反射面。
- 杂光的指数值是按镀膜年代的估计：公开资料里没有任何电影变形镜头的 ISO 9358 实测。
- Geometry 模式与 Analytic 在 Panavision 长焦预设上仍有全画面一致的约 0.3–0.4 px（270p）平移，原因未查明（单片球面镜复现不出，排查记录见 RESEARCH.md §5 第 14 条）；成片用 Analytic。
- 光阑轴向位置专利未给，取 P6–P7 间隔中点；变形器到主镜间隔 5 mm 是我方取值。
- 预设里的光圈数值取自 Panavision 公布的 T 值，但在模型里当作几何 f 值使用（同样数值的真实镜头会稍暗约 0.2 档）。
- Panavision 后置预设没有成品镜头规格；最近对焦取变焦镜专利的 F3（物面到第一面 133 mm，距像面 0.48 m）。内对焦行程与专利 TABLE 1 吻合：F3 21.686 / 21.684 mm，F2 11.221 / 11.099 mm（1%，两组位移比用 F2/F3 平均值 0.9088）。
- 老专利只给 n_d / V_d 的玻璃（Rosin、Nikon）用两项 Cauchy；Geometry 模式为单一 d 线折射率。
- 每个 .blend 只能有一支 BlenderLens 镜头。
- CPU / OSL 路径的波长随机数仍是哈希（OSL 相机只有两维随机数），也无法与 Blender 5.3 的色散材质共用路径波长（`bl.diagnose()` 会警告）；GPU 路径是分层的、并且共用。
- GPU 路径：Geometry 模式仍走 CPU；只在 Metal 上实测。
- Lateral in Post 没有纵向色差（焦外彩边）；需要它时用 Full。

## 10. 许可与来源

- 代码：GPL-3.0-or-later（见 `blenderlens/blender_manifest.toml`）。
- 镜头处方转录自公开的专利文献，来源与核对见各处方 JSON 的 `source` 字段和 `blenderlens/research/RESEARCH.md`。
- README 中的示例图使用 [Poly Haven](https://polyhaven.com) 的 CC0 模型与 HDRI。资产本身不在仓库里，示例场景脚本 `tools/real_scene.py` 需要自行下载到 `assets/`。
- Panavision 是 Panavision Inc. 的商标。本项目与 Panavision 无关；「C / E / G 系列风格」只表示参照其公布的规格设定观感参数。
