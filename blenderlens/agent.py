# AI 原生接口（0.8.1，需要 bpy）：让 AI / 脚本不看界面也能完整地发现、设置、检查、预览镜头。
#   import blenderlens.api as bl
#   bl.describe()                    → 版本、预设（含关键光学数字）、全部相机设置的模式（类型 / 范围 / 选项 / 说明）、API 列表
#   bl.state(cam)                    → 当前全部设置 + 光学信息 + 渲染设置 + GPU 状态（可 JSON 序列化）
#   bl.configure(cam, f_number=2.8, focus_m=3, coating_type='BROADBAND')   → 校验后设置，返回改了什么
#   bl.diagnose()                    → 场景里的问题清单（level / code / message / fix）
#   bl.preview("//p.png")            → 快速低清渲染 + 图像统计（给 AI 看图用）
#   bl.contact_sheet("//ab.png", [dict(f_number=2.3), dict(f_number=8)])   → 多个设置并排成一张图
# 所有返回值都只含 JSON 可序列化的基本类型。
import inspect, json, math, os, time
import bpy

from . import optics, rig

FOCUS_KEYS = ("focus_m", "focus_mm")             # configure 的特殊键：对焦（米 / 毫米，0 或 None = 无穷远）


def _settings_rna():
    return bpy.types.Camera.bl_rna.properties["blenderlens"].fixed_type


def _prop_schema(prop):
    d = dict(type=prop.type.lower(), name=prop.name, description=prop.description)
    if prop.type in ('FLOAT', 'INT'):
        if getattr(prop, "array_length", 0):
            d["length"] = prop.array_length
            d["default"] = list(prop.default_array)
        else:
            d["default"] = prop.default
        d["min"], d["max"] = prop.hard_min, prop.hard_max
        if prop.soft_max != prop.hard_max:
            d["soft_max"] = prop.soft_max
        if prop.subtype not in ('NONE', ''):
            d["subtype"] = prop.subtype.lower()
    elif prop.type == 'BOOLEAN':
        d["default"] = prop.default
    elif prop.type == 'ENUM':
        d["default"] = prop.default
        d["items"] = {it.identifier: f"{it.name}：{it.description}" if it.description else it.name for it in prop.enum_items}
    return d


def settings_schema():
    """相机设置（cam.data.blenderlens 的每个属性）的模式，直接从 Blender 的属性定义读出，不会与代码脱节。"""
    out = {}
    for prop in _settings_rna().properties:
        if prop.identifier == "rna_type":
            continue
        out[prop.identifier] = _prop_schema(prop)
    out["focus_m"] = dict(type="float", description="（configure 专用）对焦距离，米，从像面量起；0 / None = 无穷远；低于最近对焦按最近对焦算")
    out["focus_mm"] = dict(type="float", description="（configure 专用）同上，毫米")
    return out


def _preset_summary(fn, name, tag):
    rx = optics.load(os.path.join(optics.PRESCRIPTION_DIR, fn))
    S = optics.layout(rx)
    mfd = rx.get("focus", {}).get("min_distance_mm") or rx.get("look", {}).get("min_focus_mm")
    d = dict(file=fn, name=name, tagline=tag,
             efl_y_mm=round(optics.paraxial_infinity(S, "Ry")[0], 3), efl_x_mm=round(optics.paraxial_infinity(S, "Rx")[0], 3),
             f_number=rx.get("look", {}).get("f_number", optics.nominal_f_number(rx)), min_focus_mm=mfd,
             squeeze_inf=round(optics.squeeze_at(rx, math.inf), 4), desqueeze=optics.desqueeze(rx),
             sensor_mm=[rx["sensor"]["width"], rx["sensor"]["height"]],
             has_mumps_compensator=bool(optics.compensator(rx)),
             real_prescription=not any(w in name for w in ("风格", "style")))
    if mfd:
        d["squeeze_at_min_focus"] = round(optics.squeeze_at(rx, mfd), 4)
    return d


def _api_listing():
    from . import api
    out = {}
    for n in api.__all__:
        f = getattr(api, n, None)
        if callable(f):
            out[n] = dict(signature=f"{n}{inspect.signature(f)}", doc=(inspect.getdoc(f) or "").split("\n")[0])
    return out


def version():
    """扩展版本（读 blender_manifest.toml，唯一来源）。"""
    import re
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml")
    m = re.search(r'^version\s*=\s*"([^"]+)"', open(path, encoding="utf-8").read(), re.M)
    return m.group(1) if m else None


def describe(include_presets=True):
    """一次拿到所有「我能做什么」：版本、预设、设置模式、API。"""
    from . import gpukernel
    d = dict(version=version(), blender=bpy.app.version_string,
             units_note="API 距离一律用米（configure 的 focus_m）；镜头内部毫米；场景单位任意（见 lens_info 的 mm_per_scene_unit）",
             settings=settings_schema(), api=_api_listing(),
             gpu=dict(gpukernel.status()))
    if include_presets:
        d["presets"] = [_preset_summary(fn, name, tag) for fn, name, tag in optics.list_presets()]
    return d


def _jsonable(v):
    if isinstance(v, (bool, int, str)) or v is None:
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            return None if math.isnan(v) else ("inf" if v > 0 else "-inf")
        return float(f"{v:.7g}")                       # 去掉 float32 属性的尾数噪声（2.299999952 → 2.3）
    if hasattr(v, "__len__") and not isinstance(v, dict):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def _cam(cam_ob):
    from . import api
    cam_ob = cam_ob or api.camera()
    if cam_ob is None:
        raise RuntimeError("场景里没有 BlenderLens 相机：先 bl.build(...)")
    return cam_ob


def state(cam_ob=None):
    """当前全部设置 + 光学信息 + 渲染设置 + GPU 状态。"""
    from . import api, gpukernel
    cam_ob = _cam(cam_ob)
    p = cam_ob.data.blenderlens
    settings = {k: _jsonable(getattr(p, k)) for k in settings_schema() if hasattr(p, k)}
    sc = rig._cam_scene(cam_ob)
    r, c = sc.render, sc.cycles
    render = dict(resolution=[r.resolution_x, r.resolution_y], percentage=r.resolution_percentage, engine=r.engine,
                  device=getattr(c, "device", None), samples=getattr(c, "samples", None),
                  adaptive=getattr(c, "use_adaptive_sampling", None), denoise=getattr(c, "use_denoising", None),
                  motion_blur=r.use_motion_blur, frame=sc.frame_current, view_transform=sc.view_settings.view_transform)
    try:
        render["compute_device_type"] = bpy.context.preferences.addons["cycles"].preferences.compute_device_type
    except (KeyError, AttributeError):
        pass
    return _jsonable(dict(camera=cam_ob.name, settings=settings, lens=api.lens_info(cam_ob), render=render,
                          gpu=dict(gpukernel.status(), block_reason=rig.gpu_block_reason(sc)),
                          location=list(cam_ob.matrix_world.translation), animated=bool(cam_ob.data.animation_data)))


def configure(cam_ob=None, **settings):
    """按名字设置相机属性（见 describe()['settings']），先全部校验再一次写入；返回 {名字: [旧值, 新值]}。
    未知名字、枚举值不合法、超出硬范围 → ValueError（信息里列出可选值），什么都不改。"""
    cam_ob = _cam(cam_ob)
    p = cam_ob.data.blenderlens
    schema = settings_schema()
    errors, plan = [], []
    for k, v in settings.items():
        if k not in schema:
            close = [n for n in schema if k.split("_")[0] in n]
            errors.append(f"未知设置 {k!r}" + (f"（是不是 {close}？）" if close else "") + f"；可用：{sorted(schema)}")
            continue
        s = schema[k]
        if k in FOCUS_KEYS:
            plan.append((k, v)); continue
        if s["type"] == "enum" and v not in s["items"]:
            errors.append(f"{k} = {v!r} 不合法；可选 {list(s['items'])}")
        elif s["type"] in ("float", "int") and "length" not in s:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                errors.append(f"{k} 需要数字，收到 {v!r}"); continue
            if not (s["min"] - 1e-9 <= fv <= s["max"] + 1e-9):
                errors.append(f"{k} = {v} 超出范围 [{s['min']}, {s['max']}]")
        plan.append((k, v))
    if errors:
        raise ValueError("；".join(errors))
    changed = {}
    for k, v in plan:
        if k in FOCUS_KEYS:
            from . import api
            old = rig.focus_mm(cam_ob)
            mm = None if v in (None, 0, "inf") else (float(v) * (1000.0 if k == "focus_m" else 1.0))
            new = api.set_focus(cam_ob, mm=mm)
            changed["focus_mm"] = [_jsonable(old), _jsonable(new)]
            continue
        old = _jsonable(getattr(p, k))
        if k == "mumps_correction" and not cam_ob.get("bl_has_compensator"):
            raise ValueError("这支镜头没有发腮补偿板（只有 C / E / G 风格有）")
        setattr(p, k, v)
        new = _jsonable(getattr(p, k))
        if new != old:
            changed[k] = [old, new]
    rig.apply_settings(cam_ob)
    return changed


def _issue(level, code, message, fix):
    return dict(level=level, code=code, message=message, fix=fix)


def diagnose(scene=None):
    """检查场景里常见的问题。level：error（会出错 / 出黑图）、warning（结果可能不对）、info（建议）。"""
    from . import api, gpukernel
    sc = scene or bpy.context.scene
    out = []
    cams = [ob for ob in sc.objects if rig.is_blenderlens_camera(ob)]
    if not cams:
        return [_issue("error", "no_camera", "场景里没有 BlenderLens 相机", "bl.build('c_series_style.json')")]
    if len(cams) > 1:
        out.append(_issue("warning", "multiple_cameras", f"有 {len(cams)} 支 BlenderLens 相机，但每个 .blend 只能有一支（处方只存一份）",
                          "删掉多余的，或重新 bl.build"))
    cam = sc.camera if sc.camera in cams else cams[0]
    if sc.camera not in cams:
        out.append(_issue("warning", "not_scene_camera", f"场景相机是 {sc.camera.name if sc.camera else None}，不是 BlenderLens 相机",
                          f"bpy.context.scene.camera = bpy.data.objects[{cam.name!r}]"))
    p = cam.data.blenderlens
    c = sc.cycles
    if sc.render.engine != 'CYCLES':
        out.append(_issue("error", "engine", f"渲染引擎是 {sc.render.engine}，BlenderLens 只支持 Cycles", "bpy.context.scene.render.engine = 'CYCLES'"))
    reason = rig.gpu_block_reason(sc)
    if reason:
        out.append(_issue("warning", "gpu_fallback", f"设成了 GPU，但这次会自动改回 CPU：{reason}",
                          "第一次 GPU 渲染前调用 bl.enable_gpu()；Geometry 模式只能 CPU"))
    elif getattr(c, "device", 'CPU') == 'CPU' and not gpukernel.is_active():
        out.append(_issue("info", "cpu_only", "正在用 CPU 渲染；Metal 上 GPU 路径快约 19 倍", "bl.enable_gpu()（本次启动后第一次 GPU 渲染之前）"))
    rx = rig.read_prescription()
    mfd = cam.get("bl_min_focus_mm") or 0.0
    want = mfd and p.focus_distance > 0 and p.focus_distance * rig.mm_per_unit(sc) < mfd - 1e-6
    if want:
        out.append(_issue("warning", "focus_below_min", f"对焦距离 {p.focus_distance * rig.mm_per_unit(sc):.0f} mm 小于最近对焦 {mfd:.0f} mm，按最近对焦算",
                          "bl.configure(cam, focus_m=...) 设 ≥ 最近对焦"))
    W, H = rig.desqueezed_resolution(rx, sc.render.resolution_y)
    if abs(sc.render.resolution_x - W) > 2:
        out.append(_issue("warning", "resolution_not_desqueezed",
                          f"输出 {sc.render.resolution_x}×{sc.render.resolution_y}，按解挤压应为 {W}×{H}（画面会被拉伸）",
                          f"scene.render.resolution_x = {W}"))
    spp = getattr(c, "samples", 0)
    if p.mode == 'GEOMETRY' and spp < 512:
        out.append(_issue("warning", "geometry_low_spp", f"Geometry 模式 {spp} spp 噪点很多", "≥ 512 spp，或改用 Analytic"))
    elif p.dispersion and p.dispersion_mode == 'FULL' and spp < 256 and not getattr(c, "use_denoising", False):
        out.append(_issue("info", "dispersion_noise", f"全色散 {spp} spp 且未降噪：色噪明显", "≥ 512 spp + OIDN，或 dispersion_mode='LATERAL_POST'"))
    if (p.ghosts or p.mode == 'GEOMETRY') and getattr(c, "use_adaptive_sampling", False):
        out.append(_issue("warning", "adaptive_with_ghosts", "鬼影 / Geometry 模式下自适应采样会把鬼影低估约 8 倍", "scene.cycles.use_adaptive_sampling = False"))
    if p.dispersion and p.dispersion_mode == 'LATERAL_POST':
        out.append(_issue("info", "lateral_post_only_api", "Lateral in Post 的横向色差只在 bl.render_still / render_frames / Render with Flare 输出里加，F12 看不到",
                          "用 bl.render_still(...) 出图"))
    if (p.starburst or p.veiling_glare > 0) and not rig.flare_sources(sc):
        out.append(_issue("info", "no_flare_source", "开了星芒 / 杂光但没有炫光光源；杂光仍会加，星芒不会", "obj['bl_flare_source'] = 1 标记小的发光物体"))
    if p.ghosts and p.mode == 'ANALYTIC':
        out.append(_issue("info", "camera_ghosts", "相机端鬼影只适合大面积亮源；点光源炫光用 bl.render_still(flare=True)", ""))
    us = rig.mm_per_unit(sc)
    dims = [max(o.dimensions) * us for o in sc.objects if o.type == 'MESH' and not o.name.startswith("BL_") and o.parent is None]
    if dims and max(dims) < 5.0:
        out.append(_issue("warning", "scene_tiny", f"场景里最大的物体只有 {max(dims):.2f} mm——单位可能不对（米制场景却按毫米搭了镜头？）",
                          "bl.build(..., units='SCENE') 保留场景单位"))
    if cam.data.animation_data and sc.render.use_motion_blur and not (cam.animation_data and cam.animation_data.action):
        out.append(_issue("info", "motion_blur_static_camera", "开了运动模糊但相机没有位置关键帧（每帧拷贝矩阵会丢运动模糊）", "bl.follow_camera(cam, source)"))
    return out


def _image_stats(path):
    im = bpy.data.images.load(path, check_existing=False)
    try:
        import numpy as np
        a = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)[:, :, :3]
        lum = a.mean(axis=2)
        return dict(size=list(im.size), mean_rgb=[round(float(v), 5) for v in a.reshape(-1, 3).mean(axis=0)],
                    max=round(float(a.max()), 4), clipped_fraction=round(float((a >= 1.0).any(axis=2).mean()), 5),
                    black_fraction=round(float((lum <= 1e-4).mean()), 5))
    finally:
        bpy.data.images.remove(im)


QUALITY = dict(draft=(64, 270), standard=(256, 540), final=(1024, 1080))


def preview(filepath="//blenderlens_preview.png", quality="draft", samples=None, height=None, flare=True, scene=None):
    """快速渲染（给 AI 看图）。quality：draft（64 spp、270 高）/ standard（256、540）/ final（1024、1080）。
    临时改采样与分辨率，渲完恢复。返回 {path, seconds, device, stats, state}。"""
    from . import api
    sc = scene or bpy.context.scene
    cam = _cam(api.camera(sc))
    spp0, h0 = QUALITY[quality]
    spp, h = samples or spp0, height or h0
    r, c = sc.render, sc.cycles
    keep = (r.resolution_x, r.resolution_y, r.resolution_percentage, c.samples, c.use_denoising)
    rx = rig.read_prescription()
    try:
        r.resolution_x, r.resolution_y = rig.desqueezed_resolution(rx, h)
        r.resolution_percentage = 100
        c.samples = spp
        c.use_denoising = quality != "draft"
        t = time.time()
        path = api.render_still(filepath, scene=sc, flare=flare)
        dt = time.time() - t
        dev = c.device
    finally:
        r.resolution_x, r.resolution_y, r.resolution_percentage, c.samples, c.use_denoising = keep
    return _jsonable(dict(path=path, seconds=round(dt, 2), device=dev, samples=spp, stats=_image_stats(path), state=state(cam)))


def contact_sheet(filepath, variants, quality="draft", cols=None, samples=None, height=None, flare=True, scene=None):
    """把多组设置并排渲染成一张图（A/B 比较）。variants：[{设置名: 值, …}, …]（同 configure，可含 focus_m）。
    每格按顺序从左到右、从上到下；渲完恢复原设置。返回 {path, grid, tiles: [{index, row, col, settings, stats}]}。"""
    import numpy as np, tempfile
    from . import api
    sc = scene or bpy.context.scene
    cam = _cam(api.camera(sc))
    p = cam.data.blenderlens
    base = {k: getattr(p, k) for k in settings_schema() if hasattr(p, k)}
    base_focus = rig.focus_mm(cam)
    for v in variants:                                       # 先全部校验，免得渲到一半才报错
        bad = [k for k in v if k not in settings_schema()]
        if bad:
            raise ValueError(f"未知设置 {bad}")
    tiles, imgs = [], []
    tmpdir = tempfile.mkdtemp(prefix="blenderlens_sheet_")
    try:
        for i, v in enumerate(variants):
            configure(cam, **v)
            res = preview(os.path.join(tmpdir, f"tile_{i}.png"), quality=quality, samples=samples, height=height, flare=flare, scene=sc)
            im = bpy.data.images.load(res["path"], check_existing=False)
            imgs.append(np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4))
            bpy.data.images.remove(im)
            tiles.append(dict(index=i, settings=_jsonable(v), stats=res["stats"], seconds=res["seconds"]))
    finally:
        for k, val in base.items():
            try:
                setattr(p, k, val)
            except (TypeError, AttributeError, ValueError):
                pass
        api.set_focus(cam, mm=base_focus if math.isfinite(base_focus) else None)
    n = len(imgs)
    cols = cols or min(n, 3)
    rows = -(-n // cols)
    h, w = imgs[0].shape[:2]
    gap = 4
    sheet = np.zeros((rows * h + (rows - 1) * gap, cols * w + (cols - 1) * gap, 4), np.float32)
    sheet[..., 3] = 1.0
    for i, a in enumerate(imgs):
        rr, cc = divmod(i, cols)
        y0 = (rows - 1 - rr) * (h + gap)                      # Blender 图像原点在左下
        sheet[y0:y0 + h, cc * (w + gap):cc * (w + gap) + w] = a[:h, :w]
        tiles[i].update(row=rr, col=cc)
    out = bpy.data.images.new("bl_contact_sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    out.pixels.foreach_set(sheet.ravel())
    out.filepath_raw = bpy.path.abspath(filepath)
    out.file_format = 'PNG'
    out.save()
    bpy.data.images.remove(out)
    return _jsonable(dict(path=bpy.path.abspath(filepath), grid=[rows, cols], tile_size=[w, h], tiles=tiles))


def dump(obj):
    """JSON 字符串（中文不转义），方便 print 给调用方解析。"""
    return json.dumps(_jsonable(obj), ensure_ascii=False)
