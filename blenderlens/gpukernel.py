# GPU 路径（A-lite）：在 Metal 等不支持 OSL 的 GPU 上运行 BlenderLens 相机，不重新编译 Blender、不改 Blender.app。
#
# 原理（2026-10-18 在 M2 Max / Blender 5.3 实测）：
#   · Cycles 的 Metal 内核在运行时从源码编译，源码随 Blender 分发在 scripts/addons_core/cycles/source/kernel/；
#   · 没有 OSL 的设备上，kernel/camera/camera.h 的 camera_sample_custom() 直接返回 0（所以 Metal 上自定义相机是黑图）；
#   · Python 里调用 _cycles.init(<路径>, …) 可以让 Cycles 改读另一份源码。
# 做法：把整个 Cycles 插件目录拷到本扩展的用户目录，只改 camera.h 的那一个分支，让它调用生成的
#   kernel/camera/blenderlens_lens.h（处方常量 + 逐面追迹，与 OSL 相机的 Analytic 直射光路同一份数据、同一算法）。
# 每帧会变的量（对焦位移、f 值、叶片数）经 KernelCamera 里自定义相机用不到的字段传入，见 FIELD_MAP；
# 其余设置（色散、出瞳重要性采样、曝光补偿、叶片旋转、场景单位）编进头文件，改了会触发一次内核重编译（约 10 秒）。
# 版本检查：camera.h 中被替换的那段必须与 EXPECTED_ORIGINAL 逐字一致，否则拒绝启用并回退 CPU/OSL。
# 删除：remove_cache() 删掉拷贝目录即恢复原状（重启 Blender 后完全不留痕迹）。
import hashlib, json, math, os, shutil

try:
    from . import optics as O
    from . import oslgen as G
except ImportError:
    import optics as O
    import oslgen as G

PATCH_VERSION = "blenderlens-gpu-2"      # 2（0.8）：自定义相机分支多传一维随机数（time），波长分层采样
HEADER_NAME = "blenderlens_lens.h"

# 字段 → 新含义（只对 BlenderLens 自定义相机生效；普通相机不受影响）
FIELD_MAP = [
    # (Blender 相机属性, KernelCamera 字段, 编码, 含义)
    ("data.stereo.convergence_distance", "convergence_distance", "1000 + Δ1", "对焦位移 Δ1（mm），整体单元 / 前组"),
    ("data.fisheye_fov", "fisheye_fov", "16 + Δ2 / 5", "对焦位移 Δ2（mm），变形器前组 / 中间组"),
    ("data.dof.aperture_ratio", "inv_aperture_ratio", "1 / f 值", "光圈 f 值（内核里读到的就是 f 值）"),
    ("data.dof.aperture_blades", "blades", "叶片数", "光圈叶片数（0 = 圆形）"),
    ("data.dof.use_dof", "—", "True", "让 Blender 同步上面两个 dof 字段"),
    ("data.fisheye_lens", "fisheye_lens", "50 + 10000·φ", "发腮补偿板光焦度 φ（1/mm；0.8）"),
]

EXPECTED_ORIGINAL = '''#else
  (void)kg;
  (void)cam;
  (void)cam_motion;
  (void)raster;
  (void)rand_lens;
  (void)ray;
  (void)r_cache_miss;
  return zero_spectrum();
#endif
}'''

PATCHED = '''#else
#  ifdef BLENDERLENS_GPU_CAMERA
  /* BlenderLens GPU camera (PATCH_VERSION) */
  (void)kg;
  r_cache_miss = false;
  return blenderlens_camera_sample(cam, cam_motion, raster, rand_lens, -1.0f, ray);
#  else
  (void)kg;
  (void)cam;
  (void)cam_motion;
  (void)raster;
  (void)rand_lens;
  (void)ray;
  (void)r_cache_miss;
  return zero_spectrum();
#  endif
#endif
}'''.replace("PATCH_VERSION", PATCH_VERSION)

ANCHOR = "ccl_device_inline Spectrum camera_sample_custom("

# 第二处（0.8）：camera_sample() 里调用自定义相机的地方，改为直接调用并多传 time 随机数。
# 不开运动模糊时 time 这一维（与出瞳两维同属一个三维 Sobol 点）没人用，拿来采样波长 → 波长与出瞳点联合分层。
EXPECTED_CALL = """  if (kernel_data.cam.type == CAMERA_CUSTOM) {
    const ccl_global DecomposedTransform *cam_motion = kernel_data_array(camera_motion);
    return camera_sample_custom(
        kg, &kernel_data.cam, cam_motion, raster, lens_uv, ray, r_cache_miss);
  }"""
PATCHED_CALL = """  if (kernel_data.cam.type == CAMERA_CUSTOM) {
    const ccl_global DecomposedTransform *cam_motion = kernel_data_array(camera_motion);
#if defined(BLENDERLENS_GPU_CAMERA) && !defined(WITH_OSL)
    /* BlenderLens GPU camera (PATCH_VERSION): pass the Sobol time dimension for wavelength sampling */
    r_cache_miss = false;
    return blenderlens_camera_sample(&kernel_data.cam, cam_motion, raster, lens_uv, time, ray);
#else
    return camera_sample_custom(
        kg, &kernel_data.cam, cam_motion, raster, lens_uv, ray, r_cache_miss);
#endif
  }""".replace("PATCH_VERSION", PATCH_VERSION)


class GPUPathError(RuntimeError):
    pass


# ───────────────────────── 头文件生成（纯 Python）─────────────────────────

def _cf(v):
    """C 浮点字面量：必须带小数点或指数；无穷大（无遮罩）写成 1e30f。"""
    v = float(v)
    if not math.isfinite(v):
        return "1e30f" if v > 0 else "-1e30f"
    t = f"{v:.9g}"
    if "." not in t and "e" not in t and "n" not in t:
        t += ".0"
    return t + "f"

def _carr(name, vals, typ="float"):
    if typ == "float":
        body = ", ".join(_cf(v) for v in vals)
    else:
        body = ", ".join(str(int(v)) for v in vals)
    return f"ccl_inline_constant {typ} {name}[{len(vals)}] = {{ {body} }};\n"

def static_settings(dispersion=True, dispersion_strength=1.0, exposure_compensate=True, pupil_importance=True,
                    blade_rotation_deg=0.0, unit_scale=1.0, ghosts=False, ghost_fraction=0.5, ghost_gain=1.0,
                    coated=True, coating_nm=100.0, coating_ior=1.38, coating_type="SINGLE"):
    return dict(dispersion=int(bool(dispersion)), dispersion_strength=float(dispersion_strength),
                exposure_compensate=int(bool(exposure_compensate)), pupil_importance=int(bool(pupil_importance)),
                blade_rotation=math.radians(blade_rotation_deg), unit_scale=float(unit_scale),
                ghosts=int(bool(ghosts)), ghost_fraction=float(ghost_fraction), ghost_gain=float(ghost_gain),
                coated=int(bool(coated)), coating_nm=float(coating_nm), coating_ior=float(coating_ior),
                coating_type=1 if coating_type == "BROADBAND" else 0)

def generate_header(rx, settings=None):
    """处方 + 静态设置 → C++ 头文件（Cycles 内核语言，Metal / CUDA / HIP 通用）。"""
    st = settings or static_settings()
    D = G.lens_data(rx, importance=bool(st["pupil_importance"]))
    NS = D["NS"]
    sp = D["SPEC"]
    A = D["AS"]
    h = []
    h.append(f"/* BlenderLens GPU camera — 自动生成，不要手改。{rx['meta']['name']}\n"
             f" * 来源：{D['sources']}；{PATCH_VERSION} */\n")
    h.append("#define BLENDERLENS_GPU_CAMERA 1\n")
    h.append(f"#define BL_NS {NS}\n")
    for nm, key in (("BL_Z", "Z"), ("BL_CX", "CX"), ("BL_CY", "CY"), ("BL_KX", "KX"), ("BL_KY", "KY"), ("BL_NA", "A_"),
                    ("BL_NB", "B_"), ("BL_NC", "C_"), ("BL_NE", "E_"), ("BL_ND", "ND"), ("BL_CLR", "CLR"), ("BL_MSX", "MSX"), ("BL_MSY", "MSY"), ("BL_G1", "G1"), ("BL_G2", "G2")):
        h.append(_carr(nm, D[key]))
    h.append(_carr("BL_AT", D["AT"], "int"))
    for nm, key in (("BL_AC", "AC"), ("BL_AMX", "AMX"), ("BL_AMY", "AMY"), ("BL_KCX", "KCX"), ("BL_KCY", "KCY")):
        h.append(_carr(nm, D[key]))
    for m, nm in enumerate(("BL_A4", "BL_A6", "BL_A8", "BL_A10", "BL_A12")):
        h.append(_carr(nm, A[m]))
    for nm, arr in zip(("BL_PX0", "BL_PX1", "BL_PY0", "BL_PY1"), D["PB"]):
        h.append(_carr(nm, arr))
    h.append(_carr("BL_CO", D["CO"], "int"))
    h.append(_carr("BL_AG", D["AG"] if D["AG"] else [0], "int"))
    h.append(_carr("BL_SPC", sp["cdf"]))
    for c, nm in enumerate(("BL_SPR", "BL_SPG", "BL_SPB")):
        h.append(_carr(nm, sp["w"][c]))
    S0 = D["S"]
    efl_x = abs(O.paraxial_infinity(S0, "Rx")[0]); efl_y = abs(O.paraxial_infinity(S0, "Ry")[0])
    consts = dict(BL_EFL_X=efl_x, BL_EFL_Y=efl_y, BL_SENSOR_W=D["W"], BL_SENSOR_H=D["H"], BL_Z_STOP=D["z_stop"], BL_R_STOP=D["r_stop"],
                  BL_Z_EXP=D["z_exp"], BL_R_EXP=D["r_exp"], BL_N0=D["N0"], BL_EXPOSURE_NORM=1.0 / D["T0"],
                  BL_SG1=float(D["SG1"]), BL_SG2=float(D["SG2"]), BL_SP_L0=sp["l0"], BL_SP_DL=sp["dl"],
                  BL_LAMBDA_D=O.LAMBDA_D, BL_UNIT_SCALE=st["unit_scale"], BL_BLADE_ROT=st["blade_rotation"],
                  BL_DISP_STRENGTH=st["dispersion_strength"], BL_PUPIL_MARGIN=1.4,
                  BL_GHOST_FRACTION=st["ghost_fraction"], BL_GHOST_GAIN=st["ghost_gain"],
                  BL_COATING_NM=st["coating_nm"], BL_COATING_IOR=st["coating_ior"], BL_COAT_HIGH_N=O.COAT_HIGH_N)
    for k, v in consts.items():
        h.append(f"#define {k} ({_cf(v)})\n")
    ints = dict(BL_PNX=D["PNX"], BL_PNY=D["PNY"], BL_SPN=len(sp["cdf"]), BL_DISPERSION=st["dispersion"],
                BL_EXPOSURE_COMP=st["exposure_compensate"], BL_PUPIL_IMPORTANCE=st["pupil_importance"],
                BL_GHOSTS=st["ghosts"], BL_COATED=st["coated"], BL_COATING_TYPE=st.get("coating_type", 0), BL_NAG=len(D["AG"]), BL_NPAIRS=D["NPAIRS"])
    for k, v in ints.items():
        h.append(f"#define {k} {int(v)}\n")
    h.append(KERNEL_BODY)
    return "".join(h)


# 与 oslgen 的 Analytic 直射光路逐行对应（变量名保持一致，便于对照）
KERNEL_BODY = r'''
ccl_device_inline float bl_sag_grad(const float x, const float y, const int i, const float cx, const float cy,
                                    ccl_private float *gx, ccl_private float *gy, ccl_private int *ok)
{
  const float kx = BL_KX[i], ky = BL_KY[i];
  const float N = cx * x * x + cy * y * y;
  const float Q = 1.0f - (1.0f + kx) * cx * cx * x * x - (1.0f + ky) * cy * cy * y * y;
  if (Q <= 0.0f) {
    *ok = 0; *gx = 0.0f; *gy = 0.0f;
    return 0.0f;
  }
  *ok = 1;
  const float Sq = sqrtf(Q);
  const float den = 1.0f + Sq;
  const float dSx = -(1.0f + kx) * cx * cx * x / Sq;
  const float dSy = -(1.0f + ky) * cy * cy * y / Sq;
  float g_x = (2.0f * cx * x * den - N * dSx) / (den * den);
  float g_y = (2.0f * cy * y * den - N * dSy) / (den * den);
  float s = N / den;
  const float r2 = x * x + y * y;
  const float r4 = r2 * r2, r6 = r4 * r2, r8 = r6 * r2, r10 = r8 * r2;
  s += BL_A4[i] * r4 + BL_A6[i] * r6 + BL_A8[i] * r8 + BL_A10[i] * r10 + BL_A12[i] * r10 * r2;
  const float g = 4.0f * BL_A4[i] * r2 + 6.0f * BL_A6[i] * r4 + 8.0f * BL_A8[i] * r6 + 10.0f * BL_A10[i] * r8 +
                  12.0f * BL_A12[i] * r10;
  *gx = g_x + g * x;
  *gy = g_y + g * y;
  return s;
}

ccl_device_inline float bl_fresnel_plain(const float n1, const float n2, const float ci)
{
  const float st = n1 / n2 * sqrtf(fmaxf(0.0f, 1.0f - ci * ci));
  if (st >= 1.0f) {
    return 1.0f;
  }
  const float ct = sqrtf(1.0f - st * st);
  const float rs = (n1 * ci - n2 * ct) / (n1 * ci + n2 * ct);
  const float rp = (n2 * ci - n1 * ct) / (n2 * ci + n1 * ct);
  return 0.5f * (rs * rs + rp * rp);
}

/* 镀膜反射率（非偏振）：特征矩阵法，最多 3 层，与 OSL 的 coat_R / optics.stack_reflectance 相同 */
ccl_device_inline float bl_coat_R(const float n_in, const float n_out, const float c_in, const float lam_um,
                                  const float n_glass)
{
  const float s0 = sqrtf(fmaxf(0.0f, 1.0f - c_in * c_in));
  const float k = n_in * s0;
  if (k >= n_out) {
    return 1.0f;
  }
  float ln[3], ld[3];
  int nl = 1;
  ln[0] = BL_COATING_IOR;
  ld[0] = BL_COATING_NM;
  if (BL_COATING_TYPE == 1) {
    const float l0 = 4.0f * BL_COATING_IOR * BL_COATING_NM;
    const float n3 = clamp(BL_COATING_IOR * sqrtf(n_glass), 1.55f, 2.0f);
    ln[1] = BL_COAT_HIGH_N;
    ld[1] = l0 / (2.0f * BL_COAT_HIGH_N);
    ln[2] = n3;
    ld[2] = l0 / (4.0f * n3);
    nl = 3;
  }
  const bool from_air = n_in < n_out;
  const float cs_out = sqrtf(fmaxf(1e-12f, 1.0f - (k / n_out) * (k / n_out)));
  float R = 0.0f;
  for (int pol = 0; pol < 2; ++pol) {
    float A = 1.0f, B = 0.0f, C = 0.0f, D = 1.0f;
    for (int j = 0; j < nl; ++j) {
      const int jj = from_air ? j : nl - 1 - j;
      const float nj = ln[jj], dj = ld[jj];
      const float q = 1.0f - (k / nj) * (k / nj);
      if (q <= 0.0f) {
        return bl_fresnel_plain(n_in, n_out, c_in); /* 层内倏逝：退回无膜 */
      }
      const float cj = sqrtf(q);
      const float ej = pol == 0 ? nj * cj : nj / cj;
      const float dl = 2.0f * M_PI_F * nj * dj * cj / (lam_um * 1000.0f);
      const float cd = cosf(dl), sd = sinf(dl);
      const float A2 = A * cd - B * ej * sd, B2 = A * sd / ej + B * cd;
      const float C2 = C * cd + D * ej * sd, D2 = -C * sd / ej + D * cd;
      A = A2; B = B2; C = C2; D = D2;
    }
    const float e0 = pol == 0 ? n_in * c_in : n_in / fmaxf(c_in, 1e-12f);
    const float es = pol == 0 ? n_out * cs_out : n_out / cs_out;
    const float nr = e0 * A - D * es, ni = e0 * B * es - C;
    const float dr = e0 * A + D * es, di = e0 * B * es + C;
    R += 0.5f * (nr * nr + ni * ni) / (dr * dr + di * di);
  }
  return R;
}

ccl_device_inline float bl_n(const int k, const float il2)
{
  /* 第 k 面之后的介质在 il2 = 1/λ² 处的折射率（k < 0：物方空气） */
  return k < 0 ? 1.0f : BL_NA[k] + il2 * (BL_NB[k] + il2 * (BL_NC[k] + il2 * BL_NE[k]));
}

ccl_device_inline float bl_lerp(const float a, const float b, const float t)
{
  return a + (b - a) * t;
}

ccl_device_inline Spectrum blenderlens_camera_sample(ccl_constant KernelCamera *cam,
                                                     const ccl_global DecomposedTransform *cam_motion,
                                                     const float2 raster,
                                                     const float2 rand_lens,
                                                     const float rand_time,
                                                     ccl_private Ray *ray)
{
  /* 每帧参数（见 gpukernel.FIELD_MAP） */
  const float delta_unit = cam->convergence_distance - 1000.0f;
  const float delta_front = (cam->fisheye_fov - 16.0f) * 5.0f;
  const float f_number = fmaxf(cam->inv_aperture_ratio, BL_N0);
  const int blades = (int)(cam->blades + 0.5f);
  const float comp_phi = (cam->fisheye_lens - 50.0f) * 1e-4f;   /* 发腮补偿板光焦度 φ（1/mm） */

  /* 与 OSL 的 camera_shader_raster_position() 相同：rastertocamera 把像素映射到 [0,1]² */
  const ProjectionTransform rastertocamera = cam->rastertocamera;   /* Metal：取地址前先拷到本地（地址空间） */
  const float3 ras = transform_perspective(&rastertocamera, make_float3(raster.x, raster.y, 0.0f));
  const float sx = (ras.x - 0.5f) * BL_SENSOR_W;
  const float sy = -(ras.y - 0.5f) * BL_SENSOR_H;

  const float k_stop = BL_N0 / f_number;
  const float r_samp = BL_R_EXP * k_stop * BL_PUPIL_MARGIN;
  const float zexp = BL_Z_EXP + BL_SG1 * delta_unit + BL_SG2 * delta_front;
  float3 p = make_float3(sx, sy, 0.0f);

  float ex = 0.0f, ey = 0.0f, area_w = 1.0f;
  int use_box = 0;
  float bx0 = 0.0f, bx1 = 0.0f, by0 = 0.0f, by1 = 0.0f;
  if (BL_PUPIL_IMPORTANCE && !BL_GHOSTS) {      /* 鬼影路径会穿过盒外区域 → 开鬼影时用整盘 */
    const float fx = fabsf(sx) / (0.5f * BL_SENSOR_W) * (BL_PNX - 1);
    const float fy = fabsf(sy) / (0.5f * BL_SENSOR_H) * (BL_PNY - 1);
    const int ix = min((int)floorf(fx), BL_PNX - 2), iy = min((int)floorf(fy), BL_PNY - 2);
    const float tx = clamp(fx - ix, 0.0f, 1.0f), ty = clamp(fy - iy, 0.0f, 1.0f);
    const int k00 = ix * BL_PNY + iy, k10 = (ix + 1) * BL_PNY + iy, k01 = ix * BL_PNY + iy + 1,
              k11 = (ix + 1) * BL_PNY + iy + 1;
    bx0 = bl_lerp(bl_lerp(BL_PX0[k00], BL_PX0[k10], tx), bl_lerp(BL_PX0[k01], BL_PX0[k11], tx), ty);
    bx1 = bl_lerp(bl_lerp(BL_PX1[k00], BL_PX1[k10], tx), bl_lerp(BL_PX1[k01], BL_PX1[k11], tx), ty);
    by0 = bl_lerp(bl_lerp(BL_PY0[k00], BL_PY0[k10], tx), bl_lerp(BL_PY0[k01], BL_PY0[k11], tx), ty);
    by1 = bl_lerp(bl_lerp(BL_PY1[k00], BL_PY1[k10], tx), bl_lerp(BL_PY1[k01], BL_PY1[k11], tx), ty);
    if (sx < 0.0f) { const float t0 = bx0; bx0 = -bx1; bx1 = -t0; }
    if (sy < 0.0f) { const float t0 = by0; by0 = -by1; by1 = -t0; }
    bx0 = fmaxf(bx0, -r_samp); bx1 = fminf(bx1, r_samp); by0 = fmaxf(by0, -r_samp); by1 = fminf(by1, r_samp);
    if (bx1 <= bx0 || by1 <= by0) {
      return zero_spectrum();
    }
    use_box = (bx1 - bx0) * (by1 - by0) < M_PI_F * r_samp * r_samp;
  }
  if (use_box) {
    ex = bx0 + rand_lens.x * (bx1 - bx0);
    ey = by0 + rand_lens.y * (by1 - by0);
    if (ex * ex + ey * ey > r_samp * r_samp) {
      return zero_spectrum();
    }
    area_w = (bx1 - bx0) * (by1 - by0) / (M_PI_F * r_samp * r_samp);
  }
  else {
    /* 同心映射 */
    const float a = 2.0f * rand_lens.x - 1.0f, b = 2.0f * rand_lens.y - 1.0f;
    float rr = 0.0f, th = 0.0f;
    if (a == 0.0f && b == 0.0f) { rr = 0.0f; th = 0.0f; }
    else if (fabsf(a) > fabsf(b)) { rr = a; th = M_PI_4_F * (b / a); }
    else { rr = b; th = M_PI_2_F - M_PI_4_F * (a / b); }
    ex = rr * cosf(th) * r_samp;
    ey = rr * sinf(th) * r_samp;
  }
  float3 d = normalize(make_float3(ex, ey, zexp) - p);

  const float c2 = d.z * d.z;
  float w = c2 * c2 * BL_PUPIL_MARGIN * BL_PUPIL_MARGIN * area_w * BL_EXPOSURE_NORM;
  if (!BL_EXPOSURE_COMP) {
    w *= k_stop * k_stop;
  }

  /* 连续波长色散（与 OSL 相同的光谱表）。波长的随机数：不开运动模糊时用 Cycles 的 time 维（与出瞳两维同一个
   * 三维 Sobol 点 → 波长与出瞳点联合分层，色噪按 ~1/N 以上的速度下降）；开运动模糊时 time 已用于快门，
   * 退回与 OSL 相同的哈希（否则运动拖影会按波长分色） */
  float3 chan = make_float3(1.0f, 1.0f, 1.0f);
  float lam = BL_LAMBDA_D;
  if (BL_DISPERSION && BL_DISP_STRENGTH > 0.0f) {
    const float h = (rand_time >= 0.0f && cam->shuttertime == -1.0f) ?
                        rand_time :
                        hash_float3_to_float(make_float3(ras.x * 1000.0f + rand_lens.x * 17.0f,
                                                         ras.y * 1000.0f + rand_lens.y * 17.0f, 6.29f));
    int j = 0;
    while (j < BL_SPN - 1 && h > BL_SPC[j]) {
      j += 1;
    }
    const float c0 = j > 0 ? BL_SPC[j - 1] : 0.0f;
    const float fr = clamp((h - c0) / fmaxf(BL_SPC[j] - c0, 1e-9f), 0.0f, 1.0f);
    lam = BL_SP_L0 + (j + fr) * BL_SP_DL;
    chan = make_float3(BL_SPR[j], BL_SPG[j], BL_SPB[j]);
  }
  const float il2 = 1.0f / (lam * lam);       /* n(λ) = A + B/λ² + C/λ⁴ + E/λ⁶，按强度在 n_d 与之间插值 */

  const float r_stop = BL_R_STOP * k_stop;
  const float z_stop = BL_Z_STOP + BL_SG1 * delta_unit + BL_SG2 * delta_front;
  const float ca = cosf(BL_BLADE_ROT), sa = sinf(BL_BLADE_ROT);

  /* 鬼影：以 BL_GHOST_FRACTION 的概率在空气-玻璃面里均匀选一对 (ga < gb)，权重做无偏补偿（与 OSL 相同） */
  int ga = -1, gb = -1;
  float wsel = 1.0f;
  if (BL_GHOSTS && BL_NPAIRS > 0) {
    const float h1 = hash_float3_to_float(make_float3(ras.x * 911.0f + rand_lens.y * 23.0f, ras.y * 911.0f + rand_lens.x * 23.0f, 0.71f));
    if (h1 < BL_GHOST_FRACTION) {
      const float h2 = hash_float3_to_float(make_float3(ras.x * 577.0f + rand_lens.x * 41.0f, ras.y * 577.0f + rand_lens.y * 41.0f, 0.13f));
      int k = min((int)floorf(h2 * BL_NPAIRS), BL_NPAIRS - 1);
      int u = 0;
      while (k >= BL_NAG - 1 - u) {
        k -= BL_NAG - 1 - u;
        u += 1;
      }
      ga = BL_AG[u];
      gb = BL_AG[u + 1 + k];
      wsel = BL_NPAIRS / BL_GHOST_FRACTION * BL_GHOST_GAIN;
    }
    else {
      wsel = 1.0f / (1.0f - BL_GHOST_FRACTION);
    }
  }
  float3 T = make_float3(1.0f, 1.0f, 1.0f);
  const float LR = 0.610f, LG = 0.550f, LB = 0.465f;

  int i = BL_NS - 1, dirn = 1, state = 0; /* dirn = 1：沿 +Z（面序号递减）；−1：沿 −Z */
  for (int step = 0; step < 3 * BL_NS + 4; ++step) {
    if (i < 0 || i >= BL_NS) {
      break;
    }
    const float zi = BL_Z[i] + BL_G1[i] * delta_unit + BL_G2[i] * delta_front;

    /* 光阑 */
    const float ts = (z_stop - p.z) / d.z, tz = (zi - p.z) / d.z;
    if (ts > 0.0f && ts < tz) {
      const float qx = p.x + ts * d.x, qy = p.y + ts * d.y;
      if (blades >= 3) {
        const float bx = ca * qx + sa * qy, by = -sa * qx + ca * qy;
        const float seg = 2.0f * M_PI_F / blades;
        const float ang = atan2f(by, bx);
        const float m = ang - seg * floorf(ang / seg) - 0.5f * seg;
        if (sqrtf(bx * bx + by * by) * cosf(m) > r_stop * cosf(0.5f * seg)) {
          return zero_spectrum();
        }
      }
      else if (qx * qx + qy * qy > r_stop * r_stop) {
        return zero_spectrum();
      }
    }

    /* 求交：平面 / 球面 / 纯柱面解析（稳定二次式），其余牛顿 */
    float t = tz;
    float gx = 0.0f, gy = 0.0f;
    int ok = 1;
    if (BL_AT[i]) {
      const float x0 = p.x + tz * d.x, y0 = p.y + tz * d.y;
      const float c_ = BL_AC[i], mx = BL_AMX[i], my = BL_AMY[i];
      const float qa = c_ * (mx * d.x * d.x + my * d.y * d.y + d.z * d.z);
      const float qb = 2.0f * (c_ * (mx * x0 * d.x + my * y0 * d.y) + d.z);
      const float qc = c_ * (mx * x0 * x0 + my * y0 * y0);
      const float disc = qb * qb - 4.0f * qa * qc;
      if (disc < 0.0f) {
        return zero_spectrum();
      }
      const float den = qb + (qb >= 0.0f ? 1.0f : -1.0f) * sqrtf(disc);
      t = tz + (den != 0.0f ? -2.0f * qc / den : 0.0f);
    }
    const float cxi = BL_CX[i] + BL_KCX[i] * comp_phi, cyi = BL_CY[i] + BL_KCY[i] * comp_phi;
    for (int it = 0; it < (BL_AT[i] ? 0 : 12); ++it) {
      const float x = p.x + t * d.x, y = p.y + t * d.y;
      const float sg = bl_sag_grad(x, y, i, cxi, cyi, &gx, &gy, &ok);
      if (!ok) {
        return zero_spectrum();
      }
      const float g = p.z + t * d.z - zi + sg;
      const float dg = d.z + gx * d.x + gy * d.y;
      const float dt = g / dg;
      t -= dt;
      if (fabsf(dt) < 1e-7f) {
        break;
      }
    }
    if (t <= 1e-6f) {
      return zero_spectrum();
    }
    p = p + t * d;
    bl_sag_grad(p.x, p.y, i, cxi, cyi, &gx, &gy, &ok);
    if (!ok) {
      return zero_spectrum();
    }

    /* 口径与遮罩 */
    if (p.x * p.x + p.y * p.y > BL_CLR[i] * BL_CLR[i] || fabsf(p.x) > BL_MSX[i] || fabsf(p.y) > BL_MSY[i]) {
      return zero_spectrum();
    }

    /* 两侧介质：沿 +Z 从「面后」到「面前」，沿 −Z 反之 */
    const float na_ = BL_ND[i] + BL_DISP_STRENGTH * (bl_n(i, il2) - BL_ND[i]);
    const float nb_ = (i > 0) ? BL_ND[i - 1] + BL_DISP_STRENGTH * (bl_n(i - 1, il2) - BL_ND[i - 1]) : 1.0f;
    const float n1 = dirn == 1 ? na_ : nb_;
    const float n2 = dirn == 1 ? nb_ : na_;
    float3 nrm = normalize(make_float3(gx, gy, 1.0f));
    float c = -dot(nrm, d);
    if (c < 0.0f) {
      nrm = -nrm;
      c = -c;
    }

    float3 R = make_float3(0.0f, 0.0f, 0.0f);
    if (BL_GHOSTS) {
      if (BL_CO[i] && BL_COATED && BL_COATING_NM > 0.0f) {
        const float ng = fmaxf(BL_ND[i], i > 0 ? BL_ND[i - 1] : 1.0f);
        if (BL_DISPERSION && BL_DISP_STRENGTH > 0.0f) {
          const float r = bl_coat_R(n1, n2, c, lam, ng); /* 光谱采样：反射率按本条光线的波长 */
          R = make_float3(r, r, r);
        }
        else {
          const float iR = 1.0f / (LR * LR), iB = 1.0f / (LB * LB);
          const float naR = bl_n(i, iR), naB = bl_n(i, iB);
          const float nbR = (i > 0) ? bl_n(i - 1, iR) : 1.0f, nbB = (i > 0) ? bl_n(i - 1, iB) : 1.0f;
          const float n1R = dirn == 1 ? naR : nbR, n2R = dirn == 1 ? nbR : naR;
          const float n1B = dirn == 1 ? naB : nbB, n2B = dirn == 1 ? nbB : naB;
          R = make_float3(bl_coat_R(n1R, n2R, c, LR, ng), bl_coat_R(n1, n2, c, LG, ng), bl_coat_R(n1B, n2B, c, LB, ng));
        }
      }
      else {
        const float r = bl_fresnel_plain(n1, n2, c);
        R = make_float3(r, r, r);
      }
    }

    const bool refl = (state == 0 && i == ga) || (state == 1 && i == gb);
    if (refl) {
      d = normalize(d + 2.0f * c * nrm);
      T = T * R;
      state += 1;
      dirn = -dirn;
    }
    else {
      const float eta = n1 / n2;
      const float kk = 1.0f - eta * eta * (1.0f - c * c);
      if (kk < 0.0f) {
        return zero_spectrum();
      }
      d = normalize(eta * d + (eta * c - sqrtf(kk)) * nrm);
      if (BL_GHOSTS) {
        T = T * (make_float3(1.0f, 1.0f, 1.0f) - R);
      }
    }
    i -= dirn;
  }
  if (i != -1 || dirn != 1 || state != (ga >= 0 ? 2 : 0)) {
    return zero_spectrum();
  }
  (void)LG;
  w *= wsel;
  chan = chan * T;

  const float3 P = make_float3(-p.x, p.y, p.z) * BL_UNIT_SCALE;
  const float3 Dout = make_float3(-d.x, d.y, d.z);
  /* 光线微分（纹理过滤用）：相邻像素的方向差 ≈ 像素尺寸 / 焦距（两向分别；OSL 版由自动微分得到） */
  const float3 Dx = normalize(Dout + make_float3(BL_SENSOR_W / cam->width / BL_EFL_X, 0.0f, 0.0f));
  const float3 Dy = normalize(Dout + make_float3(0.0f, BL_SENSOR_H / cam->height / BL_EFL_Y, 0.0f));
  camera_sample_to_ray(cam,
                       cam_motion,
                       P,
                       Dout,
#  ifdef __RAY_DIFFERENTIALS__
                       P,
                       Dout,
                       P,
                       Dx,
                       P,
                       Dy,
#  endif
                       ray);
  return make_float3(w * chan.x, w * chan.y, w * chan.z);
}
'''

STUB_HEADER = "/* BlenderLens GPU camera: 尚未生成镜头（自定义相机在 GPU 上照旧返回 0）。 */\n"


# ───────────────────────── 拷贝 / 打补丁 / 启用（需要 bpy）─────────────────────────

def _bpy():
    import bpy
    return bpy

def cycles_addon_dir():
    import cycles
    return os.path.dirname(os.path.abspath(cycles.__file__))

def cache_root(sub="gpu_kernel"):
    """扩展自己的用户目录（安装为扩展时）；开发副本退回用户缓存目录。删除它即恢复原状。"""
    bpy = _bpy()
    pkg = __package__ or "blenderlens"
    try:
        return bpy.utils.extension_path_user(pkg, path=sub, create=True)
    except (ValueError, AttributeError, KeyError):
        import sys
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Caches")
        elif os.name == "nt":
            base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        else:
            base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        d = os.path.join(base, "blenderlens_" + sub)
        os.makedirs(d, exist_ok=True)
        return d

def _build_tag():
    bpy = _bpy()
    raw = f"{bpy.app.version_string}|{getattr(bpy.app, 'build_hash', b'').decode() if isinstance(getattr(bpy.app, 'build_hash', b''), bytes) else getattr(bpy.app, 'build_hash', '')}|{PATCH_VERSION}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

def patched_dir():
    return os.path.join(cache_root(), f"cycles_{_build_tag()}")

def check_compatible(src_dir=None):
    """返回 (ok, 原因)。camera.h 里要替换的那段必须与预期逐字一致。"""
    src_dir = src_dir or cycles_addon_dir()
    p = os.path.join(src_dir, "source", "kernel", "camera", "camera.h")
    if not os.path.exists(p):
        return False, f"找不到 Cycles 内核源码 {p}（这个 Blender 构建不带运行时编译的 GPU 内核源码）"
    s = open(p, encoding="utf-8").read()
    if s.count(EXPECTED_ORIGINAL) != 1 or s.count(ANCHOR) != 1 or s.count(EXPECTED_CALL) != 1:
        return False, "camera.h 与补丁预期不一致（Blender 版本变了）：GPU 路径不可用，使用 CPU / OSL"
    return True, "ok"

def prepare(force=False):
    """拷贝 Cycles 插件目录并打补丁（只做一次）。返回拷贝目录。"""
    dst = patched_dir()
    marker = os.path.join(dst, "BLENDERLENS_PATCHED")
    if os.path.exists(marker) and not force:
        return dst
    ok, why = check_compatible()
    if not ok:
        raise GPUPathError(why)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    root = os.path.dirname(dst)
    for old in os.listdir(root):                     # 旧版本补丁 / 旧 Blender 构建的拷贝（各几十 MB）
        if old.startswith("cycles_") and os.path.join(root, old) != dst and os.path.isdir(os.path.join(root, old)):
            shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    shutil.copytree(cycles_addon_dir(), dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    p = os.path.join(dst, "source", "kernel", "camera", "camera.h")
    s = open(p, encoding="utf-8").read()
    s = s.replace(EXPECTED_ORIGINAL, PATCHED)
    s = s.replace(EXPECTED_CALL, PATCHED_CALL)
    s = s.replace(ANCHOR, f'#include "kernel/camera/{HEADER_NAME}"\n\n' + ANCHOR)
    open(p, "w", encoding="utf-8").write(s)
    write_header(STUB_HEADER, dst)
    open(marker, "w").write(json.dumps({"patch": PATCH_VERSION, "source": cycles_addon_dir()}))
    return dst

def header_path(dst=None):
    return os.path.join(dst or patched_dir(), "source", "kernel", "camera", HEADER_NAME)

def write_header(text, dst=None):
    """内容没变就不写（避免触发 Metal 重新编译）。返回是否改写。"""
    p = header_path(dst)
    if os.path.exists(p) and open(p, encoding="utf-8").read() == text:
        return False
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return True

_STATE = {"active": False, "reason": "未启用", "header_hash": None}

def is_active():
    return _STATE["active"]

def status():
    return dict(_STATE)

def enable():
    """让 Cycles 改读打过补丁的内核源码。必须在本进程第一次用 GPU 渲染之前调用（命令行 -b 渲染时放在脚本开头）。"""
    bpy = _bpy()
    try:
        dst = prepare()
    except GPUPathError as e:
        _STATE.update(active=False, reason=str(e))
        print("BlenderLens GPU:", e)
        return False
    import _cycles
    user_path = os.path.dirname(os.path.abspath(bpy.utils.user_resource('CONFIG', path='')))
    _cycles.init(dst, user_path, bpy.app.background)
    _STATE.update(active=True, reason="已启用（Cycles 读取 " + dst + "）")
    return True

def disable():
    """恢复 Cycles 读取原始源码（本进程之后新建的渲染生效；最稳妥是重启 Blender）。"""
    bpy = _bpy()
    import _cycles
    user_path = os.path.dirname(os.path.abspath(bpy.utils.user_resource('CONFIG', path='')))
    _cycles.init(cycles_addon_dir(), user_path, bpy.app.background)
    _STATE.update(active=False, reason="已停用")

def remove_cache():
    """删除所有拷贝的内核源码（一键恢复原状）。"""
    disable()
    root = cache_root()
    for n in os.listdir(root):
        if n.startswith("cycles_"):
            shutil.rmtree(os.path.join(root, n), ignore_errors=True)

def sync_lens(rx, settings):
    """把当前镜头写进内核头文件。返回是否改写（改写后下一次 GPU 渲染会重新编译内核，约 10 秒）。"""
    text = generate_header(rx, settings)
    _STATE["header_hash"] = hashlib.sha1(text.encode()).hexdigest()[:12]
    return write_header(text)
