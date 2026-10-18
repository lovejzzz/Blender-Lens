# BlenderLens：由公开专利处方驱动的 2× 前组变形镜头（Cycles OSL 自定义相机 + 真实镜片几何）
import math
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
        name="Focus Distance", description="从像面量起的对焦距离；0 = 无穷远",
        default=0.0, min=0.0, soft_max=20000.0, subtype='DISTANCE', unit='LENGTH', update=_on_change)
    f_number: FloatProperty(name="f-Number", default=2.0, min=2.0, max=22.0, update=_on_change)
    blades: IntProperty(name="Blades", description="光圈叶片数，0 = 圆形", default=0, min=0, max=16, update=_on_change)
    blade_rotation: FloatProperty(name="Blade Rotation", default=0.0, update=_on_change)
    mode: EnumProperty(
        name="Mode", default='ANALYTIC', update=_on_change,
        items=[('ANALYTIC', "Analytic", "OSL 内按处方逐面追迹：快、干净、带色散"),
               ('GEOMETRY', "Geometry", "Cycles 穿过真实镜片网格：鬼影与一字拉丝炫光")])
    dispersion: BoolProperty(name="Dispersion", default=True, update=_on_change)
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


class BL_OT_build(bpy.types.Operator):
    """由处方 JSON 搭建整支变形镜头与 OSL 相机"""
    bl_idname = "blenderlens.build"
    bl_label = "Build Anamorphic Lens"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(name="Prescription", subtype='FILE_PATH',
                             description="处方 JSON；留空 = 插件自带的 US2944464 + US4234242")
    coated: BoolProperty(name="MgF₂ Coating", default=True)
    film_nm: FloatProperty(name="Coating Thickness (nm)", default=100.0, min=0.0, max=400.0)
    set_render: BoolProperty(name="Set Render Settings", default=True,
                             description="Cycles CPU、解挤压后的输出分辨率、设为场景相机")
    height: IntProperty(name="Output Height", default=1080, min=64)

    def execute(self, context):
        try:
            rx = optics.load(bpy.path.abspath(self.filepath)) if self.filepath else optics.load()
            cam_ob = rig.build(context, rx, coated=self.coated, film_nm=self.film_nm)
            cam_ob.data.blenderlens.coated = self.coated
            cam_ob.data.blenderlens.coating_nm = self.film_nm
        except Exception as e:                        # noqa: BLE001 — 报给用户
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if self.set_render:
            sc = context.scene
            sc.camera = cam_ob
            rig.render_preset(sc)
            sc.render.resolution_x, sc.render.resolution_y = rig.desqueezed_resolution(rig.read_prescription(), self.height)
            sc.render.pixel_aspect_x = sc.render.pixel_aspect_y = 1.0
        self.report({'INFO'}, f"已搭建 {cam_ob.name}；无穷远压缩比 {rig.squeeze_info(cam_ob):.3f}")
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
    col = layout.column(align=True)
    col.prop(p, "mode")
    col.prop(p, "focus_distance")
    col.prop(p, "f_number")
    row = col.row(align=True)
    row.prop(p, "blades")
    row.prop(p, "blade_rotation", text="Rot")
    col.prop(p, "dispersion")
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
    try:
        layout.label(text=f"Squeeze at this focus: {rig.squeeze_info(cam_ob):.3f}×")
    except Exception:                                   # noqa: BLE001
        pass
    if p.mode == 'GEOMETRY':
        layout.label(text="Geometry：需要 Cycles 深反弹，噪点较多", icon='INFO')


class BL_PT_sidebar(bpy.types.Panel):
    bl_label = "BlenderLens"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BlenderLens"

    def draw(self, context):
        lay = self.layout
        lay.operator(BL_OT_build.bl_idname, icon='OUTLINER_OB_CAMERA')
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
def _frame_change(scene, depsgraph=None):
    """对焦/光圈做了关键帧时，属性 update 不会触发 → 每帧同步一次。"""
    for ob in scene.objects:
        if ob.type == 'CAMERA' and ob.data.animation_data and any(c.name.startswith("BL_Rig") for c in ob.children):
            rig.apply_settings(ob)


classes = (BLCameraSettings, BL_OT_build, BL_OT_rebuild_apertures, BL_OT_mark_flare, BL_OT_render_flare,
           BL_OT_export, BL_PT_sidebar, BL_PT_camera)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Camera.blenderlens = PointerProperty(type=BLCameraSettings)
    bpy.app.handlers.frame_change_post.append(_frame_change)

def unregister():
    if _frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_change)
    del bpy.types.Camera.blenderlens
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
