# BlenderLens — Panavision 风格 2× 前组变形镜头（Blender / Cycles）

用公开专利处方 + 真实光线追迹，在 Blender 里搭一支「前组无焦柱面变形器 + 双高斯主镜」的 2× 变形镜头。
Panavision 的量产处方没有公开，这里是**同架构的公开专利组合**，不是逐片复刻。

| 部分 | 来源 | 核对 |
|---|---|---|
| 前组变形器 | US 2,944,464（S. Rosin / Scanoptic，1957），2:1 反伽利略柱面组 | 对照扫描件逐位核对；近轴 f = −74.73 / 149.87（专利 −74.74 / 149.88） |
| 主镜 | US 4,234,242 实施例 1（S. Nakamura / Nippon Kogaku，1978），f/2 双高斯 6 片 5 组 | 近轴 EFL 100.000、BFL 74.84（专利 74.8）；缩放到 50 mm |

整支镜头：水平 EFL 24.86 mm、垂直 50.0 mm，无穷远压缩比 **2.011**，两向焦面差 0.04 mm。

## 安装到任意机器（Blender 5.0+）

1. 拿到 `dist/blenderlens-0.1.0.zip`。
2. Blender → Edit → Preferences → Get Extensions → 右上角 ⌄ → **Install from Disk…** → 选这个 zip。
   命令行：`blender --command extension install-file -r user_default -e blenderlens-0.1.0.zip`
3. 3D 视图按 N → **BlenderLens** 标签 → **Build Anamorphic Lens**。

渲染引擎会被设为 Cycles，设备为 **CPU**：Cycles 自定义相机在 GPU 上只支持 OptiX（NVIDIA）。Apple Silicon / AMD 只能用 CPU。

## 使用

- **Mode**
  - **Analytic**：在 OSL 里按处方逐面真实追迹。快、干净，带 R/G/B 三波长色散。画面、焦外、猫眼、发腮、畸变都由它产生。
  - **Geometry**：相机只从传感器向出瞳发光线，由 Cycles 穿过场景里真实的镜片网格（界面法 + MgF₂ 镀膜 + 黑色镜框）。它是对 Analytic 的独立校验：画面位置两者吻合到 1 px 以内。另外它含菲涅耳损失，也能看到真实网格产生的反射。噪点更多，反弹深度设为 64。
- **Render with Flare**（面板按钮）：先渲染当前帧，再叠加**正向追迹的炫光层**。做法是从光源向前片发一张密集光线网格，对 120 对空气-玻璃面逐一追「两次反射」路径，镀膜按单层 MgF₂ 薄膜公式按 R/G/B 计算，结果确定、无噪点。只追迹用 **Mark Flare Source** 标记过的发光物体；没有标记时，自动选取发光强度 ≥ 2×10⁴ 的物体。一字拉丝、椭圆和杏仁形鬼影都由它产生。
- **炫光颜色 / 强度**（面板 Render with Flare 一栏）：
  - **Color**：Physical（由镀膜膜厚算出：100 nm 蓝紫，125–140 nm 蓝，无镀膜中性）、Light Color（取灯光本身的颜色）、Amber（琥珀色）、Custom（任选颜色）。
  - 非 Physical 模式只重新着色。拉丝和鬼影的形状、亮度分布仍由物理追迹决定，亮度严格不变。
  - **Intensity**：强度倍数，1 = 物理正确。
  - 想一次比较多种组合，可以运行 `tools/flare_variants.py`：主画面只渲染一次，之后只换颜色。
- **Ghosts / Flare**（复选框）：在 Analytic 相机里随机抽取鬼影路径。结果无偏，但对点光源收敛很慢，适合窗户、天空这类大面积亮源；点光源请用 Render with Flare。
- **MgF₂ Coating / Coating (nm)**：100 nm 是 550 nm 的四分之一波（标准），残余反射偏蓝紫；125–140 nm 是风格化取值，拉丝更蓝。
- **Focus Distance**：从像面量起，0 = 无穷远。双组联动：整体单元前移 Δ1 使垂直合焦，变形器前组再移 Δ2 使水平合焦。可以打关键帧。
- **f-Number / Blades**：光圈与叶片数（0 = 圆形）。
- 输出分辨率已按 2.0 解挤压（宽 = 高 × 22/18.6 × 2），直接得到 ≈ 2.37:1 的正常比例画面。

## 改处方（单一事实来源）

处方只在一个地方：`blenderlens/prescriptions/rosin_nikon_2x50.json`。场景里的 `BL_prescription.json` 文本块是它的副本。

1. 改 JSON 里的 R / d / 玻璃 / 缩放。
2. `python3 tools/rebuild_apertures.py`：重新追迹估算各面口径和光阑位置，检查边缘是否相撞。
3. 在 Blender 里 **Build**，或 **Recompute Apertures**。网格、OSL 相机、对焦表都由 JSON 重新生成，不要手改网格或 OSL。

## 测试

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
| `tests/bl_smoke.py` | 插件注册、搭建、网格合法、法线方向、对焦同步 |
| `tests/bl_verify_mapping.py` | 渲染刻度靶：两种模式的画面位置与 Python 真实追迹对比（< 1 px）、方位正确 |
| `tests/bl_acceptance.py` | 文档第四节四项现象的定量验收 |
| `tests/install_test.sh` | 在全新用户配置里从 zip 安装并渲染（模拟另一台机器） |

## 验证记录（2026-10-18，Blender 5.3 beta，M2 Max CPU）

| 项目 | 结果 |
|---|---|
| 画面映射 | 3 m 刻度靶，Analytic 与 Geometry 两种模式对照 Python 真实主光线追迹：竖线 ≤ 0.4 px、横线 ≤ 0.74 px；方位正确（不镜像、不倒置） |
| Geometry 透过率 | 红通道 0.875，按 16 个镀膜面估算 0.866 |
| ① 竖向椭圆焦外 | 对焦 1.2 m、点光源在 20 m：解挤压后高宽比实测 1.516，真实追迹预测 1.527 |
| ② 边缘猫眼 | 角落/中心光斑面积：f/2 时 0.62（被机械渐晕切掉），f/4 时 1.18（恢复） |
| ③ 一字拉丝 | 鬼影能量 32–44% 落在过光源的 ±6 px 水平带内（该带只占画面 3.2%）。颜色：100 nm 蓝紫，135 nm 蓝，无镀膜中性，形状不变 |
| ④ 发腮 | 压缩比：无穷远实测 2.010（预测 2.011），1 m 实测 1.759–1.766（两次运行，预测 1.748）；近摄解挤压后画面偏宽约 13% |
| 炫光交叉验证 | 正向通道与相机端鬼影（关闭自适应采样，8192 spp）的光晕/光源比：0.085 vs 0.078（后者仍随采样数上升）；颜色占比两者相差 ≤ 0.03 |

### 与原方案文档不同的三点发现

1. **「焦外高宽比 ≈ 压缩比」只是近似。** 这支「靠改变柱面组间距对焦」的镜头，背景焦外解挤压后约 1.5，前景焦外约 3.1。渲染与真实追迹吻合到 1% 以内，属于该结构本身的性质。`optics.bokeh_aspect()` 可以算任意对焦/物距组合。
2. **Cycles 薄膜在 IOR < 1 时不正确。** 同一层 100 nm MgF₂：IOR 1.6 时透过率 0.991（正确），IOR 1/1.6 时只有 0.76。所以界面法改为 IOR 恒 ≥ 1，玻璃→空气面的法线翻到像方。物理上与「法线朝 +Z、IOR = n后/n前」等价。
3. **鬼影/几何模式必须关闭自适应采样。** 这类像素绝大多数采样为黑、偶尔一次极亮，自适应采样会提前停掉，实测鬼影被低估约 8 倍。插件在这两种模式下自动关闭。

## 已知限制

- Analytic 模式不计菲涅耳损失（透过率 1）；Geometry 模式含镀膜损失（16 个镀膜面合计 ≈ 0.87）。
- 炫光通道只算两次反射的序列鬼影（主导项），不含衍射星芒和镜筒杂散光；每帧单独计算，动画需要逐帧调用。
- Geometry 模式可以出鬼影，但点光源的鬼影在路径追踪里极难收敛，实际出图请用 Render with Flare。
- 光阑轴向位置专利未给，取 P6–P7 间隔中点；变形器到主镜间隔 5 mm 是我方取值。
- 色散用 Cauchy 近似（由 n_d、V_d 换算）；Geometry 模式为单一 d 线折射率。
