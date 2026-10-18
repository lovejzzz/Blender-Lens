> 这是动手实现**之前**的审校设计文档（Claude Docs 原件导出，rev 28）。其中部分判断后来被实测修正，以 `RESEARCH.md` 为准（见那里的「修正记录」）。

# Panavision 式变形镜头 · Blender 物理光学复刻（审校修订版）

2026-10-18

## 审校摘要

**结论：思路方向对，但有 4 处会直接导致渲染结果错误的硬伤，必须先改再动手。** 原文的整体框架（专利处方 → 矢高网格 → 胶合面 → 镜筒/光圈 → 传感器采集 → 高反弹渲染）是可行的工程路线，问题集中在公式、坐标符号和几处成因解释上。

四处硬伤：

1. **Cauchy 色散换算错了约 5000 倍。** 按原公式 N-BK7 得 B ≈ 8×10⁻⁷ μm²，正确值约 0.0042 μm²。照原文填，色散等于没开。
2. **镜片排布的 Z 坐标符号反了。** 原点在像面、物方在 +Z，从 S₁（最前）走到 Sₙ（最后）是向 −Z 走，应为 Z_{i+1} = Z_i − d_i。
3. **专利出处不成立。** US 2,890,622（W. Wallin，1954 年申请）确实转让给 Panavision，但它描述的是对焦联动的反向旋转柱面镜机构（Auto Panatar 消除发腮的原理），没有数值处方；US 5,650,880 未能核实与变形镜头相关。原文暗示能拿到 C-Series/Primo 的真实处方，这在公开资料里基本不存在。
4. **示例脚本只生成散点、没有面。** 无法渲染；中心点重复生成 128 次；厚度参数未使用；孔径超出半球时静默返回 0。

修订版的主要变化：修正公式与坐标；补上专利常见的「f=100 归一化」缩放步骤；把渲染拆成 **LuxCore（要炫光/鬼影）** 与 **Cycles 5.x OSL 自定义相机（要速度/无噪点）** 两条路线；重写脚本为完整可渲染的双面镜片；修正椭圆焦外、拉丝炫光、发腮效应三处成因解释；删去文末指向不存在的「交互式实验台」的悬空句。

## 问题清单

共 16 条，按严重度排序。「致命」= 照做会得到错误画面；「重要」= 结论或解释有误；「次要」= 表述不严谨。

| # | 严重度 | 位置 | 原文说法 | 修正 |
|---|---|---|---|---|
| 1 | 致命 | 一·阿贝数行 | B ≈ (n_d−1)/(V_d×10⁴) μm² | B ≈ (n_d−1)/(1.910·V_d) μm²；N-BK7 → 0.0042 μm² |
| 2 | 致命 | 一·厚度行 | Z_{i+1} = Z_i + d_i | 物方在 +Z、光向 −Z 走，应为 Z_{i+1} = Z_i − d_i；更稳的做法是从像面倒推（见第一节） |
| 3 | 致命 | 三·脚本 | 只建顶点不建面 | 重写：两个面 + 侧壁 + 材质槽 + 解析法线（见第三节） |
| 4 | 致命 | 二·第 6 步 | Cycles 可直接用毛玻璃中继 | Cycles 下毛玻璃方案是纯焦散路径，几乎无法收敛；Cycles 应改用 OSL 自定义相机 |
| 5 | 重要 | 一·专利 | US 2,890,622 / US 5,650,880 是 Panavision 处方 | 前者是 Panavision 的 1954 年机构专利（无数值处方），后者未核实；公开处方只能做「Panavision 风格」，不是复刻 |
| 6 | 重要 | 一·整表 | 专利数值直接用 | 专利多按 f = 100 或 f = 1 归一化，所有 R、d、D 须乘缩放系数 |
| 7 | 重要 | 一·孔径行 | 专利会给有效孔径 D | 多数专利不列 D，需用近轴边缘光线 + 主光线追迹估算 |
| 8 | 重要 | 二·第 3 步 | 微小空气缝会产生「全反射黑斑」 | 真正问题是重合面的自相交伪影；Cycles 统一用「单层界面 + 相对 IOR」法即可 |
| 9 | 重要 | 二·第 4 步 | 一字拉丝来自镀膜 | 拉丝形状来自柱面镜内反射，镀膜只决定颜色；MgF₂ 标准四分之一波厚约 100 nm，125–140 nm 属风格化取值 |
| 10 | 重要 | 二·第 6 步 | 双凸轮对焦能「复刻」发腮 | 混淆了两件事：焦平面分离（像散）应被消除；发腮是压缩率随对焦变化，由对焦结构决定 |
| 11 | 重要 | 四·第 1 条 | 椭圆焦外来自柱面镜像差 | 来自水平/垂直两向有效焦距不同；猫眼才来自镜筒遮挡 |
| 12 | 重要 | 二·第 1 步 | Alexa Mini LF 变形模式可用 | 2× 前组变形镜头（C 系列级）只覆盖 Super 35 变形画幅，LF 会严重暗角；LF 用 1.3×–1.8× |
| 13 | 次要 | 二·第 2 步 | 水平 = 弧矢面，垂直 = 子午面 | 子午/弧矢是相对视场点定义的；此处应称「水平/垂直主截面」 |
| 14 | 次要 | 二·第 2 步 | 256×256 网格 | 与脚本默认 128 矛盾；有解析法线后 128 环足够 |
| 15 | 次要 | 一·孔径行 | 用布尔裁切孔径 | 在高密度光学网格上做布尔会破坏法线；直接按孔径生成网格 |
| 16 | 次要 | 文末 | 「通过下方交互式光学实验台……」 | 实验台不存在，删除 |

## 一、专利选型与处方表解析

**目标应定为「Panavision 风格的前组变形镜头」，而不是逐片复刻某支 C-Series。** Panavision 的量产处方没有公开；能拿到的是同架构的公开专利（前组无焦柱面变形器 + 后组球面主镜）。经典结构不变：前组是水平方向的反伽利略望远镜（负柱面在前、正柱面在后），把水平视场扩大 2×；后组是双高斯类球面主镜。

选专利时的三条标准：

- 明确写出「front anamorphic attachment」或「afocal cylindrical」，并给出完整的逐面处方表
- 表中含 R、d、n_d、V_d；若有非球面系数（k、A4、A6…）要一并记下
- 记下表头的归一化焦距（常见 f = 100 或 f = 1）

### 坐标约定（全文统一）

- 原点 = 像面（传感器），物方在 **+Z**，光线沿 **−Z** 传播
- R > 0：曲率中心在像方（−Z 一侧）；R < 0：在物方；R = ∞：平面
- 表面世界坐标：z = z_vertex − sag(x, y)

### 字段映射

| 专利字段 | 符号 | Blender 映射 |
|---|---|---|
| 曲率半径 | R_x, R_y | 球面 R_x = R_y；柱面 R_x = R，R_y = ∞（只在水平主截面弯曲） |
| 厚度/间隔 | d_i | 从像面倒推：z_n = BFL，z_i = z_{i+1} + d_i（n 为最后一面） |
| 折射率 | n_d（587.6 nm） | 该面后方介质的 IOR |
| 阿贝数 | V_d | 换算 Cauchy 系数（公式见下） |
| 有效孔径 | D | 专利常缺；用近轴边缘光线 + 最大视场主光线追迹估算，再加 5–10% 余量 |
| 非球面系数 | k, A4, A6… | 加入矢高方程的二次曲面与多项式项 |

### 归一化缩放

若专利以 f = 100 列表，而目标是 50 mm 镜头：缩放系数 s = 50 / 100 = 0.5，所有 R、d、D、BFL 乘以 s；n_d、V_d 不变。

### Cauchy 色散换算（已校正）

Cauchy 模型 n(λ) = A + B / λ²。用 F 线（486.1 nm）与 C 线（656.3 nm）代入阿贝数定义，得：

```latex
B = \frac{n_d - 1}{V_d \left( \lambda_F^{-2} - \lambda_C^{-2} \right)} \approx \frac{n_d - 1}{1.910\, V_d}\ \mu\mathrm{m}^2, \qquad A = n_d - \frac{B}{\lambda_d^{2}}
```

| 玻璃 | n_d | V_d | B（μm²） | A |
|---|---|---|---|---|
| N-BK7（冕牌） | 1.5168 | 64.17 | 0.00422 | 1.5046 |
| SF11（重火石） | 1.7847 | 25.68 | 0.0160 | 1.7384 |

渲染器里填之前先确认单位：若面板要求 nm²，B 再乘 10⁶（N-BK7 ≈ 4220 nm²）。

## 二、六步构建与渲染流程

**先选路线，再动手：要炫光与鬼影走 LuxCore，要快、无噪点、可做动画走 Cycles OSL 相机。** 两条路线共用第 1–4 步的镜头几何，只在第 5–6 步分叉。

|  | 路线 A：LuxCore 毛玻璃中继 | 路线 B：Cycles OSL 自定义相机 |
|---|---|---|
| 光路方向 | 场景 → 镜头 → 毛玻璃 → 正交相机 | 传感器 → 镜头 → 场景（标准路径追踪） |
| 炫光/鬼影 | 自然产生 | 需另做（或在 OSL 中追踪反射） |
| 色散 | 光谱渲染，原生支持 | Cycles 为 RGB，需 R/G/B 三次渲染各用一组 IOR |
| 噪点/速度 | 慢，MLT 有斑块噪点，动画易闪 | 快，干净，适合动画 |
| 版本 | BlendLuxCore v2.11 支持 Blender 4.5 LTS 与 5.2 LTS | Blender 5.0+；GPU 仅限 OptiX |

### 1. 毫米级基准与传感器平面

- `Unit Scale = 0.001`、`Length = Millimeters`，相机 `Clip Start = 0.1 mm`
- 原点放传感器平面，尺寸按目标画幅：经典 35 mm 变形摄影机光窗 **22.0 × 18.6 mm**（SMPTE 59）；4:3 Super 35 数字传感器约 24.9 × 18.7 mm
- 2× 前组变形（C 系列级）只为 Super 35 变形画幅设计；Alexa Mini LF 等大画幅应选 1.3×–1.8× 的设计，否则四角严重暗角

### 2. 用矢高方程生成曲面

完整的双二次曲面矢高方程（k 为二次曲面常数，球面/柱面 k = 0）：

```latex
z(x,y) = \frac{c_x x^2 + c_y y^2}{1 + \sqrt{1 - (1+k_x) c_x^2 x^2 - (1+k_y) c_y^2 y^2}} + \sum_i A_{2i}\, r^{2i}
```

- 柱面镜：c_y = 0，只在水平主截面弯曲
- 不用 UV Sphere + 细分；直接按方程生成网格，孔径在生成时裁好，不用布尔
- **关键改进：写入解析法线。** 平滑着色用的是插值法线，与真实曲面法线有误差，折射会把误差放大。脚本按 ∂z/∂x、∂z/∂y 算出精确法线写成 custom normals（第三节已实现）

### 3. 胶合面与介质

- **路线 A（LuxCore）**：体积堆栈。每个界面设定 exterior/interior volume；胶合面只留单层网格，exterior = Glass_A、interior = Glass_B，并统一法线方向
- **路线 B（Cycles）：界面法，空气面与胶合面同一套规则。** 每个光学面都建成单层开放曲面（不做封闭实体），法线朝物方（+Z），IOR = n_后 / n_前。空气 → 玻璃取 n，玻璃 → 空气取 1/n，胶合面取 n_B / n_A。Cycles 背面入射自动取倒数，所以反向鬼影光路也成立
- 两面不要重合也不要留 0.001 mm 缝：问题是自相交伪影，不是全反射

### 4. 镜筒、光圈与镀膜

- 每片侧壁单独材质槽：纯黑漫射（albedo 0）；外套不透光镜筒，内壁加消光环
- 光阑放在专利标定的 STOP 面；9 或 11 片叶片的多边形孔洞，缩放孔径改 T 值
- 镀膜（路线 A）：**所有**空气-玻璃面都要镀，否则未镀面每面约 4% 反射，鬼影过强。MgF₂（n = 1.38）的物理标准厚度 ≈ **100 nm**（550 nm 处四分之一波）。原文的 125–140 nm 把最低反射推到 690–770 nm，残余反射偏蓝，这是**风格化**取值，可以用，但要知道它不是标准镀膜
- 一字拉丝的**形状**来自柱面镜之间的内反射，镀膜只决定颜色

### 5. 采集相机

- **路线 A**：传感器平面赋 `Matte Translucent`（反射 0、透射 1）；其后 −5 mm 放正交相机，朝 +Z，`Orthographic Scale` = 传感器宽度，渲染分辨率宽高比 = 传感器宽高比，绕 Z 轴转 180° 正立画面
- **路线 B**：相机 `Lens Type = Custom`，OSL 着色器以传感器坐标为输入，从传感器点向后组出瞳采样发射光线，让 Cycles 正常穿过镜头几何。若不需要鬼影，也可在 OSL 里直接按处方逐面解析追迹（最快、无几何噪点）

### 6. 渲染参数与对焦

- **路线 A**：`Bidirectional` + `Metropolis`。毛玻璃把镜头后的光路变成漫射顶点，BiDir 才能连接；MLT 适合静帧，动画用 Sobol 采样器以免闪烁
- 反弹深度：直穿光路需 2 × 镜片数 + 1 次镜面事件，每条鬼影再 +2。13 片镜头直穿约 27 次，`Max Specular Depth` / `Total Depth` 设 **48–64** 是对的
- **路线 B**：Light Paths 中 `Transmission` 与 `Total` ≥ 64，`Filter Glossy = 0`
- 对焦：用 Drivers 绑定后组移动量 Δz₁ 与柱面组间距 Δz₂，让水平与垂直焦面重合（**消除**像散，这是必需的）。发腮效应另见第四节

## 三、重写后的 Python 建镜片脚本

**新脚本生成可直接渲染的完整镜片：两个光学面、侧壁、两个材质槽（玻璃/黑边）和解析法线，并能从处方表一次排好整支镜头。** 几何核心（矢高、法线、拓扑、Z 排布）已在 Blender 外用单元测试验证：R = 50 球面在 r = 10 处的法线倾角算得 11.537°，与 asin(10/50) 一致。

相对原脚本的改动：

- 生成面而不只是点；中心只建 1 个顶点（原脚本重复 128 次）
- 孔径超出曲面有效区时报错，不再静默返回 0
- 写入解析 custom normals，消除插值法线误差
- 支持二次曲面常数 k；从像面倒推排布，支持归一化缩放
- `build_interface()` 生成单层界面，供 Cycles 界面法使用

```python
import bpy, math

INF = float("inf")

def curv(R):
    return 0.0 if math.isinf(R) else 1.0 / R

def sag(x, y, cx, cy, kx=0.0, ky=0.0):
    q = 1.0 - (1+kx)*cx*cx*x*x - (1+ky)*cy*cy*y*y
    if q <= 0.0:
        raise ValueError(f"孔径超出曲面有效区: ({x:.2f}, {y:.2f})")
    return (cx*x*x + cy*y*y) / (1.0 + math.sqrt(q))

def sag_grad(x, y, cx, cy, kx=0.0, ky=0.0, h=1e-5):
    gx = (sag(x+h, y, cx, cy, kx, ky) - sag(x-h, y, cx, cy, kx, ky)) / (2*h)
    gy = (sag(x, y+h, cx, cy, kx, ky) - sag(x, y-h, cx, cy, kx, ky)) / (2*h)
    return gx, gy

def surface_grid(Rx, Ry, z0, D, rings, sectors, k=(0.0, 0.0)):
    """极坐标网格：1 个中心点 + rings*sectors；法线朝 +Z（物方）。"""
    cx, cy = curv(Rx), curv(Ry)
    V, N = [], []
    def add(x, y):
        gx, gy = sag_grad(x, y, cx, cy, *k)
        L = math.sqrt(gx*gx + gy*gy + 1.0)
        V.append((x, y, z0 - sag(x, y, cx, cy, *k)))
        N.append((gx/L, gy/L, 1.0/L))
    add(0.0, 0.0)
    for i in range(1, rings + 1):
        r = (D / 2) * i / rings
        for j in range(sectors):
            t = 2 * math.pi * j / sectors
            add(r * math.cos(t), r * math.sin(t))
    return V, N

def surface_faces(rings, sectors, off=0, flip=False):
    F = []
    for j in range(sectors):
        F.append((0, 1 + j, 1 + (j+1) % sectors))
    for i in range(1, rings):
        b0, b1 = 1 + (i-1)*sectors, 1 + i*sectors
        for j in range(sectors):
            j2 = (j+1) % sectors
            F.append((b0+j, b1+j, b1+j2, b0+j2))
    return [tuple(off + v for v in (f[::-1] if flip else f)) for f in F]

def _mesh(name, V, F, N, mat_idx, mats):
    me = bpy.data.meshes.new(name)
    me.from_pydata(V, [], F)
    for m in mats:
        me.materials.append(m)
    for p, mi in zip(me.polygons, mat_idx):
        p.material_index = mi
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices(N)   # Blender 4.1+
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    return ob

def build_element(name, s1, s2, z1, z2, D, mat_glass, mat_black,
                  rings=128, sectors=192):
    """封闭实体镜片（LuxCore 路线）。s1/s2 = dict(Rx, Ry, k=(kx,ky))。"""
    V1, N1 = surface_grid(s1["Rx"], s1["Ry"], z1, D, rings, sectors, s1.get("k", (0, 0)))
    V2, N2 = surface_grid(s2["Rx"], s2["Ry"], z2, D, rings, sectors, s2.get("k", (0, 0)))
    n = len(V1)
    F = surface_faces(rings, sectors) + surface_faces(rings, sectors, off=n, flip=True)
    N = N1 + [(-a, -b, -c) for a, b, c in N2]
    V = V1 + V2
    # 侧壁：复制外圈顶点，法线径向朝外
    o1, o2 = n - sectors, 2*n - sectors
    w = len(V)
    for j in range(sectors):
        t = 2 * math.pi * j / sectors
        V += [V[o1 + j], V[o2 + j]]
        N += [(math.cos(t), math.sin(t), 0.0)] * 2
    for j in range(sectors):
        j2 = (j+1) % sectors
        F.append((w + 2*j, w + 2*j + 1, w + 2*j2 + 1, w + 2*j2))
    n_optical = 2 * (sectors + (rings-1)*sectors)
    mat_idx = [0]*n_optical + [1]*sectors
    return _mesh(name, V, F, N, mat_idx, [mat_glass, mat_black])

def build_interface(name, s, z, D, mat, rings=128, sectors=192):
    """单层开放界面（Cycles 界面法）：法线朝物方，材质 IOR = n_后 / n_前。"""
    V, N = surface_grid(s["Rx"], s["Ry"], z, D, rings, sectors, s.get("k", (0, 0)))
    F = surface_faces(rings, sectors)
    return _mesh(name, V, F, N, [0]*len(F), [mat])

def surface_positions(rx, bfl, scale=1.0):
    """从像面倒推各面顶点 z：z_n = BFL，z_i = z_{i+1} + d_i。"""
    z = [0.0] * len(rx)
    z[-1] = bfl * scale
    for i in range(len(rx) - 2, -1, -1):
        z[i] = z[i+1] + rx[i]["d"] * scale
    return z

# —— 示例：前组发散柱面镜（数值为演示用，非真实处方）——
glass = bpy.data.materials.get("Glass") or bpy.data.materials.new("Glass")
black = bpy.data.materials.get("EdgeBlack") or bpy.data.materials.new("EdgeBlack")
build_element("Front_Cyl_L1",
              s1=dict(Rx=-120.0, Ry=INF), s2=dict(Rx=INF, Ry=INF),
              z1=91.5, z2=85.0, D=72.0, mat_glass=glass, mat_black=black)
```

用法要点：处方表整理成 `[{"Rx":…, "Ry":…, "d":…, "D":…}, …]`，先用 `surface_positions()` 求出每面 z，再两两配对调用 `build_element()`（LuxCore）或逐面调用 `build_interface()`（Cycles）。128 环 × 192 扇区在解析法线下足够；画面若出现同心环纹再加密。

## 四、解挤压与光学现象验证

**出图后水平放大 2×（或该设计的实际压缩比）即可解挤压；下面四个现象是检验镜头建对了的验收标准。** 在 Compositor 或 DaVinci Resolve 中设置像素宽高比即可。注意：压缩比要用近轴追迹实测，不要直接假定 2.0。

| 现象 | 正确成因 | 怎么验证 |
|---|---|---|
| 竖向椭圆焦外 | 前组只改变水平方向的有效焦距，两个方向的散焦光斑大小不同；解挤压后呈竖椭圆 | 背景放点光源，测光斑高宽比 ≈ 压缩比 |
| 边缘猫眼焦外 | 大视场光束被前后镜框/镜筒机械遮挡（口径蚀） | 收小光圈时猫眼应逐渐变圆 |
| 一字拉丝炫光 | 柱面镜面之间的内反射只在水平方向展开；颜色由镀膜决定 | 关闭镀膜，拉丝仍在但变成中性色 |
| 发腮（Mumps） | 对焦时前组偏离无焦状态，压缩比随物距变化；近摄时压缩比下降，解挤压后人脸偏宽 | 在 1 m 与无穷远各测一次压缩比 |

关于发腮，原文有一处概念混淆：**焦平面分离（像散）和发腮是两件事。** 双组联动对焦是为了让水平与垂直焦面重合，这一项必须做对，否则近摄画面整体发虚。发腮是放大率问题，它的程度取决于镜头的对焦结构：若想复刻老式镜头的发腮，就让柱面组以改变间距的方式对焦；若想要现代镜头的低发腮表现，就让对焦主要由后组完成。按 Panavision 官方历史，1958 年的 Auto Panatar「消除了早期 CinemaScope 镜头的畸变」并获得其第一个奥斯卡科技奖；其原理是 Wallin 的反向旋转柱面镜（US 2,890,622）。「发腮」（anamorphic mumps）的说法见 Panavision 的 J. Roudebush（1995）与 US 7,085,066 正文。

匹兹瓦尔场曲：中心合焦时四角焦面向镜头一侧弯曲，表现为四角柔化。用正交相机拍平面棋盘格，可直接看到合焦区域呈弧形分布。

## 五、实施结果（2026-10-18）

**路线 B 已全部落地，第四节四项现象均通过定量验收，并已打包为可安装的 Blender 扩展（blenderlens-0.1.0.zip，Blender 5.0+）。** 处方选用 US 2,944,464（Rosin，2:1 前组变形器，已对照扫描件逐位核对）加 US 4,234,242 实施例 1（Nikon f/2 双高斯，缩到 50 mm），写在一份 JSON 里作为唯一事实来源。整支镜头：水平 EFL 24.86 mm、垂直 50.0 mm，无穷远压缩比 2.011。

| 验收项 | 实测 | 对照 |
|---|---|---|
| 画面映射 | 两种相机模式误差 ≤ 0.74 px | Python 真实主光线追迹 |
| 竖向椭圆焦外 | 解挤压后高宽比 1.450 | 真实追迹预测 1.452 |
| 边缘猫眼 | f/2.3 相对 f/4.6 的渐晕比：角落 0.68、远角 0.64 | 真实追迹预测 0.68 / 0.66；收两档后消失 |
| 一字拉丝 | 32–44% 鬼影能量落在过光源的 ±6 px 水平带内（占画面 3.2%） | 无镀膜时形状不变、颜色变中性 |
| 发腮 | 压缩比：∞ 2.010，1 m 1.766 | 近轴预测 2.011 / 1.748 |

与本文方案不同、已在实现里改正的几处：

- **焦外高宽比并不等于压缩比。** 这支靠改变柱面组间距对焦的镜头，背景焦外约 1.5，前景焦外约 3.1。第四节「≈ 压缩比」应改为按真实追迹计算。
- **界面法的 IOR 要取 ≥ 1。** Cycles 5.3 的薄膜在 IOR < 1 时透过率错误（实测 0.76，应为 0.99）。玻璃→空气面改为法线朝像方、IOR = n_前 / n_后，物理上与第二节第 3 步等价。
- **柱面镜片用矩形口径。** 按圆片估算时，第 4 片边厚为 −7 mm；专利图 3 本来就是方形。球面镜片加了通光口径外的平台。
- **猫眼需要设计上的渐晕。** 口径按「角落视场只通过 60% 光瞳」估算；按无渐晕估算就不会出现猫眼，前片边厚也只剩 0.08 mm。
- **炫光不要靠 Cycles 路径追踪。** 点光源的鬼影在相机端极难收敛，自适应采样还会把它低估约 8 倍。实现里改为从光源正向追迹、逐对累加两次反射路径（薄膜按 R/G/B 计算），与相机端结果交叉验证一致。路线 A（LuxCore）因此不再必需。
- **Apple Silicon 只能用 CPU 渲染自定义相机。** 本机 M2 Max 上 720p、384 spp 实测：主渲染 3 分 50 秒，加两个光源的炫光通道约 1 分 45 秒。

炫光颜色和强度可以独立控制。颜色可选：物理镀膜色（默认）、灯光本色、琥珀色、自定义。非物理模式只重新着色，形状和亮度仍由追迹决定。强度是倍数，1 = 物理正确。

## 六、Panavision 镜头选项（2026-10-18）

**插件现在提供 4 个镜头选项。其中只有一个是真正的 Panavision 处方：US 7,085,066，后置 1.34× 变形组。** C / E / G / T 系列的量产处方从未公开；转让给 Panavision 的其他变形专利（US 7,148,947、US 3,428,398、US 2017/0192210）都没有逐面处方表。所以前三个选项是「同结构公开专利 + 按 Panavision 官网规格设定的光圈、最近对焦和炫光」。

| 选项 | 光学来源 | 焦距 · 光圈 · 最近对焦 | 压缩比 ∞ → 最近对焦 |
|---|---|---|---|
| C 系列风格（1968） | Rosin 1957 前置 2× + Nikon f/2 双高斯 | 垂直 50 mm · f/2.3 · 0.76 m | 2.011 → 1.687 |
| E 系列风格（1980 年代） | Isco 1990 前置 2.09×（两向分别矫正、凸轮对焦）+ 同一主镜 | 垂直 131 mm · f/2.8 · 1.14 m | 2.094 → 1.632 |
| G 系列风格（2007） | 同 E 系列风格光学，缩放 0.763 | 垂直 100 mm · f/3 · 0.91 m | 2.094 → 1.652 |
| Panavision 后置 1.34×（真实处方） | US 7,085,066 原样：变焦镜 + 8 片后置变形组，含 2 个非球面 | 水平 15.8 / 垂直 20.7 mm · f/3.66 · 1.0 m | 1.304 → 1.310 |

核对结果：

- **Panavision 处方**：62 行与非球面系数逐行对照扫描件无误；玻璃改用 Ohara 目录值（专利只给代码，且 FPL53 印作 438950，目录为 439950）。焦距 x 15.817 / y 20.627（专利 15.81 / 20.60），变焦镜单独 15.31（专利约 15.3），13.6 mm 像高处物方半视场 42.14°（专利 42.34°），整幅物方宽高比 2.401（专利 2.40）。
- **Isco 对焦**：用专利自己的主镜时，模型复现了对焦表全部 11 行，凸轮量误差 ≤ 0.074 mm，压缩比误差 ≤ 0.03%。
- **渲染映射**：4 个预设 × 两种相机模式，与真实追迹的最大误差 0.77 px。

与常见说法不同的实测结论：

- **Isco ****设计的发腮比 Rosin 少，但没有专利正文说的那么少****。** 同一放大率下（物距 = 20 倍垂直焦距）压缩比变化：Rosin −12.9%，Isco −9.9%。专利正文的「约 4%」是相对对焦表中段参考距离（约 6 m）的偏差，从无穷远到 2.5 m 实际变化约 10%。
- **后置变形组几乎没有发腮**（+0.5%），因为变形组在对焦组之后。
- **两向都有柱面的前置设计（Isco）会同时产生水平和竖直拉丝**；只有水平柱面的 Rosin 只有水平拉丝。
- **Isco 附加镜是长焦设计**，缩到 50 mm 会超出设计视场 2.6 倍、镜片边厚为负，所以 E / G 系列风格取 135 mm 和 100 mm。

**更正：** 第五节最初记录的「f/2 时角落光斑面积只剩中心的 62%」是错的。那是按面积阈值量的，受噪点和离轴像差干扰，当时的口径设计实际只挡掉 7–14% 的光瞳。现在猫眼强弱按每个预设单独设计（角落光瞳通过率 C 0.64 / E 0.84 / G 0.90 / Panavision 后置 0.68），验收改用能量指标并对照真实追迹预测。

## 待核实事项与下一步

**动手前最需要解决的是选定一份带完整处方表的前组变形专利。** 其余事项都可以在搭建过程中逐步验证。

- [x] 选定专利：找一份含逐面 R、d、n_d、V_d 的前组无焦柱面变形器专利，并配一份公开的双高斯主镜处方
- 核实 US 5,650,880 的实际内容（本次检索未能确认与变形镜头相关）
- 确认所用渲染器面板中 Cauchy B 的单位（μm² 还是 nm²）
- [x] 用近轴追迹实测压缩比与 BFL，再开始渲染
- [x] 先用单片柱面镜 + 点光源做 10 分钟测试渲染，确认法线方向与 IOR 设置无误，再搭整支镜头

### 来源

- [US 2,890,622 — ](https://patents.google.com/patent/US2890622A/en)[W. Wallin, Anamorphosing system（Panavision Inc.，](https://patents.google.com/patent/US2890622A/en)[Google Patents）](https://patents.google.com/patent/US2890622A/en)
- [Blender 5.0 手册：Cycles 自定义相机（OSL）](https://docs.blender.org/manual/zh-hans/5.0/render/cycles/osl/camera.html)
- [LuxCoreRender 下载页：BlendLuxCore v2.11 支持的 Blender 版本](https://luxcorerender.org/download/)
