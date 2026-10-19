# 网格生成（纯 Python，不依赖 bpy，可单测）。
# 每个函数返回 (V, F, N, M)：顶点、面、逐顶点法线、逐面材质槽（0 = 光学面，1 = 黑色）。
# 光学面法线一律朝物方（+Z）—— Cycles 界面法要求；实体镜片的后表面在 solid 模式下翻转。
import math
try:
    from .optics import surf_sag, surface_z, ap_limits
except ImportError:
    from optics import surf_sag, surface_z, ap_limits

def _vn(sf, x, y):
    s, gx, gy = surf_sag(sf, x, y)
    L = math.sqrt(gx * gx + gy * gy + 1.0)
    return (x, y, sf["z"] - s), (gx / L, gy / L, 1.0 / L)

def surface_mesh(sf, rings=96, sectors=192, nx=257, ny=9, flip=False):
    """一个光学面（含平台）。柱面矩形 → 规则网格；圆形 → 极坐标网格 + 平台环。
    flip=True：法线朝像方（−Z）——用于 n_后 < n_前 的面，使材质 IOR 恒 ≥ 1（Cycles 薄膜在 IOR<1 时不正确）。"""
    V, F, N, M = _surface_mesh(sf, rings, sectors, nx, ny)
    if flip:
        F = [tuple(reversed(f)) for f in F]
        N = [(-a, -b, -c) for (a, b, c) in N]
    return V, F, N, M

def _surface_mesh(sf, rings, sectors, nx, ny):
    a = sf["ap"]
    V, N, F, M = [], [], [], []
    if a["shape"] == "rect":
        sx, sy = a["semi_x"], a["semi_y"]
        for j in range(ny):
            y = -sy + 2 * sy * j / (ny - 1)
            for i in range(nx):
                x = -sx + 2 * sx * i / (nx - 1)
                v, n = _vn(sf, x, y)
                V.append(v); N.append(n)
        for j in range(ny - 1):
            for i in range(nx - 1):
                a0 = j * nx + i
                F.append((a0, a0 + 1, a0 + 1 + nx, a0 + nx))      # 逆时针（从 +Z 看）→ 面法线 +Z
                M.append(0)
        return V, F, N, M
    rc = a.get("clear") or a["outer"]
    ro = a["outer"]
    _, msx, msy, _ = ap_limits(a)
    v, n = _vn(sf, 0.0, 0.0)
    V.append(v); N.append(n)
    for i in range(1, rings + 1):
        r = rc * i / rings
        for j in range(sectors):
            t = 2 * math.pi * j / sectors
            v, n = _vn(sf, r * math.cos(t), r * math.sin(t))
            V.append(v); N.append(n)
    def masked(*idx):                                   # 遮罩（切边 / 矩形视场光阑）外的面画黑
        cx = sum(V[k][0] for k in idx) / len(idx); cy = sum(V[k][1] for k in idx) / len(idx)
        return 1 if (abs(cx) > msx or abs(cy) > msy) else 0
    for j in range(sectors):
        f = (0, 1 + j, 1 + (j + 1) % sectors)
        F.append(f); M.append(masked(*f))
    for i in range(1, rings):
        b0, b1 = 1 + (i - 1) * sectors, 1 + i * sectors
        for j in range(sectors):
            j2 = (j + 1) % sectors
            f = (b0 + j, b1 + j, b1 + j2, b0 + j2)
            F.append(f); M.append(masked(*f))
    if ro > rc + 1e-6:                                         # 平台：通光边缘外的环（z 沿各方向取通光边缘的 z）
        base = len(V)
        for r in (rc, ro):
            for j in range(sectors):
                t = 2 * math.pi * j / sectors
                x, y = r * math.cos(t), r * math.sin(t)
                V.append((x, y, surface_z(sf, x, y))); N.append((0.0, 0.0, 1.0))
        for j in range(sectors):
            j2 = (j + 1) % sectors
            F.append((base + j, base + sectors + j, base + sectors + j2, base + j2)); M.append(1)
    return V, F, N, M

def outline(ap, count=256):
    """镜片外轮廓（逆时针），矩形含四角。"""
    if ap["shape"] == "circle":
        R = ap["outer"]
        return [(R * math.cos(2 * math.pi * k / count), R * math.sin(2 * math.pi * k / count)) for k in range(count)]
    sx, sy = ap["semi_x"], ap["semi_y"]
    pts = []
    per = count // 4
    corners = [(sx, -sy), (sx, sy), (-sx, sy), (-sx, -sy)]
    for c in range(4):
        (x0, y0), (x1, y1) = corners[c], corners[(c + 1) % 4]
        for k in range(per):
            t = k / per
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return pts

def side_wall(front, back, count=256):
    """镜片侧壁：沿外轮廓连接前、后表面（含平台）。法线朝外。"""
    pts = outline(front["ap"], count)
    V, N, F, M = [], [], [], []
    for (x, y) in pts:
        zf = surface_z(front, x, y); zb = surface_z(back, x, y)
        L = math.hypot(x, y) or 1.0
        V += [(x, y, zf), (x, y, zb)]
        N += [(x / L, y / L, 0.0)] * 2
    n = len(pts)
    for k in range(n):
        k2 = (k + 1) % n
        F.append((2 * k, 2 * k + 1, 2 * k2 + 1, 2 * k2)); M.append(1)
    return V, F, N, M

def iris_outline(r, blades=0, rot_rad=0.0, count=256):
    """光圈内沿（逆时针）：圆，或 blades 边形（外接圆半径 r，顶点在 rot + k·2π/blades，与 OSL 相机同一判据）。"""
    if blades < 3:
        return [(r * math.cos(2 * math.pi * k / count), r * math.sin(2 * math.pi * k / count)) for k in range(count)]
    per = max(4, count // blades)
    pts = []
    for k in range(blades):
        a0 = rot_rad + 2 * math.pi * k / blades; a1 = rot_rad + 2 * math.pi * (k + 1) / blades
        x0, y0, x1, y1 = r * math.cos(a0), r * math.sin(a0), r * math.cos(a1), r * math.sin(a1)
        for j in range(per):
            u = j / per
            pts.append((x0 + (x1 - x0) * u, y0 + (y1 - y0) * u))
    return pts

def annulus(inner_pts, R_out, z):
    """平板环：内边 = 任意闭合轮廓（逆时针），外边 = 半径 R_out 的圆，同角度配对。用于镜框挡板。"""
    V, N, F, M = [], [], [], []
    n = len(inner_pts)
    for (x, y) in inner_pts:
        t = math.atan2(y, x)
        V += [(x, y, z), (R_out * math.cos(t), R_out * math.sin(t), z)]
        N += [(0.0, 0.0, -1.0)] * 2                           # 与绕序一致（朝像方）
    for k in range(n):
        k2 = (k + 1) % n
        F.append((2 * k, 2 * k2, 2 * k2 + 1, 2 * k + 1)); M.append(0)
    return V, F, N, M

def tube(R, z0, z1, count=192):
    """镜筒：半径 R、z0→z1 的圆管（法线与绕序一致，朝外；材质为双面黑）。"""
    V, N, F, M = [], [], [], []
    for k in range(count):
        t = 2 * math.pi * k / count
        c, s = math.cos(t), math.sin(t)
        V += [(R * c, R * s, z0), (R * c, R * s, z1)]
        N += [(c, s, 0.0)] * 2
    for k in range(count):
        k2 = (k + 1) % count
        F.append((2 * k, 2 * k2, 2 * k2 + 1, 2 * k + 1)); M.append(0)
    return V, F, N, M

def disc(R, z, count=192):
    V = [(0.0, 0.0, z)] + [(R * math.cos(2 * math.pi * k / count), R * math.sin(2 * math.pi * k / count), z) for k in range(count)]
    F = [(0, 1 + k, 1 + (k + 1) % count) for k in range(count)]
    return V, F, [(0.0, 0.0, 1.0)] * len(V), [0] * len(F)

def merge(*parts):
    V, F, N, M = [], [], [], []
    for (v, f, n, m) in parts:
        off = len(V)
        V += v; N += n; M += m
        F += [tuple(off + i for i in face) for face in f]
    return V, F, N, M
