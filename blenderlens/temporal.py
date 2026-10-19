# 时间稳定性（闪烁）检查（0.8.3，MessyRoom R3）。需要 bpy。
#   bl.temporal_check(scene, frame=120, n=8, height=1080, variants=[{"spp": 1024}, {"denoise": False}, {"hide": ["haze"]}])
# 相机（位置、朝向、对焦、光圈）锁在 frame 这一帧，场景时间照常走 n 帧（灯光动画、体积、粒子、随机种子……），
# 逐帧渲染后测相邻帧差。镜头不动时画面应该不变，剩下的帧间差就是闪烁（采样噪点 × 逐帧降噪、体积散射……）。
# 指标都在 x/(1+x) 压缩后的亮度上算（高光不会主导）：整幅平均、最亮 2% 像素的平均、整幅 p99；另存闪烁热力图 PNG。
# mode="moving"（0.8.4，MessyRoom R1）：相机照真实运动走，每帧写多层 EXR（BL_Final + BL_Vector），用 BL_Vector 把 t−1 帧
#   对齐到 t 帧后测残差（运动补偿闪烁）。另给运动速度直方图（1080 行等效 px/帧）和每个速度区间的平均闪烁：
#   哪段快到能盖住闪烁、哪段慢到会暴露。残差里也含对齐误差（插值、遮挡边缘），所以主要用来比较变体 / 版本。
#   开炫光（flare=True 或变体 {"flare": True}）时另给 flare_stability：炫光总能量与质心的二阶差分（R6）。
#   注意：鬼影的运动方向与场景不同（绕画面中心反向），BL_Vector 对不上它们，所以炫光会抬高运动补偿残差——
#   这是正常运动，不是闪烁；炫光本身稳不稳看 flare_stability。
# variants：每项是一组改动，与原设置对比，给出相对原设置的百分比。支持的键：
#   spp（采样数）、denoise（True/False）、hide（物体 / 集合名列表，渲染时隐藏）、flare（是否做正向炫光后期），
#   以及任何相机设置（见 bl.describe()["settings"]，如 dispersion_mode、f_number）。
import json, math, os
import numpy as np


def _tone(a):
    lum = a[..., :3].mean(axis=-1)
    return lum / (1.0 + np.maximum(lum, 0.0))


def _render(scene, path, flare):
    from . import rig
    out, _, _ = rig.render_frame(scene, path, flare=flare)
    W, H = out.size
    return np.array(out.pixels[:], np.float32).reshape(H, W, 4)[::-1]


def _detail(t):
    """细节：平均 |拉普拉斯|（4 邻域），在 x/(1+x) 亮度上。"""
    return float(np.abs(4 * t[1:-1, 1:-1] - t[:-2, 1:-1] - t[2:, 1:-1] - t[1:-1, :-2] - t[1:-1, 2:]).mean())


def _metrics_moving(frames, vecs, height):
    from . import motion as MO
    t = [MO.tone(f) for f in frames]
    rs, D = MO.mc_residual(t, vecs)
    mean_img = np.mean(t, axis=0)[8:-8, 8:-8]
    bright = mean_img >= np.percentile(mean_img, 98.0)
    m = dict(frame_mean=float(D.mean()), brightest2_mean=float(np.mean([r[bright].mean() for r in rs])),
             p99=float(np.percentile(np.stack(rs), 99.0)), detail=float(np.mean([_detail(x) for x in t])))
    return m, D, MO.speed_histogram(vecs, height, D=D)


def flare_stability(flares, veils=None):
    """炫光的帧间稳定性（0.8.4，MessyRoom R6）。flares：各帧 BL_Flare（H×W×3）。相机平滑运动时，稳定的炫光
    总能量与亮度质心随时间平滑变化 → 用二阶差分（E_t − (E_{t−1} + E_{t+1})/2）衡量跳动，与运动本身（一阶）无关。
    → energy_jitter_pct（最大二阶差 / 平均能量 × 100）、centroid_jitter_px（质心二阶差的最大值）、veil_energy_jitter_pct。"""
    def jitter(E):
        E = np.asarray(E, np.float64)
        if len(E) < 3 or E.mean() <= 0:
            return 0.0
        return float(np.max(np.abs(E[1:-1] - 0.5 * (E[:-2] + E[2:]))) / E.mean() * 100.0)
    E, C = [], []
    for F in flares:
        L = F[..., :3].mean(-1).astype(np.float64)
        e = L.sum(); E.append(e)
        if e > 0:
            yy, xx = np.indices(L.shape)
            C.append(((L * xx).sum() / e, (L * yy).sum() / e))
        else:
            C.append((0.0, 0.0))
    C = np.asarray(C)
    cj = float(np.max(np.hypot(*(C[1:-1] - 0.5 * (C[:-2] + C[2:])).T))) if len(C) >= 3 else 0.0
    out = dict(energy_jitter_pct=round(jitter(E), 4), centroid_jitter_px=round(cj, 4),
               mean_energy=round(float(np.mean(E)), 4))
    if veils is not None:
        out["veil_energy_jitter_pct"] = round(jitter([np.abs(v[..., :3]).sum() for v in veils]), 4)
    return out


def _pick(parts, layer):
    """moving 模式测哪一层：scene = 场景层（Combined + 炫光 + 杂光，不含排除层）；exclude = BL_Exclude；final = BL_Final。"""
    from . import passes as PS
    ex = PS.find(parts, "BL_Exclude")
    if layer == "final" or (layer == "scene" and ex is None):
        return PS.rgb(PS.find(parts, "BL_Final"))
    if layer == "exclude":
        if ex is None:
            raise ValueError("layer='exclude'：输出里没有 BL_Exclude（渲染时要给 exclude_collections）")
        return PS.rgb(ex)
    return PS.rgb(PS.find(parts, "Combined")) + PS.rgb(PS.find(parts, "BL_Flare")) + PS.rgb(PS.find(parts, "BL_Veil"))


def _render_layers(scene, path, flare, layer="scene", exclude_collections=None):
    """moving 模式：一帧多层 EXR（只留 Combined + 插件层），→ (BL_Final H×W×3, BL_Vector H×W×4, BL_Flare, BL_Veil)，行 0 = 顶部。"""
    from . import rig, passes as PS
    rig.render_frame_multilayer(scene, path, flare=flare, passes=["Combined"], vectors=True, exr_codec="ZIP", half_color=True,
                                exclude_collections=exclude_collections)
    parts = PS.read(path)
    fin = _pick(parts, layer)
    v = PS.find(parts, "BL_Vector")
    H, W = fin.shape[:2]
    vec = np.stack([v["channels"][c] for c in "XYZW"], -1) if v else np.zeros((H, W, 4), np.float32)
    vr, sh = PS.find(parts, "BL_VectorReflection"), PS.find(parts, "BL_ReflectionShare")
    if vr is not None and sh is not None:                 # 0.8.5：倒影为主的像素用倒影的矢量
        from . import motion as MO
        vec = MO.effective_vectors(vec, np.stack([vr["channels"][c] for c in "XYZW"], -1), sh["channels"]["X"])
    return fin, vec, PS.rgb(PS.find(parts, "BL_Flare")), PS.rgb(PS.find(parts, "BL_Veil"))


def _metrics(frames):
    t = [_tone(f) for f in frames]
    diffs = [np.abs(t[k + 1] - t[k]) for k in range(len(t) - 1)]
    D = np.mean(diffs, axis=0)
    mean_img = np.mean(t, axis=0)
    thr = np.percentile(mean_img, 98.0)
    bright = mean_img >= thr
    return dict(frame_mean=float(np.mean([d.mean() for d in diffs])),
                brightest2_mean=float(np.mean([d[bright].mean() for d in diffs])),
                p99=float(np.percentile(np.stack(diffs), 99.0))), D


def _save_heatmap(D, path):
    import bpy
    H, W = D.shape
    v = D / max(float(np.percentile(D, 99.5)), 1e-12)
    v = np.clip(v, 0.0, 1.0)
    rgba = np.zeros((H, W, 4), np.float32)                 # 黑 → 红 → 黄 → 白
    rgba[..., 0] = np.clip(3 * v, 0, 1); rgba[..., 1] = np.clip(3 * v - 1, 0, 1); rgba[..., 2] = np.clip(3 * v - 2, 0, 1)
    rgba[..., 3] = 1.0
    im = bpy.data.images.new("bl_flicker", W, H, alpha=True)
    im.pixels.foreach_set(rgba[::-1].ravel())
    im.filepath_raw = path; im.file_format = 'PNG'
    im.save()
    bpy.data.images.remove(im)


class _Locked:
    """锁住相机（动画暂时摘掉，按 frame 这一帧的值固定）；退出时全部恢复。"""

    def __init__(self, scene, cam_ob, frame):
        self.scene, self.cam, self.frame = scene, cam_ob, frame

    def __enter__(self):
        sc, cam = self.scene, self.cam
        self.cur = sc.frame_current
        sc.frame_set(self.frame)
        self.mw = cam.matrix_world.copy()
        p = cam.data.blenderlens
        self.vals = dict(focus_distance=p.focus_distance, f_number=p.f_number, mumps_correction=p.mumps_correction)
        self.actions = (cam.animation_data.action if cam.animation_data else None,
                        cam.data.animation_data.action if cam.data.animation_data else None)
        if cam.animation_data:
            cam.animation_data.action = None
        if cam.data.animation_data:
            cam.data.animation_data.action = None
        return self

    def hold(self):
        from . import rig
        self.cam.matrix_world = self.mw
        p = self.cam.data.blenderlens
        for k, v in self.vals.items():
            if abs(getattr(p, k) - v) > 1e-9:
                setattr(p, k, v)
        rig.apply_settings(self.cam)

    def __exit__(self, *a):
        from . import rig
        cam = self.cam
        if self.actions[0] is not None:
            cam.animation_data.action = self.actions[0]
        if self.actions[1] is not None:
            cam.data.animation_data.action = self.actions[1]
        self.scene.frame_set(self.cur)
        rig.apply_settings(cam)


def _apply_variant(scene, cam_ob, v):
    """改动 → 恢复用的回调。"""
    import bpy
    from . import agent
    undo = []
    c = scene.cycles
    if "spp" in v:
        old = c.samples; c.samples = int(v["spp"]); undo.append(lambda o=old: setattr(c, "samples", o))
    if "denoise" in v:
        old = c.use_denoising; c.use_denoising = bool(v["denoise"]); undo.append(lambda o=old: setattr(c, "use_denoising", o))
    for name in v.get("hide", []):
        tgt = bpy.data.objects.get(name) or bpy.data.collections.get(name)
        if tgt is None:
            raise ValueError(f"hide：找不到物体或集合 {name!r}")
        old = tgt.hide_render; tgt.hide_render = True; undo.append(lambda t=tgt, o=old: setattr(t, "hide_render", o))
    cam_keys = {k: val for k, val in v.items() if k not in ("spp", "denoise", "hide", "flare", "name")}
    if cam_keys:
        p = cam_ob.data.blenderlens
        before = {k: getattr(p, k) for k in cam_keys if hasattr(p, k)}
        agent.configure(cam_ob, **cam_keys)
        undo.append(lambda b=before: [setattr(p, k, val) for k, val in b.items()])
    return undo


def temporal_check(scene=None, frame=None, n=8, height=1080, variants=None, out_dir="//blenderlens_temporal", flare=False,
                   mode="locked", exclude_collections=None, layer="scene"):
    """→ dict(frame, n, mode, resolution, base={frame_mean, brightest2_mean, p99, heatmap}, variants=[{change, metrics, pct_of_base}])。
    pct_of_base：变体的指标 / 原设置的指标 × 100（< 100 = 闪烁更少）。
    mode="locked"：相机锁在 frame；"moving"：真实运镜 + 运动补偿（另有 detail 指标与 motion：速度直方图、各速度区间闪烁）。
    exclude_collections / layer（0.8.5，moving 模式）：排除集合（雨）单独成层；layer = "scene"（默认，不含排除层）/
    "exclude"（只看排除层：检查雨是否帧间确定）/ "final"。"""
    if mode not in ("locked", "moving"):
        raise ValueError(f"mode 必须是 'locked' 或 'moving'，收到 {mode!r}")
    import bpy
    from . import api, rig
    sc = scene or bpy.context.scene
    cam = api.camera(sc)
    if cam is None:
        raise RuntimeError("场景里没有 BlenderLens 相机")
    frame = sc.frame_current if frame is None else int(frame)
    out = bpy.path.abspath(out_dir)
    os.makedirs(out, exist_ok=True)
    r = sc.render
    keep = (r.resolution_x, r.resolution_y, r.resolution_percentage)
    W, H = rig.desqueezed_resolution(rig.read_prescription(), height)
    runs = [dict(name="base", change={})] + [dict(name=v.get("name", f"v{k + 1}"), change=v) for k, v in enumerate(variants or [])]
    rep = dict(frame=frame, n=n, mode=mode, resolution=[W, H], frames=list(range(frame, frame + n)))
    try:
        r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
        if mode == "moving":
            cur = sc.frame_current
            try:
                for run in runs:
                    undo = _apply_variant(sc, cam, run["change"])
                    try:
                        imgs, vecs, fls, vls = [], [], [], []
                        fl_on = run["change"].get("flare", flare)
                        for k in range(n):
                            sc.frame_set(frame + k)
                            rig.apply_settings(cam)
                            fin, vec, fl, ve = _render_layers(sc, os.path.join(out, f"{run['name']}_{frame + k:04d}.exr"),
                                                              flare=fl_on, layer=layer, exclude_collections=exclude_collections)
                            imgs.append(fin); vecs.append(vec)
                            if fl_on:
                                fls.append(fl); vls.append(ve)
                        m, D, mot = _metrics_moving(imgs, vecs, H)
                        if fl_on:
                            run["flare_stability"] = flare_stability(fls, vls)
                        hm = os.path.join(out, f"flicker_moving_{run['name']}_f{frame}.png")
                        _save_heatmap(D, hm)
                        run["metrics"] = {k: round(v, 6) for k, v in m.items()}
                        run["heatmap"] = hm
                        run["motion"] = mot
                        del imgs, vecs, fls, vls
                    finally:
                        for u in reversed(undo):
                            u()
            finally:
                sc.frame_set(cur)
                rig.apply_settings(cam)
        else:
            with _Locked(sc, cam, frame) as lock:
                for run in runs:
                    undo = _apply_variant(sc, cam, run["change"])
                    try:
                        imgs = []
                        for k in range(n):
                            sc.frame_set(frame + k)
                            lock.hold()
                            imgs.append(_render(sc, os.path.join(out, f"{run['name']}_{frame + k:04d}.exr"),
                                                flare=run["change"].get("flare", flare)))
                        m, D = _metrics(imgs)
                        hm = os.path.join(out, f"flicker_{run['name']}_f{frame}.png")
                        _save_heatmap(D, hm)
                        run["metrics"] = {k: round(v, 6) for k, v in m.items()}
                        run["heatmap"] = hm
                    finally:
                        for u in reversed(undo):
                            u()
    finally:
        r.resolution_x, r.resolution_y, r.resolution_percentage = keep
    base = runs[0]["metrics"]
    rep["base"] = dict(runs[0]["metrics"], heatmap=runs[0]["heatmap"])
    rep["variants"] = []
    for run in runs[1:]:
        pct = {k: round(100.0 * run["metrics"][k] / max(base[k], 1e-12), 1) for k in base}
        rep["variants"].append(dict(name=run["name"], change=run["change"], metrics=run["metrics"], pct_of_base=pct,
                                    heatmap=run["heatmap"], **{k: run[k] for k in ("motion", "flare_stability") if k in run}))
    for k in ("motion", "flare_stability"):
        if k in runs[0]:
            rep["base"][k] = runs[0][k]
    with open(os.path.join(out, f"temporal_{mode}_f{frame}.json" if mode == "moving" else f"temporal_f{frame}.json"), "w") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep
