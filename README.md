# BlenderLens — Panavision 风格变形镜头（Blender / Cycles）

用**公开专利的真实镜片处方 + 真实光线追迹**，在 Blender 里模拟变形（anamorphic）电影镜头。效果都由处方算出，不是滤镜：
- 竖椭圆焦外、边缘猫眼、发腮、水平畸变；
- 连续波长色散；
- 一字拉丝与鬼影炫光。

> **English summary.** BlenderLens is a Blender 5.x extension that renders anamorphic cinema lenses by ray-tracing real, published lens prescriptions (patents) surface by surface. The five presets cover:
> - three "Panavision-style" front anamorphics built from period patents;
> - the only public Panavision anamorphic prescription, US 7,085,066, at both zoom ends;
> - a modern 2× anamorphic by Iain Neil (Sirui patent CN 111562632 B) at 50 / 65 / 85 mm.
>
> Features:
> - Cycles OSL custom camera (CPU / OptiX), with catalog-accurate glass dispersion;
> - a GPU path for Apple Metal, about 57× faster than 0.4 on CPU in our benchmark;
> - a deterministic forward-traced flare pass, diffraction starbursts and veiling glare;
> - metre-scale scenes, native 16:9 gates, a Python API with multi-machine frame rendering.
>
> Install `dist/blenderlens-0.6.0.zip` via *Preferences → Get Extensions → Install from Disk*, then *N-panel → BlenderLens → Build Lens*. The documentation below is in Chinese; `blenderlens/HANDOFF.md` is the full user guide.

| C 系列风格 · 100 mm · f/2.3 | E 系列风格 · 100 mm · f/2.8 | G 系列风格 · 100 mm · f/3 |
|---|---|---|
| ![C](docs/images/real_c_100mm.jpg) | ![E](docs/images/real_e_100mm.jpg) | ![G](docs/images/real_g_100mm.jpg) |

| Neil 2× 现代变形 · 100 mm · f/3（0.6） | Panavision 后置 1.34× 长焦端 · 100 mm · f/4.1 |
|---|---|
| ![Neil](docs/images/real_neil_2x_85_100mm.jpg) | ![Panavision](docs/images/real_panavision_rear_134_tele_100mm.jpg) |

同一个真实 3D 场景（Poly Haven CC0 模型与 HDRI）、同一焦段、同一机位，768 spp + OIDN，带正向追迹炫光。后两张用 GPU 路径渲染（M2 Max，720p 每张约 3–5 分钟）。

> Panavision 的 C / E / G / T / Primo 变形系列量产处方从未公开。C / E / G 三个选项是「同年代、同结构的公开专利 + 按 Panavision 公布的规格设定观感参数」，**不是复刻**。唯一公开的真 Panavision 处方是 US 7,085,066（后置 1.34× 变形组），原样收录为两个预设。

---

## 0.6 新增

- **Neil 2× 现代变形三个预设**：
  - 中国授权本表格逐位读出；近轴焦距 49.998 / 65.028 / 85.012 mm（专利 50 / 65 / 85），后焦与印刷值相差 ≤ 0.1 mm。
  - G1b 内对焦，一个自由度同时对准两向。
- **目录色散**：Panavision 与 Neil 预设的 Ohara 玻璃改用目录公式（refractiveindex.info，CC0），400–700 nm 误差 ≤ 2.6×10⁻⁵。旧的两项近似在重火石紫端偏差达 8.7×10⁻³，紫边位置因此不准。
- **衍射星芒**：光圈多边形的夫琅禾费衍射，按 R/G/B 与两向数值孔径计算，变形镜头的星芒会被拉伸。
- **杂光（veiling glare）**：保能量的长尾散射，按镜头年代 / 镀膜取 ISO 9358 杂光指数典型值。
- **GPU 光线微分**：纹理过滤与 CPU / OSL 版一致。

## 1. 安装

要求：Blender **5.0 及以上**，不依赖第三方 Python 包。在 macOS / Apple M2 Max / Blender 5.3 上开发与实测，另有 M4 Max 的第三方复测；Windows / Linux 未实测。

1. 下载本仓库的 [`dist/blenderlens-0.6.0.zip`](dist/blenderlens-0.6.0.zip)。
2. Blender → Edit → Preferences → **Get Extensions** → 右上角 ⌄ → **Install from Disk…** → 选这个 zip。
   也可以用命令行：

   ```bash
   blender --command extension install-file -r user_default -e blenderlens-0.6.0.zip
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
cam.data.blenderlens.dispersion_mode = 'LATERAL_POST'

print(bl.lens_info(cam))                           # 焦距、压缩比、光窗、单位换算……
print(bl.exposure_vs_pinhole(cam))                 # 相对普通相机的曝光差（EV）
bl.render_still("//shot.exr", flare=True)          # 渲染 + 插件后期（横向色差、炫光）
```

命令行后台渲染整段动画（对焦、f 值关键帧在 GPU 上同样生效）：

```bash
blender -b shot.blend --python-expr "import blenderlens.api as bl; bl.enable_gpu(); bl.render_frames(frames=range(1, 145), out_dir='/shared/shot010')"
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
| **C 系列风格（1968）** | 前置 2×：[US 2,944,464](https://patents.google.com/patent/US2944464)（Rosin / Scanoptic 1957）+ 主镜 [US 4,234,242](https://patents.google.com/patent/US4234242A)（Nikon f/2 双高斯） | 垂直 50 mm · f/2.3 · 0.76 m | 2.011 → 1.687（−16%） | C Series 50 mm T2.3、最近对焦 76.2 cm、「明显的变形炫光」 |
| **E 系列风格 135 mm（1980 年代）** | 前置 2.09×：[US 5,184,880](https://patents.google.com/patent/US5184880A/en) 实施例 3（Isco 1990，两向分别矫正、凸轮对焦）+ 同一主镜 | 垂直 131 mm · f/2.8 · 1.14 m | 2.094 → 1.632（−22%） | E Series 135 mm T2.8、最近对焦 114.3 cm、「干净的蓝色水平炫光、无雾化」 |
| **G 系列风格 100 mm（2007）** | 同 E 系列风格光学，整体缩放 0.763 | 垂直 100 mm · f/3 · 0.91 m | 2.094 → 1.652（−21%） | G Series 100 mm T3、最近对焦 91.4 cm、「极佳的抗眩光」 |
| **Panavision 后置 1.34×（真实处方）** | [US 7,085,066](https://patents.google.com/patent/US7085066B2/en)（Iain A. Neil / Panavision 2004）原样：变焦镜短焦端 + 8 片后置变形组（3 片 Y 向柱面 + 5 片 X 向柱面），2 个非球面 | 水平 15.8 / 垂直 20.7 mm · f/3.66 · 0.48 m | 1.304 → 1.304（无发腮） | 专利实施例：16:9 画幅解挤压到 2.40:1 |
| **Neil 2× 现代变形 50 / 65 / 85 mm（真实处方，0.6）** | [CN 111562632 B](https://patents.google.com/patent/CN111562632B/en)（Iain A. Neil / 广东思锐光学 Sirui，2019）原样：同一前变形部分（X 柱面 3 片、Y 柱面 3 片，G1b 两片内对焦）+ 三个可换后组。**美国同族 US 11,287,618 的表格印刷错误多，不能用**（见 `blenderlens/research/RESEARCH.md`） | 垂直 50 / 65 / 85 mm · f/3 · 1.08 m | 2.06 → 1.93（−6.6%） | 专利实施例（不是 Panavision 镜头；设计者与 Panavision 后置组相同） |
| **Panavision 后置 1.34× 长焦端（真实处方）** | 同上，变焦镜取长焦端 Z4（变焦间隔取自 [US 6,122,111](https://patents.google.com/patent/US6122111A/en) TABLE 1，叠加到已核实的 Z1 表上） | 水平 50.9 / 垂直 66.4 mm · f/4.1（垂直）· 0.48 m | 1.305 → 1.305（无发腮） | 变焦镜单独 49.22 mm（专利 49.2） |

- **统一焦段**：Build 时填「Vertical Focal Length」，可把任意预设按比例缩放到同一垂直焦距（压缩比、f 值、像差形态不变；估算口径的预设按新视场重算口径）。Isco 附加镜（E/G）低于约 100 mm 会超出设计视场，所以几个预设能同时物理成立的共同焦段是 100 mm。
- 观感参数（光圈、最近对焦、镀膜膜厚、炫光强度、角落光瞳通过比例）写在每个处方 JSON 的 `look` 里。前四个搭建时写入相机，之后可以在面板里改；角落光瞳比例决定镜片口径（猫眼强弱），改了要重算口径。
- 猫眼强弱（f/N 时角落 95%/90% 视场的光瞳通过率，真实追迹）：C 0.64、E 0.84、G 0.90、Panavision 后置 0.68（专利原口径；专利称角落相对照度约 50%）。收两档后 C/E/G 恢复到 1.00。
- E/G 系列风格用的 Isco 附加镜是长焦设计：配 f=100 主镜时 S35 变形画幅垂直半视场只有约 4°。缩到 50 mm 会超出设计视场 2.6 倍、镜片边厚为负，所以这两个选项取 135 mm 和 100 mm。
- 解挤压比（输出分辨率宽度 = 高 × 传感器宽高比 × 解挤压比）：C 用名义 2.0（中心实测 2.010）；E/G 用光学实际值 2.09（按名义 2.0 会窄 4.5%）；Panavision 后置用 1.35，因为专利规定整幅 2.40:1 装进 16:9，真实追迹画面边缘张角比正好 2.401。压缩比都随视场变化（水平畸变）：C 从中心 2.01 到边缘 2.27，E 2.09→2.13，G 2.09→2.16，Panavision 后置 1.30→1.35。
- 发腮（同一放大率下比较，物距 = 20 × 垂直焦距）：C −12.9%、E/G −9.9%、Panavision 后置 0（内对焦）。Isco 设计比 Rosin 少约四分之一，但专利正文「约 4%」是相对其对焦表中段参考距离（约 6 m）的偏差，从无穷远到 2.5 m 实际变化约 10%。
- 后置变形组没有发腮：按 US 6,122,111 的内对焦（前两组按 1 : 0.909 移动），变焦镜输出的中间像位置不变，后置变形组看到的物像关系始终一样，两向同时合焦、压缩比不随对焦变化。这是它与前置设计最大的画面差别之一。

## 6. 改处方（单一事实来源）与继续研究

每个镜头选项只有一份处方：`blenderlens/prescriptions/*.json`（E/G 系列风格和 Panavision 后置预设由 `tools/build_presets.py` 从专利转录数据生成）。场景里的 `BL_prescription.json` 文本块是当前镜头处方的副本。往这个目录放新的 JSON，就会出现在 Build Lens 菜单里。

1. 改 JSON 里的 R / d / 玻璃 / 缩放。
2. `python3 tools/rebuild_apertures.py`：重新追迹估算各面口径和光阑位置，检查边缘是否相撞。
3. 在 Blender 里 **Build**，或 **Recompute Apertures**。网格、OSL 相机、对焦表都由 JSON 重新生成，不要手改网格或 OSL。

研究资料随扩展分发在 `blenderlens/research/`：
- `RESEARCH.md`：查过的专利、各预设的数据来源与逐位核对、我方假设、修正记录、未完成的方向；
- 专利扫描件与全文、玻璃色散数据；
- 处方生成器与全部测试。

## 7. 测试

```bash
python3 tests/test_optics.py
```

```bash
tests/run_all.sh
```

| 测试 | 内容 |
|---|---|
| `tests/test_optics.py` | Cauchy、解析梯度、近轴对照专利、真实 vs 近轴、对焦求解 |
| `tests/test_flare.py` | 炫光通道：能量守恒、镀膜强度与颜色、一字拉丝集中度 |
| `tests/test_pupil.py` | 出瞳采样表覆盖所有能穿过镜头的光线（随机视场 × 波长 × 对焦距离，无偏的前提）；光谱表白平衡 |
| `tests/bl_units.py` | 毫米场景与米制场景渲染一致；GPU 非 OptiX 自动回退 CPU；稳定 API；画幅 |
| `tests/bl_sampling.py` | 出瞳重要性采样开 / 关：各区域亮度一致（无偏），同 spp 噪点对比 |
| `tests/bl_convergence.py` | 64 → 256 spp 的噪点下降（理想 2×）：色散开 / 关 × 自适应采样开 / 关 |
| `tests/test_presets.py` | Isco 凸轮对焦复现专利对焦表（11 行，≤ 0.074 mm、≤ 0.03%）；Panavision 焦距与非球面；所有预设边缘不相撞 |
| `tests/bl_presets_smoke.py` | 每个预设在 Blender 里搭建、网格、法线、对焦同步 |
| `tests/bl_smoke.py` | 插件注册、搭建、网格合法、法线方向、对焦同步 |
| `tests/bl_verify_mapping.py` | 渲染刻度靶：两种模式的画面位置与 Python 真实追迹对比（Analytic < 1 px，Geometry < 1.5 px）、方位正确 |
| `tests/bl_acceptance.py` | 文档第四节四项现象的定量验收 |
| `tests/test_gpu_lenspack.py` | GPU 内核头文件生成（所有预设、字面量合法）；横向色差映射（d 线残差、轴上为零、白色守恒） |
| `tests/bl_gpu.py` | GPU 路径对 CPU / OSL：时间、噪点、亮度、整体位移、对焦关键帧、Geometry 回退 |
| `tests/bl_dispersion.py` | Lateral in Post 与 Full 的彩边位置对比、三种色散模式的噪点 |
| `tests/bl_frames.py` | 曝光差 API 对照实测；render_frames 的跳过、断点续渲、确定性、锁 |
| `tests/bl_percentage.py` | resolution_percentage ≠ 100% 时后期步骤尺寸正确 |
| `tests/bl_bench.py` | MessyRoom 合成基准（时间、三种噪点，结果追加进 bench.json） |
| `tests/install_test.sh` | 在全新用户配置里从 zip 安装、渲染，并按 HANDOFF 的命令行用法跑 GPU 帧（模拟另一台机器） |

## 8. 验证记录（2026-10-18，Blender 5.3 beta，M2 Max）

| 项目 | 结果 |
|---|---|
| 画面映射 | 3 m 刻度靶，5 个预设 × Analytic / Geometry 两种模式，对照 Python 真实主光线追迹：Analytic ≤ 0.8 px；Geometry ≤ 1.2 px（主要是全画面一致的亚像素平移，长焦预设最大）；方位正确（不镜像、不倒置） |
| Geometry 透过率 | 红通道 0.875，按 16 个镀膜面估算 0.866 |
| ① 竖向椭圆焦外 | C 系列风格，对焦 1.2 m、点光源在 20 m：解挤压后高宽比实测 1.450，真实追迹预测 1.452 |
| ② 边缘猫眼 | C 系列风格：f/2.3 相对 f/4.6 的渐晕比（能量指标，消掉视场相关的光斑大小与 cos⁴），水平边缘 0.79 / 角落 0.68 / 远角 0.64，真实追迹预测 0.77 / 0.68 / 0.66；收两档后猫眼消失 |
| ③ 一字拉丝 | 鬼影能量 32–44% 落在过光源的 ±6 px 水平带内（该带只占画面 3.2%）。颜色：100 nm 蓝紫，135 nm 蓝，无镀膜中性，形状不变 |
| ④ 发腮 | C 系列风格压缩比：无穷远实测 2.010（预测 2.011），1 m 实测 1.759（预测 1.752）；近摄解挤压后画面偏宽约 13% |
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

- Analytic 模式不计菲涅耳损失（透过率 1）；Geometry 模式含镀膜损失（16 个镀膜面合计 ≈ 0.87）。
- 炫光通道只算两次反射的序列鬼影（主导项），不含衍射星芒和镜筒杂散光；每帧单独计算，动画需要逐帧调用。
- Geometry 模式可以出鬼影，但点光源的鬼影在路径追踪里极难收敛，实际出图请用 Render with Flare。
- 光阑轴向位置专利未给，取 P6–P7 间隔中点；变形器到主镜间隔 5 mm 是我方取值。
- 预设里的光圈数值取自 Panavision 公布的 T 值，但在模型里当作几何 f 值使用（T 值含透过率损失，同样数值的真实镜头会稍亮一点，约 0.2 档）。
- Panavision 后置预设没有成品镜头规格；最近对焦取变焦镜专利的 F3（物面到第一面 133 mm，距像面 0.48 m）。内对焦行程与专利 TABLE 1 吻合：F3 21.686 / 21.684 mm，F2 11.221 / 11.099 mm（1%，两组位移比用 F2/F3 平均值 0.9088）。
- 色散用 Cauchy 近似（由 n_d、V_d 换算）；Geometry 模式为单一 d 线折射率。
- 每个 .blend 只能有一支 BlenderLens 镜头。
- OSL 相机只有两维随机数（给了出瞳），色散的波长用随机数与像素位置的哈希。
- GPU 路径只做 Analytic 直射光路：Geometry 模式和相机端鬼影仍走 CPU；GPU 上光线微分为 0（只影响纹理 mip 级别）；只在 Metal 上实测。
- Lateral in Post 没有纵向色差（焦外彩边）；需要它时用 Full。

## 10. 许可与来源

- 代码：GPL-3.0-or-later（见 `blenderlens/blender_manifest.toml`）。
- 镜头处方转录自美国专利（公有领域），来源与核对见各处方 JSON 的 `source` 字段和 `blenderlens/research/RESEARCH.md`。
- README 中的示例图使用 [Poly Haven](https://polyhaven.com) 的 CC0 模型与 HDRI。资产本身不在仓库里，示例场景脚本 `tools/real_scene.py` 需要自行下载到 `assets/`。
- Panavision 是 Panavision Inc. 的商标。本项目与 Panavision 无关；「C / E / G 系列风格」只表示参照其公布的规格设定观感参数。
