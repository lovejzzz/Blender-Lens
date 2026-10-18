# 镜片几何生成（审校修订版文档第三节脚本，原样摘出为模块）
# 约定：原点 = 像面；物方 +Z；光沿 −Z；表面 z = z_vertex − sag；单位 mm。
import bpy, math

INF = float("inf")

def curv(R):
    return 0.0 if math.isinf(R) else 1.0 / R

def sag(x, y, cx, cy, kx=0.0, ky=0.0):
    q = 1.0 - (1+kx)*cx*cx*x*x - (1+ky)*cy*cy*y*y
    if q <= 0.0:
        raise ValueError(f"孔径超出曲面有效区: ({x:.2f}, {y:.2f})")
    return (cx*x*x + cy*y*y) / (1.0 + math.sqrt(q))

def sag_grad(x, y, cx, cy, kx=0.0, ky=0.0, h=1e-5):
    gx = (sag(x+h, y, cx, cy, kx, ky) - sag(x-h, y, cx, cy, kx, ky)) / (2*h)
    gy = (sag(x, y+h, cx, cy, kx, ky) - sag(x, y-h, cx, cy, kx, ky)) / (2*h)
    return gx, gy

def surface_grid(Rx, Ry, z0, D, rings, sectors, k=(0.0, 0.0)):
    """极坐标网格：1 个中心点 + rings*sectors；法线朝 +Z（物方）。"""
    cx, cy = curv(Rx), curv(Ry)
    V, N = [], []
    def add(x, y):
        gx, gy = sag_grad(x, y, cx, cy, *k)
        L = math.sqrt(gx*gx + gy*gy + 1.0)
        V.append((x, y, z0 - sag(x, y, cx, cy, *k)))
        N.append((gx/L, gy/L, 1.0/L))
    add(0.0, 0.0)
    for i in range(1, rings + 1):
        r = (D / 2) * i / rings
        for j in range(sectors):
            t = 2 * math.pi * j / sectors
            add(r * math.cos(t), r * math.sin(t))
    return V, N

def surface_faces(rings, sectors, off=0, flip=False):
    F = []
    for j in range(sectors):
        F.append((0, 1 + j, 1 + (j+1) % sectors))
    for i in range(1, rings):
        b0, b1 = 1 + (i-1)*sectors, 1 + i*sectors
        for j in range(sectors):
            j2 = (j+1) % sectors
            F.append((b0+j, b1+j, b1+j2, b0+j2))
    return [tuple(off + v for v in (f[::-1] if flip else f)) for f in F]

def _mesh(name, V, F, N, mat_idx, mats):
    me = bpy.data.meshes.new(name)
    me.from_pydata(V, [], F)
    for m in mats:
        me.materials.append(m)
    for p, mi in zip(me.polygons, mat_idx):
        p.material_index = mi
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices(N)   # Blender 4.1+
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    return ob

def build_element(name, s1, s2, z1, z2, D, mat_glass, mat_black,
                  rings=128, sectors=192):
    """封闭实体镜片（LuxCore 路线）。s1/s2 = dict(Rx, Ry, k=(kx,ky))。"""
    V1, N1 = surface_grid(s1["Rx"], s1["Ry"], z1, D, rings, sectors, s1.get("k", (0, 0)))
    V2, N2 = surface_grid(s2["Rx"], s2["Ry"], z2, D, rings, sectors, s2.get("k", (0, 0)))
    n = len(V1)
    F = surface_faces(rings, sectors) + surface_faces(rings, sectors, off=n, flip=True)
    N = N1 + [(-a, -b, -c) for a, b, c in N2]
    V = V1 + V2
    # 侧壁：复制外圈顶点，法线径向朝外
    o1, o2 = n - sectors, 2*n - sectors
    w = len(V)
    for j in range(sectors):
        t = 2 * math.pi * j / sectors
        V += [V[o1 + j], V[o2 + j]]
        N += [(math.cos(t), math.sin(t), 0.0)] * 2
    for j in range(sectors):
        j2 = (j+1) % sectors
        F.append((w + 2*j, w + 2*j + 1, w + 2*j2 + 1, w + 2*j2))
    n_optical = 2 * (sectors + (rings-1)*sectors)
    mat_idx = [0]*n_optical + [1]*sectors
    return _mesh(name, V, F, N, mat_idx, [mat_glass, mat_black])

def build_interface(name, s, z, D, mat, rings=128, sectors=192):
    """单层开放界面（Cycles 界面法）：法线朝物方，材质 IOR = n_后 / n_前。"""
    V, N = surface_grid(s["Rx"], s["Ry"], z, D, rings, sectors, s.get("k", (0, 0)))
    F = surface_faces(rings, sectors)
    return _mesh(name, V, F, N, [0]*len(F), [mat])

def surface_positions(rx, bfl, scale=1.0):
    """从像面倒推各面顶点 z：z_n = BFL，z_i = z_{i+1} + d_i。"""
    z = [0.0] * len(rx)
    z[-1] = bfl * scale
    for i in range(len(rx) - 2, -1, -1):
        z[i] = z[i+1] + rx[i]["d"] * scale
    return z
