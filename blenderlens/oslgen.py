# 由处方生成 Cycles OSL 自定义相机（纯 Python 生成源码，不依赖 bpy）。
# mode 0 = 解析：在 OSL 里按处方逐面真实追迹（快、无几何噪点、可做三波长色散）
# mode 1 = 几何：只从传感器向出瞳发射光线，由 Cycles 穿过场景里的镜片网格（鬼影/炫光）
# 相机坐标：+Z 视线、+Y 上；镜头坐标经绕 Y 轴 180° 映射：cam = (−x, y, z)。
import math
try:
    from . import optics as O
except ImportError:
    import optics as O

def _exit_pupil_axis(S, z_stop, r_stop, axis):
    k = min(i for i, sf in enumerate(S) if sf["z"] < z_stop)          # 光阑后第一面
    sub = S[k:]
    u0 = 1e-3
    y = u0 * (z_stop - sub[0]["z"])
    y, nu, n = O._paraxial_core(sub, axis, y, u0)
    u = nu / n
    t = -y / u                                                        # 从最后一面沿 −Z 到出瞳（负 = 虚出瞳在其前方）
    return sub[-1]["z"] - t, r_stop * abs(u0 / u)

def exit_pupil(rx, S=None):
    """近轴出瞳：返回 (z_exp, r_exp, z_stop, r_stop)。光阑后有柱面时两截面出瞳不同 → 采样盘放在垂直截面出瞳平面，
    半径取能同时罩住水平截面出瞳（按传感器中心视锥换算到该平面）的较大值。"""
    S = S or O.layout(rx)
    z_stop, r_stop = O.stop_geometry(rx, S)
    zy, ry = _exit_pupil_axis(S, z_stop, r_stop, "Ry")
    zx, rx_ = _exit_pupil_axis(S, z_stop, r_stop, "Rx")
    return zy, max(ry, rx_ * zy / zx), z_stop, r_stop

def _arr(name, vals, fmt="{:.9g}", typ="float"):
    return f"  {typ} {name}[{len(vals)}] = {{ " + ", ".join(fmt.format(v) for v in vals) + " };\n"

def generate(rx, name="blenderlens_camera"):
    S = O.layout(rx)
    for sf in S:
        if sf["ap"] is None:
            raise ValueError(f"{sf['id']} 没有口径：先运行 tools/rebuild_apertures.py")
    z_exp, r_exp, z_stop, r_stop = exit_pupil(rx, S)
    NS = len(S)
    Z = [sf["z"] for sf in S]
    CX = [O.curv(sf["Rx"]) for sf in S]
    CY = [O.curv(sf["Ry"]) for sf in S]
    KX = [sf["kx"] for sf in S]
    KY = [sf["ky"] for sf in S]
    A_, B_ = zip(*[O.cauchy(sf["glass"]["nd"], sf["glass"].get("vd")) for sf in S])   # 该面之后的介质
    lim = [O.ap_limits(sf["ap"]) for sf in S]
    CLR = [l[0] for l in lim]; MSX = [l[1] for l in lim]; MSY = [l[2] for l in lim]
    AS = [[(sf.get("asph") or []) [m] if m < len(sf.get("asph") or []) else 0.0 for sf in S] for m in range(5)]
    G1 = [float(sf["move"][0]) for sf in S]; G2 = [float(sf["move"][1]) for sf in S]
    before = [1.0] + [sf["glass"]["nd"] for sf in S[:-1]]
    CO = [1 if (before[k] == 1.0) != (sf["glass"]["nd"] == 1.0) else 0 for k, sf in enumerate(S)]   # 空气-玻璃面（镀膜）
    AG = [k for k in range(NS) if CO[k]]                                                             # 鬼影反射候选面
    NPAIRS = len(AG) * (len(AG) - 1) // 2
    st_id = O.stop_group(rx)["stop"]["after_surface"]
    SG1, SG2 = next(sf["move"] for sf in S if sf["id"] == st_id)            # 光阑随它前面那一面所在的组移动
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    N0 = O.nominal_f_number(rx)
    sources = ", ".join(g["source"]["patent"].split(" — ")[0] for g in rx["groups"] if "source" in g)

    src = f"""// 自动生成：{rx['meta']['name']}
// 来源处方：{sources}
// 不要手改本文件——改 prescription JSON 后重新生成。

float sag_grad(float x, float y, float cx, float cy, float kx, float ky,
               float a4, float a6, float a8, float a10, float a12,
               output float gx, output float gy, output int ok)
{{
  float N = cx * x * x + cy * y * y;
  float Q = 1.0 - (1.0 + kx) * cx * cx * x * x - (1.0 + ky) * cy * cy * y * y;
  if (Q <= 0.0) {{ ok = 0; gx = 0; gy = 0; return 0.0; }}
  ok = 1;
  float Sq = sqrt(Q);
  float den = 1.0 + Sq;
  float dSx = -(1.0 + kx) * cx * cx * x / Sq;
  float dSy = -(1.0 + ky) * cy * cy * y / Sq;
  gx = (2.0 * cx * x * den - N * dSx) / (den * den);
  gy = (2.0 * cy * y * den - N * dSy) / (den * den);
  float s = N / den;
  float r2 = x * x + y * y;
  // 转动对称高次项：Σ A_{{2m}} r^{{2m}}，梯度 Σ 2m A_{{2m}} r^{{2m−2}} (x, y)
  float r4 = r2 * r2, r6 = r4 * r2, r8 = r6 * r2, r10 = r8 * r2;
  s += a4 * r4 + a6 * r6 + a8 * r8 + a10 * r10 + a12 * r10 * r2;
  float g = 4.0 * a4 * r2 + 6.0 * a6 * r4 + 8.0 * a8 * r6 + 10.0 * a10 * r8 + 12.0 * a12 * r10;
  gx += g * x; gy += g * y;
  return s;
}}

float fresnel_plain(float n1, float n2, float ci)
{{
  float st = n1 / n2 * sqrt(max(0.0, 1.0 - ci * ci));
  if (st >= 1.0) return 1.0;
  float ct = sqrt(1.0 - st * st);
  float rs = (n1 * ci - n2 * ct) / (n1 * ci + n2 * ct);
  float rp = (n2 * ci - n1 * ct) / (n2 * ci + n1 * ct);
  return 0.5 * (rs * rs + rp * rp);
}}

float airy(float r01, float r12, float cd)
{{
  return (r01 * r01 + r12 * r12 + 2.0 * r01 * r12 * cd) / (1.0 + r01 * r01 * r12 * r12 + 2.0 * r01 * r12 * cd);
}}

// 单层薄膜（入射介质 n0 | 膜 nf, 厚 d_nm | 出射介质 n2）的非偏振反射率，λ 单位 μm
float fresnel_film(float n0, float nf, float n2, float c0, float d_nm, float lam_um)
{{
  float s0 = sqrt(max(0.0, 1.0 - c0 * c0));
  float s1 = n0 * s0 / nf;
  float s2 = n0 * s0 / n2;
  if (s2 >= 1.0) return 1.0;
  if (s1 >= 1.0) return fresnel_plain(n0, n2, c0);
  float c1 = sqrt(1.0 - s1 * s1), c2 = sqrt(1.0 - s2 * s2);
  float cd = cos(4.0 * M_PI * nf * d_nm * c1 / (lam_um * 1000.0));
  float rs01 = (n0 * c0 - nf * c1) / (n0 * c0 + nf * c1), rs12 = (nf * c1 - n2 * c2) / (nf * c1 + n2 * c2);
  float rp01 = (nf * c0 - n0 * c1) / (nf * c0 + n0 * c1), rp12 = (n2 * c1 - nf * c2) / (n2 * c1 + nf * c2);
  return 0.5 * (airy(rs01, rs12, cd) + airy(rp01, rp12, cd));
}}

shader {name}(
  int mode = 0 [[ string widget = "mapper", string options = "Analytic:0|Geometry:1",
                  string help = "Analytic：OSL 内逐面追迹；Geometry：交给 Cycles 穿过场景里的镜片网格（鬼影/炫光）" ]],
  float f_number = {N0:.3g} [[ float min = {N0:.3g}, float max = 22.0, float sensitivity = 0.1,
                         string help = "光圈 f 值（以垂直截面的主镜计）" ]],
  int blades = 0 [[ int min = 0, int max = 16, string help = "光圈叶片数；0 = 圆形" ]],
  float blade_rotation = 0.0 [[ string help = "叶片旋转（度）" ]],
  float delta_unit = 0.0 [[ string help = "对焦：整体单元前移量 Δ1（mm），由插件按对焦距离写入" ]],
  float delta_front = 0.0 [[ string help = "对焦：变形器前组附加位移 Δ2（mm），由插件按对焦距离写入" ]],
  int dispersion = 1 [[ string widget = "checkBox", string help = "三波长色散（仅 Analytic）" ]],
  int exposure_compensate = 1 [[ string widget = "checkBox", string help = "收光圈时补偿曝光（关闭 = 物理 T 值变暗）" ]],
  float pupil_margin = 1.4 [[ float min = 1.0, float max = 3.0, string help = "出瞳采样盘相对近轴出瞳的放大倍数" ]],
  int ghosts = 0 [[ string widget = "checkBox", string help = "序列鬼影追迹（两次反射，炫光/一字拉丝；仅 Analytic）" ]],
  float ghost_fraction = 0.5 [[ float min = 0.05, float max = 0.95, string help = "走鬼影路径的采样比例" ]],
  float ghost_gain = 1.0 [[ float min = 0.0, float max = 1000.0, string help = "鬼影增益（1 = 物理正确）" ]],
  int coated = 1 [[ string widget = "checkBox", string help = "空气-玻璃面 MgF₂ 单层镀膜" ]],
  float coating_nm = 100.0 [[ float min = 0.0, float max = 400.0, string help = "膜厚（nm）；~100 = 550 nm 四分之一波" ]],
  float coating_ior = 1.38 [[ float min = 1.0, float max = 2.5 ]],
  output point position = 0.0,
  output vector direction = vector(0.0, 0.0, 1.0),
  output color throughput = 1.0)
{{
{_arr("Z", Z)}{_arr("CX", CX)}{_arr("CY", CY)}{_arr("KX", KX)}{_arr("KY", KY)}{_arr("NA", A_)}{_arr("NB", B_)}{_arr("CLR", CLR)}{_arr("MSX", MSX)}{_arr("MSY", MSY)}{_arr("AA4", AS[0])}{_arr("AA6", AS[1])}{_arr("AA8", AS[2])}{_arr("AA10", AS[3])}{_arr("AA12", AS[4])}{_arr("G1", G1)}{_arr("G2", G2)}{_arr("CO", CO, "{:d}", "int")}{_arr("AG", AG, "{:d}", "int")}
  float SENSOR_W = {W:.6g}, SENSOR_H = {H:.6g};
  float Z_STOP = {z_stop:.9g}, R_STOP = {r_stop:.9g}, Z_EXP = {z_exp:.9g}, R_EXP = {r_exp:.9g};
  float N0 = {N0:.6g};
  int NS = {NS}, NAG = {len(AG)}, NPAIRS = {NPAIRS};

  // 传感器点：镜头成倒像，再经镜头→相机坐标的 x 取反（绕 Y 转 180°），
  // 所以画面向右 = 传感器 +x、画面向上 = 传感器 −y，输出正立且不镜像。
  point ras = camera_shader_raster_position();
  float sx = (ras[0] - 0.5) * SENSOR_W;
  float sy = -(ras[1] - 0.5) * SENSOR_H;

  // 出瞳盘上均匀采样（同心映射）
  vector rnd = camera_shader_random_sample();
  float a = 2.0 * rnd[0] - 1.0, b = 2.0 * rnd[1] - 1.0, rr = 0.0, th = 0.0;
  if (a == 0.0 && b == 0.0) {{ rr = 0.0; th = 0.0; }}
  else if (fabs(a) > fabs(b)) {{ rr = a; th = M_PI / 4.0 * (b / a); }}
  else {{ rr = b; th = M_PI / 2.0 - M_PI / 4.0 * (a / b); }}
  float k_stop = N0 / f_number;
  float r_samp = R_EXP * k_stop * pupil_margin;
  float zexp = Z_EXP + {SG1} * delta_unit + {SG2} * delta_front;
  point p = point(sx, sy, 0.0);
  vector d = normalize(point(rr * cos(th) * r_samp, rr * sin(th) * r_samp, zexp) - p);

  float c2 = d[2] * d[2];
  float w = c2 * c2 * pupil_margin * pupil_margin;        // cos⁴θ；乘采样盘/出瞳面积比 → 轴上满光瞳 ≈ 1
  if (!exposure_compensate) w *= k_stop * k_stop;         // 采样盘随光圈缩放，物理变暗需显式乘面积比

  if (mode == 1) {{
    position = point(-p[0], p[1], p[2]);
    direction = vector(-d[0], d[1], d[2]);
    throughput = color(w);
    return;
  }}

  // 波长：用随机数与光栅位置混出第三个随机数（光栅位置含亚像素抖动）
  color chan = color(1.0);
  float lam = {O.LAMBDA_D:.7f};
  if (dispersion) {{
    float h = noise("hash", ras * 1000.0 + vector(rnd[0], rnd[1], 0.37) * 17.0);
    if (h < 1.0 / 3.0) {{ lam = 0.610; chan = color(3.0, 0.0, 0.0); }}
    else if (h < 2.0 / 3.0) {{ lam = 0.550; chan = color(0.0, 3.0, 0.0); }}
    else {{ lam = 0.465; chan = color(0.0, 0.0, 3.0); }}
  }}
  float il2 = 1.0 / (lam * lam);

  float r_stop = R_STOP * k_stop;
  float z_stop = Z_STOP + {SG1} * delta_unit + {SG2} * delta_front;
  float ca = cos(radians(blade_rotation)), sa = sin(radians(blade_rotation));

  // 鬼影：以 ghost_fraction 的概率在空气-玻璃面里均匀选一对 (ga < gb)，权重做无偏补偿
  int ga = -1, gb = -1;
  float wsel = 1.0;
  if (ghosts && NPAIRS > 0) {{
    float h1 = noise("hash", ras * 911.0 + vector(rnd[1], rnd[0], 0.71) * 23.0);
    if (h1 < ghost_fraction) {{
      float h2 = noise("hash", ras * 577.0 + vector(rnd[0], rnd[1], 0.13) * 41.0);
      int k = min((int)floor(h2 * NPAIRS), NPAIRS - 1);
      int u = 0;
      while (k >= NAG - 1 - u) {{ k -= NAG - 1 - u; u += 1; }}
      ga = AG[u]; gb = AG[u + 1 + k];
      wsel = NPAIRS / ghost_fraction * ghost_gain;
    }} else {{
      wsel = 1.0 / (1.0 - ghost_fraction);
    }}
  }}
  color T = color(1.0);
  float LR = 0.610, LG = 0.550, LB = 0.465;

  int i = NS - 1, dirn = 1, state = 0;      // dirn = 1：沿 +Z（面序号递减）；−1：沿 −Z
  for (int step = 0; step < 3 * NS + 4; ++step) {{
    if (i < 0 || i >= NS) break;
    float zi = Z[i] + G1[i] * delta_unit + G2[i] * delta_front;

    // 光阑：本段若穿过光阑平面则检查
    float ts = (z_stop - p[2]) / d[2], tz = (zi - p[2]) / d[2];
    if (ts > 0.0 && ts < tz) {{
      float qx = p[0] + ts * d[0], qy = p[1] + ts * d[1];
      if (blades >= 3) {{
        float bx = ca * qx + sa * qy, by = -sa * qx + ca * qy;
        float seg = 2.0 * M_PI / blades;
        float ang = atan2(by, bx);
        float m = ang - seg * floor(ang / seg) - 0.5 * seg;
        if (hypot(bx, by) * cos(m) > r_stop * cos(0.5 * seg)) {{ throughput = color(0.0); return; }}
      }} else if (qx * qx + qy * qy > r_stop * r_stop) {{ throughput = color(0.0); return; }}
    }}

    // 求交（牛顿）
    float t = tz;
    float gx = 0, gy = 0;
    int ok = 1;
    for (int it = 0; it < 12; ++it) {{
      float x = p[0] + t * d[0], y = p[1] + t * d[1];
      float sg = sag_grad(x, y, CX[i], CY[i], KX[i], KY[i], AA4[i], AA6[i], AA8[i], AA10[i], AA12[i], gx, gy, ok);
      if (!ok) {{ throughput = color(0.0); return; }}
      float g = p[2] + t * d[2] - zi + sg;
      float dg = d[2] + gx * d[0] + gy * d[1];
      float dt = g / dg;
      t -= dt;
      if (fabs(dt) < 1e-7) break;
    }}
    if (t <= 1e-6) {{ throughput = color(0.0); return; }}
    p = p + t * d;
    sag_grad(p[0], p[1], CX[i], CY[i], KX[i], KY[i], AA4[i], AA6[i], AA8[i], AA10[i], AA12[i], gx, gy, ok);
    if (!ok) {{ throughput = color(0.0); return; }}

    // 口径：圆形通光区 + x/y 遮罩（矩形镜片、切边、矩形视场光阑）；平台与镜筒都按挡光处理
    if (p[0] * p[0] + p[1] * p[1] > CLR[i] * CLR[i] || fabs(p[0]) > MSX[i] || fabs(p[1]) > MSY[i]) {{
      throughput = color(0.0); return;
    }}

    // 两侧介质：沿 +Z 从「面后」到「面前」，沿 −Z 反之
    float na_ = NA[i] + NB[i] * il2;
    float nb_ = (i > 0) ? NA[i - 1] + NB[i - 1] * il2 : 1.0;
    float n1 = dirn == 1 ? na_ : nb_;
    float n2 = dirn == 1 ? nb_ : na_;
    vector nrm = normalize(vector(gx, gy, 1.0));
    float c = -dot(nrm, d);
    if (c < 0.0) {{ nrm = -nrm; c = -c; }}

    color R = color(0.0);
    if (ghosts) {{
      if (CO[i] && coated) {{
        float n1R = dirn == 1 ? NA[i] + NB[i] / (LR * LR) : ((i > 0) ? NA[i - 1] + NB[i - 1] / (LR * LR) : 1.0);
        float n2R = dirn == 1 ? ((i > 0) ? NA[i - 1] + NB[i - 1] / (LR * LR) : 1.0) : NA[i] + NB[i] / (LR * LR);
        float n1B = dirn == 1 ? NA[i] + NB[i] / (LB * LB) : ((i > 0) ? NA[i - 1] + NB[i - 1] / (LB * LB) : 1.0);
        float n2B = dirn == 1 ? ((i > 0) ? NA[i - 1] + NB[i - 1] / (LB * LB) : 1.0) : NA[i] + NB[i] / (LB * LB);
        R = color(fresnel_film(n1R, coating_ior, n2R, c, coating_nm, LR),
                  fresnel_film(n1, coating_ior, n2, c, coating_nm, LG),
                  fresnel_film(n1B, coating_ior, n2B, c, coating_nm, LB));
      }} else {{
        R = color(fresnel_plain(n1, n2, c));
      }}
    }}

    int refl = (state == 0 && i == ga) || (state == 1 && i == gb);
    if (refl) {{
      d = normalize(d + 2.0 * c * nrm);
      T *= R;
      state += 1;
      dirn = -dirn;
    }} else {{
      float eta = n1 / n2;
      float kk = 1.0 - eta * eta * (1.0 - c * c);
      if (kk < 0.0) {{ throughput = color(0.0); return; }}
      d = normalize(eta * d + (eta * c - sqrt(kk)) * nrm);
      if (ghosts) T *= color(1.0) - R;
    }}
    i -= dirn;
  }}
  if (i != -1 || dirn != 1 || state != (ga >= 0 ? 2 : 0)) {{ throughput = color(0.0); return; }}

  position = point(-p[0], p[1], p[2]);
  direction = vector(-d[0], d[1], d[2]);
  throughput = color(w * wsel) * T * chan;
}}
"""
    return src
