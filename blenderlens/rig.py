# 在 Blender 里由处方搭镜头：OSL 相机 + 界面法镜片网格 + 镜框/镜筒 + 对焦分组。
# 层级：BL_Camera（相机，传感器中心）
#        └ BL_Rig（绕 Y 转 180°：镜头坐标 +Z 物方 → 相机视线方向）
#            ├ BL_Unit   （整体单元：主镜 + 变形器后组，对焦移动 Δ1）
#            ├ BL_Front  （变形器前组，移动 Δ1 + Δ2）
#            └ 镜筒、后盖（固定）
import json, math
import bpy

from . import optics as O
from . import meshgen as G
from . import oslgen

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
    return json.loads(t.as_string()) if t else O.load()


# ───────────────────────── 搭建 ─────────────────────────

def setup_scene_units(scene):
    us = scene.unit_settings
    us.system = 'METRIC'
    us.scale_length = 0.001
    us.length_unit = 'MILLIMETERS'

def desqueezed_resolution(rx, height, desqueeze=2.0):
    W, H = rx["sensor"]["width"], rx["sensor"]["height"]
    return int(round(height * W / H * desqueeze)), int(height)

def build(context, rx=None, coated=True, film_nm=100.0, film_ior=1.38, name="BL_Camera"):
    """搭整支镜头。返回相机对象。重复调用会先删除旧的同名镜头。"""
    rx = rx or O.load()
    scene = context.scene
    setup_scene_units(scene)
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
    cam.clip_start = 0.1
    cam.clip_end = 1e7
    cam_ob = bpy.data.objects.new(name, cam)
    col.objects.link(cam_ob)
    compile_camera(cam)

    rig = _empty("BL_Rig", cam_ob, col, rot=(0.0, math.pi, 0.0))
    unit = _empty("BL_Unit", rig, col)
    front = _empty("BL_Front", rig, col)
    rig.hide_viewport = unit.hide_viewport = front.hide_viewport = False

    S = O.layout(rx)
    fset = rx.get("focus", {})
    parent_of = {i: unit for i in fset.get("unit", [])}
    parent_of.update({i: front for i in fset.get("front_member", [])})
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
    R_barrel = max(math.hypot(sf["ap"]["semi_x"], sf["ap"]["semi_y"]) if sf["ap"]["shape"] == "rect"
                   else sf["ap"]["outer"] for sf in S) + 3.0
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
    lens_col.hide_render = True                 # 默认 Analytic 模式不需要镜片几何
    p = cam.blenderlens
    p.focus_distance = 0.0                      # 0 = 无穷远
    apply_settings(cam_ob)
    return cam_ob

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

def apply_settings(cam_ob):
    """把 cam.blenderlens 的设置写到：分组位置、OSL 参数、镜片几何的渲染可见性。"""
    cam = cam_ob.data
    p = cam.blenderlens
    rx = _rx_cached()
    dist = math.inf if p.focus_distance <= 0 else p.focus_distance
    d1, d2 = O.solve_focus(rx, dist)
    for ch in cam_ob.children_recursive:
        if ch.name.startswith("BL_Unit"):
            ch.location.z = d1
        elif ch.name.startswith("BL_Front"):
            ch.location.z = d1 + d2
    cc = cam.cycles_custom
    vals = dict(mode=1 if p.mode == 'GEOMETRY' else 0, f_number=p.f_number, blades=p.blades,
                blade_rotation=p.blade_rotation, delta_unit=d1, delta_front=d2,
                dispersion=int(p.dispersion), exposure_compensate=int(p.exposure_compensate),
                ghosts=int(p.ghosts), ghost_gain=p.ghost_gain, coated=int(p.coated), coating_nm=p.coating_nm)
    for k, v in vals.items():
        if k in cc.keys():
            cc[k] = v
    col = bpy.data.collections.get("BL_LensGeometry")
    if col:
        col.hide_render = (p.mode != 'GEOMETRY')
    for m in bpy.data.materials:                     # Geometry 模式的镀膜与 Analytic 共用同一组设置
        if m.name.startswith("BL_IF_") and m.get("bl_coatable") and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == 'BSDF_GLASS':
                    n.inputs["Thin Film Thickness"].default_value = p.coating_nm if p.coated else 0.0
    cam.update_tag()
    cam_ob.update_tag()
    return d1, d2

def squeeze_info(cam_ob):
    rx = _rx_cached()
    p = cam_ob.data.blenderlens
    dist = math.inf if p.focus_distance <= 0 else p.focus_distance
    return O.squeeze_at(rx, dist)

def black_world(world):
    """Blender 5.x 新建 World 默认带节点背景（≈0.05 灰），world.color 不起作用 → 显式置黑。"""
    world.color = (0, 0, 0)
    if world.node_tree:
        for n in world.node_tree.nodes:
            if n.type == 'BACKGROUND':
                n.inputs["Color"].default_value = (0, 0, 0, 1)
                n.inputs["Strength"].default_value = 0.0

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
    """世界坐标 → 镜头坐标（原点像面、物方 +Z）。相机对象局部：视线 −Z；镜头 = (−x, y, −z)。"""
    lo = cam_ob.matrix_world.inverted() @ world_co
    return (-lo.x, lo.y, -lo.z)

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

def render_with_flare(scene, filepath, n_grid=260, blur_px=0.8):
    """渲染 + 正向炫光层合成。返回合成后的 Image 数据块（线性），并按场景色彩管理写出 filepath。"""
    import os, tempfile
    import numpy as np
    from . import flare as FL
    cam_ob = scene.camera
    p = cam_ob.data.blenderlens
    ghosts_was = p.ghosts
    p.ghosts = False                                   # 正向通道取代相机端鬼影，避免重复
    apply_settings(cam_ob)
    tmp = os.path.join(tempfile.gettempdir(), "blenderlens_beauty.exr")
    fmt, path0 = scene.render.image_settings.file_format, scene.render.filepath
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.render.filepath = tmp
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        scene.render.image_settings.file_format, scene.render.filepath = fmt, path0
        p.ghosts = ghosts_was
        apply_settings(cam_ob)
    W, H = scene.render.resolution_x, scene.render.resolution_y
    img = bpy.data.images.load(tmp, check_existing=False)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)
    bpy.data.images.remove(img)
    rx = _rx_cached()
    dist = math.inf if p.focus_distance <= 0 else p.focus_distance
    layer = np.zeros((H, W, 3))
    used = []
    for ob in flare_sources(scene):
        strength, color = _emission_of(ob)
        if not color:
            continue
        src = _to_lens(cam_ob, ob.matrix_world.translation)
        if src[2] <= 0:
            continue
        radius = 0.5 * max(ob.dimensions)
        lay, info = FL.flare_layer(rx, src, strength, radius, W, H, focus_dist=dist, f_number=p.f_number,
                                   coated=p.coated, coating_nm=p.coating_nm, n_grid=n_grid, blur_px=blur_px)
        layer += recolor(lay, color, p.flare_color, tuple(p.flare_tint)) * p.flare_gain
        used.append(ob.name)
    px[:, :, :3] += layer[::-1].astype(np.float32)     # 图像行 0 = 底部
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
