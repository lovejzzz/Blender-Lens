# 一条命令渲一个镜头（0.8.4，MessyRoom R5）。需要 bpy。
#   bl.render_shot(source_camera="CamA", frames=range(40, 57), out_dir="//shots/heli", preset="c_series_style",
#                  passes="temporal", spp=256, follow=True, guard=True)
# 做的事（每一镜都要写的那些）：
#   1. 搭镜头（或用场景里已有的 BlenderLens 相机）；
#   2. follow：逐帧跟随源相机并烘焙位置、旋转、对焦距离、f 值（follow_camera(stop=True)）；
#   3. exposure="pinhole"：用 Cycles 的胶片曝光（film_exposure，写进 EXR 数据本身）补偿镜头的整幅平均曝光差，
#      让成片与普通针孔相机同样亮（exposure_vs_pinhole 的 frame_mean_ev）；"pinhole_centre" = 按画面中心补偿（中灰卡）；
#      "keep" = 不动。0.8.5：补偿乘到原有的 film_exposure 上——有关键帧（按测光打的曝光、切点阶跃）就整条曲线乘，渲完恢复；
#   4. 逐帧渲染：确定性种子、跳过已有帧、写 .partial 再改名、多机锁（render_frames）；
#   5. guard：每帧开始前看系统可用内存，低于 guard_min_gb 就等（每 15 秒看一次，最多 guard_wait_s 秒），等到了再渲；
#   6. 日志：<out_dir>/<name>_log.json，每帧写一次（用时、各步用时、峰值常驻内存、开始前可用内存、等待时间）。
import json, os, platform, resource, subprocess, sys, time


# render_shot 搭镜头时保留的场景渲染设置（build → render_preset 会改）
_KEEP_CYCLES = ("device", "use_adaptive_sampling", "blur_glossy", "caustics_reflective", "caustics_refractive", "max_bounces",
                "transmission_bounces", "glossy_bounces", "transparent_max_bounces", "diffuse_bounces", "volume_bounces")


def _available_gb():
    """系统可用内存（GB）：macOS 用 vm_stat（空闲 + 非活跃 + 推测 + 可清除页），Linux 用 /proc/meminfo；拿不到 → None。"""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = 16384
            vals = {}
            for ln in out.splitlines():
                if "page size of" in ln:
                    page = int(ln.split("page size of")[1].split()[0])
                elif ":" in ln:
                    k, v = ln.split(":", 1)
                    try:
                        vals[k.strip()] = int(v.strip().rstrip("."))
                    except ValueError:
                        pass
            pages = sum(vals.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
            return round(pages * page / 1e9, 2)
        with open("/proc/meminfo") as fh:
            for ln in fh:
                if ln.startswith("MemAvailable:"):
                    return round(int(ln.split()[1]) * 1024 / 1e9, 2)
    except Exception:                                    # noqa: BLE001
        return None
    return None


def _rss_gb():
    """本进程当前常驻内存（GB）。"""
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5).stdout
        return round(int(out.strip()) * 1024 / 1e9, 2)
    except Exception:                                    # noqa: BLE001
        return None


def _max_rss_gb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1e9 if sys.platform == "darwin" else 1e6), 2)


def render_shot(source_camera=None, frames=None, out_dir="//shot", preset=None, focal_mm=0.0, gate="16:9_NATIVE",
                passes=None, spp=None, follow=True, guard=True, scene=None, height=None, flare=False, name="f",
                exposure="keep", stop=True, focus=True, seed=0, units="SCENE", exr_codec=None, half_color=None,
                guard_min_gb=4.0, guard_wait_s=1800.0, file_ext=".exr", lock=True, exclude_collections=None, exclude_mode="hide",
                settings=None, focus_target=None, rack=None, exclude_spp=None, exclude_bounces=None):
    """渲一个镜头 → 日志 dict（同时写 <out_dir>/<name>_log.json）。
    source_camera：源相机（物体或名字）；None = 场景当前的相机（若它不是 BlenderLens 相机）。follow=False 时可以不给。
    frames：帧号序列；None = 场景的起止帧。preset：None = 用场景里已有的 BlenderLens 相机；给了 = 搭一支新镜头
    （focal_mm、gate、units、height 同 bl.build）。passes / exr_codec / half_color / flare 同 bl.render_frames。
    spp：采样数（None = 不改）。exposure："keep" 或 "pinhole"（见文件头）。
    exclude_collections（0.8.5）：如 ["FX_rain"]，单独成层 BL_Exclude，不进场景层 / 矢量 / 时间降噪。
    exclude_spp / exclude_bounces（0.8.7，可选）：排除层单独的采样数 / 弹射上限（默认与主渲染相同）。
    settings（0.8.5）：镜头设置 dict（同 bl.configure，如 {"f_number": 2.8, "mumps_correction": 1}），搭好 / 跟随之后写入；
    focus_target / rack：对焦跟住一个点 / 拉焦（同 bl.focus_track），覆盖源相机的对焦。"""
    import bpy
    from . import api, rig
    sc = scene or bpy.context.scene
    src = bpy.data.objects.get(source_camera) if isinstance(source_camera, str) else source_camera
    if src is None and follow:
        cand = sc.camera
        if cand is not None and not rig.is_blenderlens_camera(cand):
            src = cand
    if follow and src is None:
        raise ValueError("render_shot：follow=True 需要源相机（source_camera）")
    if exposure not in ("keep", "pinhole", "pinhole_centre"):
        raise ValueError(f"exposure 必须是 'keep'、'pinhole' 或 'pinhole_centre'，收到 {exposure!r}")
    cam = api.camera(sc)
    if preset is not None or cam is None:
        if preset is None:
            raise ValueError("场景里没有 BlenderLens 相机：请给 preset")
        pf = preset if preset.endswith(".json") else preset + ".json"
        keep = {k: getattr(sc.cycles, k) for k in _KEEP_CYCLES if hasattr(sc.cycles, k)}
        cam = api.build(pf, sc, focal_mm=focal_mm or 0.0, gate=gate, units=units,
                        height=height or sc.render.resolution_y, make_active=True)
        for k, v in keep.items():                         # 0.8.5：搭镜头会套 render_preset（改 CPU、弹射数…）→ 换回场景原来的
            setattr(sc.cycles, k, v)
        if rig.gpu_unsupported(sc):
            sc.cycles.device = 'CPU'
        rig.apply_settings(cam)
    sc.camera = cam
    frames = list(frames) if frames is not None else list(range(sc.frame_start, sc.frame_end + 1))
    if not frames:
        raise ValueError("render_shot：frames 为空")
    out = bpy.path.abspath(out_dir)
    os.makedirs(out, exist_ok=True)
    log_path = os.path.join(out, f"{name}_log.json")
    log = dict(version=api.version(), started=time.strftime("%Y-%m-%d %H:%M:%S"),
               host=platform.node(), blend=bpy.data.filepath, camera=cam.name, source=src.name if src else None,
               frames=[frames[0], frames[-1]], n_frames=len(frames), passes=passes, flare=flare, spp=spp,
               exclude_collections=list(exclude_collections or []), exclude_spp=exclude_spp, exclude_bounces=exclude_bounces,
               out_dir=out, records=[])
    if os.path.exists(log_path):                          # 续渲：接着旧日志
        try:
            old = json.load(open(log_path))
            log["records"] = old.get("records", [])
            log["resumed_from"] = old.get("started")
        except (ValueError, OSError):
            pass
    if follow:
        log["follow_keys"] = api.follow_camera(cam, src, min(frames), max(frames), scene=sc, focus=focus, stop=stop)
    if settings:
        log["settings"] = {k: list(v) for k, v in api.configure(cam, **settings).items()}
    if focus_target is not None or rack:
        log["focus_track"] = api.focus_track(cam, focus_target, frames=(min(frames), max(frames)), rack=rack)
    if spp is not None:
        sc.cycles.samples = int(spp)
    restore_exposure = lambda: None
    if exposure in ("pinhole", "pinhole_centre"):
        sc.frame_set(frames[0]); rig.apply_settings(cam)
        ev = api.exposure_vs_pinhole(cam)
        key = "frame_mean_ev" if exposure == "pinhole" else "centre_ev"
        restore_exposure, info = _scale_film_exposure(sc, 2.0 ** (-ev[key]))
        log["exposure"] = dict(mode=exposure, ev=ev[key], factor=round(2.0 ** (-ev[key]), 5), **info)
    else:
        log["exposure"] = dict(mode="keep", film_exposure=round(sc.cycles.film_exposure, 4))
    log["resolution"] = [sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage]
    log["state"] = api.state(cam)

    def save():
        recs = [r for r in log["records"] if r.get("rendered")]
        if recs:
            log["summary"] = dict(rendered=len(recs), mean_seconds=round(sum(r["seconds"] for r in recs) / len(recs), 2),
                                  max_seconds=max(r["seconds"] for r in recs),
                                  max_rss_gb=max((r.get("max_rss_gb") or 0) for r in recs),
                                  waited_seconds=round(sum(r.get("waited_s", 0) for r in log["records"]), 1))
        tmp = log_path + ".partial"
        with open(tmp, "w") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, log_path)

    save()
    try:
        _render_loop(sc, frames, out, name, file_ext, guard, guard_min_gb, guard_wait_s, seed, flare, lock, passes, exr_codec,
                     half_color, exclude_collections, exclude_mode, log, save, exclude_spp, exclude_bounces)
    finally:
        restore_exposure()
    log["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    return log


def _scale_film_exposure(sc, k):
    """镜头相对针孔的曝光补偿 k 乘到 film_exposure 上（0.8.5，MessyRoom 0.8.5 R4）：有关键帧就把整条曲线（关键帧值与手柄）
    乘 k —— 按测光打的曝光、切点处的阶跃都保留；没有就乘当前值。→ (恢复函数, 日志 dict)。渲完恢复原样。"""
    fc = None
    ad = sc.animation_data
    if ad and ad.action:
        from . import api
        for f in api._action_fcurves(ad.action):
            if f.data_path == "cycles.film_exposure":
                fc = f
                break
    if fc is not None:
        saved = [(kp.co[1], kp.handle_left[1], kp.handle_right[1]) for kp in fc.keyframe_points]
        for kp in fc.keyframe_points:
            kp.co[1] *= k; kp.handle_left[1] *= k; kp.handle_right[1] *= k
        fc.update()

        def restore():
            for kp, (c, hl, hr) in zip(fc.keyframe_points, saved):
                kp.co[1], kp.handle_left[1], kp.handle_right[1] = c, hl, hr
            fc.update()
        return restore, dict(animated=True, keys=len(saved))
    old = sc.cycles.film_exposure
    sc.cycles.film_exposure = old * k

    def restore():
        sc.cycles.film_exposure = old
    return restore, dict(animated=False, film_exposure=round(old * k, 5))


def _render_loop(sc, frames, out, name, file_ext, guard, guard_min_gb, guard_wait_s, seed, flare, lock, passes, exr_codec,
                 half_color, exclude_collections, exclude_mode, log, save, exclude_spp=None, exclude_bounces=None):
    import bpy
    from . import api, rig
    for fr in frames:
        path = os.path.join(out, f"{name}_{fr:04d}{file_ext}")
        rec = dict(frame=fr, file=path)
        if os.path.exists(path):
            rec.update(rendered=False, skipped="exists")
            log["records"].append(rec); save()
            continue
        waited = 0.0
        avail = _available_gb()
        rec["avail_gb_before"] = avail
        if guard and avail is not None and avail < guard_min_gb:
            import gc
            gc.collect()
            while avail is not None and avail < guard_min_gb and waited < guard_wait_s:
                time.sleep(15.0); waited += 15.0
                avail = _available_gb()
            rec["waited_s"] = waited
            rec["avail_gb_after_wait"] = avail
            if avail is not None and avail < guard_min_gb:
                rec.update(rendered=False, skipped=f"内存不足：可用 {avail} GB < {guard_min_gb} GB（等了 {waited:.0f} 秒）")
                log["records"].append(rec); save()
                log["stopped"] = rec["skipped"]; save()
                break
        t0 = time.perf_counter()
        rig.LAST_TIMING.clear()
        done = api.render_frames(sc, frames=[fr], out_dir=out, name=name, seed=seed, flare=flare, lock=lock,
                                 file_ext=file_ext, passes=passes, exr_codec=exr_codec, half_color=half_color,
                                 exclude_collections=exclude_collections, exclude_mode=exclude_mode,
                                 exclude_spp=exclude_spp, exclude_bounces=exclude_bounces)
        rec.update(rendered=bool(done), seconds=round(time.perf_counter() - t0, 2), timing=dict(rig.LAST_TIMING),
                   rss_gb=_rss_gb(), max_rss_gb=_max_rss_gb(), avail_gb_after=_available_gb(),
                   size_mb=round(os.path.getsize(path) / 1e6, 1) if os.path.exists(path) else None)
        if not done:
            rec["skipped"] = "locked"                        # 另一台机器在渲
        log["records"].append(rec); save()


def shots_from_markers(scene=None):
    """时间线上绑定了相机的标记 → 镜头清单 [dict(name, source, frames=(起, 止))]：每个标记到下一个标记的前一帧，
    最后一个到场景结束帧。相机是 BlenderLens 相机的标记跳过。"""
    import bpy
    from . import rig
    sc = scene or bpy.context.scene
    ms = sorted((m for m in sc.timeline_markers if m.camera is not None and not rig.is_blenderlens_camera(m.camera)),
                key=lambda m: m.frame)
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].frame - 1 if i + 1 < len(ms) else sc.frame_end
        if end >= m.frame:
            out.append(dict(name=m.name, source=m.camera.name, frames=(m.frame, end)))
    return out


def render_sequence(shots=None, out_dir="//sequence", preset=None, passes=None, spp=None, guard=True, scene=None, **common):
    """一条命令渲多个镜头（0.8.5，MessyRoom 0.8.5 R3）：同一个 .blend、同一个进程，按镜头依次搭镜头（焦距、预设可以不同）→
    跟随 → 渲染。GPU 内核随镜头改写，下一次渲染自动重新编译（每支镜头第一次约 10 秒，之后有缓存）。
    shots：[dict(source="S1_EXT", frames=(1, 72), focal_mm=21, preset=..., name=..., settings={...}, focus_target=...,
    rack=[...], spp=..., exposure=...)]；None = 按时间线标记（shots_from_markers）。每项没给的参数用这里的（preset、passes、
    spp、guard 以及 common 里的任何 render_shot 参数）。每个镜头写到 <out_dir>/<name>/，日志各自一份，
    另有 <out_dir>/sequence_log.json（每个镜头的摘要）。→ 汇总 dict。"""
    import bpy
    sc = scene or bpy.context.scene
    shots = list(shots) if shots is not None else shots_from_markers(sc)
    if not shots:
        raise ValueError("render_sequence：没有镜头（shots 为空，时间线上也没有绑定相机的标记）")
    out = bpy.path.abspath(out_dir)
    os.makedirs(out, exist_ok=True)
    seq = dict(started=time.strftime("%Y-%m-%d %H:%M:%S"), out_dir=out, shots=[])
    path = os.path.join(out, "sequence_log.json")

    def save():
        with open(path + ".partial", "w") as fh:
            json.dump(seq, fh, ensure_ascii=False, indent=1, default=str)
        os.replace(path + ".partial", path)
    for i, sh in enumerate(shots):
        sh = dict(sh)
        name = sh.pop("name", None) or sh.get("source") or f"shot{i + 1}"
        a, b = sh.pop("frames")
        kw = dict(common)
        kw.update(preset=sh.pop("preset", preset), passes=sh.pop("passes", passes), spp=sh.pop("spp", spp),
                  guard=sh.pop("guard", guard))
        src = sh.pop("source", None)
        kw.update(sh)
        if kw["preset"] is None:
            raise ValueError(f"render_sequence：镜头 {name} 没有 preset（每个镜头都要重新搭镜头）")
        t0 = time.perf_counter()
        log = render_shot(source_camera=src, frames=range(int(a), int(b) + 1), out_dir=os.path.join(out, name), scene=sc, **kw)
        seq["shots"].append(dict(name=name, source=src, frames=[a, b], seconds=round(time.perf_counter() - t0, 1),
                                 summary=log.get("summary"), stopped=log.get("stopped"), exposure=log.get("exposure"),
                                 log=os.path.join(out, name, "f_log.json")))
        save()
        if log.get("stopped"):
            seq["stopped"] = f"{name}: {log['stopped']}"
            break
    seq["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    return seq
