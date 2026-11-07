"""Version 2: floors in the Architecture model where they are missing.

The Revit project keeps its floor slabs in the Structural model, so Architecture on its own has no
floors. Most rooms still read as floored from above, because the ceiling of the room below (or a
roof) sits just under the slab. In parts of the core there is nothing under the slab, and stairs,
railings and furniture there seem to hang in the air.

This copies the top of each structural floor slab into Architecture (1.5 cm above the slab), but only
where looking down from the slab finds no ceiling, roof or floor within 2.5 m: it never doubles an
existing floor. Each slab triangle is split until a piece is uniformly covered or uncovered (or
smaller than 30 cm), so the new floor keeps the slab's own edges and openings. Level 1 (slab on
grade) is left out: the model stands on the sheet there.

Runs on the uncompressed GLBs (after build_hospital.py, which calls it):
    python -I pipeline/add_floors.py
Rewrites pipeline/out/arch.raw.glb, src/assets/arch.glb, arch.json and scene.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fix_piping  # noqa: E402  (read_glb)

LIFT_M = 0.015          # above the slab top, so the two never z-fight when Structure is on too
REACH_M = 2.5           # a ceiling / roof / floor this far under the slab top already reads as floor
MIN_EDGE_M = 0.3
FLOOR_LOOK = 'floor'
FLOOR_COLOR = [0.62, 0.61, 0.58]     # linear, a light warm grey in the Simple palette
COVER_CATS = {'Ceilings', 'Roofs', 'Floors'}


def covered_fn(arch_prims, arch_table):
    tris = []
    for name, a, f in arch_prims:
        el = np.rint(a['_FEATURE_ID_0']).astype(np.int64)[f[:, 0]]
        cats = np.array([arch_table[e][1] for e in el])
        m = np.isin(cats, list(COVER_CATS))
        if m.any():
            tris.append(a['POSITION'].astype(np.float64)[f[m]])
    tris = np.concatenate(tris)
    ray = trimesh.ray.ray_pyembree.RayMeshIntersector(trimesh.Trimesh(
        tris.reshape(-1, 3), np.arange(len(tris) * 3).reshape(-1, 3), process=False))

    def covered(pts):
        o = pts + [0, 0.05, 0]
        loc, ri, _ = ray.intersects_location(o, np.tile([0.0, -1.0, 0.0], (len(o), 1)), multiple_hits=False)
        out = np.zeros(len(pts), bool)
        if len(ri):
            out[ri] = (o[ri, 1] - loc[:, 1]) <= REACH_M + 0.05
        return out
    return covered


def split_missing(tris, covered):
    """tris (n,3,3) -> the pieces with no floor under them (m,3,3)."""
    keep, todo = [], tris
    while len(todo):
        a, b, c = todo[:, 0], todo[:, 1], todo[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        g = (a + b + c) / 3
        samples = np.stack([a, b, c, ab, bc, ca, g], 1)               # 7 per triangle
        cov = covered(samples.reshape(-1, 3)).reshape(-1, 7)
        edge = np.max(np.stack([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1),
                                np.linalg.norm(a - c, axis=1)], 1), axis=1)
        none_cov = ~cov.any(1)
        all_cov = cov.all(1)
        small = edge < MIN_EDGE_M
        keep.append(todo[none_cov | (small & ~all_cov & ~cov[:, 6])])
        mixed = ~none_cov & ~all_cov & ~small
        if not mixed.any():
            break
        t = todo[mixed]
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        todo = np.concatenate([np.stack(x, 1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    return np.concatenate(keep) if keep else np.zeros((0, 3, 3))


def process(arch_prims, arch_table, struct_prims, struct_table, levels):
    covered = covered_fn(arch_prims, arch_table)
    lv_y = np.array([l['y'] for l in levels])
    new_tris, new_el = [], []
    rows = {}
    for name, a, f in struct_prims:
        P = a['POSITION'].astype(np.float64)
        el = np.rint(a['_FEATURE_ID_0']).astype(np.int64)[f[:, 0]]
        cats = np.array([struct_table[e][1] for e in el])
        t = P[f]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        top = (cats == 'Floors') & (n[:, 1] > 0.9) & (t[:, :, 1].mean(1) > 1.0)    # slab tops above Level 1
        for e in np.unique(el[top]):
            m = top & (el == e)
            pieces = split_missing(t[m], covered)
            if not len(pieces):
                continue
            if e not in rows:
                rid, _, fam, typ, level, _ = struct_table[e]
                y = float(pieces[:, :, 1].mean())
                lvl = levels[int(np.abs(lv_y - y).argmin())]['name']
                rows[e] = len(arch_table)
                arch_table.append([rid, 'Floors', 'Floor', f'Floor (on {typ})', level or lvl, ''])
            new_tris.append(pieces + [0, LIFT_M, 0])
            new_el.append(np.full(len(pieces), rows[e]))
    if not new_tris:
        print('floors: nothing missing')
        return arch_prims, 0.0
    tris = np.concatenate(new_tris).astype(np.float32)
    els = np.concatenate(new_el)
    up = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])[:, 1] < 0
    tris[up] = tris[up][:, ::-1]                                       # wind them facing up
    V = tris.reshape(-1, 3)
    k = len(V)
    srgb = [c ** (1 / 2.2) for c in FLOOR_COLOR]
    attrs = {
        'POSITION': V,
        'NORMAL': np.tile(np.array([[0, 1, 0]], np.float32), (k, 1)),
        'COLOR_0': np.tile(np.array([[*FLOOR_COLOR, 1.0]], np.float32), (k, 1)),
        '_FEATURE_ID_0': np.repeat(els, 3).astype(np.float32),
        'TEXCOORD_1': np.tile(np.array([[1.0, 0.5]], np.float32), (k, 1)),     # AO ramp: open
    }
    arch_prims = [p for p in arch_prims if p[0] != FLOOR_LOOK]
    arch_prims.append((FLOOR_LOOK, attrs, np.arange(k, dtype=np.uint32).reshape(-1, 3)))
    area = float(np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1).sum() / 2)
    print(f'floors: {len(tris):,} triangles, {area:,.0f} m2 of floor where none showed, '
          f'on {len(rows)} slabs ({", ".join(sorted({arch_table[r][4] for r in rows.values()}))}); srgb {srgb}')
    return arch_prims, area


def main():
    import build_hospital as bh
    scene = json.loads((bh.ASSETS / 'scene.json').read_text(encoding='utf-8'))
    arch_table = json.loads((bh.ASSETS / 'arch.json').read_text(encoding='utf-8'))
    struct_table = json.loads((bh.ASSETS / 'struct.json').read_text(encoding='utf-8'))
    arch_table = [r for r in arch_table if not (r[1] == 'Floors' and r[2] == 'Floor')]   # idempotent
    arch_prims = fix_piping.read_glb(bh.OUT / 'arch.raw.glb')
    struct_prims = fix_piping.read_glb(bh.OUT / 'struct.raw.glb')
    arch_prims, _ = process(arch_prims, arch_table, struct_prims, struct_table, scene['levels'])
    bh.write_glb(bh.OUT / 'arch.raw.glb', arch_prims)
    bh.pack(bh.OUT / 'arch.raw.glb', bh.ASSETS / 'arch.glb')
    (bh.ASSETS / 'arch.json').write_text(json.dumps(arch_table, separators=(',', ':')))
    scene['looks'][FLOOR_LOOK] = {'color': FLOOR_COLOR}
    for d in scene['disciplines']:
        if d['key'] == 'arch':
            d['ao'][FLOOR_LOOK] = 'tex/ao_ramp.png'
            d['bytes'] = (bh.ASSETS / 'arch.glb').stat().st_size
            d['triangles'] = int(sum(len(f) for _, _, f in arch_prims))
    (bh.ASSETS / 'scene.json').write_text(json.dumps(scene, indent=1))
    print(f"arch.glb {(bh.ASSETS / 'arch.glb').stat().st_size / 1e6:.2f} MB")


if __name__ == '__main__':
    main()
