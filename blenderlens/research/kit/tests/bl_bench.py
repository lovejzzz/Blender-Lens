# MessyRoom 提供的合成基准（原样，仅加注释）。
# 用法：blender -b -P tests/bl_bench.py -- <包含 blenderlens 文件夹的目录> <输出目录> 64 256；结果追加进 <输出目录>/bench.json。
import bpy, sys, os, time, json, math, re
a = sys.argv[sys.argv.index("--") + 1:]
pkg, out = a[0], a[1]; motel = "--motel" in a
spps = [int(x) for x in a[2:] if x.isdigit()] or [64, 256]
sys.path.insert(0, pkg)
import blenderlens; blenderlens.register()
from blenderlens import rig, optics
import numpy as np
os.makedirs(out, exist_ok=True)
_man = open(os.path.join(pkg, "blenderlens", "blender_manifest.toml")).read()
VERSION = re.search(r'^version\s*=\s*"([^"]+)"', _man, re.M).group(1)

def build_lens(sc, vf=40.0):
    try:
        import blenderlens.api as bl
        cam = bl.build("c_series_style.json", sc, focal_mm=vf, gate="16:9", units="SCENE", height=540)
    except ImportError:
        rx = optics.load(os.path.join(optics.PRESCRIPTION_DIR, "c_series_style.json"))
        rx = optics.rescale_to_vertical_efl(rx, vf)
        rx["sensor"]["width"] = rx["sensor"]["height"] * 16 / 9 / optics.desqueeze(rx)
        cam = rig.build(bpy.context, rx, keep_units=True)
    sc.camera = cam
    return cam

def focus(cam, metres):
    try:
        import blenderlens.api as bl
        bl.set_focus(cam, metres=metres)
    except ImportError:
        cam.data.blenderlens.focus_distance = metres * 1000.0

def synthetic(sc):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    sc.unit_settings.scale_length = 1.0
    w = bpy.data.worlds.new("W"); sc.world = w; w.node_tree.nodes["Background"].inputs[1].default_value = 0.02
    m = bpy.data.materials.new("chart"); nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    ch = nt.nodes.new("ShaderNodeTexChecker"); ch.inputs["Scale"].default_value = 24
    nt.links.new(ch.outputs[0], b.inputs["Base Color"])
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 3.0, 0)); p = bpy.context.active_object
    p.scale = (2.0, 1.2, 1); p.rotation_euler = (math.radians(90), 0, 0); p.data.materials.append(m)
    ld = bpy.data.lights.new("key", "AREA"); ld.size = 2; ld.energy = 400
    k = bpy.data.objects.new("key", ld); sc.collection.objects.link(k); k.location = (0, 0.5, 2.5); k.rotation_euler = (math.radians(60), 0, 0)
    em = bpy.data.materials.new("pt"); em.node_tree.nodes.clear()
    o_ = em.node_tree.nodes.new("ShaderNodeOutputMaterial"); e = em.node_tree.nodes.new("ShaderNodeEmission")
    e.inputs[1].default_value = 400; em.node_tree.links.new(e.outputs[0], o_.inputs[0])
    for ix in (-1, 0, 1):
        for iz in (-1, 0, 1):
            bpy.ops.mesh.primitive_uv_sphere_add(radius=0.02, location=(ix * 3.2, 12.0, iz * 1.8))
            bpy.context.active_object.data.materials.append(em)
    cam = build_lens(sc)
    cam.location = (0, 0, 0); cam.rotation_euler = (math.radians(90), 0, 0)
    focus(cam, 3.0)
    return cam

def setup_motel(sc, frame=110):
    sc.frame_set(frame)
    src = max((m for m in sc.timeline_markers if m.frame <= frame), key=lambda m: m.frame).camera
    mw = src.matrix_world.copy(); f = src.data.dof.focus_distance
    for m in list(sc.timeline_markers):
        m.camera = None
    cam = build_lens(sc)
    cam.matrix_world = mw; focus(cam, f)
    return cam

sc = bpy.context.scene
cam = setup_motel(sc) if motel else synthetic(sc)
p = cam.data.blenderlens
p.f_number = 2.3
rig.apply_settings(cam)
rig.render_preset(sc)
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 960, 540, 100
sc.render.use_motion_blur = False; sc.cycles.use_denoising = False
sc.render.image_settings.file_format = "OPEN_EXR"; sc.render.image_settings.color_depth = "32"
tag = "motel" if motel else "synthetic"
rec = {"version": VERSION, "test": tag, "when": time.strftime("%Y-%m-%d %H:%M"), "device": sc.cycles.device, "results": []}
for spp in spps:
    imgs = []; ts = []
    for seed in (0, 1):
        sc.cycles.samples = spp; sc.cycles.seed = seed
        fp = os.path.join(out, f"{VERSION}_{tag}_{spp}_s{seed}.exr"); sc.render.filepath = fp
        t = time.time(); bpy.ops.render.render(write_still=True); ts.append(time.time() - t)
        im = bpy.data.images.load(fp); arr = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)[..., :3]
        bpy.data.images.remove(im); imgs.append(arr)
    diff = imgs[0] - imgs[1]
    noise = float(np.sqrt(np.mean(diff ** 2)) / math.sqrt(2)); level = float(np.mean(imgs[0]))
    t0, t1 = imgs[0] / (1 + imgs[0]), imgs[1] / (1 + imgs[1])
    noise_tm = float(np.sqrt(np.mean((t0 - t1) ** 2)) / math.sqrt(2))
    rec["results"].append({"spp": spp, "seconds": round(sum(ts) / 2, 1), "noise_rms": round(noise, 6), "mean": round(level, 6),
                           "rel_noise": round(noise / max(level, 1e-9), 4), "noise_tonemapped": round(noise_tm, 5)})
    print("BENCH", VERSION, tag, spp, rec["results"][-1], flush=True)
path = os.path.join(out, "bench.json")
hist = json.load(open(path)) if os.path.exists(path) else []
hist.append(rec); json.dump(hist, open(path, "w"), indent=1)
print("BENCH saved", path)
