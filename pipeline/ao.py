"""Baked ambient occlusion for the AR model.

Screen-space AO is too heavy for phones and clashes with 8th Wall's camera feed, so occlusion is
computed once here: every vertex casts cosine-weighted hemisphere rays (Embree via trimesh) and
nearby hits darken it. Large BIM faces are subdivided first so the shading has vertices to live on.
"""
import numpy as np
import trimesh

MAX_EDGE_M = 2.5     # subdivide big faces until no edge is longer than this (~8 ft)
SUBDIVIDE_GROUPS = {'walls', 'floors', 'roofs', 'ceilings'}  # framing etc. stay coarse: keeps the GLB small
AO_RAYS = 48
AO_DIST_M = 1.2      # only nearby geometry occludes: contact shadows in corners and under slabs
AO_STRENGTH = 0.6


def subdivide(group, verts, normals, faces):
    """Split long triangles; new vertices take the normal of the face they came from."""
    if group not in SUBDIVIDE_GROUPS:
        return verts, normals, faces
    edges = verts[faces[:, [1, 2, 0]]] - verts[faces]
    if np.linalg.norm(edges, axis=2).max() <= MAX_EDGE_M:
        return verts, normals, faces
    nv, nf, src = trimesh.remesh.subdivide_to_size(verts, faces, MAX_EDGE_M, max_iter=12, return_index=True)
    nn = np.zeros_like(nv)
    nn[: len(normals)] = normals
    face_n = normals[faces].mean(1)
    face_n /= np.maximum(np.linalg.norm(face_n, axis=1, keepdims=True), 1e-9)
    new = np.arange(len(nv)) >= len(verts)
    for k in range(3):  # each new vertex gets its source face's normal
        idx = nf[:, k]
        m = new[idx]
        nn[idx[m]] = face_n[src[m]]
    return nv, nn.astype(np.float32), nf


def _hemisphere(n):
    """Fixed cosine-weighted directions around +Z (stratified spiral)."""
    i = np.arange(n) + 0.5
    r = np.sqrt(i / n)
    phi = i * np.pi * (3 - np.sqrt(5))
    return np.stack([r * np.cos(phi), r * np.sin(phi), np.sqrt(1 - r * r)], 1)


def _basis(nrm):
    """Orthonormal tangent frames for an array of normals."""
    a = np.where(np.abs(nrm[:, [0]]) < 0.9, [[1, 0, 0]], [[0, 1, 0]])
    t = np.cross(nrm, a)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    b = np.cross(nrm, t)
    return t, b


def _occlusion(caster, dirs_local, vv, nn, tt, bb):
    d = (dirs_local[None, :, :1] * tt[:, None] + dirs_local[None, :, 1:2] * bb[:, None]
         + dirs_local[None, :, 2:] * nn[:, None]).reshape(-1, 3)
    o = np.repeat(vv + nn * 0.004, len(dirs_local), axis=0)
    loc, ray_idx, _ = caster.intersects_location(o, d, multiple_hits=False)
    contrib = np.zeros(len(o))
    if len(ray_idx):
        dist = np.linalg.norm(loc - o[ray_idx], axis=1)
        near = dist < AO_DIST_M
        contrib[ray_idx[near]] = 1 - dist[near] / AO_DIST_M
    return contrib.reshape(-1, len(dirs_local)).mean(1)


def bake(group_meshes, occluder_names):
    """group_meshes: {name: (verts, normals)} in model space. Returns {name: uint8 AO per vertex}."""
    occ = [trimesh.Trimesh(v, f, process=False) for name, (v, n, f) in group_meshes.items() if name in occluder_names]
    scene = trimesh.util.concatenate(occ)
    caster = trimesh.ray.ray_pyembree.RayMeshIntersector(scene)
    dirs_local = _hemisphere(AO_RAYS)
    out = {}
    for name, (v, n, _) in group_meshes.items():
        n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        t, b = _basis(n)
        occlusion = np.zeros(len(v))
        chunk = max(1, 400000 // AO_RAYS)
        for s in range(0, len(v), chunk):
            sides = [_occlusion(caster, dirs_local, v[s:s + chunk], sign * n[s:s + chunk],
                                t[s:s + chunk], b[s:s + chunk]) for sign in (1, -1)]
            # some exported faces point into their element; the open side is the visible one
            occlusion[s:s + chunk] = np.minimum(*sides)
        shade = 1 - AO_STRENGTH * np.clip(occlusion * 1.4, 0, 1)
        out[name] = np.round(shade * 255).astype(np.uint8)
        print(f'  ao {name:15s} {len(v):7d} verts  mean {shade.mean():.2f}')
    return out
