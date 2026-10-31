"""Baked ambient occlusion for the hospital, adapted from the original demo's pipeline/ao.py.

Large surfaces of Architecture and Structure (walls, slabs, roofs, ceilings, glazing): each file's
large surfaces are unwrapped into one UV atlas (xatlas). Every texel traces
cosine-weighted hemisphere rays (Embree) against the whole building and a ground plane at Level 1,
then the result is denoised inside charts and dilated into the padding. The texel density is chosen
so the atlas fills a 4096 px texture.

Small, dense parts (mullions, railings, PV panels, doors, fixtures) and all of HVAC and Piping get
per-vertex AO: their charts would be smaller than a texel, and ducts and pipes are long thin
cylinders that pack badly. It is stored in TEXCOORD_1 as (ao, 0.5) and sampled through a grey ramp
texture, so the app can use one AO code path (aoMap on uv1) for every sub-model.
"""
import math

import numpy as np
import trimesh
import xatlas
from PIL import Image
from scipy import ndimage

ATLAS_RES = 4096
MAX_TEXELS_PER_M = 12.0
FILL = 0.62           # expected atlas utilisation when choosing the texel density
RAYS = 28
AO_DIST_M = 1.6       # occluders within this distance darken: corners, slab edges, reveals, under eaves
AO_STRENGTH = 0.8
AO_GAIN = 1.3
GROUND_MARGIN_M = 40
SELF_HIT_M = 0.6      # a face whose normal ray re-enters its own element this soon points inward
MEP_RAYS = 14
MEP_DIST_M = 1.2


def _hemisphere(n):
    i = np.arange(n) + 0.5
    r = np.sqrt(i / n)
    phi = i * np.pi * (3 - np.sqrt(5))
    return np.stack([r * np.cos(phi), r * np.sin(phi), np.sqrt(1 - r * r)], 1)


def _basis(nrm, angle):
    a = np.where(np.abs(nrm[:, [0]]) < 0.9, [[1, 0, 0]], [[0, 1, 0]])
    t = np.cross(nrm, a)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    b = np.cross(nrm, t)
    c, s = np.cos(angle)[:, None], np.sin(angle)[:, None]   # per-sample rotation breaks up banding
    return c * t + s * b, -s * t + c * b


def caster(occluder_tris):
    """Embree scene of occluder triangles (m,3,3) plus a ground plane at y = 0."""
    lo, hi = occluder_tris.reshape(-1, 3).min(0), occluder_tris.reshape(-1, 3).max(0)
    g0, g1 = lo - GROUND_MARGIN_M, hi + GROUND_MARGIN_M
    ground = np.array([[[g0[0], 0, g0[2]], [g1[0], 0, g1[2]], [g1[0], 0, g0[2]]],
                       [[g0[0], 0, g0[2]], [g0[0], 0, g1[2]], [g1[0], 0, g1[2]]]], np.float32)
    tris = np.concatenate([occluder_tris, ground]).astype(np.float32)
    mesh = trimesh.Trimesh(tris.reshape(-1, 3), np.arange(len(tris) * 3).reshape(-1, 3), process=False)
    return trimesh.ray.ray_pyembree.RayMeshIntersector(mesh), len(tris) - 2


def _occlusion(ray, pts, nrm, rays, dist_m, seed=7):
    dirs = _hemisphere(rays)
    rng = np.random.default_rng(seed)
    occ = np.zeros(len(pts))
    chunk = max(1, 2_000_000 // rays)
    for s in range(0, len(pts), chunk):
        p, n = pts[s:s + chunk], nrm[s:s + chunk]
        t, b = _basis(n, rng.uniform(0, 2 * np.pi, len(p)))
        d = (dirs[None, :, :1] * t[:, None] + dirs[None, :, 1:2] * b[:, None]
             + dirs[None, :, 2:] * n[:, None]).reshape(-1, 3)
        o = np.repeat(p + n * 4e-3, rays, axis=0)
        loc, ri, _ = ray.intersects_location(o, d, multiple_hits=False)
        contrib = np.zeros(len(o))
        if len(ri):
            dist = np.linalg.norm(loc - o[ri], axis=1)
            near = dist < dist_m
            contrib[ri[near]] = (1 - dist[near] / dist_m) ** 0.75
        occ[s:s + chunk] = contrib.reshape(-1, rays).mean(1)
    return occ


def unwrap(prims):
    """prims: list of (V, N, F). Returns (list of (vmap, F2, UV), size). Triangle order is kept."""
    area = sum(float(np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1).sum() / 2)
               for V, _, F in prims)
    tpm = min(MAX_TEXELS_PER_M, math.sqrt(FILL * ATLAS_RES ** 2 / max(area, 1)))
    for attempt in range(6):
        atlas = xatlas.Atlas()
        for V, N, F in prims:
            atlas.add_mesh(V.astype(np.float32), F.astype(np.uint32), N.astype(np.float32))
        pack = xatlas.PackOptions()
        pack.resolution = ATLAS_RES
        pack.texels_per_unit = tpm
        pack.padding = 2
        pack.bilinear = True
        pack.rotate_charts = True
        chart = xatlas.ChartOptions()
        chart.max_iterations = 1
        atlas.generate(chart_options=chart, pack_options=pack)
        if atlas.atlas_count == 1:
            break
        tpm *= 0.85
    out = []
    for i, (V, N, F) in enumerate(prims):
        vmap, idx, uv = atlas[i]
        out.append((vmap, idx.reshape(-1, 3).astype(np.int64), uv))
    print(f'  atlas {atlas.width}x{atlas.height}  {tpm:.1f} texels/m  area {area:,.0f} m2  '
          f'utilisation {atlas.utilization:.2f}')
    return out, (atlas.width, atlas.height)


def _raster(tris_uv, W, H):
    """tris_uv: (m,3,2) texel coords. Returns (tri index, x, y, barycentric (k,3)) for covered texels."""
    mn = np.floor(tris_uv.min(1)).astype(np.int64)
    mx = np.ceil(tris_uv.max(1)).astype(np.int64)
    span = (mx - mn + 1).max(1)
    res_t, res_x, res_y, res_l = [], [], [], []
    for S in (2, 4, 8, 16, 32, 64):
        lo_s = S // 2 if S > 2 else 0
        sel = np.flatnonzero((span <= S) & (span > lo_s))
        if not len(sel):
            continue
        gx, gy = np.meshgrid(np.arange(S), np.arange(S))
        gx, gy = gx.ravel(), gy.ravel()
        step = max(1, 4_000_000 // (S * S))
        for s in range(0, len(sel), step):
            ids = sel[s:s + step]
            t = tris_uv[ids]
            cx = mn[ids, 0][:, None] + gx[None] + 0.5
            cy = mn[ids, 1][:, None] + gy[None] + 0.5
            (ax, ay), (bx, by), (qx, qy) = [(t[:, k, 0][:, None], t[:, k, 1][:, None]) for k in range(3)]
            den = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            l0 = ((by - qy) * (cx - qx) + (qx - bx) * (cy - qy)) / den
            l1 = ((qy - ay) * (cx - qx) + (ax - qx) * (cy - qy)) / den
            l2 = 1 - l0 - l1
            ext = np.maximum(np.abs(t[:, :, 0].max(1) - t[:, :, 0].min(1)), np.abs(t[:, :, 1].max(1) - t[:, :, 1].min(1)))
            tol = (-0.6 / np.maximum(ext, 1.0))[:, None]                     # ~half a texel of slack
            ins = (l0 >= tol) & (l1 >= tol) & (l2 >= tol) & (cx < W) & (cy < H) & (cx >= 0) & (cy >= 0)
            r, c = np.nonzero(ins)
            if not len(r):
                continue
            l = np.clip(np.stack([l0[r, c], l1[r, c], l2[r, c]], 1), 0, 1)
            l /= np.maximum(l.sum(1, keepdims=True), 1e-9)
            res_t.append(ids[r]); res_x.append((cx[r, c] - 0.5).astype(np.int64))
            res_y.append((cy[r, c] - 0.5).astype(np.int64)); res_l.append(l)
    big = np.flatnonzero(span > 64)                                             # rare: very large faces
    for k in big:
        t = tris_uv[k]
        x0, y0 = np.maximum(mn[k], 0)
        x1, y1 = np.minimum(mx[k], [W - 1, H - 1])
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        cx, cy = xs.ravel() + 0.5, ys.ravel() + 0.5
        (ax, ay), (bx, by), (qx, qy) = t
        den = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
        if abs(den) < 1e-12:
            continue
        l0 = ((by - qy) * (cx - qx) + (qx - bx) * (cy - qy)) / den
        l1 = ((qy - ay) * (cx - qx) + (ax - qx) * (cy - qy)) / den
        l2 = 1 - l0 - l1
        ins = (l0 >= -0.01) & (l1 >= -0.01) & (l2 >= -0.01)
        if not ins.any():
            continue
        l = np.clip(np.stack([l0[ins], l1[ins], l2[ins]], 1), 0, 1)
        l /= l.sum(1, keepdims=True)
        res_t.append(np.full(ins.sum(), k)); res_x.append(xs.ravel()[ins]); res_y.append(ys.ravel()[ins]); res_l.append(l)
    return (np.concatenate(res_t), np.concatenate(res_x), np.concatenate(res_y), np.concatenate(res_l))


def bake_atlas(prims_uv, size, face_el, ray, occ_el):
    """prims_uv: list of (V, F, UV) after unwrapping; face_el: element id per face (all prims,
    concatenated, same id space as occ_el). Returns uint8 (H, W)."""
    W, H = size
    V_all = np.concatenate([V[F] for V, F, _ in prims_uv])               # (m,3,3)
    UV_all = np.concatenate([UV[F] for _, F, UV in prims_uv]) * [W, H]
    fn = np.cross(V_all[:, 1] - V_all[:, 0], V_all[:, 2] - V_all[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    cen = V_all.mean(1)
    # orient each face outward: a short ray that re-enters its own element means the normal points in
    loc, ri, ti = ray.intersects_location(cen + fn * 2e-3, fn, multiple_hits=False)
    flip = np.zeros(len(fn), bool)
    if len(ri):
        d = np.linalg.norm(loc - cen[ri], axis=1)
        ok = ti < len(occ_el)
        selfhit = np.zeros(len(ri), bool)
        selfhit[ok] = (occ_el[ti[ok]] == face_el[ri[ok]]) & (d[ok] < SELF_HIT_M)
        flip[ri[selfhit]] = True
    fn[flip] *= -1
    tri, xs, ys, bary = _raster(UV_all, W, H)
    pts = np.einsum('kj,kjd->kd', bary, V_all[tri])
    nrm = fn[tri]
    # one sample per texel (later triangles overwrite earlier ones, as in the original raster)
    key = ys * W + xs
    _, first = np.unique(key[::-1], return_index=True)
    keep = len(key) - 1 - first
    pts, nrm, xs, ys = pts[keep], nrm[keep], xs[keep], ys[keep]
    print(f'  ao: {flip.sum():,} faces flipped outward, {len(pts):,} texels x {RAYS} rays')
    occ = _occlusion(ray, pts, nrm, RAYS, AO_DIST_M)
    mask = np.zeros((H, W), bool)
    mask[ys, xs] = True
    shade = np.ones((H, W), np.float32)
    shade[ys, xs] = 1 - AO_STRENGTH * np.clip(occ * AO_GAIN, 0, 1)
    m = mask.astype(np.float32)
    blur = ndimage.gaussian_filter(shade * m, 1.6) / np.maximum(ndimage.gaussian_filter(m, 1.6), 1e-6)
    shade = np.where(mask, blur, 1.0)
    _, (iy, ix) = ndimage.distance_transform_edt(~mask, return_indices=True)
    shade = shade[iy, ix]
    print(f'  ao: mean shade {shade[mask].mean():.2f}')
    return (np.clip(shade, 0, 1) * 255).round().astype(np.uint8)


def bake_vertices(V, N, ray):
    """Per-vertex AO in [0,1] (1 = open)."""
    n = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
    occ = _occlusion(ray, V, n, MEP_RAYS, MEP_DIST_M, seed=11)
    return (1 - AO_STRENGTH * np.clip(occ * AO_GAIN, 0, 1)).astype(np.float32)


def save_jpg(img, path):
    Image.fromarray(img, 'L').save(path, quality=84, optimize=True)


def save_ramp(path):
    Image.fromarray(np.tile(np.arange(256, dtype=np.uint8), (4, 1)), 'L').save(path)
