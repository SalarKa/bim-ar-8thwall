"""Piping clean-up on the uncompressed piping GLB, before it is packed.

1. Fittings carry no system. Elbows, tees, reducers and caps come out of the DDC export without a
   piping system, so with "colour by system" on they stayed white between coloured runs. Each
   such element takes the system of the pipe it touches (the nearest system-coloured vertices,
   majority vote over the element).
2. Joints open up when pipes are thickened. The app draws pipe runs 9 cm fatter than modelled so
   they read at tabletop scale, by pushing each vertex out along its normal. Fittings are faceted
   (split normals at every edge), so their facets moved apart and left gaps at every joint. Normals
   are welded: every vertex at the same position gets the averaged normal, so the surface inflates
   as one piece.
3. Valves stay their real size. A 15 mm ball valve pushed out by 9 cm turned into a spiky blob, so
   pipe accessories move to the fixture material (chrome), which is not thickened.

Used by build_hospital.py; also runs on its own after a build:
    python -I pipeline/fix_piping.py      (rewrites pipeline/out/piping.raw.glb, src/assets/piping.glb/.json)
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

PIPE_LOOKS = {'copper', 'pipe_jacket', 'pipe_steel', 'pvc'}
WELD_M = 0.002
REACH_M = 0.6


def read_glb(path):
    """-> list of (name, {attr: array}, indices) as written by build_hospital.write_glb."""
    b = Path(path).read_bytes()
    jl = int.from_bytes(b[12:16], 'little')
    js = json.loads(b[20:20 + jl])
    base = 20 + jl + 8
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

    def acc(i):
        a = js['accessors'][i]
        v = js['bufferViews'][a['bufferView']]
        dt = {5126: np.float32, 5125: np.uint32}[a['componentType']]
        w = width[a['type']]
        arr = np.frombuffer(b, dt, a['count'] * w, base + v['byteOffset']).copy()
        return arr.reshape(a['count'], w) if w > 1 else arr

    out = []
    for mesh in js['meshes']:
        p = mesh['primitives'][0]
        attrs = {k: acc(i) for k, i in p['attributes'].items()}
        out.append((mesh['name'], attrs, acc(p['indices']).reshape(-1, 3)))
    return out


def move_accessories(prims, table):
    """Faces of PipeAccessory elements leave the thickened pipe materials for 'chrome'."""
    target = next((i for i, (n, _, _) in enumerate(prims) if n.split('@')[0] == 'chrome'), None)
    if target is None:
        return 0
    acc_el = np.array([i for i, r in enumerate(table) if r[1] == 'PipeAccessory'], np.int64)
    moved = 0
    for k, (name, a, f) in enumerate(prims):
        if name.split('@')[0] not in PIPE_LOOKS:
            continue
        el = np.rint(a['_FEATURE_ID_0']).astype(np.int64)
        sel = np.isin(el[f[:, 0]], acc_el)
        if not sel.any():
            continue
        tn, ta, tf = prims[target]
        used, inv = np.unique(f[sel], return_inverse=True)
        base = len(ta['POSITION'])
        for key in ta:
            ta[key] = np.concatenate([ta[key], a[key][used]])
        tf = np.concatenate([tf, (inv.reshape(-1, 3) + base).astype(np.uint32)])
        prims[target] = (tn, ta, tf)
        prims[k] = (name, a, f[~sel])
        moved += int(sel.sum())
    return moved


def process(prims, table, systems):
    """prims: [(name, attrs, faces)], modified in place. table rows get the system label."""
    moved = move_accessories(prims, table)
    fittings = np.array([i for i, r in enumerate(table) if r[1] == 'PipeFitting'], np.int64)
    assigned = {}
    for _ in range(4):                          # a run of fittings (elbow into tee) settles in a few passes
        src_p, src_s = [], []
        for name, a, _ in prims:
            if name.split('@')[0] in PIPE_LOOKS:
                m = a['_FEATURE_ID_1'] > 0.5
                src_p.append(a['POSITION'][m])
                src_s.append(np.rint(a['_FEATURE_ID_1'][m]).astype(np.int64))
        src_p, src_s = np.concatenate(src_p), np.concatenate(src_s)
        tree = cKDTree(src_p)
        votes = defaultdict(Counter)
        for name, a, _ in prims:
            if name.split('@')[0] not in PIPE_LOOKS:
                continue
            el = np.rint(a['_FEATURE_ID_0']).astype(np.int64)
            zero = (a['_FEATURE_ID_1'] < 0.5) & np.isin(el, fittings)   # pipes without a system stay unassigned
            if not zero.any():
                continue
            d, j = tree.query(a['POSITION'][zero], distance_upper_bound=REACH_M)
            ok = np.isfinite(d)
            for e, s in zip(el[zero][ok], src_s[j[ok]]):
                votes[e][s] += 1
        new = {e: c.most_common(1)[0][0] for e, c in votes.items()}
        if not new:
            break
        assigned.update(new)
        for name, a, _ in prims:
            if name.split('@')[0] not in PIPE_LOOKS:
                continue
            el = np.rint(a['_FEATURE_ID_0']).astype(np.int64)
            sy = a['_FEATURE_ID_1']
            for e in np.unique(el[sy < 0.5]):
                if e in new:
                    sy[(el == e) & (sy < 0.5)] = new[e]
    welded = 0
    for name, a, _ in prims:
        look = name.split('@')[0]
        if look not in PIPE_LOOKS:
            continue
        key = np.rint(a['POSITION'] / WELD_M).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        inv = inv.ravel()
        acc = np.zeros((inv.max() + 1, 3))
        np.add.at(acc, inv, a['NORMAL'])
        n = acc[inv]
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        n = np.where(ln > 1e-6, n / np.maximum(ln, 1e-12), a['NORMAL'])
        welded += int((np.abs(n - a['NORMAL']).sum(1) > 1e-3).sum())
        a['NORMAL'] = n.astype(np.float32)
    for name, a, _ in prims:                     # tap-to-identify shows the system too
        if name.split('@')[0] not in PIPE_LOOKS:
            continue
        el = np.rint(a['_FEATURE_ID_0']).astype(np.int64)
        sy = np.rint(a['_FEATURE_ID_1']).astype(np.int64)
        for e, s in zip(*np.unique(np.stack([el, sy], 1), axis=0).T):
            if s > 0 and 0 <= e < len(table) and not table[e][5] and s <= len(systems):
                table[e][5] = systems[s - 1][0]
    print(f'piping: {len(assigned):,} fittings took their pipe\'s system; {welded:,} normals welded; '
          f'{moved:,} valve faces kept at real size')
    return prims


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_hospital as bh
    raw = bh.OUT / 'piping.raw.glb'
    scene = json.loads((bh.ASSETS / 'scene.json').read_text(encoding='utf-8'))
    table = json.loads((bh.ASSETS / 'piping.json').read_text(encoding='utf-8'))
    prims = process(read_glb(raw), table, scene['systems'])
    bh.write_glb(raw, prims)
    bh.pack(raw, bh.ASSETS / 'piping.glb')
    (bh.ASSETS / 'piping.json').write_text(json.dumps(table, separators=(',', ':')))
    for d in scene['disciplines']:
        if d['key'] == 'piping':
            d['bytes'] = (bh.ASSETS / 'piping.glb').stat().st_size
    for look, hexc in bh.PIPE_HEX.items():
        scene['looks'][look]['hex'] = hexc
    (bh.ASSETS / 'scene.json').write_text(json.dumps(scene, indent=1))
    print(f"piping.glb {(bh.ASSETS / 'piping.glb').stat().st_size / 1e6:.2f} MB")


if __name__ == '__main__':
    main()
