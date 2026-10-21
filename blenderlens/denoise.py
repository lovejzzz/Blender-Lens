# 时间降噪（0.8.4，MessyRoom R3）：读 render_frames / render_shot 写的多层 EXR，写 BL_Final 的稳定版。
#   bl.temporal_denoise("//shots/heli", "//shots/heli_stable", frames=range(40, 57))
# 在 MessyRoom 的 hazefix（Day 4 成片 v3）基础上，把后期靠猜的阈值换成插件知道的东西：
#   · 对的是 Combined（不含炫光 / 杂光）：BL_Flare、BL_Veil 是确定性的（没有采样噪点），最后原样加回 →
#     鬼影绕画面中心反向运动、对不上 BL_Vector 也不会被抹；
#   · 时间平均：沿 BL_Vector 一帧一帧链式对齐 t−R…t+R（权重 1-2-…-(R+1)-…-2-1），每个对齐样本按当前帧
#     clamp×clamp 邻域的 min / max（外扩 margin × 范围）钳制；
#   · 每个样本的可信度：链上各帧的 BL_VectorConfidence 相乘（遮挡 / 去遮挡、发光体外圈、镜面、速度都在里面），
#     再加前后一致性检查（t → t−1 的位置用 t−1 帧的 Z,W 走回来，偏差 > fb_px 就不用）；
#   · protect：["specular"]：镜面（Denoising Specular Albedo 高、Roughness 低）不平均（有置信度层时已经包含）；
#     ["emitters"]：亮光源（> 1.5）及外圈只在几乎静止（< 0.25–0.75 px/帧，1080 行等效）时平均；
#   · vfade_px=(2, 8)：按速度淡出（1080 行等效 px/帧）：快到能盖住闪烁、再平均只会抹细节。
# 输出（每帧一个多层 EXR，<name>_<帧>.exr）：BL_Final（稳定版，= 降噪后的 Combined + BL_Flare + BL_Veil）、
#   BL_TemporalFrames（每像素实际用了几帧：1 + 可信样本数）、BL_TemporalWeight（混合强度 0–1）。
# 0.8.5：有 BL_Exclude（render_* 的 exclude_collections，如雨）时只对场景层（Combined）做，最后
#   BL_Final = (BL_Exclude over 降噪后的 Combined) + BL_Flare + BL_Veil —— 雨不被平均、不拖重影。
import json, os
import numpy as np

from . import motion as MO


def _minmax(img, k):
    """k×k 邻域的 min / max（可分离，边缘复制）。img：H×W×C。"""
    r = k // 2
    H, W = img.shape[:2]

    def run(x, axis, op):
        p = np.pad(x, [(r, r) if i == axis else (0, 0) for i in range(x.ndim)], mode="edge")
        out = np.take(p, range(0, H if axis == 0 else W), axis=axis).copy()
        for i in range(1, 2 * r + 1):
            op(out, np.take(p, range(i, i + (H if axis == 0 else W)), axis=axis), out=out)
        return out
    lo = run(run(img, 0, np.minimum), 1, np.minimum)
    hi = run(run(img, 0, np.maximum), 1, np.maximum)
    return lo, hi


def _dilate(mask, r):
    m = mask.copy()
    for _ in range(r):
        p = np.pad(m, 1, mode="edge")
        H, W = m.shape
        m = (p[1:H + 1, 1:W + 1] | p[0:H, 1:W + 1] | p[2:H + 2, 1:W + 1] | p[1:H + 1, 0:W] | p[1:H + 1, 2:W + 2])
    return m


class _Frames:
    """按需读帧、只留窗口里的。"""

    def __init__(self, in_dir, name, ext, use_reflection=True):
        self.in_dir, self.name, self.ext, self.cache = in_dir, name, ext, {}
        self.use_reflection = use_reflection

    def path(self, t):
        return os.path.join(self.in_dir, f"{self.name}_{t:04d}{self.ext}")

    def get(self, t, full=False):
        key = (t, full)
        if key in self.cache:
            return self.cache[key]
        from . import passes as PS
        want_basic = ("Combined", "BL_Vector", "BL_VectorConfidence", "BL_VectorReflection", "BL_ReflectionShare")
        want_full = want_basic + ("BL_Flare", "BL_Veil", "BL_Exclude", "Denoising Specular Albedo", "Denoising Roughness")
        names = want_full if full else want_basic
        parts = PS.read(self.path(t), want=lambda ps: ps in names)
        d = {}
        for p in parts:
            ch = p["channels"]
            if p["pass_"] == "BL_Vector":
                d["vec"] = np.stack([ch[c] for c in "XYZW"], -1).astype(np.float32)
            elif p["pass_"] == "BL_VectorReflection":
                d["vrefl"] = np.stack([ch[c] for c in "XYZW"], -1).astype(np.float32)
            elif p["pass_"] == "BL_ReflectionShare":
                d["rshare"] = ch["X"].astype(np.float32)
            elif p["pass_"] == "BL_VectorConfidence":
                d["conf"] = ch["X"].astype(np.float32)
            elif p["pass_"] == "Denoising Roughness":
                d["rough"] = ch["X"].astype(np.float32)
            elif {"R", "G", "B"} <= set(ch):
                d[p["pass_"]] = np.stack([ch[c] for c in "RGB"], -1).astype(np.float32)
                if p["pass_"] == "Combined":
                    d["alpha"] = ch.get("A")
                    d["header"] = p["header"]
                if p["pass_"] == "BL_Exclude":
                    d["exclude_a"] = ch["A"].astype(np.float32)
        if self.use_reflection and "vec" in d and "vrefl" in d and "rshare" in d:   # 0.8.5：倒影为主的像素用倒影的矢量
            d["vec"] = MO.effective_vectors(d["vec"], d.pop("vrefl"), d.pop("rshare"))
        if "Combined" not in d or "vec" not in d:
            raise RuntimeError(f"{self.path(t)}：需要 Combined 与 BL_Vector（render_frames / render_shot 的 passes 多层输出）")
        self.cache[key] = d
        return d

    def keep(self, lo, hi):
        for k in [k for k in self.cache if not (lo <= k[0] <= hi)]:
            del self.cache[k]


def temporal_denoise(in_dir, out_dir, frames, strength=1.0, protect=("specular", "emitters"), vfade_px=(2.0, 8.0),
                     radius=3, clamp=5, margin=0.25, fb_px=0.5, use_confidence=True, name="f", file_ext=".exr",
                     out_name=None, half=True, exr_codec=None, use_reflection=True, spike_suppress=None):
    """→ dict(frames, out_dir, per_frame=[{frame, frames_used_mean, weight_mean, file}], settings)。
    in_dir：多层 EXR 所在目录（<name>_<帧4位>.exr，需要 Combined、BL_Vector；最好有 BL_VectorConfidence、BL_Flare、BL_Veil，
    以及 protect 用的去噪数据层——passes="temporal" 全有）。frames：要输出的帧（窗口里缺的邻帧自动跳过）。
    strength：0–1 混合强度上限。radius：每边用几帧。clamp / margin：钳制邻域与外扩比例。fb_px：前后一致性容差（1080 行等效）。
    half / exr_codec：输出精度与压缩（默认 half + DWAB；要逐像素比对时用无损的 "PIZ" / "ZIP"）。
    use_reflection（0.8.5）：有 BL_VectorReflection / BL_ReflectionShare 时，倒影为主（占比 > 0.5）的像素按倒影的运动对齐。
    spike_suppress（0.9.2，MessyRoom Day 5）：None（默认，不做）/ True / dict(delta=0.06, min_lum=0.25, conf=0.8)——
      场景层（降噪后的 Combined）里的孤立亮点：按 BL_Vector 对齐的前后两帧都可信（链上置信度 ≥ conf、前后一致性通过）、
      当前像素的显示亮度比两帧都高 delta 以上且 > min_lum 时，逐通道压到 max(前, 后)。正常高光在前后帧也亮，不动；
      雨（BL_Exclude）不动（它在时间上本来就不连贯，用 render 的 exclude_knee）。每帧的个数写进报告与 EXR 头。"""
    try:
        import bpy
        in_dir, out_dir = bpy.path.abspath(in_dir), bpy.path.abspath(out_dir)
    except ImportError:
        pass
    from . import passes as PS
    frames = list(frames)
    have = {t for t in range(min(frames) - radius, max(frames) + radius + 1)
            if os.path.exists(os.path.join(in_dir, f"{name}_{t:04d}{file_ext}"))}
    os.makedirs(out_dir, exist_ok=True)
    out_name = out_name or name
    F = _Frames(in_dir, name, file_ext, use_reflection)
    Wk = {k: float(radius + 1 - k) for k in range(radius + 1)}           # 1-2-3-4-3-2-1
    v0, v1 = vfade_px
    rep = dict(in_dir=in_dir, out_dir=out_dir, frames=frames, per_frame=[],
               settings=dict(strength=strength, protect=list(protect), vfade_px=list(vfade_px), radius=radius, clamp=clamp,
                             margin=margin, fb_px=fb_px, use_confidence=use_confidence, spike_suppress=spike_suppress))
    for t in frames:
        if t not in have:
            rep["per_frame"].append(dict(frame=t, error="缺这一帧"))
            continue
        F.keep(t - radius, t + radius)
        cur = F.get(t, full=True)
        comb = cur["Combined"]
        H, W = comb.shape[:2]
        k1080 = 1080.0 / H
        xx, yy = MO.grid(H, W)
        lo, hi = _minmax(comb, clamp)
        m = (hi - lo) * margin
        lo -= m; hi += m
        acc = comb * Wk[0]; wsum = np.full((H, W), Wk[0], np.float32)
        nb1 = {}                                          # 0.9.2：k = 1 的对齐邻帧（不钳制）与可信度，孤立亮点抑制用
        used = np.ones((H, W), np.float32)
        for direction in (-1, 1):
            x, y = xx.copy(), yy.copy()
            chain = np.ones((H, W), np.float32)
            here = cur
            for k in range(1, radius + 1):
                tt = t + direction * k
                if tt not in have:
                    break
                v = MO.bilinear(here["vec"], x, y)
                if use_confidence and "conf" in here:
                    chain *= MO.bilinear(here["conf"], x, y)
                if direction < 0:
                    nx, ny = x + v[..., 0], y - v[..., 1]                # X,Y = 上一帧 − 当前（y 向上 → 行向下）
                else:
                    nx, ny = x - v[..., 2], y + v[..., 3]                # Z,W = 当前 − 下一帧
                nb = F.get(tt)
                vb = MO.bilinear(nb["vec"], nx, ny)                      # 前后一致性：从那一帧走回来
                if direction < 0:
                    bx, by = nx - vb[..., 2], ny + vb[..., 3]
                else:
                    bx, by = nx + vb[..., 0], ny - vb[..., 1]
                fb = np.hypot(bx - x, by - y) * k1080
                ok = (fb <= fb_px) & (nx >= 0) & (nx <= W - 1) & (ny >= 0) & (ny <= H - 1)
                chain *= ok
                raw = MO.bilinear(nb["Combined"], nx, ny)
                if k == 1:
                    nb1[direction] = (raw, chain.copy())
                s = np.clip(raw, lo, hi)
                wk = Wk[k] * chain
                acc += s * wk[..., None]; wsum += wk
                used += chain > 0.5
                x, y, here = nx, ny, nb
        tavg = acc / wsum[..., None]
        del acc
        sp = MO.speed(cur["vec"], H)
        w = np.full((H, W), float(strength), np.float32)
        w *= np.clip((v1 - sp) / max(v1 - v0, 1e-6), 0.0, 1.0)
        if "emitters" in protect:
            hot = comb.max(-1) > 1.5
            hot = _dilate(hot, max(1, int(round(3 / k1080))))
            w *= 1.0 - hot * np.clip((sp - 0.25) / 0.5, 0.0, 1.0)
        if "specular" in protect and not (use_confidence and "conf" in cur):
            if "Denoising Specular Albedo" in cur and "rough" in cur:
                spec = cur["Denoising Specular Albedo"].mean(-1)
                mirror = np.clip((spec - 0.08) / 0.12, 0, 1) * np.clip((0.35 - cur["rough"]) / 0.2, 0, 1)
                w *= 1.0 - mirror
        stable = comb + w[..., None] * (tavg - comb)
        n_spikes = None
        if spike_suppress and -1 in nb1 and 1 in nb1:
            from . import spikes as SP
            sp_kw = dict(SP.DEFAULTS, **(spike_suppress if isinstance(spike_suppress, dict) else {}))
            (pv, cpv), (nv, cnv) = nb1[-1], nb1[1]
            valid = (cpv >= sp_kw["conf"]) & (cnv >= sp_kw["conf"])
            stable, n_spikes = SP.suppress(stable, pv, nv, valid, sp_kw["delta"], sp_kw["min_lum"])
        over = stable
        if "BL_Exclude" in cur:                           # 0.8.5：排除层（雨、粒子）不平均，原样 over 回去
            over = cur["BL_Exclude"] + (1.0 - cur["exclude_a"])[..., None] * stable
        final = over + cur.get("BL_Flare", 0.0) + cur.get("BL_Veil", 0.0)
        alpha = cur["alpha"] if cur.get("alpha") is not None else np.ones((H, W), np.float32)
        tmpl = dict(name="ViewLayer.Combined", view_layer="ViewLayer", pass_="Combined", header={})
        hdr = {k: v for k, v in cur["header"].items() if isinstance(v, str) and k.startswith("blenderlens/")}
        hdr["blenderlens/temporal_denoise"] = json.dumps(rep["settings"])
        if n_spikes is not None:
            hdr["blenderlens/spikes_suppressed"] = str(n_spikes)
        tmpl["header"] = hdr
        parts = [PS.new_part(tmpl, "BL_Final", dict(R=final[..., 0], G=final[..., 1], B=final[..., 2], A=alpha)),
                 PS.new_part(tmpl, "BL_TemporalFrames", dict(X=used)),
                 PS.new_part(tmpl, "BL_TemporalWeight", dict(X=w * (wsum - Wk[0] > 1e-6)))]
        path = os.path.join(out_dir, f"{out_name}_{t:04d}{file_ext}")
        PS.write(path + ".partial" + file_ext, parts, half_color=half, codec=exr_codec or ("DWAB" if half else "ZIP"))
        os.replace(path + ".partial" + file_ext, path)
        rep["per_frame"].append(dict(frame=t, file=path, frames_used_mean=round(float(used.mean()), 3),
                                     weight_mean=round(float(w.mean()), 3),
                                     changed_mean=round(float(np.abs(stable - comb).mean() / max(float(np.abs(comb).mean()), 1e-9)), 5),
                                     **({"spikes_suppressed": n_spikes} if n_spikes is not None else {})))
    with open(os.path.join(out_dir, f"{out_name}_temporal_denoise.json"), "w") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep
