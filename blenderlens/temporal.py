# 时间稳定性（闪烁）检查（0.8.3，MessyRoom R3）。需要 bpy。
#   bl.temporal_check(scene, frame=120, n=8, height=1080, variants=[{"spp": 1024}, {"denoise": False}, {"hide": ["haze"]}])
# 相机（位置、朝向、对焦、光圈）锁在 frame 这一帧，场景时间照常走 n 帧（灯光动画、体积、粒子、随机种子……），
# 逐帧渲染后测相邻帧差。镜头不动时画面应该不变，剩下的帧间差就是闪烁（采样噪点 × 逐帧降噪、体积散射……）。
# 指标都在 x/(1+x) 压缩后的亮度上算（高光不会主导）：整幅平均、最亮 2% 像素的平均、整幅 p99；另存闪烁热力图 PNG。
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


def temporal_check(scene=None, frame=None, n=8, height=1080, variants=None, out_dir="//blenderlens_temporal", flare=False):
    """→ dict(frame, n, resolution, base={frame_mean, brightest2_mean, p99, heatmap}, variants=[{change, metrics, pct_of_base}])。
    pct_of_base：变体的指标 / 原设置的指标 × 100（< 100 = 闪烁更少）。"""
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
    rep = dict(frame=frame, n=n, resolution=[W, H], frames=list(range(frame, frame + n)))
    try:
        r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
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
                                    heatmap=run["heatmap"]))
    with open(os.path.join(out, f"temporal_f{frame}.json"), "w") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep
