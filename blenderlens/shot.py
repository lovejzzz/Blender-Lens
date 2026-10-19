# 一条命令渲一个镜头（0.8.4，MessyRoom R5）。需要 bpy。
#   bl.render_shot(source_camera="CamA", frames=range(40, 57), out_dir="//shots/heli", preset="c_series_style",
#                  passes="temporal", spp=256, follow=True, guard=True)
# 做的事（每一镜都要写的那些）：
#   1. 搭镜头（或用场景里已有的 BlenderLens 相机）；
#   2. follow：逐帧跟随源相机并烘焙位置、旋转、对焦距离、f 值（follow_camera(stop=True)）；
#   3. exposure="pinhole"：用 Cycles 的胶片曝光（film_exposure，写进 EXR 数据本身）补偿镜头的整幅平均曝光差，
#      让成片与普通针孔相机同样亮（exposure_vs_pinhole 的 frame_mean_ev）；"keep" = 不动；
#   4. 逐帧渲染：确定性种子、跳过已有帧、写 .partial 再改名、多机锁（render_frames）；
#   5. guard：每帧开始前看系统可用内存，低于 guard_min_gb 就等（每 15 秒看一次，最多 guard_wait_s 秒），等到了再渲；
#   6. 日志：<out_dir>/<name>_log.json，每帧写一次（用时、各步用时、峰值常驻内存、开始前可用内存、等待时间）。
import json, os, platform, resource, subprocess, sys, time


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
                guard_min_gb=4.0, guard_wait_s=1800.0, file_ext=".exr", lock=True):
    """渲一个镜头 → 日志 dict（同时写 <out_dir>/<name>_log.json）。
    source_camera：源相机（物体或名字）；None = 场景当前的相机（若它不是 BlenderLens 相机）。follow=False 时可以不给。
    frames：帧号序列；None = 场景的起止帧。preset：None = 用场景里已有的 BlenderLens 相机；给了 = 搭一支新镜头
    （focal_mm、gate、units、height 同 bl.build）。passes / exr_codec / half_color / flare 同 bl.render_frames。
    spp：采样数（None = 不改）。exposure："keep" 或 "pinhole"（见文件头）。"""
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
    if exposure not in ("keep", "pinhole"):
        raise ValueError(f"exposure 必须是 'keep' 或 'pinhole'，收到 {exposure!r}")
    cam = api.camera(sc)
    if preset is not None or cam is None:
        if preset is None:
            raise ValueError("场景里没有 BlenderLens 相机：请给 preset")
        pf = preset if preset.endswith(".json") else preset + ".json"
        cam = api.build(pf, sc, focal_mm=focal_mm or 0.0, gate=gate, units=units,
                        height=height or sc.render.resolution_y, make_active=True)
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
    if spp is not None:
        sc.cycles.samples = int(spp)
    if exposure == "pinhole":
        sc.frame_set(frames[0]); rig.apply_settings(cam)
        ev = api.exposure_vs_pinhole(cam)
        sc.cycles.film_exposure = 2.0 ** (-ev["frame_mean_ev"])
        log["exposure"] = dict(mode="pinhole", frame_mean_ev=ev["frame_mean_ev"], film_exposure=round(sc.cycles.film_exposure, 4))
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
                                 file_ext=file_ext, passes=passes, exr_codec=exr_codec, half_color=half_color)
        rec.update(rendered=bool(done), seconds=round(time.perf_counter() - t0, 2), timing=dict(rig.LAST_TIMING),
                   rss_gb=_rss_gb(), max_rss_gb=_max_rss_gb(), avail_gb_after=_available_gb(),
                   size_mb=round(os.path.getsize(path) / 1e6, 1) if os.path.exists(path) else None)
        if not done:
            rec["skipped"] = "locked"                        # 另一台机器在渲
        log["records"].append(rec); save()
    log["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    return log
