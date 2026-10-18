"""Baked ambient occlusion as a lightmap texture.

Per-vertex AO smeared shading across large BIM faces and, with its "take the more open side"
shortcut, sometimes shaded the hidden side of a corner. This version:
  1. unwraps the whole model into one UV atlas (xatlas), so every surface owns texels;
  2. orients each face outward (a short ray from the face that hits its own element means the
     exported normal points into the solid, so it is flipped);
  3. rasterises the atlas and traces cosine-weighted hemisphere rays per texel (Embree);
  4. denoises within charts and dilates into the padding so bilinear filtering has no seams.
The app samples the texture through its own UV attribute and multiplies the material colour.
"""
import numpy as np
import trimesh
import xatlas
from PIL import Image
from scipy import ndimage

ATLAS_MAX = 4096
TEXELS_PER_M = 12.0  # ~8 cm per texel
AO_RAYS = 48
AO_DIST_M = 2.5       # occluders within this distance darken: corners, slab edges, reveals, under eaves
AO_STRENGTH = 0.8
AO_GAIN = 1.35
GROUND_MARGIN_M = 40  # the poster/site is a ground plane: the building's base gets contact shadow
SELF_HIT_M = 0.6      # a face whose normal ray re-enters its own element this soon points inward


def unwrap(groups):
    """groups: {name: (V, N, F)} -> {name: (V, N, F, UV)} with UV in [0,1] atlas space.
    xatlas keeps triangle order (only vertices are split at seams), so face ranges stay valid."""
    atlas = xatlas.Atlas()
    names = list(groups)
    for name in names:
        V, N, F = groups[name]
        atlas.add_mesh(V.astype(np.float32), F.astype(np.uint32), N.astype(np.float32))
    pack = xatlas.PackOptions()
    pack.resolution = ATLAS_MAX
    pack.texels_per_unit = TEXELS_PER_M
    pack.padding = 4
    pack.bilinear = True
    pack.rotate_charts = True
    atlas.generate(pack_options=pack)
    assert atlas.atlas_count == 1, atlas.atlas_count
    out = {}
    for i, name in enumerate(names):
        V, N, F = groups[name]
        vmap, idx, uv = atlas[i]
        idx = idx.reshape(-1, 3)
        assert len(idx) == len(F)
        out[name] = (V[vmap], N[vmap], idx, uv)
    out['__size__'] = (atlas.width, atlas.height)
    print(f'  atlas {atlas.width}x{atlas.height}  {atlas.texels_per_unit:.1f} texels/m  '
          f'utilisation {atlas.utilization:.2f}')
    return out


def _face_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


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
    c, s = np.cos(angle)[:, None], np.sin(angle)[:, None]   # per-texel rotation breaks up banding
    return c * t + s * b, -s * t + c * b


def bake(unwrapped, face_element, occluder_names):
    """unwrapped: {name: (V, N, F, UV)}; face_element: {name: element id per face}.
    Returns a uint8 (H, W) AO image."""
    W, H = unwrapped.pop('__size__')
    names = list(unwrapped)
    tri_V, tri_e, tri_occ = [], [], []
    for name in names:
        V, _, F, _ = unwrapped[name]
        tri_V.append(V[F])
        tri_e.append(face_element[name])
        tri_occ.append(np.full(len(F), name in occluder_names))
    allT = np.concatenate(tri_V)
    allE = np.concatenate(tri_e)
    occ_mask = np.concatenate(tri_occ)
    occT = allT[occ_mask]
    lo, hi = allT.reshape(-1, 3).min(0), allT.reshape(-1, 3).max(0)
    g0, g1, gy = lo - GROUND_MARGIN_M, hi + GROUND_MARGIN_M, 0.0   # Level 1 is y = 0
    ground = np.array([[[g0[0], gy, g0[2]], [g1[0], gy, g1[2]], [g1[0], gy, g0[2]]],
                       [[g0[0], gy, g0[2]], [g0[0], gy, g1[2]], [g1[0], gy, g1[2]]]], np.float32)
    occT = np.concatenate([occT, ground])
    scene = trimesh.Trimesh(occT.reshape(-1, 3), np.arange(len(occT) * 3).reshape(-1, 3), process=False)
    occ_elem = np.concatenate([allE[occ_mask], [-1, -1]])
    caster = trimesh.ray.ray_pyembree.RayMeshIntersector(scene)

    # --- outward orientation per face
    fn = _face_normals(allT.reshape(-1, 3), np.arange(len(allT) * 3).reshape(-1, 3))
    cen = allT.mean(1)
    loc, ray_i, tri_i = caster.intersects_location(cen + fn * 2e-3, fn, multiple_hits=False)
    flip = np.zeros(len(allT), bool)
    if len(ray_i):
        d = np.linalg.norm(loc - cen[ray_i], axis=1)
        self_hit = (occ_elem[tri_i] == allE[ray_i]) & (d < SELF_HIT_M)
        flip[ray_i[self_hit]] = True
    fn[flip] *= -1
    print(f'  ao: {flip.sum()} of {len(flip)} faces flipped outward')

    # --- rasterise the atlas: texel -> (position, normal)
    P = np.zeros((H, W, 3), np.float32)
    Nm = np.zeros((H, W, 3), np.float32)
    mask = np.zeros((H, W), bool)
    base = 0
    for name in names:
        V, _, F, UV = unwrapped[name]
        px = UV[F] * [W, H]                      # (m, 3, 2) texel coords
        for k in range(len(F)):
            t = px[k]
            x0, y0 = np.floor(t.min(0)).astype(int)
            x1, y1 = np.ceil(t.max(0)).astype(int)
            xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1 + 1, W)), np.arange(max(y0, 0), min(y1 + 1, H)))
            if xs.size == 0:
                continue
            cx, cy = xs.ravel() + 0.5, ys.ravel() + 0.5
            (ax, ay), (bx, by), (qx, qy) = t
            den = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
            if abs(den) < 1e-12:
                continue
            l0 = ((by - qy) * (cx - qx) + (qx - bx) * (cy - qy)) / den
            l1 = ((qy - ay) * (cx - qx) + (ax - qx) * (cy - qy)) / den
            l2 = 1 - l0 - l1
            tol = -0.6 / max(np.abs([ax - bx, ay - by, bx - qx, by - qy]).max(), 1.0)  # ~half a texel
            ins = (l0 >= tol) & (l1 >= tol) & (l2 >= tol)
            if not ins.any():
                continue
            l = np.clip(np.stack([l0[ins], l1[ins], l2[ins]], 1), 0, 1)
            l /= l.sum(1, keepdims=True)
            tri = V[F[k]]
            P[ys.ravel()[ins], xs.ravel()[ins]] = l @ tri
            Nm[ys.ravel()[ins], xs.ravel()[ins]] = fn[base + k]
            mask[ys.ravel()[ins], xs.ravel()[ins]] = True
        base += len(F)
    yy, xx = np.nonzero(mask)
    pts, nrm = P[yy, xx], Nm[yy, xx]
    print(f'  ao: {len(pts):,} texels')

    # --- trace
    dirs = _hemisphere(AO_RAYS)
    rng = np.random.default_rng(7)
    occlusion = np.zeros(len(pts))
    chunk = max(1, 1_500_000 // AO_RAYS)
    for s in range(0, len(pts), chunk):
        p, n = pts[s:s + chunk], nrm[s:s + chunk]
        t, b = _basis(n, rng.uniform(0, 2 * np.pi, len(p)))
        d = (dirs[None, :, :1] * t[:, None] + dirs[None, :, 1:2] * b[:, None]
             + dirs[None, :, 2:] * n[:, None]).reshape(-1, 3)
        o = np.repeat(p + n * 4e-3, AO_RAYS, axis=0)
        loc, ri, _ = caster.intersects_location(o, d, multiple_hits=False)
        contrib = np.zeros(len(o))
        if len(ri):
            dist = np.linalg.norm(loc - o[ri], axis=1)
            near = dist < AO_DIST_M
            contrib[ri[near]] = (1 - dist[near] / AO_DIST_M) ** 0.75
        occlusion[s:s + chunk] = contrib.reshape(-1, AO_RAYS).mean(1)

    shade = np.ones((H, W), np.float32)
    shade[yy, xx] = 1 - AO_STRENGTH * np.clip(occlusion * AO_GAIN, 0, 1)
    # denoise inside the charts (normalised convolution), then dilate into the padding
    m = mask.astype(np.float32)
    blur = ndimage.gaussian_filter(shade * m, 1.1) / np.maximum(ndimage.gaussian_filter(m, 1.1), 1e-6)
    shade = np.where(mask, blur, 1.0)
    _, (iy, ix) = ndimage.distance_transform_edt(~mask, return_indices=True)
    shade = shade[iy, ix]
    print(f'  ao: mean shade {shade[mask].mean():.2f}')
    return (np.clip(shade, 0, 1) * 255).round().astype(np.uint8)


def save(img, path):
    Image.fromarray(img, 'L').save(path, optimize=True)
