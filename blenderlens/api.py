# 稳定的脚本入口（需要 bpy）。启用扩展后：
#   import blenderlens.api as bl
#   cam = bl.build(preset="c_series_style.json", focal_mm=40, gate="16:9", height=1080)
#   bl.set_focus(cam, metres=8.5); bl.set_stop(cam, 2.8)
#   bl.render_still("//shot.png", flare=True)
# 距离一律用米（与场景单位无关）；镜头内部换算成毫米。
import math, os
from types import SimpleNamespace
import bpy

from . import optics, rig

__all__ = ["presets", "build", "set_focus", "set_stop", "lens_info", "render_still", "camera",
           "enable_gpu", "gpu_status", "remove_gpu_cache", "exposure_vs_pinhole", "render_frames", "follow_camera"]


def presets():
    """[(文件名, 名称, 简介)]：插件自带的处方预设。"""
    return optics.list_presets()


def camera(scene=None):
    """场景里的 BlenderLens 相机（优先场景相机）。"""
    scene = scene or bpy.context.scene
    if rig.is_blenderlens_camera(scene.camera):
        return scene.camera
    for ob in scene.objects:
        if rig.is_blenderlens_camera(ob):
            return ob
    return None


def build(preset="c_series_style.json", scene=None, *, prescription=None, focal_mm=0.0, gate="FULL",
          gate_size_mm=None, units="AUTO", height=1080, set_render=True, coated=True, film_nm=100.0,
          name="BL_Camera", make_active=True):
    """搭一支镜头并返回相机对象。
    preset：自带预设文件名，或任意 JSON 路径；prescription：直接给处方 dict（优先）。
    focal_mm：缩放到这个垂直焦距（0 = 原生）；gate：'FULL' / '2.39' / '16:9'（抽取，裁切）/ '16:9_NATIVE'（原生 16:9 满像圈，不裁切）/ 'CUSTOM'（配 gate_size_mm=(w, h)）。
    units：'AUTO' / 'SCENE'（保留米制等场景单位）/ 'MM'。"""
    scene = scene or bpy.context.scene
    if prescription is not None:
        rx = optics.normalize(prescription)
    else:
        path = preset if os.path.isabs(preset) or os.path.exists(preset) else os.path.join(optics.PRESCRIPTION_DIR, preset)
        rx = optics.load(path)
    if focal_mm and focal_mm > 0:
        rx = optics.rescale_to_vertical_efl(rx, focal_mm)
    w, h = gate_size_mm or (None, None)
    rx = optics.apply_gate(rx, gate, w, h)
    cam_ob = rig.build(SimpleNamespace(scene=scene), rx, coated=coated, film_nm=film_nm, name=name, units=units)
    if set_render:
        if make_active:
            scene.camera = cam_ob
        rig.render_preset(scene)
        scene.render.resolution_x, scene.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), height)
        scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0
    return cam_ob


def set_focus(cam_ob, metres=None, mm=None):
    """对焦距离（从像面量起）；None / 0 / inf = 无穷远。会按预设最近对焦截断。"""
    v_mm = mm if mm is not None else (None if metres is None else metres * 1000.0)
    p = cam_ob.data.blenderlens
    if v_mm is None or v_mm <= 0 or math.isinf(v_mm):
        p.focus_distance = 0.0
    else:
        p.focus_distance = v_mm / rig.mm_per_unit(rig._cam_scene(cam_ob))
    rig.apply_settings(cam_ob)
    return rig.focus_mm(cam_ob)


def set_stop(cam_ob, f_number):
    """光圈 f 值（低于镜头最大光圈时取最大光圈）。"""
    cam_ob.data.blenderlens.f_number = f_number
    rig.apply_settings(cam_ob)
    return cam_ob.data.blenderlens.f_number


def set_mumps_correction(cam_ob, level):
    """发腮补偿强度 0–1（0.8，只对带补偿板的 C / E / G 风格有效；其余预设返回 None）。
    1 = Auto Panatar 式完全补偿（默认）：每个距离压缩比 = 无穷远；0 = 1950 年代未校正。"""
    if not cam_ob.get("bl_has_compensator"):
        return None
    cam_ob.data.blenderlens.mumps_correction = float(level)
    return cam_ob.data.blenderlens.mumps_correction


def lens_info(cam_ob):
    """当前状态的光学信息（全部由处方算出）。"""
    rx = rig.read_prescription()
    dist = rig.focus_mm(cam_ob)
    S = optics.layout(rx, *optics.solve_focus(rx, dist))
    return dict(preset=cam_ob.get("bl_preset"), focus_mm=dist, f_number=cam_ob.data.blenderlens.f_number,
                efl_x=optics.paraxial_infinity(S, "Rx")[0], efl_y=optics.paraxial_infinity(S, "Ry")[0],
                squeeze=optics.squeeze_at(rx, dist), desqueeze=optics.desqueeze(rx),
                gate_mm=(rx["sensor"]["width"], rx["sensor"]["height"]), units=cam_ob.get("bl_units"),
                mm_per_scene_unit=rig.mm_per_unit(rig._cam_scene(cam_ob)), min_focus_mm=cam_ob.get("bl_min_focus_mm"),
                mumps_correction=(optics.compensator(rx) or {}).get("level"))


def render_still(filepath, scene=None, flare=True, n_grid=260):
    """渲染当前帧。flare=True：叠加正向追迹的炫光层（Render with Flare）。返回写出的路径。"""
    scene = scene or bpy.context.scene
    if rig.gpu_unsupported(scene):
        scene.cycles.device = 'CPU'
    rig.render_frame(scene, filepath, flare=flare, n_grid=n_grid)    # 含 LATERAL_POST 的横向色差
    return bpy.path.abspath(filepath)


def enable_gpu(use_gpu=True, backend='METAL'):
    """启用 BlenderLens GPU 路径（Metal 等没有 OSL 的 GPU）。必须在本进程第一次 GPU 渲染之前调用；
    命令行渲染：blender -b scene.blend --python-expr "import blenderlens.api as bl; bl.enable_gpu()" -a
    use_gpu=True 时同时把 Cycles 设成该 GPU 后端。返回 (是否启用, 说明)。"""
    from . import gpukernel
    ok = gpukernel.enable()
    if ok and use_gpu:
        pr = bpy.context.preferences.addons["cycles"].preferences
        pr.compute_device_type = backend
        pr.get_devices()
        for d in pr.devices:
            d.use = d.type == backend
        for sc in bpy.data.scenes:
            sc.cycles.device = 'GPU'
            cam = camera(sc)
            if cam is not None:
                rig.apply_settings(cam)
    return ok, gpukernel.status()["reason"]


def gpu_status():
    from . import gpukernel
    return gpukernel.status()


def remove_gpu_cache():
    from . import gpukernel
    gpukernel.remove_cache()


def exposure_vs_pinhole(cam_ob, n=25):
    """相对 Cycles 普通针孔相机（无渐晕、同场景亮度 → 像素值 = 亮度）的曝光差，单位 EV（档）。
    centre_ev：画面中心（含当前光圈下其他镜片的切光；开 Compensate Exposure 时只受光阑限光 = 0）。
    frame_mean_ev：整幅平均（再乘渐晕 × cos⁴ 的画面平均），即「同机位整幅比普通相机暗多少」。
    普通相机渲染要匹配 BlenderLens，就把曝光加上 frame_mean_ev（负数 = 调暗）。"""
    import numpy as np
    from . import lenspack as LP, pupil as PU
    rx = rig.read_prescription()
    p = cam_ob.data.blenderlens
    f = max(p.f_number, optics.nominal_f_number(rx))
    k = optics.nominal_f_number(rx) / f
    T0 = PU.axial_transmission(rx)
    Tk = PU.axial_transmission(rx, k=k)
    centre = Tk / T0 * (1.0 if p.exposure_compensate else k * k)
    vig = LP.vignette_map(rx, 64, 36, f_number=f, focus_dist=rig.focus_mm(cam_ob), n=n)
    mean = centre * float(np.mean(vig))
    return dict(centre_ev=round(math.log2(max(centre, 1e-9)), 3), frame_mean_ev=round(math.log2(max(mean, 1e-9)), 3),
                centre_rel=round(centre, 4), frame_mean_rel=round(mean, 4), f_number=f,
                exposure_compensate=bool(p.exposure_compensate))


def render_frames(scene=None, frames=None, out_dir="//frames", name="frame", seed=0, animated_seed=True,
                  flare=False, skip_existing=True, lock=True, stale_hours=12.0, file_ext=".exr"):
    """逐帧渲染（命令行 -b 用）：确定性、可断点续渲、跳过已有帧、多机分帧。
    · 种子：seed + 帧号（animated_seed=False 时恒为 seed），同一帧任何机器渲染结果相同；
    · 断点续渲：先写 <名>.partial，完成后原子改名；已存在的帧跳过；
    · 多机：两台机器对同一个共享目录跑同一条命令即可自动分帧 —— 每帧开始前原子创建 .lock
      （内容为主机名 / 进程 / 时间），别的机器看到未过期（< stale_hours）的锁就跳过；
      也可以显式分：frames=range(1, 145, 2) 与 range(2, 145, 2)。
    · 每帧都会按关键帧更新对焦 / 光圈（frame_set → apply_settings），GPU 路径同样生效。
    返回本次渲染的帧号列表。"""
    import socket, time as _time
    scene = scene or bpy.context.scene
    cam = camera(scene)
    if cam is None:
        raise RuntimeError("场景里没有 BlenderLens 相机")
    out = bpy.path.abspath(out_dir)
    os.makedirs(out, exist_ok=True)
    frames = list(frames) if frames is not None else list(range(scene.frame_start, scene.frame_end + 1, scene.frame_step))
    done = []
    for fr in frames:
        path = os.path.join(out, f"{name}_{fr:04d}{file_ext}")
        if skip_existing and os.path.exists(path):
            continue
        lk = path + ".lock"
        if lock:
            try:
                fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{socket.gethostname()} pid {os.getpid()} {_time.strftime('%Y-%m-%d %H:%M:%S')}".encode())
                os.close(fd)
            except FileExistsError:
                if _time.time() - os.path.getmtime(lk) < stale_hours * 3600:
                    continue                                   # 另一台机器正在渲染这一帧
                os.remove(lk)                                  # 过期的锁（上次中断）：接手
                fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd)
        try:
            scene.frame_set(fr)
            rig.apply_settings(cam)
            scene.cycles.seed = seed + (fr if animated_seed else 0)
            scene.cycles.use_animated_seed = False
            if rig.gpu_unsupported(scene):
                scene.cycles.device = 'CPU'
            tmp = path + ".partial" + file_ext
            rig.render_frame(scene, tmp, flare=flare)
            os.replace(tmp, path)
            done.append(fr)
        finally:
            if lock and os.path.exists(lk):
                os.remove(lk)
    return done


def follow_camera(cam_ob, source, frame_start=None, frame_end=None, scene=None, substeps=1, focus=True,
                  interpolation='BEZIER'):
    """让 BlenderLens 相机逐帧跟随源相机，并烘焙成关键帧（位置、旋转，可选对焦距离）。
    只在每帧拷贝 matrix_world 时，快门时间里相机不动，相机运动模糊会丢失；烘焙成关键帧后 Cycles 可以在快门内插值。
    substeps > 1 时在子帧也采样（快门内运动是曲线或很快时更准，例如 substeps=4）。
    focus=True：同时把源相机的景深对焦距离（或对焦物体的距离）烘焙到 focus_distance。
    返回烘焙的关键帧数。相机不能有父物体（matrix_world 直接写到自身变换）。"""
    import math as _m
    scene = scene or bpy.context.scene
    fs = scene.frame_start if frame_start is None else frame_start
    fe = scene.frame_end if frame_end is None else frame_end
    if cam_ob.parent is not None:
        raise RuntimeError("follow_camera：BlenderLens 相机不能有父物体")
    cam_ob.rotation_mode = 'XYZ'
    prev = None
    n = 0
    cur = (scene.frame_current, scene.frame_subframe)
    p = cam_ob.data.blenderlens
    for f in range(fs, fe + 1):
        for k in range(max(1, int(substeps))):
            sub = k / max(1, int(substeps))
            if f == fe and sub > 0:
                break
            scene.frame_set(f, subframe=sub)
            mw = source.matrix_world.copy()
            loc, rot, _ = mw.decompose()
            eul = rot.to_euler('XYZ', prev) if prev is not None else rot.to_euler('XYZ')
            prev = eul
            fr = f + sub
            cam_ob.location = loc
            cam_ob.rotation_euler = eul
            cam_ob.keyframe_insert("location", frame=fr)
            cam_ob.keyframe_insert("rotation_euler", frame=fr)
            if focus and source.type == 'CAMERA' and source.data.dof.use_dof:
                dof = source.data.dof
                if dof.focus_object is not None:
                    d = (dof.focus_object.matrix_world.translation - mw.translation).length
                else:
                    d = dof.focus_distance
                p.focus_distance = d                            # 场景单位，与源相机一致
                cam_ob.data.keyframe_insert("blenderlens.focus_distance", frame=fr)
            n += 1
    for ad in (cam_ob.animation_data, cam_ob.data.animation_data):
        if ad and ad.action:
            for fc in _action_fcurves(ad.action):
                for kp in fc.keyframe_points:
                    kp.interpolation = interpolation
    scene.frame_set(cur[0], subframe=cur[1])
    rig.apply_settings(cam_ob)
    return n


def _action_fcurves(action):
    """Blender 4.4+ 的分层动作（layers / strips / channelbags）与旧的 action.fcurves 都兼容。"""
    fcs = list(getattr(action, "fcurves", []) or [])
    if fcs:
        return fcs
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out
