#!/usr/bin/env python3
# BlenderLens 命令行（0.8.1，给 AI 与脚本用）：所有命令的输出都是一个 JSON 对象（--pretty 缩进）。
#
#   python3 tools/blcli.py presets                       # 预设清单（纯 Python，不启动 Blender，< 1 秒）
#   python3 tools/blcli.py info neil_2x_50 --focus-m 2   # 某个预设的光学数字：焦距、压缩比、焦外、呼吸、发腮
#   python3 tools/blcli.py describe                      # 启动 Blender：完整的设置模式与 API（bl.describe()）
#   python3 tools/blcli.py render --preset c_series_style --scene demo --focus-m 3 --stop 2.8 \
#           --set coating_type=BROADBAND --quality draft --gpu --out renders/cli/c.png
#   python3 tools/blcli.py render --preset c_series_style --scene demo --variants '[{"f_number":2.3},{"f_number":8}]' --out renders/cli/ab.png
#   python3 tools/blcli.py render --blend my_shot.blend --quality standard --out renders/cli/shot.png
#   python3 tools/blcli.py diagnose --blend my_shot.blend
#   python3 tools/blcli.py focus --blend film.blend --frame 120 --target Room --near 19 --far 38      # 0.8.3 对焦助手
#   python3 tools/blcli.py temporal --blend film.blend --frame 120 --n 8 --gpu --variants FILE.json     # 0.8.3 闪烁检查
#   python3 tools/blcli.py temporal --blend film.blend --frame 40 --n 16 --mode moving --gpu             # 0.8.4 运镜中的闪烁
#   python3 tools/blcli.py shot --blend film.blend --source CamHeli --frames 40-56 --preset c_series_style \
#           --passes temporal --spp 256 --gpu --out renders/shots/heli                                     # 0.8.4 渲一个镜头
#   python3 tools/blcli.py denoise --in renders/shots/heli --frames 40-56 --out renders/shots/heli_stable # 0.8.4 时间降噪
#   python3 tools/blcli.py sequence --blend day5.blend --shots shots.json --preset c_series_style --passes temporal \
#           --exclude FX_rain --gpu --out renders/day5                                                        # 0.8.5 多镜头（--markers：按时间线标记）
#   python3 tools/blcli.py test quick                    # = python3 tests/run.py quick
#
# Blender 路径：环境变量 BLENDER，默认 /Applications/Blender.app/Contents/MacOS/Blender。
# 用的是这个源码树里的 blenderlens（不是已安装的扩展），改了代码马上生效。
import argparse, json, math, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
MARK = "BLJSON "


def _preset_file(name):
    import blenderlens.optics as O
    fn = name if name.endswith(".json") else name + ".json"
    if os.path.exists(fn):
        return os.path.abspath(fn)
    p = os.path.join(O.PRESCRIPTION_DIR, fn)
    if not os.path.exists(p):
        raise SystemExit(json.dumps({"error": f"没有预设 {name}", "presets": [f for f, _, _ in O.list_presets()]}, ensure_ascii=False))
    return p


def cmd_presets(a):
    import blenderlens.optics as O
    return {"presets": [dict(file=f, name=n, tagline=t) for f, n, t in O.list_presets()]}


def cmd_info(a):
    """纯 Python 光学摘要（与 Blender 里的 lens_info 同一套计算）。"""
    import copy
    import blenderlens.optics as O
    rx = O.prepare(O.load(_preset_file(a.preset)), getattr(a, "format", None) or "S35", a.focal_mm or 0.0, a.gate or "FULL")
    S = O.layout(rx)
    mfd = rx.get("focus", {}).get("min_distance_mm") or rx.get("look", {}).get("min_focus_mm")
    out = dict(preset=rx["meta"].get("short_name", rx["meta"]["name"]), file=os.path.basename(_preset_file(a.preset)),
               efl_x_mm=round(O.paraxial_infinity(S, "Rx")[0], 4), efl_y_mm=round(O.paraxial_infinity(S, "Ry")[0], 4),
               bfl_y_mm=round(O.paraxial_infinity(S, "Ry")[1], 4), f_number_max=O.nominal_f_number(rx),
               sensor_mm=[rx["sensor"]["width"], rx["sensor"]["height"]], desqueeze=O.desqueeze(rx), min_focus_mm=mfd,
               look=rx.get("look", {}), sources=[g["source"]["patent"] for g in rx["groups"] if "source" in g])
    levels = [None]
    if O.compensator(rx):
        levels = [rx["focus"]["compensator"].get("level", 0.0), 0.0, 1.0]
        levels = list(dict.fromkeys(levels))
    dists = [a.focus_m * 1000.0] if a.focus_m else [math.inf] + ([mfd, 2 * mfd, 5 * mfd] if mfd else [])
    table = []
    for lev in levels:
        r = copy.deepcopy(rx)
        if lev is not None:
            r["focus"]["compensator"]["level"] = lev
        for dd in dists:
            row = dict(focus_mm=None if math.isinf(dd) else dd, squeeze=round(O.squeeze_at(r, dd), 4))
            if lev is not None:
                row["mumps_correction"] = lev
            if not math.isinf(dd):
                d1, d2 = O.solve_focus(r, dd)
                row["focus_travel_mm"] = [round(d1, 4), round(d2, 4)]
                row["bokeh_aspect_background"] = round(O.bokeh_aspect(r, dd, 1e7)["aspect_desqueezed"], 3)
            table.append(row)
    out["focus_table"] = table
    if a.coverage:                                          # 0.8.6：覆盖与畸变（与渲染同一估计器）
        import blenderlens.coverage as CV
        out["coverage"] = CV.coverage_report(rx, (a.focus_m or 7.0) * 1000.0, a.stop or None)
    return out


def _blender(script, timeout=3600):
    """在 Blender 里跑一段脚本（源码树的 blenderlens 已注册），取回 MARK 行的 JSON。"""
    prelude = f"import sys, json\nsys.path.insert(0, {ROOT!r})\nimport blenderlens\nblenderlens.register()\nimport blenderlens.api as bl\nfrom blenderlens.agent import dump\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(prelude + script)
        path = f.name
    try:
        t = time.time()
        pr = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", path], capture_output=True, text=True, timeout=timeout)
        text = pr.stdout + pr.stderr
        for line in text.splitlines():
            if line.startswith(MARK):
                res = json.loads(line[len(MARK):])
                if isinstance(res, dict):
                    res.setdefault("wall_seconds", round(time.time() - t, 1))
                return res
        return {"error": "Blender 没有返回结果", "returncode": pr.returncode, "tail": text[-3000:]}
    finally:
        os.unlink(path)


def cmd_describe(a):
    return _blender(f"print({MARK!r} + dump(bl.describe()))\n")


DEMO_SCENE = r'''
# 内置演示场景（毫米）：棋盘地面 + 远近几个发光小球（焦外 / 星芒）+ 一个强光源（炫光）+ 暗天空
import bpy, math
from blenderlens import rig
sc = bpy.context.scene
for o in list(sc.objects):
    bpy.data.objects.remove(o)
def emit(name, strength, color=(1, 1, 1)):
    m = bpy.data.materials.new(name)
    if m.node_tree is None: m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial"); e = nt.nodes.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*color, 1); e.inputs[1].default_value = strength; nt.links.new(e.outputs[0], o.inputs[0])
    return m
ground = bpy.data.materials.new("ground")
if ground.node_tree is None: ground.use_nodes = True
nt = ground.node_tree; bsdf = nt.nodes.get("Principled BSDF")
chk = nt.nodes.new("ShaderNodeTexChecker"); chk.inputs["Scale"].default_value = 40.0
chk.inputs["Color1"].default_value = (0.8, 0.8, 0.8, 1); chk.inputs["Color2"].default_value = (0.08, 0.08, 0.08, 1)
nt.links.new(chk.outputs[0], bsdf.inputs["Base Color"])
bpy.ops.mesh.primitive_plane_add(size=60000, location=(0, 20000, 0)); bpy.context.object.data.materials.append(ground)
bpy.ops.object.light_add(type='SUN', rotation=(math.radians(55), 0, math.radians(150))); bpy.context.object.data.energy = 3.0
# 相机在 (0, 0, 1500) 看 +Y；发光小球：近 / 中 / 远（焦外大小不同），一个在画面边缘（彗差、猫眼）
for i, (x, y, z) in enumerate([(-700, 2500, 1300), (500, 6000, 1700), (-1500, 12000, 2200), (1300, 3500, 1200), (200, 30000, 1500)]):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12 + 0.0015 * y, location=(x, y, z))
    bpy.context.object.data.materials.append(emit(f"pt{i}", 300.0, [(1, .6, .3), (.4, .7, 1), (1, 1, 1), (1, .3, .3), (.8, 1, .6)][i]))
bpy.ops.mesh.primitive_cube_add(size=600, location=(-400, 4000, 300)); bpy.context.object.data.materials.append(ground)
bpy.ops.mesh.primitive_uv_sphere_add(radius=40, location=(2300, 14000, 2600)); sun = bpy.context.object
sun.data.materials.append(emit("lamp", 3.0e4, (1, .95, .85))); sun[rig.FLARE_PROP] = 1
w = bpy.data.worlds.new("W"); sc.world = w
if w.node_tree is None: w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.025, 0.04, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
'''


def cmd_render(a):
    s = []
    if a.gpu:
        s.append("print('gpu', bl.enable_gpu(use_gpu=True))")
    if a.blend:
        s.append(f"bpy.ops.wm.open_mainfile(filepath={os.path.abspath(a.blend)!r})")
        s.append("cam = bl.camera()")
        if a.preset:
            s.append(f"cam = bl.build({_preset_file(a.preset)!r}, units='SCENE', focal_mm={a.focal_mm or 0.0}, gate={a.gate or 'FULL'!r}, format={a.format or 'S35'!r})")
    else:
        s.append(DEMO_SCENE)
        s.append(f"cam = bl.build({_preset_file(a.preset or 'c_series_style')!r}, units='MM', focal_mm={a.focal_mm or 0.0}, gate={a.gate or 'FULL'!r}, format={a.format or 'S35'!r})")
        s.append("import math; cam.location = (0, 0, 1500); cam.rotation_euler = (math.pi / 2, 0, 0)")
    if a.gpu:
        s.append("print('gpu2', bl.enable_gpu(use_gpu=True))")
    settings = {}
    if a.focus_m is not None:
        settings["focus_m"] = a.focus_m
    if a.stop is not None:
        settings["f_number"] = a.stop
    for kv in a.set or []:
        k, v = kv.split("=", 1)
        try:
            settings[k] = json.loads(v)
        except json.JSONDecodeError:
            settings[k] = v
    s.append(f"changed = bl.configure(cam, **{settings!r})")
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    common = f"quality={a.quality!r}, samples={a.samples!r}, height={a.height!r}, flare={not a.no_flare!r}"
    if a.variants:
        variants = json.loads(open(a.variants).read() if os.path.exists(a.variants) else a.variants)
        s.append(f"res = bl.contact_sheet({out!r}, {variants!r}, {common})")
    else:
        s.append(f"res = bl.preview({out!r}, {common})")
    s.append("res['configured'] = changed")
    s.append("res['diagnose'] = bl.diagnose()")
    s.append(f"print({MARK!r} + dump(res))")
    return _blender("import bpy\n" + "\n".join(s) + "\n")


def cmd_focus(a):
    """对焦助手（0.8.3）：在 .blend 的某一帧求目标物距、景深范围，可选求保住两个距离的光圈。"""
    tgt = a.target
    if tgt and "," in tgt:
        tgt = tuple(float(v) for v in tgt.split(","))
    s = [f"import bpy\nbpy.ops.wm.open_mainfile(filepath={os.path.abspath(a.blend)!r})",
         "sc = bpy.context.scene; cam = bl.camera()"]
    if a.frame is not None:
        s.append(f"sc.frame_set({a.frame})")
    s.append("res = {}")
    if tgt:
        s.append(f"res['target_m'] = bl.focus_on(cam, {tgt!r}, apply={bool(a.apply)!r})")
    s.append(f"res['dof'] = bl.dof_range(cam, coc_px={a.coc})")
    if a.near and a.far:
        s.append(f"res['stop_for'] = bl.stop_for(cam, {a.near}, {a.far}, coc_px={a.coc})")
    s.append(f"print({MARK!r} + dump(res))")
    return _blender("\n".join(s) + "\n")


def cmd_temporal(a):
    """闪烁检查（0.8.3）：相机锁在 --frame，时间走 --n 帧。"""
    raw = a.variants or "[]"
    variants = json.loads(open(raw).read() if os.path.exists(raw) else raw)
    s = [f"import bpy\nbpy.ops.wm.open_mainfile(filepath={os.path.abspath(a.blend)!r})"]
    if a.gpu:
        s.append("bl.enable_gpu(use_gpu=True)")
    s.append(f"res = bl.temporal_check(frame={a.frame}, n={a.n}, height={a.height}, variants={variants!r}, "
             f"out_dir={os.path.abspath(a.out)!r}, mode={a.mode!r}, flare={bool(a.flare)})")
    s.append(f"print({MARK!r} + dump(res))")
    return _blender("\n".join(s) + "\n", timeout=6 * 3600)


def _frames(spec):
    """"40-56" / "40-56:2" / "1,5,9" → 帧号列表。"""
    out = []
    for part in spec.split(","):
        step = 1
        if ":" in part:
            part, st = part.split(":"); step = int(st)
        if "-" in part[1:]:
            lo, hi = part.split("-", 1) if not part.startswith("-") else part[1:].split("-", 1)
            out += list(range(int(lo), int(hi) + 1, step))
        else:
            out.append(int(part))
    return out


def cmd_shot(a):
    """渲一个镜头（0.8.4）：跟随源相机、续渲、内存守护、逐帧日志（<out>/<name>_log.json）。"""
    s = [f"import bpy\nbpy.ops.wm.open_mainfile(filepath={os.path.abspath(a.blend)!r})"]
    if a.gpu:
        s.append("bl.enable_gpu(use_gpu=True)")
    passes = a.passes if a.passes in (None, "all", "temporal", "delivery") else [x.strip() for x in a.passes.split(",")]
    s.append(f"res = bl.render_shot(source_camera={a.source!r}, frames={_frames(a.frames)!r}, out_dir={os.path.abspath(a.out)!r}, "
             f"preset={a.preset!r}, focal_mm={a.focal_mm or 0.0}, gate={a.gate or '16:9_NATIVE'!r}, format={a.format or 'S35'!r}, passes={passes!r}, "
             f"spp={a.spp!r}, follow={not a.no_follow}, guard={not a.no_guard}, height={a.height!r}, flare={bool(a.flare)}, "
             f"exposure={a.exposure!r}, guard_min_gb={a.guard_gb}, exr_codec={a.exr_codec!r}, "
             f"exclude_collections={a.exclude.split(',') if a.exclude else None!r}, focus_target={a.focus_target!r}, "
             f"exclude_spp={a.exclude_spp!r}, exclude_bounces={a.exclude_bounces!r}, exclude_clamp={_pair(a.exclude_clamp)!r}, exclude_knee={_pair(a.exclude_knee)!r}, exclude_motion_blur={a.exclude_motion_blur!r})")
    s.append("res = dict(records=res['records'], summary=res.get('summary'), exposure=res.get('exposure'), "
             "stopped=res.get('stopped'), log=res['out_dir'])")
    s.append(f"print({MARK!r} + dump(res))")
    return _blender("\n".join(s) + "\n", timeout=48 * 3600)


def cmd_verify(a):
    """相机输入指纹（0.9.2）：处方 / lens_data / OSL / GPU 头文件 / 出瞳表 / 补丁的 sha1；不启动 Blender。"""
    sys.path.insert(0, os.path.join(ROOT, "blenderlens"))
    import verify
    return verify.verify_inputs(_preset_file(a.preset), gate=a.gate or "FULL", format=a.format or "S35", focal_mm=a.focal_mm or 0.0)


def _pair(v):
    """「a,b」→ (a, b)（float）；None → None。"""
    return None if not v else tuple(float(x) for x in v.split(","))


def cmd_sequence(a):
    """多镜头（0.8.5）：--shots JSON（列表，每项 source / frames / focal_mm / preset / name / settings / focus_target / rack …）
    或 --markers（时间线上绑定相机的标记）；每个镜头写到 <out>/<name>/，汇总在 <out>/sequence_log.json。"""
    if a.shots:
        raw = open(a.shots).read() if os.path.exists(a.shots) else a.shots
        shots = json.loads(raw)
        for sh in shots:
            sh["frames"] = tuple(sh["frames"])
    elif not a.markers:
        return {"error": "要给 --shots 或 --markers"}
    else:
        shots = None
    s = [f"import bpy\nbpy.ops.wm.open_mainfile(filepath={os.path.abspath(a.blend)!r})"]
    if a.gpu:
        s.append("bl.enable_gpu(use_gpu=True)")
    passes = a.passes if a.passes in (None, "all", "temporal", "delivery") else [x.strip() for x in a.passes.split(",")]
    s.append(f"res = bl.render_sequence({shots!r}, out_dir={os.path.abspath(a.out)!r}, preset={a.preset!r}, passes={passes!r}, "
             f"spp={a.spp!r}, guard={not a.no_guard}, flare={bool(a.flare)}, exposure={a.exposure!r}, height={a.height!r}, "
             f"exclude_collections={a.exclude.split(',') if a.exclude else None!r}, "
             f"exclude_spp={a.exclude_spp!r}, exclude_bounces={a.exclude_bounces!r}, exclude_clamp={_pair(a.exclude_clamp)!r}, exclude_knee={_pair(a.exclude_knee)!r}, exclude_motion_blur={a.exclude_motion_blur!r}"
             + (f", format={a.format!r}" if a.format else "") + ")")
    s.append(f"print({MARK!r} + dump(res))")
    return _blender("\n".join(s) + "\n", timeout=96 * 3600)


def cmd_denoise(a):
    """时间降噪（0.8.4）：读多层 EXR，写 BL_Final 的稳定版 + 每像素用了几帧的图。"""
    s = [f"res = bl.temporal_denoise({os.path.abspath(a.inp)!r}, {os.path.abspath(a.out)!r}, frames={_frames(a.frames)!r}, "
         f"strength={a.strength}, radius={a.radius}, name={a.name!r}, "
         f"spike_suppress={(dict(zip(('delta', 'min_lum'), _pair(a.spike_suppress))) if a.spike_suppress not in (None, '1') else (True if a.spike_suppress == '1' else None))!r})"]
    s.append(f"print({MARK!r} + dump(res))")
    return _blender("\n".join(s) + "\n", timeout=12 * 3600)


def cmd_diagnose(a):
    return _blender(f"import bpy\nbpy.ops.wm.open_mainfile(filepath={os.path.abspath(a.blend)!r})\n"
                    f"res = dict(issues=bl.diagnose(), state=bl.state() if bl.camera() else None)\n"
                    f"print({MARK!r} + dump(res))\n")


def main():
    ap = argparse.ArgumentParser(description="BlenderLens 命令行（输出 JSON）")
    ap.add_argument("--pretty", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("presets")
    p = sub.add_parser("info"); p.add_argument("preset"); p.add_argument("--focus-m", type=float)
    p.add_argument("--coverage", action="store_true", help="覆盖与畸变报告（像圈、四角照度、畸变、竖线弯曲）"); p.add_argument("--stop", type=float)
    p.add_argument("--focal-mm", type=float); p.add_argument("--gate")
    p.add_argument("--format", choices=["S35", "FF", "IMAX", "65MM"], help="画幅版本：S35 原设计 / FF 全画幅（0.8.8）/ IMAX 15/70 / 65MM 5 齿孔（0.9.1）")
    p = sub.add_parser("verify", help="相机输入指纹（0.9.2）：混用版本时确认逐字节相同")
    p.add_argument("preset"); p.add_argument("--gate"); p.add_argument("--focal-mm", type=float)
    p.add_argument("--format", choices=["S35", "FF", "IMAX", "65MM"])
    sub.add_parser("describe")
    p = sub.add_parser("render")
    p.add_argument("--preset"); p.add_argument("--blend"); p.add_argument("--scene", default="demo", choices=["demo"])
    p.add_argument("--focal-mm", type=float); p.add_argument("--gate")
    p.add_argument("--format", choices=["S35", "FF", "IMAX", "65MM"], help="画幅版本：S35 原设计 / FF 全画幅（0.8.8）/ IMAX 15/70 / 65MM 5 齿孔（0.9.1）")
    p.add_argument("--focus-m", type=float); p.add_argument("--stop", type=float)
    p.add_argument("--set", action="append", help="设置=值（值按 JSON 解析，如 coating_type=BROADBAND、mumps_correction=0.5）")
    p.add_argument("--variants", help="对比图：JSON 列表或 JSON 文件，每项一组设置")
    p.add_argument("--quality", default="draft", choices=["draft", "standard", "final"])
    p.add_argument("--samples", type=int); p.add_argument("--height", type=int)
    p.add_argument("--gpu", action="store_true"); p.add_argument("--no-flare", action="store_true")
    p.add_argument("--out", default="renders/cli/preview.png")
    p = sub.add_parser("diagnose"); p.add_argument("--blend", required=True)
    p = sub.add_parser("focus"); p.add_argument("--blend", required=True); p.add_argument("--frame", type=int)
    p.add_argument("--target", help="物体名，或 u,v（画面坐标，0–1，原点左下）"); p.add_argument("--apply", action="store_true")
    p.add_argument("--coc", type=float, default=1.0); p.add_argument("--near", type=float); p.add_argument("--far", type=float)
    p = sub.add_parser("temporal"); p.add_argument("--blend", required=True); p.add_argument("--frame", type=int, required=True)
    p.add_argument("--n", type=int, default=8); p.add_argument("--height", type=int, default=1080)
    p.add_argument("--variants", help="JSON 列表或文件")
    p.add_argument("--gpu", action="store_true"); p.add_argument("--out", default="renders/temporal")
    p.add_argument("--mode", default="locked", choices=["locked", "moving"]); p.add_argument("--flare", action="store_true")
    p = sub.add_parser("shot"); p.add_argument("--blend", required=True); p.add_argument("--source")
    p.add_argument("--frames", required=True, help="40-56、40-56:2 或 1,5,9"); p.add_argument("--out", required=True)
    p.add_argument("--preset"); p.add_argument("--focal-mm", type=float); p.add_argument("--gate")
    p.add_argument("--format", choices=["S35", "FF", "IMAX", "65MM"], help="画幅版本：S35 原设计 / FF 全画幅（0.8.8）/ IMAX 15/70 / 65MM 5 齿孔（0.9.1）")
    p.add_argument("--passes", help="all / temporal / 逗号分隔的渲染层名"); p.add_argument("--spp", type=int)
    p.add_argument("--height", type=int); p.add_argument("--flare", action="store_true")
    p.add_argument("--exposure", default="keep", choices=["keep", "pinhole", "pinhole_centre"]); p.add_argument("--exr-codec")
    p.add_argument("--no-follow", action="store_true"); p.add_argument("--no-guard", action="store_true")
    p.add_argument("--guard-gb", type=float, default=4.0); p.add_argument("--gpu", action="store_true")
    p.add_argument("--exclude", help="排除集合（逗号分隔，如 FX_rain）"); p.add_argument("--focus-target", help="对焦跟住的物体名")
    p.add_argument("--exclude-spp", type=int, help="排除层单独的采样数（0.8.7，默认同主渲染）"); p.add_argument("--exclude-bounces", type=int)
    p.add_argument("--exclude-clamp", help="排除层的样本截断 直接,间接（0.9.1，如 2,1）"); p.add_argument("--exclude-knee", help="排除层亮度软压缩 起点,上限（0.9.1，如 0.05,0.10）")
    p.add_argument("--exclude-motion-blur", type=float, help="排除层单独开运动模糊（0.9.2）：快门（帧），如 0.5")
    p = sub.add_parser("sequence"); p.add_argument("--blend", required=True); p.add_argument("--out", required=True)
    p.add_argument("--shots", help="JSON 列表或文件"); p.add_argument("--markers", action="store_true")
    p.add_argument("--preset"); p.add_argument("--passes"); p.add_argument("--spp", type=int); p.add_argument("--height", type=int)
    p.add_argument("--flare", action="store_true"); p.add_argument("--exposure", default="keep", choices=["keep", "pinhole", "pinhole_centre"])
    p.add_argument("--format", choices=["S35", "FF", "IMAX", "65MM"])
    p.add_argument("--exclude"); p.add_argument("--no-guard", action="store_true"); p.add_argument("--gpu", action="store_true")
    p.add_argument("--exclude-spp", type=int); p.add_argument("--exclude-bounces", type=int)
    p.add_argument("--exclude-clamp"); p.add_argument("--exclude-knee"); p.add_argument("--exclude-motion-blur", type=float)
    p = sub.add_parser("denoise"); p.add_argument("--in", dest="inp", required=True); p.add_argument("--out", required=True)
    p.add_argument("--frames", required=True); p.add_argument("--strength", type=float, default=1.0)
    p.add_argument("--radius", type=int, default=3); p.add_argument("--name", default="f")
    p.add_argument("--spike-suppress", help="孤立亮点抑制（0.9.2）：1 = 默认阈值，或 delta,min_lum（如 0.06,0.25）")
    p = sub.add_parser("test"); p.add_argument("args", nargs=argparse.REMAINDER)
    argv = sys.argv[1:]
    pretty = "--pretty" in argv and argv[0] != "test"
    a = ap.parse_args([x for x in argv if x != "--pretty"] if pretty else argv)
    a.pretty = a.pretty or pretty
    if a.cmd == "test":
        sys.exit(subprocess.call([sys.executable, os.path.join(ROOT, "tests", "run.py")] + a.args))
    res = dict(presets=cmd_presets, info=cmd_info, describe=cmd_describe, render=cmd_render, diagnose=cmd_diagnose,
               focus=cmd_focus, temporal=cmd_temporal, shot=cmd_shot, denoise=cmd_denoise, sequence=cmd_sequence,
               verify=cmd_verify)[a.cmd](a)
    print(json.dumps(res, ensure_ascii=False, indent=1 if a.pretty else None, default=str))
    sys.exit(1 if isinstance(res, dict) and "error" in res else 0)


if __name__ == "__main__":
    main()
