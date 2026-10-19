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

def _dither_zero_normals(N):
    """Cycles 把顶点法线压成八面体 16+16 位（util/types_normal.h，65535 级），0 不可表示：恰为 0 的分量一律
    解码成 +1.5×10⁻⁵（柱面一个方向、平面两个方向恒为 0 → 各成一个指向 +x / +y 的微小棱镜）。把恰为 0 的分量
    按顶点序号交替写成 ±1e-12，量化后正负各半，平均为 0；平面另外改平直着色（用精确的几何法线）。
    实测这项偏差很小：Panavision 长焦端 Geometry 模式的同向残差（约 0.3 px @ 270p）修前修后相差 < 0.01 px。"""
    out = []
    for k, (x, y, z) in enumerate(N):
        s = 1e-12 if k % 2 else -1e-12
        out.append((x if x != 0.0 else s, y if y != 0.0 else -s, z))
    return out

def _mesh_object(name, data, mats, parent, collection, flat=False):
    V, F, N, M = data
    me = bpy.data.meshes.get(name)
    if me:
        bpy.data.meshes.remove(me)
    me = bpy.data.meshes.new(name)
    me.from_pydata(V, [], F)
    for m in mats:
        me.materials.append(m)
    me.polygons.foreach_set("material_index", M)
    if flat:
        # 平面光学面：平直着色 → Cycles 用三角形的几何法线（浮点、精确），不经过压缩的顶点法线
        me.shade_flat()
        me.validate(clean_customdata=False)
    else:
        me.shade_smooth()
        _set_normals(me, N)
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    ob.parent = parent
    return ob

def _set_normals(me, N):
    """逐顶点法线（解析值）。Blender 4.5+ 用浮点「自由法线」（custom_normal 属性）：旧接口
    normals_split_custom_set_from_vertices 把法线压成角点法线空间里的两个 16 位角度，回读误差最大约 100″
    （同样不是 Geometry 残差的主因，见 RESEARCH.md §5 第 14 条）。"""
    N = _dither_zero_normals(N)
    a = me.attributes.get("custom_normal")
    try:
        if a is None:
            a = me.attributes.new("custom_normal", 'FLOAT_VECTOR', 'POINT')
        a.data.foreach_set("vector", [c for n in N for c in n])
    except (TypeError, RuntimeError, AttributeError):
        me.normals_split_custom_set_from_vertices(N)
    me.validate(clean_customdata=False)

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
        plane = (math.isinf(sf["Rx"]) and math.isinf(sf["Ry"]) and not any(sf.get("asph") or [])
                 and not any(sf.get("kc", (0.0, 0.0))))                   # 补偿板会改形，保持平滑着色
        ob = _mesh_object(f"BL_S_{sf['id']}", G.surface_mesh(sf, flip=flip), [mat, black],
                          parent_of.get(sf["id"], rig), lens_col, flat=plane)
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

    # 光圈（0.8 之前 Geometry 模式没有光圈：只靠 OSL 出瞳采样盘（1.4 倍近轴出瞳）和镜片口径限光）
    st_id = O.stop_group(rx)["stop"]["after_surface"]
    iris = _mesh_object("BL_Iris", _iris_mesh(rx, S, R_barrel, O.nominal_f_number(rx), 0, 0.0), [black],
                        parent_of.get(st_id, rig), lens_col, flat=True)
    iris["bl_iris"] = 1

    # 镜筒（固定）+ 后盖（传感器后 0.5 mm，挡住反射回来的光线去看相机背后）
    z_front = S[0]["z"] + 6.0
    _mesh_object("BL_Barrel", G.merge(G.tube(R_barrel, -0.5, z_front), G.disc(R_barrel, -0.5)), [black], rig, lens_col)

    cam_ob["bl_R_barrel"] = R_barrel
    cam_ob["bl_units"] = units_used
    cam_ob["bl_has_compensator"] = bool(O.compensator(rx))
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
    p.coating_type = look.get("coating", 'SINGLE')
    if O.compensator(rx):
        p["mumps_correction"] = float(look.get("mumps_correction", rx["focus"]["compensator"].get("level", 0.0)))
        set_compensation(cam_ob, p["mumps_correction"])
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
    phi = O.comp_phi(rx, d1)
    vals = dict(mode=1 if p.mode == 'GEOMETRY' else 0, f_number=max(p.f_number, O.nominal_f_number(rx)), blades=p.blades,
                blade_rotation=p.blade_rotation, delta_unit=d1, delta_front=d2, comp_phi=phi,
                dispersion=int(p.dispersion and p.dispersion_mode == 'FULL'), exposure_compensate=int(p.exposure_compensate),
                ghosts=int(p.ghosts), ghost_gain=p.ghost_gain, coated=int(p.coated), coating_nm=p.coating_nm,
                coating_type=1 if p.coating_type == 'BROADBAND' else 0, unit_scale=us)
    vals.update(_xyz_params())                       # 0.8.2：Rec.709 → 场景工作色彩空间（CPU / OSL 的色散颜色；GPU 用 Cycles 的 rec709_to_rgb）
    keys = cc.keys()
    for k, v in vals.items():
        if k in keys:
            cur = cc[k]
            if (abs(cur - v) > 1e-9 * max(1.0, abs(v))) if isinstance(v, float) else cur != v:
                cc[k] = v
                changed = True
    changed |= _write_gpu_fields(cam, d1, d2, vals["f_number"], p.blades, phi)
    changed |= _update_iris(cam_ob, rx, vals["f_number"], p.blades, p.blade_rotation)
    if O.compensator(rx) and abs(cam_ob.get("bl_comp_phi", 0.0) - phi) > 1e-12:
        _update_compensator_mesh(cam_ob, rx, phi)
        cam_ob["bl_comp_phi"] = phi
        changed = True
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

def dispersive_materials(sc):
    """场景里用到的、透射色散不为 0 的材质名（Blender 5.3 Principled BSDF）。有的话 GPU 路径进入「波长共用模式」。"""
    names, seen = [], set()
    for ob in sc.objects:
        for slot in getattr(ob, "material_slots", []):
            m = slot.material
            if not m or m.name in seen or not m.node_tree:
                continue
            seen.add(m.name)
            for n in m.node_tree.nodes:
                s = n.inputs.get("Transmission Dispersion Scale") if hasattr(n, "inputs") else None
                if s is not None and (s.is_linked or s.default_value > 0.0):
                    names.append(m.name)
                    break
    return names

_XYZ_CACHE = {}

def _xyz_params():
    from . import spectral as SP
    name = SP.working_space_name()
    if name not in _XYZ_CACHE:
        M = SP.working_space_matrix()
        _XYZ_CACHE[name] = {f"rec_{c}{j}": float(M[r][j]) for r, c in enumerate("rgb") for j in range(3)}
    return _XYZ_CACHE[name]

def _update_compensator_mesh(cam_ob, rx, phi):
    """Geometry 模式：补偿板前表面的网格随 φ 改形（顶点 + 自由法线原地改写，拓扑不变）。"""
    comp = O.compensator(rx)
    ob = next((c for c in cam_ob.children_recursive if c.get("bl_surface") == comp["surface"]), None)
    if ob is None or ob.type != 'MESH':
        return
    S = O.layout(rx)
    sf = next(s for s in S if s["id"] == comp["surface"])
    k = 1.0 / (sf["n"] - 1.0)
    sf["Rx"] = math.inf if phi == 0 else 1.0 / (-k * phi)
    sf["Ry"] = math.inf if phi == 0 else 1.0 / (k * phi)
    V, F, N, M = G.surface_mesh(sf, flip=ob.get("bl_normal_sign", 1) < 0)
    me = ob.data
    if len(me.vertices) != len(V):
        return
    me.vertices.foreach_set("co", [c for v in V for c in v])
    _set_normals(me, N)
    me.update()

def _iris_mesh(rx, S, R_barrel, f_number, blades, rot_deg):
    z_stop, r0 = O.stop_geometry(rx, S)                      # 静止状态（对焦位移由父物体 BL_Move 给）
    r = r0 * O.nominal_f_number(rx) / max(f_number, O.nominal_f_number(rx))
    return G.annulus(G.iris_outline(r, int(blades), math.radians(rot_deg)), R_barrel - 0.05, z_stop)

def _update_iris(cam_ob, rx, f_number, blades, rot_deg):
    ob = next((c for c in cam_ob.children_recursive if c.get("bl_iris")), None)
    if ob is None:
        return False
    key = f"{f_number:.6f}|{int(blades)}|{rot_deg:.6f}"
    if ob.get("bl_iris_key") == key:
        return False
    V, F, N, M = _iris_mesh(rx, O.layout(rx), cam_ob.get("bl_R_barrel", 50.0), f_number, blades, rot_deg)
    me = ob.data
    if len(me.vertices) == len(V) and len(me.polygons) == len(F):
        me.vertices.foreach_set("co", [c for v in V for c in v])
        me.update()
    else:
        new = bpy.data.meshes.new(me.name)
        new.from_pydata(V, [], F)
        for m in me.materials:
            new.materials.append(m)
        new.shade_flat()
        ob.data = new
        bpy.data.meshes.remove(me)
        new.name = "BL_Iris"
    ob["bl_iris_key"] = key
    return True

def set_compensation(cam_ob, level):
    """发腮补偿强度（0–1）写进场景里的处方（所有派生计算都从处方读），再同步。"""
    rx = read_prescription()
    if not O.compensator(rx):
        return False
    level = min(max(float(level), 0.0), 1.0)
    if abs(rx["focus"]["compensator"].get("level", 0.0) - level) < 1e-9:
        return False
    rx["focus"]["compensator"]["level"] = level
    store_prescription(rx)
    apply_settings(cam_ob)
    return True

def _write_gpu_fields(cam, d1, d2, f_number, blades, phi=0.0):
    """GPU 路径的每帧参数通道（见 gpukernel.FIELD_MAP）。自定义相机用不到这些字段；CPU / OSL 路径忽略它们。
    它们跟随对焦 / 光圈的关键帧（frame_change 时 apply_settings 重写），所以动画在 GPU 路径下同样生效。返回是否改写。"""
    c = _set(cam.stereo, "convergence_distance", 1000.0 + d1, 1e-7)
    c |= _set(cam, "fisheye_fov", 16.0 + d2 / 5.0, 1e-7)
    c |= _set(cam.dof, "use_dof", True)
    c |= _set(cam.dof, "aperture_ratio", 1.0 / max(f_number, 1e-3), 1e-7)
    c |= _set(cam.dof, "aperture_blades", int(blades))
    c |= _set(cam, "fisheye_lens", 50.0 + 1.0e4 * phi, 1e-7)
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
                                     coated=p.coated, coating_nm=p.coating_nm, coating_ior=get("coating_ior", 1.38),
                                     coating_type=p.coating_type,
                                     spectral_shared=bool(dispersive_materials(_cam_scene(cam_ob))))

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

def _region(scene, W, H):
    """开了 Render Region + Crop 时：渲染出来的 W×H 在整幅里的位置 → (x0, y0_top, W_full, H_full)；否则 None。"""
    r = scene.render
    Wf = int(round(r.resolution_x * r.resolution_percentage / 100.0))
    Hf = int(round(r.resolution_y * r.resolution_percentage / 100.0))
    if not (r.use_border and r.use_crop_to_border) or (W, H) == (Wf, Hf):
        return None
    x0 = int(round(r.border_min_x * Wf)); y0b = int(round(r.border_min_y * Hf))
    x0 = min(max(x0, 0), Wf - W); y0b = min(max(y0b, 0), Hf - H)
    return (x0, Hf - (y0b + H), Wf, Hf)

def _lateral_maps(cam_ob, W, H, region=None):
    """横向色差的像素映射（行 0 = 顶部），按需放大（lenspack.LazyMaps，常驻内存只有粗网格）。
    region：裁切区域在整幅里的位置（整幅算映射，再切出这一块）。"""
    from . import lenspack as LP
    rx = _rx_cached()
    dist = focus_mm(cam_ob)
    Wf, Hf = (region[2], region[3]) if region else (W, H)
    key = (hash(json.dumps(rx, sort_keys=True, default=str)), round(dist, 3) if math.isfinite(dist) else -1, Wf, Hf,
           tuple(region) if region else None)
    if key not in _LATERAL_CACHE:                      # 对焦不变的动画只算一次（粗网格追迹）
        if len(_LATERAL_CACHE) > 4:
            _LATERAL_CACHE.clear()
        _LATERAL_CACHE[key] = LP.LazyMaps(rx, Wf, Hf, dist, crop=(region[0], region[1], W, H) if region else None)
    return _LATERAL_CACHE[key]

def _apply_ca(img_top, cam_ob, maps):
    from . import lenspack as LP
    return LP.apply_lateral_ca(img_top, _rx_cached(), focus_mm(cam_ob), maps=maps)

def _lateral_post(px, cam_ob, W, H, region=None):
    """色散模式 LATERAL_POST：给关色散渲染（d 线）的线性图像加横向色差。px：Blender 像素顺序（行 0 = 底部）。"""
    maps = _lateral_maps(cam_ob, W, H, region)
    out = _apply_ca(px[::-1, :, :3].astype(np.float64), cam_ob, maps)
    px[:, :, :3] = out[::-1].astype(np.float32)
    return px

def _flare_layers(scene, cam_ob, W, H, region, n_grid, blur_px):
    """正向炫光 + 星芒（整幅算，有裁切时切出这一块）。返回 (H×W×3 行 0 = 顶部, 光源名单)。"""
    from . import flare as FL
    p = cam_ob.data.blenderlens
    rx = _rx_cached()
    dist = focus_mm(cam_ob)
    Wf, Hf = (region[2], region[3]) if region else (W, H)
    layer = np.zeros((Hf, Wf, 3))
    used = []
    for ob in flare_sources(scene):
        strength, color = _emission_of(ob)
        if not color:
            continue
        src = _to_lens(cam_ob, ob.matrix_world.translation)
        if src[2] <= 0:
            continue
        radius = 0.5 * max(ob.dimensions) * mm_per_unit(scene)
        lay, info = FL.flare_layer(rx, src, strength, radius, Wf, Hf, focus_dist=dist, f_number=p.f_number,
                                   coated=p.coated, coating_nm=p.coating_nm, coating_type=p.coating_type,
                                   n_grid=n_grid, blur_px=blur_px)
        layer += recolor(lay, color, p.flare_color, tuple(p.flare_tint)) * p.flare_gain
        if p.starburst and p.starburst_gain > 0:                 # 衍射星芒：光源本色（不受炫光着色影响）
            sb, _ = FL.starburst_layer(rx, src, strength, radius, Wf, Hf, focus_dist=dist, f_number=p.f_number,
                                       blades=p.blades, blade_rotation_deg=p.blade_rotation, gain=p.starburst_gain)
            layer += sb * np.asarray(color)[None, None, :]
        used.append(ob.name)
    if region:
        x0, y0, _, _ = region
        layer = layer[y0:y0 + H, x0:x0 + W]
    return layer, used

# ───────────────────────── 多层 EXR（0.8.3）─────────────────────────

def render_frame_multilayer(scene, filepath, flare=True, n_grid=260, blur_px=0.8, passes="all", vectors=True):
    """渲染当前帧，写多层（多部分）EXR，保留视图层打开的所有渲染层：
      · 颜色层（Combined、各光组 Combined_<名>、Emit、Diffuse/Glossy/Transmission/Volume…、AO、颜色 AOV、Noisy Image、
        Denoising Albedo）做与 Combined 相同的横向色差（LATERAL_POST 时）→ 各层相加仍约等于 Combined；
      · 数据层（Depth、Normal、Position、Vector、UV、Index、Cryptomatte、数值 AOV、Denoising Normal/Depth）原样；
      · 插件加的层：BL_Flare（正向炫光 + 星芒）、BL_Veil（杂光，差值层，可为负）、BL_Final（Combined + BL_Flare + BL_Veil，
        即单层输出的成片），以及 BL_Vector（vectors=True 时）。Combined 本身不含炫光与杂光。
      · BL_Vector：按本镜头的真实成像（主光线、当前对焦）算的运动矢量，像素单位、y 向上，约定与 Cycles 的 Vector 相同：
        X,Y = 上一帧位置 − 当前位置，Z,W = 当前位置 − 下一帧位置。相机运动 + 刚体物体运动（按 Cryptomatte 识别物体）；
        形变（骨骼、形状键）不计。Cycles 自己的 Vector 层对自定义相机不对（源码 TODO：退回相机坐标），原样保留但不要用。
    passes："all" 或要保留的渲染层名列表（如 ["Combined", "Depth", "Combined_lamps"]）；插件层总是写入。
    为了 BL_Vector，会临时打开 Position 与 Cryptomatte Object 层（用户没开的不写进输出）。渲染时临时关闭合成（输出原始渲染层）。
    返回 (BL_Final 的 Image 数据块, 炫光光源名单, 炫光层 H×W×3（行 0 = 底部，与旧接口一致）)。"""
    import os, tempfile
    from . import flare as FL, passes as PS
    cam_ob = scene.camera
    p = cam_ob.data.blenderlens
    vl = bpy.context.view_layer if bpy.context.view_layer in scene.view_layers[:] else scene.view_layers[0]
    added = []
    if vectors:
        for attr, pname in (("use_pass_position", "Position"), ("use_pass_cryptomatte_object", "CryptoObject")):
            if hasattr(vl, attr) and not getattr(vl, attr):
                setattr(vl, attr, True); added.append((attr, pname))
    ghosts_was = p.ghosts
    if flare:
        p.ghosts = False
    apply_settings(cam_ob)
    tmp = os.path.join(tempfile.gettempdir(), f"blenderlens_layers_{os.getpid()}.exr")
    r, ims = scene.render, scene.render.image_settings
    keep = (getattr(ims, "media_type", None), ims.file_format, ims.color_depth, getattr(ims, "exr_codec", None), r.filepath, r.use_compositing)
    try:
        if keep[0] is not None:
            ims.media_type = 'MULTI_LAYER_IMAGE'
        ims.file_format = 'OPEN_EXR_MULTILAYER'
        ims.color_depth = '32'
        r.use_compositing = False
        r.filepath = tmp
        bpy.ops.render.render(write_still=True)
    finally:
        if keep[0] is not None:
            ims.media_type = keep[0]
        ims.file_format = keep[1]
        try:
            ims.color_depth = keep[2]
        except TypeError:
            pass
        if keep[3] is not None:
            ims.exr_codec = keep[3]
        r.filepath, r.use_compositing = keep[4], keep[5]
        for attr, _ in added:
            setattr(vl, attr, False)
        p.ghosts = ghosts_was
        apply_settings(cam_ob)
    parts = PS.read(tmp)
    if not os.environ.get("BLENDERLENS_KEEP_LAYERS"):          # 调试：保留原始渲染层文件
        try:
            os.remove(tmp)
        except OSError:
            pass
    comb = PS.find(parts, "Combined", vl.name) or PS.find(parts, "Combined")
    if comb is None:
        raise RuntimeError("多层 EXR 里没有 Combined 渲染层")
    H, W = comb["channels"]["R"].shape
    region = _region(scene, W, H)
    if p.dispersion_mode == 'LATERAL_POST':
        from . import lenspack as LP
        maps = _lateral_maps(cam_ob, W, H, region)
        color_parts = [part for part in parts if PS.is_color(part)]
        outs = LP.apply_lateral_ca_many([PS.rgb(part) for part in color_parts], maps)   # 采样位置每个波长只算一次
        for part, img in zip(color_parts, outs):
            PS.set_rgb(part, img)
        del outs
    beauty = PS.rgb(comb)
    layer, used = _flare_layers(scene, cam_ob, W, H, region, n_grid, blur_px) if flare else (np.zeros((H, W, 3)), [])
    veil = np.zeros_like(beauty)
    if flare and p.veiling_glare > 0:
        base = beauty + layer
        veil = FL.veiling_glare(base, p.veiling_glare) - base
    final = beauty + layer + veil
    alpha = comb["channels"].get("A", np.ones((H, W), np.float32))
    new = [PS.new_part(comb, "BL_Flare", dict(R=layer[..., 0], G=layer[..., 1], B=layer[..., 2])),
           PS.new_part(comb, "BL_Veil", dict(R=veil[..., 0], G=veil[..., 1], B=veil[..., 2])),
           PS.new_part(comb, "BL_Final", dict(R=final[..., 0], G=final[..., 1], B=final[..., 2], A=alpha))]
    fill = fill_first_sample_passes(scene, cam_ob, parts, vl.name, W, H, region)
    comb["header"]["blenderlens/fill"] = json.dumps(fill)
    if vectors:
        vec = bl_vector(scene, cam_ob, parts, vl.name, W, H, region)
        if vec is not None:
            new.append(PS.new_part(comb, "BL_Vector", dict(X=vec[..., 0], Y=vec[..., 1], Z=vec[..., 2], W=vec[..., 3])))
    drop = {pn for _, pn in added}
    keep_parts = []
    for part in parts:
        base_name = part["pass_"]
        if any(base_name.startswith(d) for d in drop):
            continue
        if passes != "all" and base_name not in passes:
            continue
        keep_parts.append(part)
    if filepath:
        out_path = bpy.path.abspath(filepath)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        PS.write(out_path, keep_parts + new, half_color=(keep[2] == '16'))
    out = bpy.data.images.get("BL Flare Composite") or bpy.data.images.new("BL Flare Composite", W, H, float_buffer=True)
    if tuple(out.size) != (W, H):
        out.scale(W, H)
    px = np.concatenate([final, alpha[..., None]], axis=-1)[::-1].astype(np.float32)
    out.pixels.foreach_set(px.ravel())
    out.update()
    return out, used, layer[::-1]


def _crypto_coverage(parts, vl_name):
    from . import passes as PS
    p = PS.find(parts, "CryptoObject00", vl_name) or PS.find(parts, "CryptoObject00")
    if p is None:
        return None
    ch = p["channels"]
    return ch["G"] if "G" in ch else ch["g"]


def fill_first_sample_passes(scene, cam_ob, parts, vl_name, W, H, region=None, max_raycast=300000):
    """Cycles 只在第 0 个采样写 Depth / Position / 物体 / 材质索引（film/data_passes.h）。普通相机第 0 个采样总能打到东西；
    真实镜头的第 0 条光线常被光阑 / 镜片边缘挡掉（出瞳采样带光率 0.6–0.75）→ 约一半像素这些层是 0。
    另外纯发光材质的表面根本不写数据层。这里补上：
      1. 有物体（Cryptomatte 覆盖 > 0）但 Position 为 0 的像素：用 3×3 邻域里同一物体的有效值平均，迭代几次；
      2. 仍然没有（如整个发光物体）：沿本镜头的主光线在场景里 ray_cast；
      3. Depth（自定义相机 = 到相机原点的距离，与 Cycles 定义一致）由补好的 Position 重算这些像素；IndexOB / IndexMA 用邻域填。
    原地修改 parts，返回统计 dict。没有 Position 或 Cryptomatte 层时什么都不做。"""
    from . import passes as PS, lenspack as LP
    pos = PS.find(parts, "Position", vl_name) or PS.find(parts, "Position")
    cov = _crypto_coverage(parts, vl_name)
    idx, names = PS.crypto_object_ids(parts, vl_name)
    if pos is None or cov is None or idx is None:
        return dict(skipped=True)
    P = np.stack([pos["channels"][c] for c in "XYZ"], -1).astype(np.float64)
    have = np.abs(P).sum(-1) > 0
    obj = (cov > 0) & (idx >= 0)
    missing = obj & ~have
    n0 = int(missing.sum())
    for _ in range(6):                                   # 1. 邻域填（同一物体）
        if not missing.any():
            break
        acc = np.zeros_like(P); cnt = np.zeros(P.shape[:2])
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                sh = lambda a: np.roll(np.roll(a, dy, 0), dx, 1)
                ok = sh(have) & (sh(idx) == idx)
                acc += np.where(ok[..., None], sh(P), 0.0); cnt += ok
        fill = missing & (cnt > 0)
        P[fill] = acc[fill] / cnt[fill][:, None]
        have = have | fill
        missing = missing & ~fill
    n_cast = 0
    if missing.any() and missing.sum() <= max_raycast:  # 2. 主光线 ray_cast
        rx = _rx_cached()
        SW, SH = rx["sensor"]["width"], rx["sensor"]["height"]
        Wf, Hf = (region[2], region[3]) if region else (W, H)
        x0, y0t = (region[0], region[1]) if region else (0, 0)
        rows, cols = np.nonzero(missing)
        pyup = Hf - 1 - (rows + y0t)
        sx = ((cols + x0 + 0.5) / Wf - 0.5) * SW
        sy = -((pyup + 0.5) / Hf - 0.5) * SH
        M = LP.LensMap(rx, focus_mm(cam_ob))
        n = len(sx)
        P0 = np.column_stack([sx, sy, np.zeros(n)])
        D0 = np.column_stack([-sx, -sy, np.full(n, M.z_exp)]); D0 /= np.linalg.norm(D0, axis=1)[:, None]
        Po, Do = LP._trace_out(M.S, M.z_stop, M.r_stop, P0, D0, O.LAMBDA_D)
        us = mm_per_unit(scene)
        mw = cam_ob.matrix_world
        dg = bpy.context.evaluated_depsgraph_get()
        from mathutils import Vector
        for k in range(n):
            if not (np.isfinite(Po[k]).all() and np.isfinite(Do[k]).all()):
                continue
            o = mw @ Vector((-Po[k, 0] / us, Po[k, 1] / us, -Po[k, 2] / us))
            d = (mw.to_3x3() @ Vector((-Do[k, 0], Do[k, 1], -Do[k, 2]))).normalized()
            hit, loc, _n, _i, ob, _m = scene.ray_cast(dg, o + d * (20.0 / us), d)
            if hit and not ob.name.startswith("BL_"):
                P[rows[k], cols[k]] = tuple(loc); n_cast += 1
                missing[rows[k], cols[k]] = False
    filled = obj & (np.abs(P).sum(-1) > 0) & ~(np.abs(np.stack([pos["channels"][c] for c in "XYZ"], -1)).sum(-1) > 0)
    for k, c in enumerate("XYZ"):
        pos["channels"][c] = P[..., k].astype(np.float32)
    dep = PS.find(parts, "Depth", vl_name) or PS.find(parts, "Depth")
    if dep is not None:                                  # 3. Depth = 到相机原点的距离（自定义相机的 Cycles 定义）
        cpos = np.array(cam_ob.matrix_world.translation)
        d = np.linalg.norm(P - cpos[None, None, :], axis=-1)
        dep["channels"]["Z"] = np.where(filled, d, dep["channels"]["Z"]).astype(np.float32)
    for name in ("IndexOB", "IndexMA"):
        q = PS.find(parts, name, vl_name) or PS.find(parts, name)
        if q is None:
            continue
        a = q["channels"]["X"].astype(np.float64)
        for _ in range(6):
            hole = obj & (a == 0)
            if not hole.any():
                break
            best = np.zeros_like(a)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    sh = np.roll(np.roll(a, dy, 0), dx, 1); same = np.roll(np.roll(idx, dy, 0), dx, 1) == idx
                    best = np.where((best == 0) & same & (sh != 0), sh, best)
            a = np.where(hole, best, a)
        q["channels"]["X"] = a.astype(np.float32)
    return dict(missing_before=n0, raycast=n_cast, still_missing=int(missing.sum()), filled=int(filled.sum()))


def bl_vector(scene, cam_ob, parts, vl_name, W, H, region=None, project=None):
    """BL_Vector：H×W×4（行 0 = 顶部），像素单位、y 向上；X,Y = 上一帧 − 当前，Z,W = 当前 − 下一帧（Cycles 约定）。
    需要 Position 层；有 Cryptomatte Object 层时计入刚体物体运动。project：可替换的投影函数（测试用：针孔相机），
    签名 project(Q_lens_mm N×3, focus_mm, s0) → (sx, sy, valid)。没有 Position 层时返回 None。"""
    from . import passes as PS, lenspack as LP, oslgen
    pos = PS.find(parts, "Position", vl_name) or PS.find(parts, "Position")
    if pos is None:
        return None
    P = np.stack([pos["channels"]["X"], pos["channels"]["Y"], pos["channels"]["Z"]], axis=-1).astype(np.float64)
    idx, names = PS.crypto_object_ids(parts, vl_name)
    cov = _crypto_coverage(parts, vl_name)
    bg = ~(np.abs(P).sum(-1) > 0)                       # 没有位置（背景 / 补不上的像素）
    if cov is not None:
        bg |= cov <= 0
    rx = _rx_cached()
    SW, SH = rx["sensor"]["width"], rx["sensor"]["height"]
    Wf, Hf = (region[2], region[3]) if region else (W, H)
    x0, y0t = (region[0], region[1]) if region else (0, 0)
    f0, sub0 = scene.frame_current, scene.frame_subframe
    frames = (f0 - 1, f0, f0 + 1)
    cams, focus, objs = {}, {}, {}
    try:
        for f in frames:
            scene.frame_set(f)
            cams[f] = np.array(cam_ob.matrix_world, np.float64)
            focus[f] = focus_mm(cam_ob)
            for nm in names:
                ob = bpy.data.objects.get(nm)
                if ob is not None:
                    objs.setdefault(nm, {})[f] = np.array(ob.matrix_world, np.float64)
    finally:
        scene.frame_set(f0, subframe=sub0)
        apply_settings(cam_ob)
    us = mm_per_unit(scene)
    yy, xx = np.mgrid[0:H, 0:W]
    px_full = xx + x0
    py_up = (Hf - 1 - (yy + y0t))                       # 整幅里的行号，向上为正
    s0x = ((px_full + 0.5) / Wf - 0.5) * SW
    s0y = -((py_up + 0.5) / Hf - 0.5) * SH
    m = ~bg
    Pw = P[m]
    s0 = (s0x[m], s0y[m])
    ids = idx[m] if idx is not None else None
    if project is None:
        cache = {}
        def project(Q, fmm, s0):
            key = round(fmm, 3) if math.isfinite(fmm) else -1
            if key not in cache:
                cache[key] = LP.LensProjector(rx, fmm)
            return cache[key].project(Q, s0)
    proj = {}
    for f in frames:
        Pk = Pw.copy()
        if ids is not None:                              # 刚体物体：P_f = M_f · M_cur⁻¹ · P
            for k, nm in enumerate(names):
                mats = objs.get(nm)
                if not mats or f == f0:
                    continue
                T = mats[f] @ np.linalg.inv(mats[f0])
                if np.allclose(T, np.eye(4), atol=1e-9):
                    continue
                sel = ids == k
                if sel.any():
                    v = np.c_[Pk[sel], np.ones(sel.sum())] @ T.T
                    Pk[sel] = v[:, :3]
        Cinv = np.linalg.inv(cams[f])
        sx = np.empty(len(Pk)); sy = np.empty(len(Pk)); ok = np.zeros(len(Pk), bool)
        CH = 500000                                     # 分块：4K 时一次投影 800 万点的临时数组要好几 GB
        for a in range(0, len(Pk), CH):
            b = min(a + CH, len(Pk))
            pc = (np.c_[Pk[a:b], np.ones(b - a)] @ Cinv.T)[:, :3]
            Q = np.column_stack([-pc[:, 0], pc[:, 1], -pc[:, 2]]) * us     # 相机本地 → 镜头坐标（mm）
            sx[a:b], sy[a:b], ok[a:b] = project(Q, focus[f], (s0[0][a:b], s0[1][a:b]))
        pxf = (sx / SW + 0.5) * Wf - 0.5
        pyf = (-sy / SH + 0.5) * Hf - 0.5
        proj[f] = (pxf, pyf, ok)
    out = np.zeros((H, W, 4), np.float32)
    (ax, ay, aok), (cx, cy, cok), (bx, by, bok) = proj[frames[0]], proj[frames[1]], proj[frames[2]]
    ok = aok & cok & bok
    vec = np.zeros((len(Pw), 4))
    vec[:, 0], vec[:, 1] = ax - cx, ay - cy
    vec[:, 2], vec[:, 3] = cx - bx, cy - by
    vec[~ok] = 0.0
    out[m] = vec.astype(np.float32)
    return out


def render_with_flare(scene, filepath, n_grid=260, blur_px=0.8):
    """渲染 + 正向炫光层合成（旧接口，等同 render_frame(flare=True)）。"""
    return render_frame(scene, filepath, flare=True, n_grid=n_grid, blur_px=blur_px)

def render_frame(scene, filepath, flare=True, n_grid=260, blur_px=0.8, flare_layer_path=None, passes=None, vectors=True):
    """渲染当前帧并做插件的后期：LATERAL_POST 的横向色差、正向炫光层（flare=True）。
    返回 (合成后的 Image 数据块（线性）, 炫光光源名单, 炫光层 H×W×3)。filepath 按扩展名写出（PNG/TIFF 16 位按场景色彩管理，EXR 线性）。
    flare_layer_path：另存炫光层为线性 EXR（单独调色 / 时域稳定用）。
    passes（0.8.3）："all" 或渲染层名列表 → filepath（.exr）写成多层 EXR，保留视图层打开的渲染层（见 render_frame_multilayer）。"""
    import os, tempfile
    from . import flare as FL
    if passes is not None:
        return render_frame_multilayer(scene, filepath, flare=flare, n_grid=n_grid, blur_px=blur_px, passes=passes, vectors=vectors)
    cam_ob = scene.camera
    p = cam_ob.data.blenderlens
    ghosts_was = p.ghosts
    if flare:
        p.ghosts = False                               # 正向通道取代相机端鬼影，避免重复
    apply_settings(cam_ob)
    tmp = os.path.join(tempfile.gettempdir(), f"blenderlens_beauty_{os.getpid()}.exr")
    ims = scene.render.image_settings
    media, fmt, path0 = getattr(ims, "media_type", None), ims.file_format, scene.render.filepath
    if media is not None:
        ims.media_type = 'IMAGE'
    ims.file_format = 'OPEN_EXR'
    scene.render.filepath = tmp
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        if media is not None:
            ims.media_type = media
        ims.file_format, scene.render.filepath = fmt, path0
        p.ghosts = ghosts_was
        apply_settings(cam_ob)
    img = bpy.data.images.load(tmp, check_existing=False)
    W, H = img.size                                    # 实际输出尺寸（含 resolution_percentage、Render Region 裁切）
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)
    bpy.data.images.remove(img)
    try:
        os.remove(tmp)
    except OSError:
        pass
    region = _region(scene, W, H)                      # 0.8.3：裁切区域按它在整幅里的位置做色差 / 炫光
    if p.dispersion_mode == 'LATERAL_POST':
        px = _lateral_post(px, cam_ob, W, H, region)
    layer, used = _flare_layers(scene, cam_ob, W, H, region, n_grid, blur_px) if flare else (np.zeros((H, W, 3)), [])
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
