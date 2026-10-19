# 在 Blender 里由处方搭镜头：OSL 相机 + 界面法镜片网格 + 镜框/镜筒 + 对焦分组。
# 层级：BL_Camera（相机，传感器中心）
#        └ BL_Rig（绕 Y 转 180°：镜头坐标 +Z 物方 → 相机视线方向；缩放 = 场景单位 / mm，镜头内部恒为毫米）
#            ├ BL_Move_c1_c2（对焦分组：该组镜片移动 c1·Δ1 + c2·Δ2，系数来自处方 focus.groups）
#            └ 固定的镜片、镜筒、后盖
import json, math
import bpy
import numpy as np

from . import optics as O
from . import meshgen as G
from . import oslgen
from . import gpukernel

TEXT_RX = "BL_prescription.json"
TEXT_OSL = "BL_camera.osl"


# ───────────────────────── 材质 ─────────────────────────

def _black():
    m = bpy.data.materials.get("BL_Black")
    if m:
        return m
    m = bpy.data.materials.new("BL_Black")
    if m.node_tree is None:                          # Blender 6.0 起 use_nodes 移除，材质恒有节点树
        m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (0.004, 0.004, 0.004, 1)
    p.inputs["Roughness"].default_value = 0.85
    p.inputs["Specular IOR Level"].default_value = 0.15     # 消光漆仍有一点点反射
    m.diffuse_color = (0.02, 0.02, 0.02, 1)
    return m

def _interface(sf_id, ior, coatable, coated, film_nm, film_ior):
    """界面法材质：IOR = n_后 / n_前（法线朝物方）。空气-玻璃面可加 MgF₂ 薄膜。"""
    name = f"BL_IF_{sf_id}"
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if m.node_tree is None:                          # Blender 6.0 起 use_nodes 移除，材质恒有节点树
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    g = nt.nodes.new("ShaderNodeBsdfGlass")
    g.inputs["IOR"].default_value = ior
    g.inputs["Roughness"].default_value = 0.0
    g.inputs["Thin Film Thickness"].default_value = film_nm if (coated and coatable) else 0.0
    g.inputs["Thin Film IOR"].default_value = film_ior
    nt.links.new(g.outputs[0], out.inputs["Surface"])
    m.diffuse_color = (0.6, 0.8, 1.0, 0.25)
    m["bl_ior"] = ior
    m["bl_coatable"] = 1 if coatable else 0
    return m


# ───────────────────────── 网格 ─────────────────────────

def _mesh_object(name, data, mats, parent, collection):
    V, F, N, M = data
    me = bpy.data.meshes.get(name)
    if me:
        bpy.data.meshes.remove(me)
    me = bpy.data.meshes.new(name)
    me.from_pydata(V, [], F)
    for m in mats:
        me.materials.append(m)
    me.polygons.foreach_set("material_index", M)
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices(N)
    me.validate(clean_customdata=False)
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    ob.parent = parent
    return ob

def _empty(name, parent, collection, loc=(0, 0, 0), rot=(0, 0, 0)):
    ob = bpy.data.objects.new(name, None)
    collection.objects.link(ob)
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    ob.empty_display_size = 5
    return ob


# ───────────────────────── 处方读写（存进 .blend，便于搬运）─────────────────────────

def store_prescription(rx):
    t = bpy.data.texts.get(TEXT_RX) or bpy.data.texts.new(TEXT_RX)
    t.clear()
    t.write(json.dumps(rx, ensure_ascii=False, indent=2))
    return t

def read_prescription():
    t = bpy.data.texts.get(TEXT_RX)
    return O.normalize(json.loads(t.as_string())) if t else O.load()


# ───────────────────────── 搭建 ─────────────────────────

def setup_scene_units(scene):
    us = scene.unit_settings
    us.system = 'METRIC'
    us.scale_length = 0.001
    us.length_unit = 'MILLIMETERS'

# ── 场景单位：镜头内部（处方、对焦求解、OSL 追迹）恒为毫米；与场景之间只差一个统一比例 ──
FACTORY_OBJECTS = {"Cube", "Light", "Camera"}

def mm_per_unit(scene):
    """1 个场景单位 = 多少 mm（毫米场景 1，米制场景 1000）。"""
    # scale_length 是 float32（0.001 存成 0.0010000000475）→ 取 6 位小数，毫米场景恰好为 1
    return round(max(scene.unit_settings.scale_length, 1e-12) * 1000.0, 6)

def unit_scale(scene):
    """场景单位 / mm：OSL 出射光线起点与 BL_Rig 的缩放。"""
    return 1.0 / mm_per_unit(scene)

def _cam_scene(cam_ob):
    sc = cam_ob.users_scene
    return sc[0] if sc else bpy.context.scene

def resolve_units(scene, units='AUTO'):
    """'MM'：把场景改成毫米（旧行为）；'SCENE'：保留场景单位；
    'AUTO'：已是毫米或场景里有用户内容（除默认立方体/灯/相机之外的物体）→ 保留，空场景 → 改成毫米。
    返回实际采用的方式（'MM' / 'SCENE'）。"""
    if units == 'AUTO':
        if abs(scene.unit_settings.scale_length - 0.001) < 1e-12:
            units = 'SCENE'
        else:
            user = [o for o in scene.objects if o.name.split(".")[0] not in FACTORY_OBJECTS
                    and not o.name.startswith("BL_")]
            units = 'SCENE' if user else 'MM'
    if units == 'MM':
        setup_scene_units(scene)
    return units

def focus_mm(cam_ob):
    """面板对焦距离（场景单位）→ 镜头对焦距离（mm，从像面量起），并按最近对焦截断；0 = 无穷远。"""
    p = cam_ob.data.blenderlens
    if p.focus_distance <= 0:
        return math.inf
    return max(p.focus_distance * mm_per_unit(_cam_scene(cam_ob)), cam_ob.get("bl_min_focus_mm", 0.0))

def desqueezed_resolution(rx, height, desqueeze=None):
    """输出分辨率：传感器宽高比 × 解挤压比（处方 anamorphic.desqueeze）。"""
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    return int(round(height * W / H * (desqueeze or O.desqueeze(rx)))), int(height)

def build(context, rx=None, coated=True, film_nm=100.0, film_ior=1.38, name="BL_Camera", units='AUTO'):
    """搭整支镜头。返回相机对象。重复调用会先删除旧的同名镜头。
    units：'AUTO' / 'SCENE'（保留场景单位，如米制生产场景）/ 'MM'（把场景改成毫米），见 resolve_units。"""
    rx = rx or O.load()
    scene = context.scene
    units_used = resolve_units(scene, units)
    if rx_needs_apertures(rx):
        rx, _ = O.apply_apertures(rx)
    store_prescription(rx)
    remove(name)

    col = bpy.data.collections.new("BlenderLens")
    scene.collection.children.link(col)
    lens_col = bpy.data.collections.new("BL_LensGeometry")
    col.children.link(lens_col)

    # 相机 + OSL
    cam = bpy.data.cameras.new(name)
    cam.type = 'CUSTOM'
    cam.custom_mode = 'INTERNAL'
    txt = bpy.data.texts.get(TEXT_OSL) or bpy.data.texts.new(TEXT_OSL)
    txt.clear()
    txt.write(oslgen.generate(rx))
    cam.custom_shader = txt
    cam.sensor_fit = 'HORIZONTAL'
    cam.sensor_width = rx["sensor"]["width"]
    cam.clip_start = 0.1 * unit_scale(scene)
    cam.clip_end = 1e7 * unit_scale(scene)
    cam_ob = bpy.data.objects.new(name, cam)
    col.objects.link(cam_ob)
    compile_camera(cam)

    rig = _empty("BL_Rig", cam_ob, col, rot=(0.0, math.pi, 0.0))
    S = O.layout(rx)
    # 对焦分组：每种移动系数 (c1, c2) 一个空物体，位置 z = c1·Δ1 + c2·Δ2（apply_settings 写入）
    movers = {}
    for c in sorted({sf["move"] for sf in S if sf["move"] != (0, 0)}):
        e = _empty(f"BL_Move_{c[0]:g}_{c[1]:g}", rig, col)
        e["bl_move"] = list(c)
        movers[c] = e
    parent_of = {sf["id"]: movers.get(sf["move"], rig) for sf in S}
    nb, na = O.media(S)
    black = _black()

    for k, sf in enumerate(S):
        # 界面法：IOR 取 ≥ 1 的那一侧比值；n_后 < n_前 时法线翻到像方。
        # 与「法线恒朝 +Z、IOR = n_后/n_前」物理等价，但 Cycles 的薄膜在 IOR < 1 时透过率错误（实测 0.76 vs 0.99）。
        flip = na[k] < nb[k]
        ior = nb[k] / na[k] if flip else na[k] / nb[k]
        air_glass = (nb[k] == 1.0) != (na[k] == 1.0)
        mat = _interface(sf["id"], ior, air_glass, coated, film_nm, film_ior)
        ob = _mesh_object(f"BL_S_{sf['id']}", G.surface_mesh(sf, flip=flip), [mat, black],
                          parent_of.get(sf["id"], rig), lens_col)
        ob["bl_surface"] = sf["id"]
        ob["bl_normal_sign"] = -1 if flip else 1

    # 每片镜片：侧壁 + 挡板（挡板外缘贴镜筒，镜片之间不漏光）
    by_id = {sf["id"]: sf for sf in S}
    R_barrel = max(O.ap_limits(sf["ap"])[3] for sf in S) + 3.0
    for ids in O.element_groups(rx):
        a, b = by_id[ids[0]], by_id[ids[-1]]
        par = parent_of.get(ids[0], rig)
        wall = G.side_wall(a, b)
        zc = 0.5 * (a["z"] + b["z"])
        plate = G.annulus(G.outline(a["ap"]), R_barrel - 0.05, zc)
        _mesh_object(f"BL_E_{ids[0]}_{ids[-1]}", G.merge(wall, plate), [black], par, lens_col)

    # 镜筒（固定）+ 后盖（传感器后 0.5 mm，挡住反射回来的光线去看相机背后）
    z_front = S[0]["z"] + 6.0
    _mesh_object("BL_Barrel", G.merge(G.tube(R_barrel, -0.5, z_front), G.disc(R_barrel, -0.5)), [black], rig, lens_col)

    cam_ob["bl_R_barrel"] = R_barrel
    cam_ob["bl_units"] = units_used
    lens_col.hide_render = True                 # 默认 Analytic 模式不需要镜片几何
    apply_look(cam_ob, rx)
    return cam_ob

def apply_look(cam_ob, rx):
    """把处方的观感参数（光圈、镀膜、炫光、对焦）写到相机设置上；没有 look 时用默认。"""
    p = cam_ob.data.blenderlens
    look = rx.get("look", {})
    p.f_number = max(look.get("f_number", O.nominal_f_number(rx)), O.nominal_f_number(rx))
    p.coating_nm = look.get("coating_nm", 100.0)
    p.coated = p.coating_nm > 0
    p.flare_color = look.get("flare_color", 'PHYSICAL')
    p.flare_gain = look.get("flare_gain", 1.0)
    p.veiling_glare = look.get("veiling_glare", 0.0)
    p.focus_distance = 0.0                      # 0 = 无穷远
    cam_ob["bl_min_focus_mm"] = rx.get("focus", {}).get("min_distance_mm", look.get("min_focus_mm", 0.0))
    cam_ob["bl_preset"] = rx["meta"].get("short_name", rx["meta"]["name"])
    apply_settings(cam_ob)

def rx_needs_apertures(rx):
    return any(sf.get("aperture") is None for g in rx["groups"] for sf in g["surfaces"])

def compile_camera(cam):
    from cycles import osl as cyc_osl
    msgs = []
    ok = cyc_osl.update_custom_camera_shader(cam, lambda t, m: msgs.append((t, m)))
    if not ok:
        raise RuntimeError(f"OSL 相机编译失败：{msgs}")
    return ok

def remove(name="BL_Camera"):
    col = bpy.data.collections.get("BlenderLens")
    if col:
        for c in list(col.children_recursive) + [col]:
            for ob in list(c.objects):
                data = ob.data
                bpy.data.objects.remove(ob)
                if data is not None and data.users == 0:
                    if isinstance(data, bpy.types.Mesh):
                        bpy.data.meshes.remove(data)
                    elif isinstance(data, bpy.types.Camera):
                        bpy.data.cameras.remove(data)
        for c in list(col.children_recursive):
            bpy.data.collections.remove(c)
        bpy.data.collections.remove(col)


# ───────────────────────── 参数同步（对焦 / 光圈 / 模式）─────────────────────────

_rx_cache = {}

def _rx_cached():
    t = bpy.data.texts.get(TEXT_RX)
    key = (t.name, hash(t.as_string())) if t else None
    if key not in _rx_cache:
        _rx_cache.clear()
        _rx_cache[key] = read_prescription()
    return _rx_cache[key]

def _set(obj, attr, value, tol=1e-9):
    """只在值变化时写（RNA 写入会触发更新；动画渲染的运动模糊子帧里多余的写入会打断相机运动的导出）。返回是否改写。"""
    cur = getattr(obj, attr)
    if isinstance(value, (tuple, list)):
        if all(abs(a - b) <= tol for a, b in zip(cur, value)):
            return False
    elif isinstance(value, float):
        if abs(cur - value) <= tol * max(1.0, abs(value)):
            return False
    elif cur == value:
        return False
    setattr(obj, attr, value)
    return True

def apply_settings(cam_ob):
    """把 cam.blenderlens 的设置写到：分组位置、OSL 参数、镜片几何的渲染可见性。只写变化了的值。"""
    cam = cam_ob.data
    p = cam.blenderlens
    rx = _rx_cached()
    dist = focus_mm(cam_ob)
    us = unit_scale(_cam_scene(cam_ob))
    d1, d2 = O.solve_focus(rx, dist)
    changed = False
    for ch in cam_ob.children:
        if ch.name.startswith("BL_Rig"):
            changed |= _set(ch, "scale", (us, us, us))   # 镜片网格与对焦分组都在 BL_Rig 下，内部仍是毫米
    changed |= _set(cam, "clip_start", 0.1 * us, 1e-6)
    changed |= _set(cam, "clip_end", 1e7 * us, 1e-6)
    for ch in cam_ob.children_recursive:
        if "bl_move" in ch:
            c1, c2 = ch["bl_move"]
            z = c1 * d1 + c2 * d2
            if abs(ch.location.z - z) > 1e-7:
                ch.location.z = z
                changed = True
    cc = cam.cycles_custom
    vals = dict(mode=1 if p.mode == 'GEOMETRY' else 0, f_number=max(p.f_number, O.nominal_f_number(rx)), blades=p.blades,
                blade_rotation=p.blade_rotation, delta_unit=d1, delta_front=d2,
                dispersion=int(p.dispersion and p.dispersion_mode == 'FULL'), exposure_compensate=int(p.exposure_compensate),
                ghosts=int(p.ghosts), ghost_gain=p.ghost_gain, coated=int(p.coated), coating_nm=p.coating_nm,
                unit_scale=us)
    keys = cc.keys()
    for k, v in vals.items():
        if k in keys:
            cur = cc[k]
            if (abs(cur - v) > 1e-9 * max(1.0, abs(v))) if isinstance(v, float) else cur != v:
                cc[k] = v
                changed = True
    changed |= _write_gpu_fields(cam, d1, d2, vals["f_number"], p.blades)
    if gpukernel.is_active():
        sync_gpu_lens(cam_ob)
    col = bpy.data.collections.get("BL_LensGeometry")
    if col:
        _set(col, "hide_render", p.mode != 'GEOMETRY')
    film = p.coating_nm if p.coated else 0.0
    for m in bpy.data.materials:                     # Geometry 模式的镀膜与 Analytic 共用同一组设置
        if m.name.startswith("BL_IF_") and m.get("bl_coatable") and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == 'BSDF_GLASS' and abs(n.inputs["Thin Film Thickness"].default_value - film) > 1e-6:
                    n.inputs["Thin Film Thickness"].default_value = film
    if changed:
        cam.update_tag()
    return d1, d2

def _write_gpu_fields(cam, d1, d2, f_number, blades):
    """GPU 路径的每帧参数通道（见 gpukernel.FIELD_MAP）。自定义相机用不到这些字段；CPU / OSL 路径忽略它们。
    它们跟随对焦 / 光圈的关键帧（frame_change 时 apply_settings 重写），所以动画在 GPU 路径下同样生效。返回是否改写。"""
    c = _set(cam.stereo, "convergence_distance", 1000.0 + d1, 1e-7)
    c |= _set(cam, "fisheye_fov", 16.0 + d2 / 5.0, 1e-7)
    c |= _set(cam.dof, "use_dof", True)
    c |= _set(cam.dof, "aperture_ratio", 1.0 / max(f_number, 1e-3), 1e-7)
    c |= _set(cam.dof, "aperture_blades", int(blades))
    return c

def gpu_settings(cam_ob):
    cam = cam_ob.data
    p = cam.blenderlens
    cc = cam.cycles_custom
    get = lambda k, d: cc[k] if k in cc.keys() else d
    return gpukernel.static_settings(dispersion=p.dispersion and p.dispersion_mode == 'FULL',
                                     dispersion_strength=get("dispersion_strength", 1.0),
                                     exposure_compensate=p.exposure_compensate,
                                     pupil_importance=bool(get("pupil_importance", 1)),
                                     blade_rotation_deg=p.blade_rotation, unit_scale=unit_scale(_cam_scene(cam_ob)),
                                     ghosts=p.ghosts, ghost_fraction=get("ghost_fraction", 0.5), ghost_gain=p.ghost_gain,
                                     coated=p.coated, coating_nm=p.coating_nm, coating_ior=get("coating_ior", 1.38))

def sync_gpu_lens(cam_ob):
    """把当前镜头写进 GPU 内核头文件（内容不变则不动；变了下一次 GPU 渲染会重编内核，约 10 秒）。"""
    changed = gpukernel.sync_lens(_rx_cached(), gpu_settings(cam_ob))
    cam_ob["bl_gpu_header"] = gpukernel.status()["header_hash"]
    return changed

def squeeze_info(cam_ob):
    return O.squeeze_at(_rx_cached(), focus_mm(cam_ob))

def black_world(world):
    """Blender 5.x 新建 World 默认带节点背景（≈0.05 灰），world.color 不起作用 → 显式置黑。"""
    world.color = (0, 0, 0)
    if world.node_tree:
        for n in world.node_tree.nodes:
            if n.type == 'BACKGROUND':
                n.inputs["Color"].default_value = (0, 0, 0, 1)
                n.inputs["Strength"].default_value = 0.0

def is_blenderlens_camera(ob):
    return bool(ob and ob.type == 'CAMERA' and any(c.name.startswith("BL_Rig") for c in ob.children))

def gpu_block_reason(scene):
    """场景相机是 BlenderLens、Cycles 设为 GPU、后端不是 OptiX 时，GPU 能否渲染。返回 None = 可以；否则返回原因。
    可以的条件：GPU 路径已启用（gpukernel.enable）、Analytic 模式、不开相机端鬼影、内核头文件与当前镜头一致。"""
    if scene.render.engine != 'CYCLES' or getattr(scene.cycles, "device", 'CPU') != 'GPU':
        return None
    cam_ob = scene.camera
    if not is_blenderlens_camera(cam_ob):
        return None
    try:
        backend = bpy.context.preferences.addons["cycles"].preferences.compute_device_type
    except (KeyError, AttributeError):
        return None
    if backend == 'OPTIX':
        return None
    if not gpukernel.is_active():
        return f"{backend} 不支持 OSL 相机，且 BlenderLens GPU 路径未启用（{gpukernel.status()['reason']}）"
    p = cam_ob.data.blenderlens
    if p.mode != 'ANALYTIC':
        return "GPU 路径只支持 Analytic 模式（Geometry 模式需要 CPU）"
    sync_gpu_lens(cam_ob)
    return None

def gpu_unsupported(scene):
    return gpu_block_reason(scene) is not None

def render_preset(scene, mode=None):
    """Cycles 设置：自定义相机在非 OptiX 设备上走 CPU；几何模式需要深反弹。
    鬼影 / 几何模式下关闭自适应采样：这类像素绝大多数采样为黑、偶尔一次极亮，
    自适应采样会把它们判为「已收敛」提前停掉，鬼影能量被系统性低估（实测差约 8 倍）。"""
    cam = scene.camera
    p = cam.data.blenderlens if cam is not None and cam.type == 'CAMERA' and hasattr(cam.data, "blenderlens") else None
    if mode is None:
        mode = p.mode if p is not None else 'ANALYTIC'
    rare_paths = mode == 'GEOMETRY' or (p is not None and p.ghosts)
    scene.render.engine = 'CYCLES'
    c = scene.cycles
    c.device = 'CPU'
    c.use_adaptive_sampling = not rare_paths
    c.blur_glossy = 0.0
    c.caustics_reflective = True
    c.caustics_refractive = True
    deep = 64 if mode == 'GEOMETRY' else 12
    c.max_bounces = deep
    c.transmission_bounces = deep
    c.glossy_bounces = deep
    c.transparent_max_bounces = deep
    c.diffuse_bounces = 4


# ───────────────────────── 炫光通道（正向追迹，确定性）─────────────────────────

FLARE_PROP = "bl_flare_source"

def _emission_of(ob):
    """物体材质里最强的 Emission：返回 (strength, (r, g, b))；没有则 (0, None)。"""
    best = (0.0, None)
    for slot in ob.material_slots:
        m = slot.material
        if not m or not m.node_tree:
            continue
        for n in m.node_tree.nodes:
            if n.type == 'EMISSION':
                s = n.inputs["Strength"].default_value
                c = tuple(n.inputs["Color"].default_value[:3])
                if s * max(c) > best[0] * (max(best[1]) if best[1] else 0):
                    best = (s, c)
    return best

def flare_sources(scene, auto_threshold=2.0e4):
    """被标记为炫光光源的物体；一个都没标记时，取发光强度 ≥ auto_threshold 的物体。"""
    obs = [o for o in scene.objects if o.type == 'MESH' and o.get(FLARE_PROP)]
    if not obs:
        obs = [o for o in scene.objects if o.type == 'MESH' and _emission_of(o)[0] >= auto_threshold]
    return obs

def _to_lens(cam_ob, world_co):
    """世界坐标 → 镜头坐标（mm；原点像面、物方 +Z）。相机对象局部：视线 −Z；镜头 = (−x, y, −z)。"""
    lo = cam_ob.matrix_world.inverted() @ world_co
    k = mm_per_unit(_cam_scene(cam_ob))
    return (-lo.x * k, lo.y * k, -lo.z * k)

AMBER = (1.0, 0.55, 0.12)
_LUMA = (0.2126, 0.7152, 0.0722)

def recolor(layer, source_color, mode='PHYSICAL', tint=(0.25, 0.55, 1.0)):
    """炫光层着色。PHYSICAL：物理镀膜色 × 灯光颜色；其他模式：取物理结果的亮度，换成目标颜色（亮度不变）。"""
    import numpy as np
    src = np.asarray(source_color, float)
    if mode == 'PHYSICAL':
        return layer * src[None, None, :]
    target = {'SOURCE': src, 'AMBER': np.asarray(AMBER), 'CUSTOM': np.asarray(tint, float)}[mode]
    luma = np.asarray(_LUMA)
    y_tgt = float((target * luma).sum())
    if y_tgt <= 0:
        return np.zeros_like(layer)
    mono = (layer * (src * luma)[None, None, :]).sum(axis=2)                          # 物理结果（含灯光颜色）的亮度
    return mono[:, :, None] * (target / y_tgt)[None, None, :]

_LATERAL_CACHE = {}

def _lateral_post(px, cam_ob, W, H):
    """色散模式 LATERAL_POST：给关色散渲染（d 线）的线性图像加横向色差。px：Blender 像素顺序（行 0 = 底部）。"""
    from . import lenspack as LP
    rx = _rx_cached()
    dist = focus_mm(cam_ob)
    key = (hash(json.dumps(rx, sort_keys=True, default=str)), round(dist, 3) if math.isfinite(dist) else -1, W, H)
    if key not in _LATERAL_CACHE:                      # 对焦不变的动画只算一次映射
        if len(_LATERAL_CACHE) > 4:
            _LATERAL_CACHE.clear()
        _LATERAL_CACHE[key] = LP.pixel_maps(rx, W, H, dist)
    img = px[::-1, :, :3].astype(np.float64)
    out = LP.apply_lateral_ca(img, rx, dist, maps=_LATERAL_CACHE[key])
    px[:, :, :3] = out[::-1].astype(np.float32)
    return px

def render_with_flare(scene, filepath, n_grid=260, blur_px=0.8):
    """渲染 + 正向炫光层合成（旧接口，等同 render_frame(flare=True)）。"""
    return render_frame(scene, filepath, flare=True, n_grid=n_grid, blur_px=blur_px)

def render_frame(scene, filepath, flare=True, n_grid=260, blur_px=0.8, flare_layer_path=None):
    """渲染当前帧并做插件的后期：LATERAL_POST 的横向色差、正向炫光层（flare=True）。
    返回 (合成后的 Image 数据块（线性）, 炫光光源名单, 炫光层 H×W×3)。filepath 按扩展名写出（PNG/TIFF 16 位按场景色彩管理，EXR 线性）。
    flare_layer_path：另存炫光层为线性 EXR（单独调色 / 时域稳定用）。"""
    import os, tempfile
    from . import flare as FL
    cam_ob = scene.camera
    p = cam_ob.data.blenderlens
    ghosts_was = p.ghosts
    if flare:
        p.ghosts = False                               # 正向通道取代相机端鬼影，避免重复
    apply_settings(cam_ob)
    tmp = os.path.join(tempfile.gettempdir(), f"blenderlens_beauty_{os.getpid()}.exr")
    fmt, path0 = scene.render.image_settings.file_format, scene.render.filepath
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.render.filepath = tmp
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        scene.render.image_settings.file_format, scene.render.filepath = fmt, path0
        p.ghosts = ghosts_was
        apply_settings(cam_ob)
    img = bpy.data.images.load(tmp, check_existing=False)
    W, H = img.size                                    # 实际输出尺寸（含 resolution_percentage）
    if scene.render.use_border and scene.render.use_crop_to_border and (flare or p.dispersion_mode == 'LATERAL_POST'):
        bpy.data.images.remove(img)
        raise RuntimeError("Render with Flare / Lateral in Post 需要整幅画面：请关闭 Crop to Render Region（或关闭 Render Region）")
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)
    bpy.data.images.remove(img)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if p.dispersion_mode == 'LATERAL_POST':
        px = _lateral_post(px, cam_ob, W, H)
    rx = _rx_cached()
    dist = focus_mm(cam_ob)
    layer = np.zeros((H, W, 3))
    used = []
    for ob in (flare_sources(scene) if flare else []):
        strength, color = _emission_of(ob)
        if not color:
            continue
        src = _to_lens(cam_ob, ob.matrix_world.translation)
        if src[2] <= 0:
            continue
        radius = 0.5 * max(ob.dimensions) * mm_per_unit(scene)
        lay, info = FL.flare_layer(rx, src, strength, radius, W, H, focus_dist=dist, f_number=p.f_number,
                                   coated=p.coated, coating_nm=p.coating_nm, n_grid=n_grid, blur_px=blur_px)
        layer += recolor(lay, color, p.flare_color, tuple(p.flare_tint)) * p.flare_gain
        if p.starburst and p.starburst_gain > 0:                 # 衍射星芒：光源本色（不受炫光着色影响）
            sb, _ = FL.starburst_layer(rx, src, strength, radius, W, H, focus_dist=dist, f_number=p.f_number,
                                       blades=p.blades, blade_rotation_deg=p.blade_rotation, gain=p.starburst_gain)
            layer += sb * np.asarray(color)[None, None, :]
        used.append(ob.name)
    px[:, :, :3] += layer[::-1].astype(np.float32)     # 图像行 0 = 底部
    if flare and p.veiling_glare > 0:                  # 杂光：对整幅（含炫光）做保能量的长尾散射
        px[:, :, :3] = FL.veiling_glare(px[:, :, :3].astype(np.float64), p.veiling_glare).astype(np.float32)
    if flare_layer_path:
        fl = bpy.data.images.new("BL Flare Layer", W, H, float_buffer=True)
        a = np.ones((H, W, 4), np.float32); a[:, :, :3] = layer[::-1]
        fl.pixels.foreach_set(a.ravel())
        fl.filepath_raw = bpy.path.abspath(flare_layer_path); fl.file_format = 'OPEN_EXR'
        fl.save()
        bpy.data.images.remove(fl)
    out = bpy.data.images.get("BL Flare Composite") or bpy.data.images.new("BL Flare Composite", W, H, float_buffer=True)
    if tuple(out.size) != (W, H):
        out.scale(W, H)
    out.pixels.foreach_set(px.ravel())
    out.update()
    if filepath:
        fmt_by_ext = {".png": 'PNG', ".exr": 'OPEN_EXR', ".jpg": 'JPEG', ".jpeg": 'JPEG', ".tif": 'TIFF', ".tiff": 'TIFF'}
        ext = os.path.splitext(filepath)[1].lower()
        ims = scene.render.image_settings
        old_fmt, old_depth = ims.file_format, ims.color_depth
        ims.file_format = fmt_by_ext.get(ext, old_fmt)
        if ims.file_format in ('PNG', 'TIFF'):
            ims.color_depth = '16'                                         # 炫光的大面积渐变在 8 位下会出色带
        try:
            out.save_render(bpy.path.abspath(filepath), scene=scene)     # 按场景色彩管理（如 AgX）写出
        finally:
            ims.file_format = old_fmt
            try:
                ims.color_depth = old_depth
            except TypeError:
                pass
    return out, used, layer
