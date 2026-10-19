# 运动补偿工具（0.8.4，MessyRoom R1 / R2 / R3）：用 BL_Vector 把相邻帧对齐到当前帧。纯 numpy，不依赖 bpy。
# 数组约定与 passes.read() 相同：行 0 = 顶部；BL_Vector 是像素单位、y 向上，X,Y = 上一帧位置 − 当前位置，
# Z,W = 当前位置 − 下一帧位置（Cycles 约定）。
import numpy as np

SPEED_EDGES = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, np.inf)   # px/帧（按 1080 行换算）


def tone(a):
    """闪烁指标用的亮度：RGB 平均后 x/(1+x)（高光不会主导）。"""
    lum = a[..., :3].mean(axis=-1)
    return lum / (1.0 + np.maximum(lum, 0.0))


def bilinear(img, x, y):
    """img（H×W 或 H×W×C）在浮点坐标 (x = 列, y = 行) 处双线性取值，越界夹边。"""
    H, W = img.shape[:2]
    x = np.clip(x, 0, W - 1.001); y = np.clip(y, 0, H - 1.001)
    x0 = x.astype(np.int32); y0 = y.astype(np.int32)
    fx = (x - x0).astype(np.float32); fy = (y - y0).astype(np.float32)
    if img.ndim == 3:
        fx = fx[..., None]; fy = fy[..., None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy)
            + img[y0 + 1, x0] * (1 - fx) * fy + img[y0 + 1, x0 + 1] * fx * fy)


def grid(H, W):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    return xx, yy


def prev_coords(vec, xx, yy):
    """当前帧每个像素在上一帧里的位置（列, 行）。"""
    return xx + vec[..., 0], yy - vec[..., 1]


def next_coords(vec, xx, yy):
    """当前帧每个像素在下一帧里的位置（列, 行）。"""
    return xx - vec[..., 2], yy + vec[..., 3]


def warp_prev(prev, vec):
    """把上一帧（H×W 或 H×W×C）对齐到当前帧：vec 是当前帧的 BL_Vector。"""
    H, W = vec.shape[:2]
    xx, yy = grid(H, W)
    x, y = prev_coords(vec, xx, yy)
    return bilinear(prev, x, y)


def speed(vec, height=None):
    """每像素运动速度（px/帧，取到上一帧的位移）。height：换算成 1080 行的等效值（不同分辨率可比）。"""
    s = np.hypot(vec[..., 0], vec[..., 1])
    return s * (1080.0 / height) if height else s


def mc_residual(tones, vecs, border=8):
    """运动补偿闪烁：r_t = |f_t − warp(f_{t−1})|，t = 1…n−1。→ (各帧残差列表（去掉 border 像素边）, 平均残差图)。"""
    rs = []
    for t in range(1, len(tones)):
        r = np.abs(tones[t] - warp_prev(tones[t - 1], vecs[t]))
        rs.append(r[border:-border, border:-border] if border else r)
    return rs, np.mean(rs, axis=0)


def speed_histogram(vecs, height, border=8, D=None):
    """速度直方图（1080 行等效 px/帧）：各速度区间的像素比例；给了残差图 D 时，再给每个区间的平均闪烁。"""
    sp = np.mean([speed(v, height) for v in vecs[1:]], axis=0)
    if border:
        sp = sp[border:-border, border:-border]
    E = np.asarray(SPEED_EDGES)
    which = np.clip(np.searchsorted(E, sp, side="right") - 1, 0, len(E) - 2)
    frac = np.bincount(which.ravel(), minlength=len(E) - 1) / which.size
    out = dict(edges_px=[float(e) if np.isfinite(e) else None for e in E], fraction=[round(float(f), 4) for f in frac],
               median_px=round(float(np.median(sp)), 3), p90_px=round(float(np.percentile(sp, 90)), 3),
               per_frame_median_px=[round(float(np.median(speed(v, height))), 3) for v in vecs[1:]])
    if D is not None:
        out["flicker_by_speed"] = [round(float(D[which == k].mean()), 6) if (which == k).any() else None
                                   for k in range(len(E) - 1)]
    return out


def effective_vectors(vec, vrefl=None, share=None, thr=0.5):
    """0.8.5：下游实际该用的矢量——反射占比 > thr 且有倒影矢量（BL_VectorReflection）的像素用倒影的，其余用 BL_Vector。"""
    if vrefl is None or share is None:
        return vec
    return np.where((share > thr)[..., None], vrefl, vec)
