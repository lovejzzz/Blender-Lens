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
                coating_type=1 if p.coating_type == 'BROADBAND' else 0, unit_scale=us, weight_scale=float(WEIGHT_SCALE[0]))
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

WEIGHT_SCALE = [1.0]           # 0.8.7：相机光线权重缩放（排除层的两次辅助渲染临时设 0.25，见 render_frame_multilayer）
EMPTY_FRAME_WORLD = ["none"]   # 0.8.8：空画面不带世界（"scene" = 用场景的世界，对照测试用）
EMPTY_FRAME_MODE = ["scene"]   # 0.8.8：空画面在临时场景里渲（"hide" = 0.8.7 的做法，对照测试用）


def _write_gpu_fields(cam, d1, d2, f_number, blades, phi=0.0):
    """GPU 路径的每帧参数通道（见 gpukernel.FIELD_MAP）。自定义相机用不到这些字段；CPU / OSL 路径忽略它们。
    它们跟随对焦 / 光圈的关键帧（frame_change 时 apply_settings 重写），所以动画在 GPU 路径下同样生效。返回是否改写。"""
    c = _set(cam.stereo, "convergence_distance", 1000.0 + d1, 1e-7)
    c |= _set(cam, "fisheye_fov", 16.0 + d2 / 5.0, 1e-7)
    c |= _set(cam.dof, "use_dof", True)
    c |= _set(cam.dof, "aperture_ratio", 1.0 / max(f_number, 1e-3), 1e-7)
    c |= _set(cam.dof, "aperture_blades", int(blades))
    c |= _set(cam, "fisheye_lens", 50.0 + 1.0e4 * phi, 1e-7)
    s = float(WEIGHT_SCALE[0])
    c |= _set(cam, "fisheye_polynomial_k0", s if s < 1.0 else 0.0, 1e-9)
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
    layer = np.zeros((Hf, Wf, 3), np.float32)
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

PASS_THROUGH_PROP = "bl_pass_through"


def _material_pass_through(mat):
    """材质是不是「薄透射面」：Principled（Transmission ≥ 0.9、Roughness ≤ 0.1）或 Glass BSDF（Roughness ≤ 0.1）。
    输入接了节点（纹理）就无法判断 → 不算。"""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    def val(node, *names):
        for nm in names:
            sock = node.inputs.get(nm)
            if sock is not None:
                return None if sock.is_linked else float(sock.default_value)
        return None
    found = False
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            t, r = val(n, "Transmission Weight", "Transmission"), val(n, "Roughness")
            if t is None or r is None or t < 0.9 or r > 0.1:
                return False
            found = True
        elif n.type == 'BSDF_GLASS':
            r = val(n, "Roughness")
            if r is None or r > 0.1:
                return False
            found = True
        elif n.type in ('BSDF_DIFFUSE', 'EMISSION', 'SUBSURFACE_SCATTERING', 'BSDF_SHEEN'):
            return False
    return found


def pass_through_objects(scene):
    """矢量与深度要穿过的透射面（0.8.5，MessyRoom 0.8.5 R1）：物体自定义属性 bl_pass_through = True，
    或（没有设 False 时）所有材质都是薄透射面（_material_pass_through）。只看参与渲染的网格物体。"""
    out = []
    for ob in scene.objects:
        if ob.type != 'MESH' or ob.hide_render or ob.name.startswith("BL_"):
            continue
        flag = ob.get(PASS_THROUGH_PROP)
        if flag is not None:
            if bool(flag):
                out.append(ob)
            continue
        mats = [s.material for s in ob.material_slots]
        if mats and all(_material_pass_through(m) for m in mats):
            out.append(ob)
    return out


def _aux_render(scene, path, hide=(), data_only=True, need_passes=(), holdout=None, film_transparent=None, holdout_objects=(),
                only_combined=False, samples=None, bounces=None, adaptive=None, denoise=None, weight_scale=None, clamp=None):
    """辅助渲染（多层 EXR，float32，不压缩）：hide 里的物体渲染时隐藏；data_only = 只要数据层（8 采样、0 次弹射、
    不降噪——Position / Depth 只在第 0 个采样写；Cryptomatte 按全部采样累积，补洞要靠它认物体）；need_passes：临时打开的视图层开关
    （(对象路径, 属性)）；holdout：{集合名: True/False} 临时设视图层集合的 holdout；holdout_objects：临时设成 holdout 的物体；
    film_transparent：临时设。only_combined（0.8.7）：只要 Combined——其余渲染层开关全部临时关掉（Cycles 少算、少占内存；
    降噪器照样用它内部的引导数据，Combined 不变）。samples / bounces：临时设采样数 / 弹射上限（None = 不改）。
    clamp（0.9.1）：(直接, 间接) 临时设样本截断（0 = 不截断；在 weight_scale 缩放之前设）。
    weight_scale（0.8.7）：调用方已把相机权重乘了这个数——样本截断（sample_clamp_*，绝对值）跟着乘，并关掉俄罗斯轮盘
    （它按路径通量决定是否提前终止，通量缩小会多杀路径、多出噪点）：除回去之后与不缩放、不用轮盘的渲染逐像素相同。全部恢复。"""
    vl = bpy.context.view_layer if bpy.context.view_layer in scene.view_layers[:] else scene.view_layers[0]
    c, r, ims = scene.cycles, scene.render, scene.render.image_settings
    undo = []
    def setv(owner, attr, v):
        if hasattr(owner, attr) and getattr(owner, attr) != v:
            undo.append((owner, attr, getattr(owner, attr))); setattr(owner, attr, v)
    try:
        for ob in hide:
            setv(ob, "hide_render", True)
        for ob in holdout_objects:
            setv(ob, "is_holdout", True)
        if only_combined:
            from . import passes as PS
            for (path_, attr) in PS.FLAG_PASSES:
                setv(getattr(vl, path_) if path_ else vl, attr, False)
        if samples is not None:
            setv(c, "samples", int(samples))
        if adaptive is not None:
            setv(c, "use_adaptive_sampling", bool(adaptive))
        if denoise is not None:
            setv(c, "use_denoising", bool(denoise))
        if bounces is not None:
            b = int(bounces)
            for attr in ("max_bounces", "diffuse_bounces", "glossy_bounces", "transmission_bounces", "volume_bounces"):
                # 透明弹射不限：雨丝重叠时要穿过多层透明，限了雨会变「实」（实测 alpha +48%）
                if getattr(c, attr, None) is not None and getattr(c, attr) > b:
                    setv(c, attr, b)
        if clamp is not None:
            setv(c, "sample_clamp_direct", float(clamp[0])); setv(c, "sample_clamp_indirect", float(clamp[1]))
        if weight_scale is not None:
            for attr in ("sample_clamp_direct", "sample_clamp_indirect"):
                if getattr(c, attr, 0.0) > 0.0:
                    setv(c, attr, getattr(c, attr) * float(weight_scale))
            setv(c, "min_light_bounces", c.max_bounces)              # 弹射数 ≤ 下限的路径不做轮盘
            setv(c, "min_transparent_bounces", c.transparent_max_bounces)
        if data_only:
            setv(c, "samples", 8); setv(c, "use_denoising", False); setv(c, "use_adaptive_sampling", False)   # Cryptomatte 按全部采样累积：
                                                                                    # 1 采样时它和 Position 一样有洞，补洞就认不出物体
            setv(c, "max_bounces", 0)
        for path_, attr in need_passes:
            setv(getattr(vl, path_) if path_ else vl, attr, True)
        if holdout:
            def walk(lc):
                yield lc
                for ch in lc.children:
                    yield from walk(ch)
            for lc in walk(vl.layer_collection):
                if lc.name in holdout:
                    setv(lc, "holdout", holdout[lc.name])
        if film_transparent is not None:
            setv(r, "film_transparent", film_transparent)
        if getattr(ims, "media_type", None) is not None:
            setv(ims, "media_type", 'MULTI_LAYER_IMAGE')
        setv(ims, "file_format", 'OPEN_EXR_MULTILAYER'); setv(ims, "color_depth", '32')
        if hasattr(ims, "exr_codec"):
            setv(ims, "exr_codec", 'NONE')
        setv(r, "use_compositing", False)
        old_fp = r.filepath; r.filepath = path
        try:
            bpy.ops.render.render(write_still=True, scene=scene.name)
        finally:
            r.filepath = old_fp
    finally:
        for owner, attr, v in reversed(undo):
            try:
                setattr(owner, attr, v)
            except TypeError:
                pass


def _copy_rna(src, dst):
    """把 src 的可写标量属性（不含指针 / 集合）抄到 dst（同类型的 RNA 结构）。"""
    for prop in src.bl_rna.properties:
        if prop.is_readonly or prop.type in ('POINTER', 'COLLECTION') or prop.identifier == "rna_type":
            continue
        try:
            setattr(dst, prop.identifier, getattr(src, prop.identifier))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass


def _empty_frame_render(scene, cam_ob, path, samples=None, weight_scale=None):
    """排除层 alpha 的分母：「什么都没有、只有世界背景」的画面（0.8.8）。
    空画面的透明度只取决于镜头权重与采样序列（种子、采样数、帧号、分辨率、运动模糊设置），与场景里的物体、相机位置都无关，
    所以在一个只有相机（共用同一份相机数据 = 同一支镜头）和世界的临时场景里渲：不用同步 / 隐藏几千个物体
    （MessyRoom 3,100 个物体的场景，原来的「全部隐藏」做法这一步约 30 秒）。渲染、Cycles、色彩设置逐项抄过去，渲完删掉。"""
    tmp = bpy.data.scenes.new("BL_EmptyFrame")
    cam = None
    try:
        for a in ("render", "cycles", "view_settings", "display_settings"):
            if hasattr(scene, a) and hasattr(tmp, a):
                _copy_rna(getattr(scene, a), getattr(tmp, a))
        tmp.render.use_persistent_data = False
        tmp.world = scene.world if EMPTY_FRAME_WORLD[0] == "scene" else None   # 透明度与背景颜色无关：不用世界（HDRI / 天空纹理
                                                                                 # 每次渲染要建重要性采样图）
        tmp.frame_current = scene.frame_current
        cam = bpy.data.objects.new("BL_EmptyFrameCam", cam_ob.data)
        tmp.collection.objects.link(cam)
        cam.matrix_world = cam_ob.matrix_world.copy()
        tmp.camera = cam
        _aux_render(tmp, path, data_only=False, film_transparent=True, only_combined=True, samples=samples,
                    adaptive=False, denoise=False, weight_scale=weight_scale)
    finally:
        if cam is not None:
            bpy.data.objects.remove(cam)
        bpy.data.scenes.remove(tmp)


def exclude_objects(scene, collections):
    """exclude_collections（集合名列表）→ 其中（含子集合）参与渲染的物体。名字不存在 → ValueError（渲染之前）。"""
    out, seen = [], set()
    for name in collections or ():
        col = bpy.data.collections.get(name)
        if col is None:
            raise ValueError(f"exclude_collections：找不到集合 {name!r}（现有：{sorted(c.name for c in bpy.data.collections)}）")
        for ob in col.all_objects:
            if ob.name not in seen and not ob.hide_render and ob.name in scene.objects:
                seen.add(ob.name); out.append(ob)
    return out


REFLECTOR_PROP = "bl_reflector"


def _plane_of(ob, tol=1e-4):
    """网格是不是平面：→ (局部原点, 局部法线) 或 None。顶点在最佳拟合平面上的偏差 / 尺寸 < tol。"""
    me = ob.data
    n = len(me.vertices)
    if n < 3 or n > 400000:
        return None
    co = np.empty(n * 3, np.float64); me.vertices.foreach_get("co", co); co = co.reshape(n, 3)
    c = co.mean(0)
    u, sv, vt = np.linalg.svd(co - c, full_matrices=False)
    if sv[0] <= 0 or sv[2] / sv[0] > tol:
        return None
    return c, vt[2]


def _principled_emits(ob):
    """材质里 Principled 的发光强度 > 0 且颜色不黑（接了纹理也算发光）。"""
    for sl in ob.material_slots:
        m = sl.material
        if m is None or not m.use_nodes or m.node_tree is None:
            continue
        for nd in m.node_tree.nodes:
            if nd.type == 'BSDF_PRINCIPLED':
                st, col = nd.inputs.get("Emission Strength"), nd.inputs.get("Emission Color")
                if st is None:
                    continue
                if st.is_linked or (st.default_value > 0 and (col is None or col.is_linked or max(col.default_value[:3]) > 0)):
                    return True
    return False


def reflector_objects(scene):
    """平面反射体（0.8.5，MessyRoom 0.8.5 R6）：物体属性 bl_reflector = True，或（没设 False 时）平面网格且材质是
    Principled、粗糙度 ≤ 0.4 或接了纹理（湿地面、水洼常用噪声控制粗糙度）、不是透射面、不发光。→ [(物体, 局部原点, 局部法线)]。
    0.8.6：只对倒影占比 > 0.3 的像素追反射光线（reflection_vectors(need=…)），所以自动识别多认几块平面也不会很慢。"""
    out = []
    for ob in scene.objects:
        if ob.type != 'MESH' or ob.hide_render or ob.name.startswith("BL_"):
            continue
        flag = ob.get(REFLECTOR_PROP)
        if flag is not None and not bool(flag):
            continue
        if flag is None:
            if _emission_of(ob)[0] > 0 or _principled_emits(ob):   # 0.8.6：发光的招牌 / 灯箱 / 卡片不算（MessyRoom：自动识别太宽）
                continue
            ok = False
            for sl in ob.material_slots:
                m = sl.material
                if m is None or not m.use_nodes or m.node_tree is None or _material_pass_through(m):
                    continue
                for nd in m.node_tree.nodes:
                    if nd.type == 'BSDF_PRINCIPLED':
                        r = nd.inputs.get("Roughness")
                        if r is not None and (r.is_linked or r.default_value <= 0.4):
                            ok = True
            if not ok:
                continue
        pl = _plane_of(ob)
        if pl is not None:
            out.append((ob, pl[0], pl[1]))
    return out


def _scene_bvh(dg):
    """场景里所有网格（含实例，镜头自己的 BL_ 物体除外）合成一棵 BVH 树（世界坐标）→ (树, 三角形下标 → 物体名)。
    与 scene.ray_cast 用同一份求值后的几何；百万三角形约 0.4 秒建好，每条光线约 1 µs（scene.ray_cast 约 4.6 µs）。"""
    from mathutils.bvhtree import BVHTree
    verts, tris, owner_idx, names = [], [], [], []
    base = 0
    for inst in dg.object_instances:
        ob = inst.object
        if ob.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META') or ob.name.startswith("BL_"):
            continue
        try:
            me = ob.to_mesh()
        except RuntimeError:
            continue
        if me is None or not len(me.vertices):
            ob.to_mesh_clear(); continue
        me.calc_loop_triangles()
        n = len(me.vertices)
        co = np.empty(n * 3, np.float32); me.vertices.foreach_get("co", co); co = co.reshape(n, 3)
        M = np.array(inst.matrix_world, np.float64)
        co = co @ M[:3, :3].T + M[:3, 3]
        lt = np.empty(len(me.loop_triangles) * 3, np.int32); me.loop_triangles.foreach_get("vertices", lt)
        ob.to_mesh_clear()
        if not len(lt):
            continue
        verts.append(co); tris.append(lt.reshape(-1, 3) + base); base += n
        owner_idx.append(np.full(len(lt) // 3, len(names), np.int32)); names.append(ob.name)
    if not verts:
        return None, None
    V = np.concatenate(verts); T = np.concatenate(tris)
    tree = BVHTree.FromPolygons(V.tolist(), T.tolist(), all_triangles=True)
    own = np.concatenate(owner_idx)
    return tree, _Owners(own, names)


class _Owners:
    def __init__(self, idx, names):
        self.idx, self.names = idx, names

    def __getitem__(self, i):
        return self.names[self.idx[i]]


def reflection_vectors(scene, cam_ob, parts, vl_name, W, H, region, reflectors, max_rays=20000000, need=None):
    """平面反射体上主反射的运动矢量（虚像点投影回去）：反射体像素 S 处，视线按平面镜面反射，ray_cast 找到倒影里的点 Q
    （没打到 = 天空，取无穷远）；上一帧 / 下一帧时 Q 随它的物体刚体运动、对当时的平面取镜像 Q′，用当时的相机与对焦投影。
    → (H×W×4 矢量（Cycles 约定，只在反射体像素上非零）, H×W bool 有效, 统计)。像素多于 max_rays 时隔一个取一个、邻近填
    （0.8.6 起光线多时用 BVH 树，默认上限 2000 万 = 不再隔点）。"""
    from mathutils import Vector
    from . import passes as PS, lenspack as LP
    pos = PS.find(parts, "Position", vl_name) or PS.find(parts, "Position")
    idx, names = PS.crypto_object_ids(parts, vl_name)
    out = np.zeros((H, W, 4), np.float32); valid = np.zeros((H, W), bool)
    if pos is None or idx is None or not reflectors:
        return out, valid, dict(skipped=True)
    P = np.stack([pos["channels"][c] for c in "XYZ"], -1).astype(np.float64)
    rx = _rx_cached()
    SW, SH = rx["sensor"]["width"], rx["sensor"]["height"]
    Wf, Hf = (region[2], region[3]) if region else (W, H)
    x0, y0t = (region[0], region[1]) if region else (0, 0)
    us = mm_per_unit(scene)
    f0, sub0 = scene.frame_current, scene.frame_subframe
    frames = (f0 - 1, f0, f0 + 1)
    refl_names = {ob.name: (ob, c, n) for ob, c, n in reflectors}
    mask = np.zeros((H, W), bool)
    for k, nm in enumerate(names):
        if nm in refl_names:
            mask |= idx == k
    mask &= np.abs(P).sum(-1) > 0
    n_refl = int(mask.sum())
    if need is not None:                                 # 0.8.6：只追倒影占比够高的像素（下游只在占比 > 0.5 处用倒影矢量）
        mask &= need
    rows, cols = np.nonzero(mask)
    step = 1
    if len(rows) > max_rays:
        step = 2
        keep = (rows % 2 == 0) & (cols % 2 == 0)
        rows, cols = rows[keep], cols[keep]
    if len(rows) == 0:
        return out, valid, dict(pixels=0)
    who = np.array([names[i] for i in idx[rows, cols]])
    dg = bpy.context.evaluated_depsgraph_get()
    C = np.array(cam_ob.matrix_world.translation, np.float64)
    far = FAR_MM / us
    Q = np.empty((len(rows), 3)); hit_ob = np.empty(len(rows), object)
    n_hit = 0
    tree = owners = None
    if len(rows) > 20000:                                 # 0.8.6：光线多时用 BVH 树（约 5 倍快，同一几何、同一交点）
        tree, owners = _scene_bvh(dg)
    for i in range(len(rows)):
        ob, lc, ln = refl_names[who[i]]
        mw = ob.matrix_world
        nrm = (mw.to_3x3().inverted_safe().transposed() @ Vector(ln)).normalized()
        S = Vector(P[rows[i], cols[i]])
        v = (S - Vector(C)).normalized()
        if v.dot(nrm) > 0:
            nrm = -nrm
        r = v - 2.0 * v.dot(nrm) * nrm
        o = S + nrm * (0.5 / us) + r * (0.5 / us)
        if tree is not None:
            loc, _n, fi, _d = tree.ray_cast(o, r)
            hit, hname = loc is not None, (owners[fi] if loc is not None else None)
        else:
            hit, loc, _n, _i, hob, _m = scene.ray_cast(dg, o, r)
            for _ in range(4):                            # 跳过镜头自己的网格
                if not (hit and hob.name.startswith("BL_")):
                    break
                hit, loc, _n, _i, hob, _m = scene.ray_cast(dg, loc + r * (0.5 / us), r)
            hname = hob.name if hit else None
        if hit:
            Q[i] = tuple(loc); hit_ob[i] = hname; n_hit += 1
        else:
            Q[i] = tuple(S + r * far); hit_ob[i] = None
    cams, focus, mats, planes = {}, {}, {}, {}
    used = {h for h in hit_ob if h}
    try:
        for f in frames:
            scene.frame_set(f)
            cams[f] = np.array(cam_ob.matrix_world, np.float64); focus[f] = focus_mm(cam_ob)
            for nm in used:
                ob = bpy.data.objects.get(nm)
                if ob is not None:
                    mats.setdefault(nm, {})[f] = np.array(ob.matrix_world, np.float64)
            for nm, (ob, lc, ln) in refl_names.items():
                mw = ob.matrix_world
                planes.setdefault(nm, {})[f] = (np.array(mw @ Vector(lc)),
                                                np.array((mw.to_3x3().inverted_safe().transposed() @ Vector(ln)).normalized()))
    finally:
        scene.frame_set(f0, subframe=sub0)
        apply_settings(cam_ob)
    pyup = Hf - 1 - (rows + y0t)
    s0x = ((cols + x0 + 0.5) / Wf - 0.5) * SW; s0y = -((pyup + 0.5) / Hf - 0.5) * SH
    projs = {}
    proj = {}
    for f in frames:
        Qf = Q.copy()
        for nm, mm in mats.items():                       # 倒影里的物体刚体运动
            if f == f0 or f not in mm:
                continue
            T = mm[f] @ np.linalg.inv(mm[f0])
            sel = hit_ob == nm
            if sel.any():
                Qf[sel] = (np.c_[Qf[sel], np.ones(sel.sum())] @ T.T)[:, :3]
        for nm in refl_names:                             # 对当时的平面取镜像
            sel = who == nm
            o, n = planes[nm][f]
            d = (Qf[sel] - o) @ n
            Qf[sel] = Qf[sel] - 2.0 * d[:, None] * n[None, :]
        pc = (np.c_[Qf, np.ones(len(Qf))] @ np.linalg.inv(cams[f]).T)[:, :3]
        Ql = np.column_stack([-pc[:, 0], pc[:, 1], -pc[:, 2]]) * us
        key = round(focus[f], 3) if math.isfinite(focus[f]) else -1
        if key not in projs:
            projs[key] = LP.LensProjector(rx, focus[f])
        sx, sy, ok = projs[key].project(Ql, (s0x, s0y))
        proj[f] = ((sx / SW + 0.5) * Wf - 0.5, (-sy / SH + 0.5) * Hf - 0.5, ok)
    (ax, ay, aok), (cx, cy, cok), (bx, by, bok) = proj[frames[0]], proj[frames[1]], proj[frames[2]]
    ok = aok & cok & bok
    vec = np.zeros((len(rows), 4), np.float32)
    vec[:, 0], vec[:, 1] = ax - cx, ay - cy
    vec[:, 2], vec[:, 3] = cx - bx, cy - by
    vec[~ok] = 0.0
    out[rows, cols] = vec; valid[rows, cols] = ok
    if step == 2:                                         # 隔点算的：其余像素取左上邻点
        for dy, dx in ((0, 1), (1, 0), (1, 1)):
            r2, c2 = rows + dy, cols + dx
            inb = (r2 < H) & (c2 < W)
            r2, c2 = r2[inb], c2[inb]
            m2 = mask[r2, c2]
            out[r2[m2], c2[m2]] = vec[inb][m2]; valid[r2[m2], c2[m2]] = ok[inb][m2]
    return out, valid, dict(pixels=n_refl, traced=int(mask.sum()), rays=len(rows), hits=n_hit, step=step,
                            reflectors=sorted(refl_names))


LAST_TIMING = {}                                         # 上一次 render_frame_multilayer 各步用时（秒），render_shot 写日志用

def check_exclude_filters(clamp, knee):
    """exclude_clamp / exclude_knee 的格式检查（渲染之前调用）：clamp = (直接 ≥ 0, 间接 ≥ 0)，knee = (0 ≤ 起点 < 上限)。"""
    if clamp is not None:
        if len(clamp) != 2 or min(float(v) for v in clamp) < 0:
            raise ValueError(f"exclude_clamp 要 (直接, 间接)，都 ≥ 0（0 = 不截断），收到 {clamp!r}")
    if knee is not None:
        if len(knee) != 2 or not float(knee[1]) > float(knee[0]) >= 0.0:
            raise ValueError(f"exclude_knee 要 (起点, 上限)，0 ≤ 起点 < 上限，收到 {knee!r}")


def exclude_knee_apply(ch, knee):
    """排除层亮度软压缩（0.9.1）：ch = {"R","G","B": 数组}（预乘），knee = (起点 a, 上限 b)。亮度 L ≤ a 不变；
    L > a 时 L' = a + (b − a)·(1 − exp(−(L − a)/(b − a)))（在 a 处一阶连续，L → ∞ 时趋向 b），RGB 等比缩放（色相不变）。
    原地修改，返回统计 {knee_pixels, max_before, max_after}。"""
    a, b = float(knee[0]), float(knee[1])
    if not b > a >= 0.0:
        raise ValueError(f"exclude_knee 要 0 ≤ 起点 < 上限，收到 {knee}")
    R, G, B = (ch[c].astype(np.float32) for c in "RGB")
    L = 0.2126 * R + 0.7152 * G + 0.0722 * B
    over = L > a
    Ln = np.where(over, a + (b - a) * (1.0 - np.exp(-(np.maximum(L, a) - a) / (b - a))), L)
    k = np.where(over, Ln / np.maximum(L, 1e-12), 1.0).astype(np.float32)
    for c, X in zip("RGB", (R, G, B)):
        ch[c] = X * k
    return dict(knee_pixels=int(over.sum()), max_before=round(float(L.max()), 4), max_after=round(float(Ln.max()), 4))


def render_frame_multilayer(scene, filepath, flare=True, n_grid=260, blur_px=0.8, passes="all", vectors=True,
                            exr_codec=None, half_color=None, exclude_collections=None, exclude_mode="hide",
                            exclude_spp=None, exclude_bounces=None, exclude_clamp=None, exclude_knee=None):
    """渲染当前帧，写多层（多部分）EXR，保留视图层打开的所有渲染层：
      · 颜色层（Combined、各光组 Combined_<名>、Emit、Diffuse/Glossy/Transmission/Volume…、AO、颜色 AOV、Noisy Image、
        Denoising Albedo）做与 Combined 相同的横向色差（LATERAL_POST 时）→ 各层相加仍约等于 Combined；
      · 数据层（Depth、Normal、Position、Vector、UV、Index、Cryptomatte、数值 AOV、Denoising Normal/Depth）原样；
      · 插件加的层：BL_Flare（正向炫光 + 星芒）、BL_Veil（杂光，差值层，可为负）、BL_Final（Combined + BL_Flare + BL_Veil，
        即单层输出的成片），以及 BL_Vector（vectors=True 时）。Combined 本身不含炫光与杂光。
      · BL_Vector：按本镜头的真实成像（主光线、当前对焦）算的运动矢量，像素单位、y 向上，约定与 Cycles 的 Vector 相同：
        X,Y = 上一帧位置 − 当前位置，Z,W = 当前位置 − 下一帧位置。相机运动 + 刚体物体运动（按 Cryptomatte 识别物体）；
        形变（骨骼、形状键）不计。Cycles 自己的 Vector 层对自定义相机不对（源码 TODO：退回相机坐标），原样保留但不要用。
    passes："all"、预设 "temporal"（时间降噪需要的层，会临时打开 Depth / 去噪数据 / 体积层）、或渲染层名列表
      （可混用预设名与 "lightgroups"，如 ["temporal", "lightgroups"]）；插件层总是写入。
    exr_codec / half_color（0.8.4）：输出压缩与颜色层精度。颜色层 half + 压缩，数据层（Depth、Position、BL_Vector…）恒为
      float32，遇有损压缩时改用 ZIP。默认：用了预设 → "DWAB" + half；否则沿用场景的输出设置（EXR 压缩、色深）。
    exclude_collections（0.8.5，MessyRoom 0.8.5 R2）：这些集合（如雨、粒子）不进主渲染，另渲一次只有它们的层 BL_Exclude
      （RGBA 预乘，其余物体做 holdout → 被场景挡住的部分自动去掉，胶片透明）。Combined、矢量、置信度都是不含它们的场景层；
      BL_Final = (BL_Exclude over Combined) + BL_Flare + BL_Veil。exclude_mode："hide"（主渲染里完全隐藏 → 场景层与不渲雨
      逐像素相同）或 "camera"（只对相机不可见：保留它们的阴影 / 反射）。
      排除层的渲染只算 Combined（0.8.7，其余渲染层临时关掉，结果不变）。exclude_spp / exclude_bounces（0.8.7，可选，默认不改）：
      排除层单独的采样数 / 弹射上限（不含透明弹射）——雨丝这类东西噪点小，可以少用采样省时间，代价是排除层噪点多一点、间接光少一点。
      exclude_clamp / exclude_knee（0.9.1，MessyRoom Day 5：雨滴把车灯 / 亮窗折射成一帧一闪的亮点）：
        exclude_clamp=(直接, 间接)：只对排除层那次渲染的样本截断（Cycles sample_clamp，0 = 不截断；主渲染不变）；
        exclude_knee=(起点, 上限)：排除层（预乘 RGB）按亮度软压缩——起点以下不变，以上平滑地趋向上限（色相不变），
        雨的样子保留、单帧的强亮点压下去；BL_Final 用压缩后的排除层合成。雨在时间上不连贯（每帧移动几十厘米），
        所以是逐像素的软压缩，不是时间滤波。两者都写进 EXR 头的 blenderlens/exclude。
    为了 BL_Vector，会临时打开 Position 与 Cryptomatte Object 层（用户没开的不写进输出）。渲染时临时关闭合成（输出原始渲染层）。
    返回 (BL_Final 的 Image 数据块, 炫光光源名单, 炫光层 H×W×3（行 0 = 底部，与旧接口一致）)。"""
    import os, tempfile, time
    from . import flare as FL, passes as PS
    T = {}; t0 = time.perf_counter()
    check_exclude_filters(exclude_clamp, exclude_knee)
    M = {}
    def _mem(stage):
        import resource, sys
        M[stage] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e9 if sys.platform == "darwin" else 1e6), 2)
    cam_ob = scene.camera
    p = cam_ob.data.blenderlens
    vl = bpy.context.view_layer if bpy.context.view_layer in scene.view_layers[:] else scene.view_layers[0]
    keep_pass, flags, preset, wanted_names = PS.expand(passes)
    ims0 = scene.render.image_settings
    if exr_codec is None:
        exr_codec = "DWAB" if preset else getattr(ims0, "exr_codec", None)
    if half_color is None:
        half_color = True if preset else ims0.color_depth == '16'
    if exr_codec is not None and exr_codec not in PS.CODECS:      # 渲染之前就检查（别白渲一帧）
        raise ValueError(f"exr_codec 必须是 {PS.CODECS} 之一，收到 {exr_codec!r}")
    added = []
    flag_undo = []
    for path_, attr in flags:                             # 预设需要的渲染层：临时打开，渲完恢复（不在输出里额外多出层）
        owner = getattr(vl, path_) if path_ else vl
        if hasattr(owner, attr) and not getattr(owner, attr):
            setattr(owner, attr, True); flag_undo.append((owner, attr, False))
    if passes != "all":                                   # 没人要的渲染层：渲染期间关掉（Cycles 少算、少占内存）
        need = {("", "use_pass_position"), ("", "use_pass_cryptomatte_object")} if vectors else set()   # 补洞 / 矢量要用
        for (path_, attr), names in PS.FLAG_PASSES.items():
            owner = getattr(vl, path_) if path_ else vl
            if (path_, attr) in need or not hasattr(owner, attr) or not getattr(owner, attr):
                continue
            if PS.flag_wanted(keep_pass, wanted_names, names):
                continue
            setattr(owner, attr, False); flag_undo.append((owner, attr, True))
    through = pass_through_objects(scene) if vectors else []      # 0.8.5：矢量 / 深度穿过透射面
    if exclude_mode not in ("hide", "camera"):
        raise ValueError(f"exclude_mode 必须是 'hide' 或 'camera'，收到 {exclude_mode!r}")
    ex_objs = exclude_objects(scene, exclude_collections)
    ex_names = {o.name for o in ex_objs}
    through = [o for o in through if o.name not in ex_names]
    reflectors = [r for r in reflector_objects(scene) if r[0].name not in ex_names] if vectors else []   # 0.8.5 R6
    for ob in ex_objs:                                    # 主渲染里不要它们
        if exclude_mode == "hide":
            ob.hide_render = True; flag_undo.append((ob, "hide_render", False))
        else:
            if ob.visible_camera:
                ob.visible_camera = False; flag_undo.append((ob, "visible_camera", True))
    if vectors:
        need = [("use_pass_position", "Position"), ("use_pass_cryptomatte_object", "CryptoObject")]
        if through or reflectors:                         # 透射面 / 反射体上的反射占比 = 光泽层 / Combined
            need += [("use_pass_glossy_direct", "Glossy Direct"), ("use_pass_glossy_indirect", "Glossy Indirect")]
        for attr, pname in need:
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
        ims.color_depth = '16' if half_color else '32'       # 半精度时 Blender 自己就把数据层留成 float32
        if keep[3] is not None:
            ims.exr_codec = 'NONE'                         # 临时文件：不压缩（省时间）
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
        for owner, attr, val in reversed(flag_undo):
            setattr(owner, attr, val)
        p.ghosts = ghosts_was
        apply_settings(cam_ob)
    _mem("render"); T["render"] = time.perf_counter() - t0; t1 = time.perf_counter()
    drop = {pn for _, pn in added}
    helpers = ("Position", "CryptoObject00", "Depth", "IndexOB", "IndexMA",       # 补洞 / 矢量 / 置信度要用：先读进内存
               "Denoising Specular Albedo", "Denoising Roughness") + (("Glossy Direct", "Glossy Indirect") if (through or reflectors) else ())
    in_mem = lambda ps: ps == "Combined" or ps.startswith(helpers)
    out_keep = lambda ps: keep_pass(ps) and not any(ps.startswith(d) for d in drop)
    keep_tmp = bool(os.environ.get("BLENDERLENS_KEEP_LAYERS"))   # 调试：保留原始渲染层文件
    tmp_t = None
    try:
        if through:                                       # 辅助渲染：透射面隐藏，只要数据层（第一个非透射命中）
            tmp_t = os.path.join(tempfile.gettempdir(), f"blenderlens_through_{os.getpid()}.exr")
            p.ghosts = False
            try:
                _aux_render(scene, tmp_t, hide=list(through) + (ex_objs if exclude_mode == "hide" else []), data_only=True,
                            need_passes=[("", "use_pass_position"), ("", "use_pass_cryptomatte_object"), ("", "use_pass_z")])
            finally:
                p.ghosts = ghosts_was
                apply_settings(cam_ob)
            T["through_render"] = time.perf_counter() - t1; t1 = time.perf_counter()
        ex_part = None
        if ex_objs:                                       # 只有排除集合的一层：其余物体 holdout、胶片透明
            tmp_x = os.path.join(tempfile.gettempdir(), f"blenderlens_exclude_{os.getpid()}.exr")
            others = [o for o in scene.objects if o.name not in ex_names and not o.hide_render
                      and o.type in ('MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME', 'POINTCLOUD', 'CURVES', 'GREASEPENCIL')
                      and not o.name.startswith("BL_")]
            # 0.8.7：排除层的 alpha。Cycles 的 alpha = 1 − Σ(相机光线权重 × 透过) / N：用真实镜头时权重里带着渐晕与出瞳
            # 采样的「过 / 不过」，alpha 里就混进了渐晕和噪点（且透明度 > 1 时被截断 → 偏亮的 alpha），合成时把没有雨的地方也压暗了。
            # 做法：同一种子、同样采样数、关自适应，再渲一次「什么都不显示」的空画面（相机采样逐个相同），
            # 雨的透过率 = 两次透明度之比——镜头权重逐个约掉，没有雨的像素恰好 = 1。两次都把相机权重缩到 1/4，透明度不会被截断；
            # 样本截断随之缩放、关俄罗斯轮盘（_aux_render weight_scale），颜色除回去后噪点不比不缩放的多（实测 RMSE 相同）。
            tmp_0 = os.path.join(tempfile.gettempdir(), f"blenderlens_exclude0_{os.getpid()}.exr")
            everything = [o for o in scene.objects if not o.hide_render and o.type not in ('CAMERA', 'LIGHT')
                          and not o.name.startswith("BL_")]
            XS = 0.25
            p.ghosts = False
            WEIGHT_SCALE[0] = XS
            apply_settings(cam_ob)
            try:
                _aux_render(scene, tmp_x, data_only=False, film_transparent=True, holdout_objects=others, only_combined=True,
                            samples=exclude_spp, bounces=exclude_bounces, adaptive=False, weight_scale=XS, clamp=exclude_clamp)
                t_e0 = time.perf_counter()
                if EMPTY_FRAME_MODE[0] == "scene":
                    _empty_frame_render(scene, cam_ob, tmp_0, samples=exclude_spp, weight_scale=XS)
                else:                                     # 0.8.7 的做法（对照测试用）：在原场景里把所有物体隐藏
                    _aux_render(scene, tmp_0, data_only=False, film_transparent=True, hide=everything, only_combined=True,
                                samples=exclude_spp, adaptive=False, denoise=False, weight_scale=XS)
                T["exclude_empty"] = time.perf_counter() - t_e0
            finally:
                WEIGHT_SCALE[0] = 1.0
                p.ghosts = ghosts_was
                apply_settings(cam_ob)
            try:
                ex_part = next(PS.read_one(tmp_x, h) for h in PS.heads(tmp_x) if h["pass_"] == "Combined")
                e0 = next(PS.read_one(tmp_0, h) for h in PS.heads(tmp_0) if h["pass_"] == "Combined")
            finally:
                if not keep_tmp:
                    for f_ in (tmp_x, tmp_0):
                        try:
                            os.remove(f_)
                        except OSError:
                            pass
            ch = ex_part["channels"]
            T_e = 1.0 - ch["A"].astype(np.float32); T_0 = 1.0 - e0["channels"]["A"].astype(np.float32)
            tau = np.where(T_0 > 1e-6, np.clip(T_e / np.maximum(T_0, 1e-6), 0.0, 1.0), 1.0).astype(np.float32)
            for c_ in "RGB":
                ch[c_] = (ch[c_].astype(np.float32) / XS)
            knee_stats = exclude_knee_apply(ch, exclude_knee) if exclude_knee else {}
            ch["A"] = 1.0 - tau
            ex_stats = dict(alpha_mean=round(float(ch["A"].mean()), 5), raw_alpha_mean=round(float(1.0 - T_e.mean()), 5),
                            empty_alpha_mean=round(float(1.0 - T_0.mean()), 5), weight_scale=XS, **knee_stats)
            del T_e, T_0, tau, e0
            T["exclude_render"] = time.perf_counter() - t1; t1 = time.perf_counter()
        hs = PS.heads(tmp)
        parts = [PS.read_one(tmp, h) for h in hs if in_mem(h["pass_"])]
        comb = PS.find(parts, "Combined", vl.name) or PS.find(parts, "Combined")
        if comb is None:
            raise RuntimeError("多层 EXR 里没有 Combined 渲染层")
        H, W = comb["channels"]["R"].shape
        region = _region(scene, W, H)
        _mem("read"); T["read"] = time.perf_counter() - t1; t1 = time.perf_counter()
        maps = _lateral_maps(cam_ob, W, H, region) if p.dispersion_mode == 'LATERAL_POST' else None

        def lateral(*grp):                                 # 颜色层：与 Combined 相同的横向色差（几层一起：采样位置只算一次）
            from . import lenspack as LP
            outs = LP.apply_lateral_ca_planes([[q["channels"][c] for c in "RGB"] for q in grp], maps)
            for q, out in zip(grp, outs):
                for c, pl in zip("RGB", out):
                    q["channels"][c] = pl
        if maps is not None:
            lateral(comb)
        if ex_part is not None:
            ex = ex_part["channels"]
            exa = ex.get("A", np.zeros((H, W), np.float32)).astype(np.float32)
            if maps is not None:
                lateral(ex_part)
                apart = dict(channels=dict(R=exa, G=exa, B=exa))
                lateral(apart); exa = apart["channels"]["G"]
            ex["A"] = exa
        T["lateral"] = time.perf_counter() - t1
        _mem("lateral"); t1 = time.perf_counter()
        beauty = PS.rgb(comb)
        if ex_part is not None:                           # BL_Exclude（预乘）over 场景层
            beauty = PS.rgb(ex_part) + (1.0 - ex_part["channels"]["A"])[..., None] * beauty
        layer, used = _flare_layers(scene, cam_ob, W, H, region, n_grid, blur_px) if flare else (np.zeros((H, W, 3), np.float32), [])
        layer = layer.astype(np.float32, copy=False)
        veil = np.zeros_like(beauty)
        if flare and p.veiling_glare > 0:
            base = beauty + layer
            veil = (FL.veiling_glare(base, p.veiling_glare) - base).astype(np.float32); del base
        final = beauty + layer + veil
        del beauty
        alpha = comb["channels"].get("A", np.ones((H, W), np.float32))
        _mem("flare"); T["flare"] = time.perf_counter() - t1; t1 = time.perf_counter()
        new = [PS.new_part(comb, "BL_Flare", dict(R=layer[..., 0], G=layer[..., 1], B=layer[..., 2])),
               PS.new_part(comb, "BL_Veil", dict(R=veil[..., 0], G=veil[..., 1], B=veil[..., 2])),
               PS.new_part(comb, "BL_Final", dict(R=final[..., 0], G=final[..., 1], B=final[..., 2], A=alpha))]
        if ex_part is not None:
            exc = ex_part["channels"]
            new.append(PS.new_part(comb, "BL_Exclude", dict(R=exc["R"], G=exc["G"], B=exc["B"], A=exc["A"])))
            comb["header"]["blenderlens/exclude"] = json.dumps(dict(collections=list(exclude_collections), objects=len(ex_objs),
                                                                    mode=exclude_mode, exclude_spp=exclude_spp,
                                                                    exclude_clamp=list(exclude_clamp) if exclude_clamp else None,
                                                                    exclude_knee=list(exclude_knee) if exclude_knee else None, **ex_stats))
        del veil
        fill = fill_first_sample_passes(scene, cam_ob, parts, vl.name, W, H, region)
        comb["header"]["blenderlens/fill"] = json.dumps(fill)
        _mem("fill"); T["fill"] = time.perf_counter() - t1; t1 = time.perf_counter()
        glass = None
        if vectors and through:                          # 0.8.5：BL_Vector / Depth 取第一个非透射命中；透射面自己的进 *Surface 层
            names_t = {o.name for o in through}
            tparts = [PS.read_one(tmp_t, h) for h in PS.heads(tmp_t)
                      if h["pass_"] in ("Combined", "Position", "CryptoObject00", "Depth")]
            fill_t = fill_first_sample_passes(scene, cam_ob, tparts, vl.name, W, H, region, skip=names_t)
            res_s = bl_vector(scene, cam_ob, parts, vl.name, W, H, region)
            if res_s is not None:
                new.append(PS.new_part(comb, "BL_VectorSurface", dict(X=res_s[..., 0], Y=res_s[..., 1], Z=res_s[..., 2], W=res_s[..., 3])))
            idx_s, names_s = PS.crypto_object_ids(parts, vl.name)
            gmask = np.zeros((H, W), bool)
            if idx_s is not None:
                for k, nm in enumerate(names_s):
                    if nm in names_t:
                        gmask |= idx_s == k
            dep = PS.find(parts, "Depth", vl.name) or PS.find(parts, "Depth")
            dep_t = PS.find(tparts, "Depth", vl.name) or PS.find(tparts, "Depth")
            if dep is not None and dep_t is not None:
                new.append(PS.new_part(comb, "BL_DepthSurface", dict(Z=dep["channels"]["Z"].copy())))
                dep["channels"]["Z"] = np.where(gmask, dep_t["channels"]["Z"], dep["channels"]["Z"]).astype(np.float32)
            gl = [PS.find(parts, n_, vl.name) or PS.find(parts, n_) for n_ in ("Glossy Direct", "Glossy Indirect")]
            lum = lambda q: (q["channels"]["R"].astype(np.float32) + q["channels"]["G"] + q["channels"]["B"]) / 3.0
            share = None
            if all(q is not None for q in gl):
                share = np.clip((lum(gl[0]) + lum(gl[1])) / np.maximum(lum(comb), 1e-6), 0.0, 1.0)
            glass = (gmask, share)
            comb["header"]["blenderlens/pass_through"] = json.dumps(dict(objects=sorted(names_t), pixels=int(gmask.sum()), fill=fill_t))
            vec_parts = tparts
        else:
            vec_parts = parts
        if vectors:
            res = bl_vector(scene, cam_ob, vec_parts, vl.name, W, H, region, return_valid=True)
            if res is not None and glass is not None:     # 只有透射面像素用穿过去的矢量，其余与不穿透时逐像素相同
                res_m = bl_vector(scene, cam_ob, parts, vl.name, W, H, region, return_valid=True)
                g = glass[0]
                res = (np.where(g[..., None], res[0], res_m[0]), np.where(g, res[1], res_m[1]))
                del res_m
            if res is not None:
                vec, valid = res
                new.append(PS.new_part(comb, "BL_Vector", dict(X=vec[..., 0], Y=vec[..., 1], Z=vec[..., 2], W=vec[..., 3])))
                refl_use = None
                if reflectors:                           # 0.8.5：平面反射体上主反射的运动矢量 + 反射占比
                    gl = [PS.find(parts, n_, vl.name) or PS.find(parts, n_) for n_ in ("Glossy Direct", "Glossy Indirect")]
                    share0 = None
                    if all(q is not None for q in gl):
                        lum3 = lambda q: (q["channels"]["R"].astype(np.float32) + q["channels"]["G"] + q["channels"]["B"]) / 3.0
                        share0 = np.clip((lum3(gl[0]) + lum3(gl[1])) / np.maximum(lum3(comb), 1e-6), 0.0, 1.0)
                    rv, rvalid, rstats = reflection_vectors(scene, cam_ob, parts, vl.name, W, H, region, reflectors,
                                                            need=None if share0 is None else share0 > 0.3)
                    new.append(PS.new_part(comb, "BL_VectorReflection", dict(X=rv[..., 0], Y=rv[..., 1], Z=rv[..., 2], W=rv[..., 3])))
                    if share0 is not None:
                        share_r = share0 * rvalid
                        new.append(PS.new_part(comb, "BL_ReflectionShare", dict(X=share_r.astype(np.float32))))
                        refl_use = share_r > 0.5
                        rstats["share_mean_on_reflectors"] = round(float(share_r[rvalid].mean()), 4) if rvalid.any() else None
                        rstats["reflection_dominant_pixels"] = int(refl_use.sum())
                    comb["header"]["blenderlens/reflection"] = json.dumps(rstats)
                    del rv, rvalid
                conf, cstats = vector_confidence(scene, cam_ob, parts, vl.name, vec, valid, W, H, region, glass=glass,
                                                 reflection_used=refl_use)
                new.append(PS.new_part(comb, "BL_VectorConfidence", dict(X=conf)))
                comb["header"]["blenderlens/vector_confidence"] = json.dumps(cstats)
                del vec, valid, conf
        _mem("vector"); T["vector"] = time.perf_counter() - t1; t1 = time.perf_counter()
        if filepath:                                       # 流式写出：其余渲染层逐个读 → 色差 → 写 → 释放
            out_path = bpy.path.abspath(filepath)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            by_name = {q["name"]: q for q in parts}
            kept = [h for h in hs if out_keep(h["pass_"])]
            plan = []
            for h in kept:
                q = by_name.get(h["name"])
                plan.append(dict(name=h["name"], suffixes=h["suffixes"], header=q["header"] if q else h["header"],
                                 color=PS.is_color(dict(pass_=h["pass_"], channels=dict.fromkeys(h["suffixes"])))))
            plan += [dict(name=q["name"], suffixes=list(q["channels"]), header={}, color=PS.is_color(q)) for q in new]
            wr = PS.StreamWriter(out_path, plan, W, H, half_color=half_color, codec=exr_codec or "ZIP")
            try:
                t_lat = 0.0
                for k in range(0, len(kept), 4):          # 每批 4 层：内存里最多 4 层，色差一起做
                    batch = [by_name.get(h["name"]) or PS.read_one(tmp, h) for h in kept[k:k + 4]]
                    todo = [q for q, pl in zip(batch, plan[k:k + 4]) if pl["color"] and q["name"] not in by_name]
                    if maps is not None and todo:
                        t2 = time.perf_counter(); lateral(*todo); t_lat += time.perf_counter() - t2
                    for q in batch:
                        wr.write(q["channels"])
                    del batch, todo
                for q in new:
                    wr.write(q["channels"])
            finally:
                wr.close()
            T["lateral"] += t_lat
    finally:
        if not keep_tmp:
            for f_ in (tmp, tmp_t):
                if f_:
                    try:
                        os.remove(f_)
                    except OSError:
                        pass
    _mem("write"); T["write"] = time.perf_counter() - t1
    T["total"] = time.perf_counter() - t0
    LAST_TIMING.clear(); LAST_TIMING.update({k: round(v, 2) for k, v in T.items()})
    LAST_TIMING["max_rss_gb"] = M                       # 进程到这一步为止的峰值常驻内存
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


def fill_first_sample_passes(scene, cam_ob, parts, vl_name, W, H, region=None, max_raycast=300000, skip=()):
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
    P = np.stack([pos["channels"][c] for c in "XYZ"], -1).astype(np.float32)
    have = np.abs(P).sum(-1) > 0
    obj = (cov > 0) & (idx >= 0)
    missing = obj & ~have
    n0 = int(missing.sum())
    Hh, Ww = have.shape
    Pf, havef, idxf = P.reshape(-1, 3), have.reshape(-1), idx.reshape(-1)
    for _ in range(6):                                   # 1. 邻域填（同一物体）：只在缺的像素上做（0.8.6，原来整幅平移 8 次）
        mi = np.flatnonzero(missing)
        if not len(mi):
            break
        r, c = np.divmod(mi, Ww)
        acc = np.zeros((len(mi), 3), np.float32); cnt = np.zeros(len(mi), np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                rr, cc = r - dy, c - dx                  # 与原来 np.roll(·, dy / dx) 同一个邻居；出界的不算（原来会绕到对边）
                inb = (rr >= 0) & (rr < Hh) & (cc >= 0) & (cc < Ww)
                ni = np.where(inb, rr * Ww + cc, 0)
                ok = inb & havef[ni] & (idxf[ni] == idxf[mi])
                acc += np.where(ok[:, None], Pf[ni], 0.0); cnt += ok
        sel = cnt > 0
        fi = mi[sel]
        Pf[fi] = acc[sel] / cnt[sel][:, None]            # 本轮补的值都只用上一轮已有的邻居（与原来一致）
        havef[fi] = True
        missing.reshape(-1)[fi] = False
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
        tree = owners = None
        if n > 20000:                                    # 0.8.7：光线多时用 BVH 树（同一几何，约 5 倍快）
            tree, owners = _scene_bvh(dg)

        def cast(o_, d_):
            if tree is None:
                h, l, _n, _i, ob_, _m = scene.ray_cast(dg, o_, d_)
                return h, l, (ob_.name if h else None)
            l, _n, fi, _d = tree.ray_cast(o_, d_)
            return l is not None, l, (owners[fi] if l is not None else None)
        for k in range(n):
            if not (np.isfinite(Po[k]).all() and np.isfinite(Do[k]).all()):
                continue
            o = mw @ Vector((-Po[k, 0] / us, Po[k, 1] / us, -Po[k, 2] / us))
            d = (mw.to_3x3() @ Vector((-Do[k, 0], Do[k, 1], -Do[k, 2]))).normalized()
            hit, loc, hname = cast(o + d * (20.0 / us), d)
            for _ in range(8):                          # 0.8.5：透射面（skip）穿过去，取后面第一个非透射面
                if not (hit and hname in skip):
                    break
                hit, loc, hname = cast(loc + d * (1.0 / us), d)
            if hit and not hname.startswith("BL_"):
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


def bl_vector(scene, cam_ob, parts, vl_name, W, H, region=None, project=None, far=True, return_valid=False):
    """BL_Vector：H×W×4（行 0 = 顶部），像素单位、y 向上；X,Y = 上一帧 − 当前，Z,W = 当前 − 下一帧（Cycles 约定）。
    需要 Position 层；有 Cryptomatte Object 层时计入刚体物体运动。project：可替换的投影函数（测试用：针孔相机），
    签名 project(Q_lens_mm N×3, focus_mm, s0) → (sx, sy, valid)。没有 Position 层时返回 None。
    far（0.8.4）：没有几何的像素（天空、环境）当作无穷远，只随相机转动；False = 这些像素矢量为 0（0.8.3 的行为）。
    return_valid：另返回 H×W bool（矢量算出来了的像素）。"""
    from . import passes as PS, lenspack as LP, oslgen
    pos = PS.find(parts, "Position", vl_name) or PS.find(parts, "Position")
    if pos is None:
        return None
    P = np.stack([pos["channels"]["X"], pos["channels"]["Y"], pos["channels"]["Z"]], axis=-1).astype(np.float32)   # 4K：float32 省 ~0.5 GB
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
    yy, xx = np.indices((H, W), dtype=np.float32)
    px_full = xx + x0
    py_up = (Hf - 1 - (yy + y0t))                       # 整幅里的行号，向上为正
    s0x = ((px_full + 0.5) / Wf - 0.5) * SW
    s0y = -((py_up + 0.5) / Hf - 0.5) * SH
    m = ~bg
    Pw = P[m]
    s0 = (s0x[m], s0y[m])                               # 只用作牛顿迭代的初值
    ids = idx[m] if idx is not None else None
    cache = {}

    def projector(fmm):
        key = round(fmm, 3) if math.isfinite(fmm) else -1
        if key not in cache:
            cache[key] = LP.LensProjector(rx, fmm)
        return cache[key]
    if far and bg.any():                               # 0.8.4：背景（天空 / 环境）= 无穷远：沿主光线取很远的点 → 只随相机转动
        Pb, okb = _far_points(rx, cam_ob, cams[f0], s0x[bg], s0y[bg], us, projector(focus[f0]))
        mb = bg.copy(); mb[bg] = okb
        m = m | mb
        Pw = np.concatenate([Pw, Pb[okb]])
        s0 = (np.concatenate([s0[0], s0x[mb]]), np.concatenate([s0[1], s0y[mb]]))
        if ids is not None:
            ids = np.concatenate([ids, np.full(int(mb.sum()), -1, ids.dtype)])
        order = np.concatenate([np.flatnonzero((~bg).ravel()), np.flatnonzero(mb.ravel())])   # Pw 的行 → 像素
    else:
        order = np.flatnonzero(m.ravel())
    del yy, xx, px_full, py_up, s0x, s0y, P
    if project is None:
        def project(Q, fmm, s0):
            return projector(fmm).project(Q, s0)
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
        CH = 250000                                     # 分块：4K 时一次投影 800 万点的临时数组要好几 GB

        def one(a):
            b = min(a + CH, len(Pk))
            pc = (np.c_[Pk[a:b], np.ones(b - a)] @ Cinv.T)[:, :3]
            Q = np.column_stack([-pc[:, 0], pc[:, 1], -pc[:, 2]]) * us     # 相机本地 → 镜头坐标（mm）
            sx[a:b], sy[a:b], ok[a:b] = project(Q, focus[f], (s0[0][a:b], s0[1][a:b]))
        if len(Pk):
            one(0)                                      # 先在主线程建好投影表（缓存），再多线程（numpy 运算释放 GIL）
            from concurrent.futures import ThreadPoolExecutor
            import os as _os
            with ThreadPoolExecutor(max_workers=min(8, _os.cpu_count() or 4)) as ex:
                list(ex.map(one, range(CH, len(Pk), CH)))
        pxf = ((sx / SW + 0.5) * Wf - 0.5).astype(np.float32)
        pyf = ((-sy / SH + 0.5) * Hf - 0.5).astype(np.float32)
        del sx, sy, Pk
        proj[f] = (pxf, pyf, ok)
    out = np.zeros((H, W, 4), np.float32)
    (ax, ay, aok), (cx, cy, cok), (bx, by, bok) = proj[frames[0]], proj[frames[1]], proj[frames[2]]
    ok = aok & cok & bok
    vec = np.zeros((len(Pw), 4), np.float32)
    vec[:, 0], vec[:, 1] = ax - cx, ay - cy
    vec[:, 2], vec[:, 3] = cx - bx, cy - by
    vec[~ok] = 0.0
    out.reshape(-1, 4)[order] = vec
    if return_valid:
        valid = np.zeros(H * W, bool); valid[order] = ok
        return out, valid.reshape(H, W)
    return out


def _shift_stack(a, op):
    """3×3 邻域（边缘复制）的 op（np.maximum / np.minimum）。"""
    p = np.pad(a, 1, mode="edge")
    H, W = a.shape
    r = a.copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            r = op(r, p[dy:dy + H, dx:dx + W])
    return r


def vector_confidence(scene, cam_ob, parts, vl_name, vec, valid, W, H, region=None, glass=None, reflection_used=None):
    """BL_VectorConfidence（0.8.4，MessyRoom R2）：0–1，各项相乘（都在 1080 行等效像素上定阈值）：
      · valid：矢量算出来了，且上一帧位置在画面里；
      · occlusion（遮挡 / 去遮挡）：当前帧各像素按深度（Depth）溅射到上一帧 / 下一帧的位置；自己的位置在那一帧被
        明显更近的东西占着（近 3% 以上，且超过局部深度变化的 2 倍）→ 0（外扩 1 像素）。只看得到当前帧可见的遮挡物；
      · edge（深度不连续处的混合像素）：相邻像素矢量差 dv（视差）> 0.5 px 开始降，2 px 为 0（外扩 1 像素）；
      · emitter：亮光源（Combined 亮度 > 1.5）的边缘与外圈：外圈像素其实是光源的散焦 / 辉光，跟着光源动，
        矢量却是背景的 → 光源与该像素矢量差 > 0.25 px 开始降，1 px 为 0；外圈宽度 = 光源深度处的散焦直径 / 2 + 2 px；
      · specular：镜面（Denoising Specular Albedo 高、Roughness 低）：倒影不跟表面走 → 1 − mirror；
        没有去噪数据层时不评估（记在头里）；
      · speed：> 16 px/帧开始降，48 px/帧为 0（快速运动时帧内运动模糊、插值误差大）。
      · reflection_used（0.8.5）：反射占比 > 0.5 且有 BL_VectorReflection 的像素，下游该用倒影的矢量 → 不再按镜面项扣分；
      · glass（0.8.5）：矢量穿过透射面的像素（glass = (掩码, 反射占比)）：矢量是后面物体的，透射面上的反射
        不跟它走 → 1 − 反射占比（光泽层 / Combined，即 Fresnel × 反射来的亮度 / 总亮度）；这些像素不再按镜面项打分。
    → (H×W float32, 统计 dict)。"""
    from . import passes as PS, focus as FC
    Wf, Hf = (region[2], region[3]) if region else (W, H)
    x0, y0t = (region[0], region[1]) if region else (0, 0)
    k1080 = 1080.0 / Hf
    X, Y = vec[..., 0], vec[..., 1]
    yy, xx = np.indices((H, W), dtype=np.float32)
    xp, yp = xx + x0 + X, yy + y0t - Y
    c_valid = (valid & (xp >= 0) & (xp <= Wf - 1) & (yp >= 0) & (yp <= Hf - 1)).astype(np.float32)
    del xx, yy, xp, yp
    # occlusion：把当前帧每个像素按深度溅射到上一帧 / 下一帧的位置（z-buffer）；自己的位置在那一帧被更近的东西
    # 占着 → 那一帧看不到它（去遮挡 / 将被遮挡）
    dep = PS.find(parts, "Depth", vl_name) or PS.find(parts, "Depth")
    c_occ = np.ones((H, W), np.float32)
    n_occ = 0
    if dep is not None:
        Z = dep["channels"]["Z"].astype(np.float32)
        Z = np.where((Z > 0) & np.isfinite(Z) & (Z < 1e9), Z, np.inf)   # 天空 / 背景 = 无穷远
        Zf = np.where(np.isfinite(Z), Z, 0.0)
        dZ = _shift_stack(Zf, np.maximum) - _shift_stack(Zf, np.minimum)   # 局部深度变化（掠射的地面相邻像素就差很多）
        tol = np.where(np.isfinite(Z), Z - np.maximum(0.03 * Zf, 2.0 * dZ), np.inf)
        del Zf, dZ
        yy, xx = np.indices((H, W), dtype=np.float32)
        occ = np.zeros((H, W), bool)
        for qx, qy in ((xx + vec[..., 0], yy - vec[..., 1]), (xx - vec[..., 2], yy + vec[..., 3])):
            zb = np.full(H * W, np.inf, np.float32)
            fin = valid & np.isfinite(Z)
            for ox in (0, 1):
                for oy in (0, 1):
                    cx = np.floor(qx).astype(np.int32) + ox; cy = np.floor(qy).astype(np.int32) + oy
                    ok = fin & (cx >= 0) & (cx < W) & (cy >= 0) & (cy < H)
                    np.minimum.at(zb, (cy[ok] * W + cx[ok]), Z[ok])
            rx_ = np.clip(np.rint(qx), 0, W - 1).astype(np.int32); ry_ = np.clip(np.rint(qy), 0, H - 1).astype(np.int32)
            zq = zb[ry_ * W + rx_].reshape(H, W)
            occ |= valid & (zq < tol)
        n_occ = int(occ.sum())
        c_occ = 1.0 - _shift_stack(occ.astype(np.float32), np.maximum)       # 外扩 1 像素（边缘混合像素）
        del yy, xx, Z, occ, tol
    # edge：相邻矢量差（两组矢量取大）：边缘像素混着前景与背景，矢量只属于其中一个
    dv = np.zeros((H, W), np.float32)
    for a, b in ((0, 1), (2, 3)):
        va, vb = np.pad(vec[..., a], 1, mode="edge"), np.pad(vec[..., b], 1, mode="edge")
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                if dy == 1 and dx == 1:
                    continue
                d = np.hypot(va[dy:dy + H, dx:dx + W] - vec[..., a], vb[dy:dy + H, dx:dx + W] - vec[..., b])
                np.maximum(dv, d, out=dv)
    dv *= k1080
    c_edge = np.clip(1.0 - (_shift_stack(dv, np.maximum) - 0.5) / 1.5, 0.0, 1.0)
    # emitter：把光源像素的矢量往外推 r 圈，比较
    comb = PS.find(parts, "Combined", vl_name) or PS.find(parts, "Combined")
    c_emit = np.ones((H, W), np.float32)
    n_hot = 0
    if comb is not None:
        lum = (comb["channels"]["R"].astype(np.float32) + comb["channels"]["G"] + comb["channels"]["B"]) / 3.0
        hot = lum > 1.5
        n_hot = int(hot.sum())
        if n_hot:
            p = cam_ob.data.blenderlens
            blur = 0.0
            if dep is not None:
                d_mm = float(np.median(dep["channels"]["Z"][hot])) * mm_per_unit(scene)
                if np.isfinite(d_mm) and d_mm > 0:
                    blur = FC.DofModel(_rx_cached(), focus_mm(cam_ob), p.f_number, Wf, Hf).blur_px(d_mm)[0]
            r = int(min(24, math.ceil(0.5 * blur + 2.0 / k1080)))
            ev = np.where(hot[..., None], vec[..., :2], np.nan).astype(np.float32)
            reach = hot.copy()
            for _ in range(r):                             # 每圈：没被覆盖的像素取邻居里光源矢量的平均
                pe = np.pad(ev, ((1, 1), (1, 1), (0, 0)), constant_values=np.nan)
                acc = np.zeros((H, W, 2), np.float32); cnt = np.zeros((H, W), np.float32)
                for dy in (0, 1, 2):
                    for dx in (0, 1, 2):
                        q = pe[dy:dy + H, dx:dx + W]
                        okq = ~np.isnan(q[..., 0])
                        acc += np.where(okq[..., None], q, 0.0); cnt += okq
                new = ~reach & (cnt > 0)
                ev[new] = acc[new] / cnt[new][:, None]
                reach |= new
            ring = reach & ~hot
            dve = np.hypot(vec[..., 0] - ev[..., 0], vec[..., 1] - ev[..., 1]) * k1080
            c_emit = np.where(ring, np.clip(1.0 - (np.nan_to_num(dve) - 0.25) / 0.75, 0.0, 1.0), 1.0).astype(np.float32)
    # specular
    sa = PS.find(parts, "Denoising Specular Albedo", vl_name) or PS.find(parts, "Denoising Specular Albedo")
    ro = PS.find(parts, "Denoising Roughness", vl_name) or PS.find(parts, "Denoising Roughness")
    if sa is not None and ro is not None:
        spec = (sa["channels"]["R"].astype(np.float32) + sa["channels"]["G"] + sa["channels"]["B"]) / 3.0
        rough = ro["channels"]["X"].astype(np.float32)
        mirror = np.clip((spec - 0.08) / 0.12, 0, 1) * np.clip((0.35 - rough) / 0.2, 0, 1)
        c_spec = (1.0 - mirror).astype(np.float32)
    else:
        c_spec = None
    sp = np.hypot(X, Y) * k1080
    c_speed = np.clip((48.0 - sp) / 32.0, 0.0, 1.0).astype(np.float32)
    conf = c_valid * c_occ * c_edge * c_emit * c_speed
    if reflection_used is not None and c_spec is not None:   # 0.8.5：这些像素下游用 BL_VectorReflection（倒影的矢量）→ 不按镜面扣分
        c_spec = np.where(reflection_used, 1.0, c_spec).astype(np.float32)
    c_glass = None
    if glass is not None:
        gmask, share = glass
        if c_spec is not None:
            c_spec = np.where(gmask, 1.0, c_spec).astype(np.float32)
        c_glass = np.where(gmask, 1.0 - (share if share is not None else 0.04), 1.0).astype(np.float32)
        conf *= c_glass
    if c_spec is not None:
        conf *= c_spec
    terms = dict(valid=c_valid, occlusion=c_occ, edge=c_edge, emitter=c_emit, speed=c_speed)
    if c_spec is not None:
        terms["specular"] = c_spec
    if c_glass is not None:
        terms["glass"] = c_glass
    stats = dict(mean=round(float(conf.mean()), 4), below_half=round(float((conf < 0.5).mean()), 4),
                 terms={k: round(float(v.mean()), 4) for k, v in terms.items()},
                 specular_assessed=c_spec is not None, hot_pixels=n_hot, occluded_pixels=n_occ)
    return conf.astype(np.float32), stats


FAR_MM = 1.0e8                                          # 「无穷远」：100 km（相机平移几米的视差 < 0.01 px）

def _far_points(rx, cam_ob, mw, sx, sy, us, projector=None):
    """传感器坐标 (sx, sy)（mm）的主光线出镜头后，在世界里 FAR_MM 处的点 → (N×3 世界坐标, 有效)。
    0.8.4：用 LensProjector 的表（深度 z 处主光线横向位置 = A(s) + z·B(s)）插值，不再逐像素追迹（4K 天空 15 s → 不到 1 s）。"""
    from . import lenspack as LP
    P = projector or LP.LensProjector(rx, focus_mm(cam_ob))
    sx = np.asarray(sx, np.float64); sy = np.asarray(sy, np.float64)
    ok = (sx >= P.gx[0]) & (sx <= P.gx[-1]) & (sy >= P.gy[0]) & (sy <= P.gy[-1]) & bool(P.ok)
    out = np.zeros((len(sx), 3), np.float32)
    R, t = mw[:3, :3], mw[:3, 3]
    CH = 1 << 20
    for a in range(0, len(sx), CH):
        b = min(a + CH, len(sx))
        x = P._interp(P.Ax, sx[a:b], sy[a:b])[0] + FAR_MM * P._interp(P.Bx, sx[a:b], sy[a:b])[0]
        y = P._interp(P.Ay, sx[a:b], sy[a:b])[0] + FAR_MM * P._interp(P.By, sx[a:b], sy[a:b])[0]
        local = np.column_stack([-x, y, np.full(b - a, -FAR_MM)]) / us     # 镜头坐标（物方 +Z）→ 相机本地（视线 −Z）
        out[a:b] = (local @ R.T + t).astype(np.float32)
    return out, ok


def render_with_flare(scene, filepath, n_grid=260, blur_px=0.8):
    """渲染 + 正向炫光层合成（旧接口，等同 render_frame(flare=True)）。"""
    return render_frame(scene, filepath, flare=True, n_grid=n_grid, blur_px=blur_px)

def render_frame(scene, filepath, flare=True, n_grid=260, blur_px=0.8, flare_layer_path=None, passes=None, vectors=True,
                 exr_codec=None, half_color=None, exclude_collections=None, exclude_mode="hide", exclude_spp=None, exclude_bounces=None, exclude_clamp=None, exclude_knee=None):
    """渲染当前帧并做插件的后期：LATERAL_POST 的横向色差、正向炫光层（flare=True）。
    返回 (合成后的 Image 数据块（线性）, 炫光光源名单, 炫光层 H×W×3)。filepath 按扩展名写出（PNG/TIFF 16 位按场景色彩管理，EXR 线性）。
    flare_layer_path：另存炫光层为线性 EXR（单独调色 / 时域稳定用）。
    passes（0.8.3）："all" 或渲染层名列表 → filepath（.exr）写成多层 EXR，保留视图层打开的渲染层（见 render_frame_multilayer）。"""
    import os, tempfile
    from . import flare as FL
    if exclude_collections and passes is None:
        passes = ["Combined"]                             # 排除层只有多层输出能放
    if passes is not None:
        return render_frame_multilayer(scene, filepath, flare=flare, n_grid=n_grid, blur_px=blur_px, passes=passes, vectors=vectors,
                                       exr_codec=exr_codec, half_color=half_color, exclude_collections=exclude_collections,
                                       exclude_mode=exclude_mode, exclude_spp=exclude_spp, exclude_bounces=exclude_bounces, exclude_clamp=exclude_clamp, exclude_knee=exclude_knee)
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
