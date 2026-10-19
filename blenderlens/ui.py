# BlenderLens 界面：属性、操作符、面板、帧同步（需要 bpy）
import math, os
import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import optics, rig


def _owner_object(cam_data):
    for ob in bpy.data.objects:
        if ob.data == cam_data:
            return ob
    return None

def _on_change(self, context):
    ob = _owner_object(self.id_data)
    if ob is not None and "BL_Rig" in {c.name.split(".")[0] for c in ob.children}:
        rig.apply_settings(ob)


class BLCameraSettings(bpy.types.PropertyGroup):
    focus_distance: FloatProperty(
        name="Focus Distance", description="从像面量起的对焦距离（场景单位，按场景长度单位显示）；0 = 无穷远",
        default=0.0, min=0.0, subtype='DISTANCE', unit='LENGTH', update=_on_change)
    f_number: FloatProperty(name="f-Number", default=2.0, min=2.0, max=22.0, update=_on_change)
    blades: IntProperty(name="Blades", description="光圈叶片数，0 = 圆形", default=0, min=0, max=16, update=_on_change)
    blade_rotation: FloatProperty(name="Blade Rotation", default=0.0, update=_on_change)
    mode: EnumProperty(
        name="Mode", default='ANALYTIC', update=_on_change,
        items=[('ANALYTIC', "Analytic", "OSL 内按处方逐面追迹：快、干净、带色散"),
               ('GEOMETRY', "Geometry", "Cycles 穿过真实镜片网格：鬼影与一字拉丝炫光")])
    dispersion: BoolProperty(name="Dispersion", default=True, update=_on_change)
    dispersion_mode: EnumProperty(
        name="Dispersion Mode", default='FULL', update=_on_change,
        items=[('FULL', "Full (per-sample λ)", "每个采样按波长追迹：横向 + 纵向色差（焦外紫 / 绿边），色噪最多"),
               ('LATERAL_POST', "Lateral in Post", "渲染时关色散（无色噪），横向色差（边缘彩边）由处方算出的每波长映射在后期精确加上；"
                                "只在 Render with Flare / api.render_still / api.render_frames 输出里生效，没有纵向色差"),
               ('OFF', "Off", "无色散")],
        description="色散的计算方式（Dispersion 勾选时生效）")
    exposure_compensate: BoolProperty(name="Compensate Exposure", default=True, update=_on_change)
    ghosts: BoolProperty(name="Ghosts / Flare", default=False, update=_on_change,
                         description="Analytic 模式下的序列鬼影追迹（两次反射）：一字拉丝与鬼影；需要更多采样")
    ghost_gain: FloatProperty(name="Ghost Gain", default=1.0, min=0.0, soft_max=50.0, update=_on_change,
                              description="1 = 物理正确；调大只为看清")
    coated: BoolProperty(name="MgF₂ Coating", default=True, update=_on_change)
    coating_nm: FloatProperty(name="Coating (nm)", default=100.0, min=0.0, max=400.0, update=_on_change,
                              description="≈100 nm 为 550 nm 四分之一波（标准）；125–140 nm 残余反射偏蓝（风格化）")
    flare_color: EnumProperty(
        name="Flare Color", default='PHYSICAL',
        items=[('PHYSICAL', "Physical (Coating)", "由镀膜膜厚算出的颜色：100 nm 蓝紫，125–140 nm 蓝，无镀膜中性"),
               ('SOURCE', "Light Color", "拉丝与鬼影取灯光本身的颜色"),
               ('AMBER', "Amber", "琥珀色"),
               ('CUSTOM', "Custom", "自定义颜色")],
        description="Render with Flare 的炫光颜色。形状与亮度分布仍由物理追迹决定，只重新着色（保持亮度）")
    flare_tint: FloatVectorProperty(name="Flare Tint", subtype='COLOR', size=3, min=0.0, max=1.0,
                                    default=(0.25, 0.55, 1.0))
    flare_gain: FloatProperty(name="Flare Intensity", default=1.0, min=0.0, soft_max=10.0,
                              description="炫光强度倍数；1 = 物理正确")
    starburst: BoolProperty(name="Diffraction Starburst", default=True,
                            description="光圈多边形的衍射星芒（夫琅禾费，按 R/G/B 与两向数值孔径计算）；只在 Render with Flare / API 输出里加，"
                                        "叶片数 0 时只有 Airy 光环")
    starburst_gain: FloatProperty(name="Starburst Intensity", default=1.0, min=0.0, soft_max=10.0,
                                  description="星芒强度倍数；1 = 物理正确")
    veiling_glare: FloatProperty(name="Veiling Glare", default=0.0, min=0.0, max=0.2, precision=3,
                                 description="杂光 / 雾化比例（保能量的长尾散射）；按预设观感取 ISO 9358 杂光指数的典型值，0 = 关。"
                                             "只在 Render with Flare / API 输出里加")


def _preset_items(self, context):
    global _PRESET_CACHE
    if not _PRESET_CACHE:
        _PRESET_CACHE = [(fn, name, tag) for fn, name, tag in optics.list_presets()]
    return _PRESET_CACHE

_PRESET_CACHE = []


class BL_OT_build(bpy.types.Operator):
    """由处方 JSON 搭建整支变形镜头与 OSL 相机"""
    bl_idname = "blenderlens.build"
    bl_label = "Build Anamorphic Lens"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Lens", items=_preset_items,
                         description="插件自带的处方预设（prescriptions/ 目录下的 JSON）")
    filepath: StringProperty(name="Custom JSON", subtype='FILE_PATH',
                             description="自定义处方 JSON；填写时优先于上面的预设")
    focal_mm: FloatProperty(name="Vertical Focal Length (mm)", default=0.0, min=0.0, soft_max=300.0,
                            description="把整支镜头按比例缩放到这个垂直焦距（变形镜头按垂直方向命名焦距）；0 = 预设原生焦距。"
                                        "估算口径的组会按新视场重算口径")
    coated: BoolProperty(name="MgF₂ Coating", default=True,
                         description="Geometry 模式镜片材质的镀膜；之后由相机设置（处方 look 的初值）统一控制")
    film_nm: FloatProperty(name="Coating Thickness (nm)", default=100.0, min=0.0, max=400.0)
    gate: EnumProperty(name="Gate", default='FULL',
                       items=[(k, {"FULL": "Full Aperture", "2.39": "2.39 Extraction", "16:9": "16:9 Extraction",
                                   "16:9_NATIVE": "16:9 Native (Full Circle)", "CUSTOM": "Custom"}[k], v)
                              for k, v in optics.GATES.items()],
                       description="画幅（光窗）：处方原光窗、2.39 / 16:9 抽取（裁切）、原生 16:9 满像圈（不裁切）或自定义；镜头本身不变")
    gate_width: FloatProperty(name="Gate Width (mm)", default=22.0, min=1.0, max=80.0,
                              description="自定义光窗宽（压缩前，mm）")
    gate_height: FloatProperty(name="Gate Height (mm)", default=18.6, min=1.0, max=60.0,
                               description="自定义光窗高（mm）")
    units: EnumProperty(name="Scene Units", default='AUTO',
                        items=[('AUTO', "Auto", "已是毫米或场景里已有内容 → 保留场景单位；空场景 → 改成毫米"),
                               ('SCENE', "Keep Scene Units", "保留场景单位（米制生产场景）；镜头按比例换算"),
                               ('MM', "Millimetres", "把场景改成毫米（1 单位 = 1 mm）")],
                        description="镜头内部恒为毫米；场景单位只影响换算")
    set_render: BoolProperty(name="Set Render Settings", default=True,
                             description="Cycles CPU、解挤压后的输出分辨率、设为场景相机")
    height: IntProperty(name="Output Height", default=1080, min=64)

    def execute(self, context):
        try:
            path = bpy.path.abspath(self.filepath) if self.filepath else os.path.join(optics.PRESCRIPTION_DIR, self.preset)
            rx = optics.load(path)
            if self.focal_mm > 0:
                rx = optics.rescale_to_vertical_efl(rx, self.focal_mm)
            rx = optics.apply_gate(rx, self.gate, self.gate_width, self.gate_height)
            cam_ob = rig.build(context, rx, coated=self.coated, film_nm=self.film_nm, units=self.units)
        except Exception as e:                        # noqa: BLE001 — 报给用户
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if self.gate != 'FULL':
            corner = optics.gate_corner_transmission(rig.read_prescription(), cam_ob.data.blenderlens.f_number)
            cam_ob["bl_gate_corner"] = corner
            if corner < 0.3:
                self.report({'WARNING'}, f"画幅超出像圈：光窗四角光瞳通过率只有 {corner:.2f}，角落会发黑")
        if self.set_render:
            sc = context.scene
            sc.camera = cam_ob
            rig.render_preset(sc)
            sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), self.height)
            sc.render.pixel_aspect_x = sc.render.pixel_aspect_y = 1.0
        unit_note = "毫米场景" if cam_ob.get("bl_units") == 'MM' else f"保留场景单位（1 单位 = {rig.mm_per_unit(context.scene):g} mm）"
        self.report({'INFO'}, f"已搭建 {cam_ob.get('bl_preset', cam_ob.name)}；无穷远压缩比 {rig.squeeze_info(cam_ob):.3f}；{unit_note}")
        return {'FINISHED'}


class BL_OT_rebuild_apertures(bpy.types.Operator):
    """重新追迹估算各面口径与光阑（改了处方 R/d/视场后使用），然后重建镜头"""
    bl_idname = "blenderlens.rebuild_apertures"
    bl_label = "Recompute Apertures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rx = rig.read_prescription()
        for g in rx["groups"]:
            for sf in g["surfaces"]:
                sf.pop("aperture", None)
        cam_ob = rig.build(context, rx)
        context.scene.camera = cam_ob
        clash = [r for r in optics.edge_report(rig.read_prescription()) if r["min_gap"] < 0]
        self.report({'WARNING'} if clash else {'INFO'}, f"口径已更新；相撞 {len(clash)} 处")
        return {'FINISHED'}


class BL_OT_mark_flare(bpy.types.Operator):
    """把选中的发光物体标记为炫光光源（Render with Flare 只追迹这些；都没标记时自动选强光源）"""
    bl_idname = "blenderlens.mark_flare_source"
    bl_label = "Mark Flare Source"
    bl_options = {'REGISTER', 'UNDO'}
    clear: BoolProperty(name="Clear", default=False)

    def execute(self, context):
        for ob in context.selected_objects:
            if self.clear:
                ob.pop(rig.FLARE_PROP, None)
            else:
                ob[rig.FLARE_PROP] = 1
        return {'FINISHED'}


class BL_OT_render_flare(bpy.types.Operator):
    """渲染当前帧，并叠加正向追迹的炫光层（鬼影、一字拉丝）：确定性、无噪点"""
    bl_idname = "blenderlens.render_flare"
    bl_label = "Render with Flare"
    filepath: StringProperty(name="Output", subtype='FILE_PATH', default="//flare_render.png")

    def execute(self, context):
        sc = context.scene
        if not (sc.camera and any(c.name.startswith("BL_Rig") for c in sc.camera.children)):
            self.report({'ERROR'}, "场景相机不是 BlenderLens 相机")
            return {'CANCELLED'}
        out, used, _ = rig.render_with_flare(sc, self.filepath)
        for area in context.screen.areas if context.screen else []:
            if area.type == 'IMAGE_EDITOR':
                area.spaces.active.image = out
        self.report({'INFO'}, f"炫光光源：{', '.join(used) or '无'}；已写出 {self.filepath}")
        return {'FINISHED'}


class BL_OT_gpu_enable(bpy.types.Operator):
    """启用 GPU 路径（Metal 等）：拷贝并修补 Cycles 内核源码到本扩展的用户目录，不改 Blender 本身。
    须在本次启动后第一次 GPU 渲染之前启用；首次内核编译约 2 分钟"""
    bl_idname = "blenderlens.gpu_enable"
    bl_label = "Enable BlenderLens GPU Path"

    def execute(self, context):
        from . import gpukernel
        ok = gpukernel.enable()
        cam = context.scene.camera
        if ok and rig.is_blenderlens_camera(cam):
            rig.apply_settings(cam)
        self.report({'INFO'} if ok else {'ERROR'}, gpukernel.status()["reason"])
        return {'FINISHED'} if ok else {'CANCELLED'}


class BL_OT_gpu_remove(bpy.types.Operator):
    """停用 GPU 路径并删除拷贝的内核源码（恢复原状）"""
    bl_idname = "blenderlens.gpu_remove"
    bl_label = "Remove BlenderLens GPU Kernel Copy"

    def execute(self, context):
        from . import gpukernel
        gpukernel.remove_cache()
        self.report({'INFO'}, "已删除 GPU 内核拷贝；重启 Blender 后完全恢复原状")
        return {'FINISHED'}


class BL_OT_export(bpy.types.Operator):
    """把 .blend 里的处方导出为 JSON（之后改 JSON、再用 Build 读回）"""
    bl_idname = "blenderlens.export_prescription"
    bl_label = "Export Prescription"
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = "prescription.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        optics.save(rig.read_prescription(), bpy.path.abspath(self.filepath))
        return {'FINISHED'}


def _draw_settings(layout, cam_ob):
    p = cam_ob.data.blenderlens
    if cam_ob.get("bl_preset"):
        layout.label(text=cam_ob["bl_preset"], icon='CAMERA_DATA')
    col = layout.column(align=True)
    col.prop(p, "mode")
    col.prop(p, "focus_distance")
    col.prop(p, "f_number")
    row = col.row(align=True)
    row.prop(p, "blades")
    row.prop(p, "blade_rotation", text="Rot")
    col.prop(p, "dispersion")
    sub = col.column(align=True)
    sub.active = p.dispersion
    sub.prop(p, "dispersion_mode", text="")
    if p.dispersion and p.dispersion_mode == 'LATERAL_POST':
        sub.label(text="横向色差只在 Render with Flare / API 输出里加", icon='INFO')
    col.prop(p, "exposure_compensate")
    col = layout.column(align=True)
    col.prop(p, "ghosts")
    sub = col.column(align=True)
    sub.active = p.ghosts
    sub.prop(p, "ghost_gain")
    col.prop(p, "coated")
    sub = col.column(align=True)
    sub.active = p.coated
    sub.prop(p, "coating_nm")
    col = layout.column(align=True)
    col.label(text="Render with Flare")
    col.prop(p, "flare_color", text="Color")
    if p.flare_color == 'CUSTOM':
        col.prop(p, "flare_tint", text="")
    col.prop(p, "flare_gain", text="Intensity")
    row = col.row(align=True)
    row.prop(p, "starburst", text="Starburst")
    sub = row.row(align=True); sub.active = p.starburst
    sub.prop(p, "starburst_gain", text="")
    col.prop(p, "veiling_glare")
    if cam_ob.get("bl_min_focus_mm"):
        layout.label(text=f"Min focus: {cam_ob['bl_min_focus_mm'] / 1000:.2f} m")
    try:
        layout.label(text=f"Squeeze at this focus: {rig.squeeze_info(cam_ob):.3f}×")
    except Exception:                                   # noqa: BLE001
        pass
    if p.mode == 'GEOMETRY':
        layout.label(text="Geometry：需要 Cycles 深反弹，噪点较多", icon='INFO')
    sc = bpy.context.scene
    from . import gpukernel
    box = layout.box()
    st = gpukernel.status()
    box.label(text=("GPU 路径：已启用" if st["active"] else "GPU 路径：未启用"), icon='CHECKMARK' if st["active"] else 'INFO')
    row = box.row(align=True)
    row.operator("blenderlens.gpu_enable", text="Enable GPU Path")
    row.operator("blenderlens.gpu_remove", text="Remove", icon='TRASH')
    why = rig.gpu_block_reason(sc)
    if why:
        box.label(text=why[:90], icon='ERROR')
        box.label(text="渲染时会自动改用 CPU")


class BL_PT_sidebar(bpy.types.Panel):
    bl_label = "BlenderLens"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BlenderLens"

    def draw(self, context):
        lay = self.layout
        lay.operator_menu_enum(BL_OT_build.bl_idname, "preset", text="Build Lens", icon='OUTLINER_OB_CAMERA')
        cam_ob = context.scene.camera
        if cam_ob and cam_ob.type == 'CAMERA' and any(c.name.startswith("BL_Rig") for c in cam_ob.children):
            _draw_settings(lay, cam_ob)
            col = lay.column(align=True)
            col.operator(BL_OT_render_flare.bl_idname, icon='LIGHT_SUN')
            col.operator(BL_OT_mark_flare.bl_idname, icon='OUTLINER_OB_LIGHT')
            row = lay.row(align=True)
            row.operator(BL_OT_rebuild_apertures.bl_idname)
            row.operator(BL_OT_export.bl_idname, text="Export JSON")


class BL_PT_camera(bpy.types.Panel):
    bl_label = "BlenderLens"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob and ob.type == 'CAMERA' and any(c.name.startswith("BL_Rig") for c in ob.children)

    def draw(self, context):
        _draw_settings(self.layout, context.object)


@persistent
def _render_guard(scene, depsgraph=None):
    """Cycles 的 OSL 自定义相机只在 CPU 与 OptiX 上运行；Metal / HIP / oneAPI / 非 OptiX CUDA 会静默出黑图（实测）→ 改用 CPU。"""
    why = rig.gpu_block_reason(scene)
    if why:
        scene.cycles.device = 'CPU'
        print("BlenderLens: " + why + "；本次渲染已改用 CPU")


@persistent
def _frame_change(scene, depsgraph=None):
    """对焦/光圈做了关键帧时，属性 update 不会触发 → 每帧同步一次。
    运动模糊导出时 Cycles 会在快门内的子帧求值；这里跳过子帧（镜头参数取整帧值），否则写入会打断相机运动的导出。"""
    if scene.frame_subframe != 0.0:
        return
    for ob in scene.objects:
        if ob.type == 'CAMERA' and ob.data.animation_data and any(c.name.startswith("BL_Rig") for c in ob.children):
            rig.apply_settings(ob)


classes = (BLCameraSettings, BL_OT_build, BL_OT_rebuild_apertures, BL_OT_mark_flare, BL_OT_render_flare,
           BL_OT_gpu_enable, BL_OT_gpu_remove, BL_OT_export, BL_PT_sidebar, BL_PT_camera)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Camera.blenderlens = PointerProperty(type=BLCameraSettings)
    bpy.app.handlers.frame_change_post.append(_frame_change)
    bpy.app.handlers.render_init.append(_render_guard)

def unregister():
    if _frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_change)
    if _render_guard in bpy.app.handlers.render_init:
        bpy.app.handlers.render_init.remove(_render_guard)
    del bpy.types.Camera.blenderlens
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
