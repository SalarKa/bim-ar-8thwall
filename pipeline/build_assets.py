"""Convert the DDC Revit export (Collada + element spreadsheet) into AR web assets.

Outputs (under src/assets):
  model.glb          merged meshes, one per display group, Y-up, meters, origin = plan centre at Level 1
  model-index.json   element metadata, levels, and triangle ranges for tap-to-identify
  plans/*.svg        per-level cut plans (2D map); each element is a <g data-id="...">
Also writes pipeline/out/plan_geom.json for the printable poster.

Usage: python -I pipeline/build_assets.py <model.dae> <model.xlsx>
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import collada
import numpy as np
import pandas as pd
import trimesh
from shapely.geometry import MultiLineString, Polygon
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
OUT = ROOT / 'pipeline' / 'out'
FT = 0.3048
CUT_HEIGHT_FT = 4.0  # plan cut plane above each level, like a Revit plan view

LEVELS = [('Level 1', 0.0), ('Level 2', 12.0), ('Level 3', 24.0), ('Roof', 36.0)]
SKIP_CATEGORIES = {'OST_Levels', 'OST_Grids'}
GLASS_MATERIALS = {'ID33-material'}

# display group -> (label, RGBA, plan fill)
GROUPS = {
    'walls': ('Walls', (0.91, 0.89, 0.85, 1), '#3b3b3b'),
    'glass': ('Glazing', (0.50, 0.72, 0.85, 0.40), '#7fb8d9'),
    'mullions': ('Curtain wall mullions', (0.36, 0.40, 0.44, 1), '#5b6670'),
    'framing': ('Structural framing', (0.78, 0.33, 0.24, 1), '#c8553d'),
    'struct_columns': ('Structural columns', (0.69, 0.29, 0.23, 1), '#b04a3a'),
    'columns': ('Columns', (0.61, 0.56, 0.50, 1), '#9c8f80'),
    'doors': ('Doors', (0.66, 0.46, 0.31, 1), '#a8754f'),
    'windows': ('Windows', (0.43, 0.48, 0.52, 1), '#6e7b85'),
    'plumbing': ('Plumbing fixtures', (0.87, 0.90, 0.93, 1), '#8aa4b8'),
    'ceilings': ('Ceilings', (0.96, 0.95, 0.92, 1), '#d8d2c6'),
    'floors': ('Floors', (0.74, 0.72, 0.68, 1), '#e9e6df'),
    'roofs': ('Roofs', (0.54, 0.56, 0.58, 1), '#8a8f94'),
    'stairs': ('Stairs & railings', (0.79, 0.72, 0.61, 1), '#9b8a70'),
    'other': ('Other', (0.63, 0.63, 0.63, 1), '#8c8c8c'),
}
CATEGORY_GROUP = {
    'OST_Walls': 'walls', 'OST_CurtainWallPanels': 'glass', 'OST_CurtainWallMullions': 'mullions',
    'OST_StructuralFraming': 'framing', 'OST_StructuralColumns': 'struct_columns',
    'OST_Columns': 'columns', 'OST_Doors': 'doors', 'OST_Windows': 'windows',
    'OST_PlumbingFixtures': 'plumbing', 'OST_Ceilings': 'ceilings', 'OST_Floors': 'floors',
    'OST_Roofs': 'roofs', 'OST_Fascia': 'roofs', 'OST_Stairs': 'stairs',
    'OST_StairsRailing': 'stairs', 'OST_StructuralFoundation': 'floors',
}
# Plan symbology: cut elements get poché, below-cut elements get thin projected outlines
CUT_FILL_GROUPS = {'walls', 'struct_columns', 'columns'}
PROJECT_GROUPS = {'floors', 'plumbing', 'stairs', 'doors', 'roofs'}
INFO_PARAMS = ['Family', 'Type Name', 'Mark', 'Base Constraint', 'Top Constraint', 'Height',
               'Unconnected Height', 'Length', 'Width', 'Area', 'Volume', 'Structural Material',
               'Phase Created', 'Comments']


def pretty_category(cat):
    return cat.replace('OST_', '').replace('StructuralFraming', 'Structural Framing') \
        .replace('StructuralColumns', 'Structural Columns').replace('CurtainWall', 'Curtain Wall ') \
        .replace('PlumbingFixtures', 'Plumbing Fixtures').replace('StairsRailing', 'Railings') \
        .replace('GenericModel', 'Generic Model').replace('StructuralFoundation', 'Foundation').strip()


def load_elements(xlsx):
    df = pd.read_excel(xlsx).set_index('ID')
    cols = {c.split(' :')[0]: c for c in df.columns if not c.startswith('[')}
    return df, cols


def read_geometry(dae_path):
    """Yield (element_id, material_id, verts[N,3] ft, normals[N,3], faces[M,3]) per triangle set."""
    dae = collada.Collada(str(dae_path), ignore=[collada.common.DaeUnsupportedError,
                                                 collada.common.DaeBrokenRefError])

    def walk(node, matrix, eid):
        m = matrix @ node.matrix if hasattr(node, 'matrix') else matrix
        nid = getattr(node, 'id', None)
        if nid and str(nid).isdigit():
            eid = int(nid)
        for child in getattr(node, 'children', []):
            if isinstance(child, collada.scene.GeometryNode):
                for prim in child.geometry.primitives:
                    if not isinstance(prim, (collada.triangleset.TriangleSet,
                                             collada.polylist.Polylist, collada.polygons.Polygons)):
                        continue
                    tri = prim if isinstance(prim, collada.triangleset.TriangleSet) else prim.triangleset()
                    if len(tri.vertex_index) == 0:
                        continue
                    yield eid, tri.material, *bake(tri, m)
            else:
                yield from walk(child, m, eid)

    for n in dae.scene.nodes:
        yield from walk(n, np.eye(4), None)


def bake(tri, m):
    vi = tri.vertex_index.reshape(-1)
    if tri.normal is not None and tri.normal_index is not None:
        ni = tri.normal_index.reshape(-1)
        keys, inv = np.unique(np.stack([vi, ni], 1), axis=0, return_inverse=True)
        verts, normals = tri.vertex[keys[:, 0]], tri.normal[keys[:, 1]]
    else:
        keys, inv = np.unique(vi, return_inverse=True)
        verts, normals = tri.vertex[keys], None
    verts = verts @ m[:3, :3].T + m[:3, 3]
    if normals is not None:
        normals = normals @ m[:3, :3].T
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
    return verts, normals, inv.reshape(-1, 3)


def level_for(level_param, zmin):
    names = [n for n, _ in LEVELS]
    if isinstance(level_param, str) and level_param in names:
        return level_param
    best = LEVELS[0][0]
    for name, elev in LEVELS:
        if zmin >= elev - 0.75:
            best = name
    return best


def main(dae_path, xlsx_path):
    df, cols = load_elements(xlsx_path)
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / 'plans').mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    pieces = defaultdict(list)  # group -> [(eid, verts, normals, faces)]
    per_element = defaultdict(list)  # eid -> [(verts, faces, group)]
    for eid, mat, verts, normals, faces in read_geometry(dae_path):
        cat = df.at[eid, cols['Category']] if eid in df.index else None
        if cat in SKIP_CATEGORIES or eid is None:
            continue
        group = 'glass' if mat in GLASS_MATERIALS else CATEGORY_GROUP.get(cat, 'other')
        pieces[group].append((eid, verts, normals, faces))
        per_element[eid].append((verts, faces, group))

    allv = np.vstack([v for g in pieces.values() for _, v, _, _ in g])
    cx, cy = (allv[:, 0].min() + allv[:, 0].max()) / 2, (allv[:, 1].min() + allv[:, 1].max()) / 2
    print(f'bbox ft x[{allv[:,0].min():.1f},{allv[:,0].max():.1f}] y[{allv[:,1].min():.1f},'
          f'{allv[:,1].max():.1f}] z[{allv[:,2].min():.1f},{allv[:,2].max():.1f}] centre ({cx:.2f},{cy:.2f})')

    def to_gl(v):  # Revit ft, Z-up -> meters, Y-up, north = -Z
        return np.stack([(v[:, 0] - cx) * FT, v[:, 2] * FT, -(v[:, 1] - cy) * FT], 1)

    def to_gl_dir(n):
        return np.stack([n[:, 0], n[:, 2], -n[:, 1]], 1)

    # ---------- element metadata ----------
    elements = {}
    for eid, parts in per_element.items():
        v = np.vstack([p[0] for p in parts])
        row = df.loc[eid]
        cat = row[cols['Category']]
        info = {}
        for p in INFO_PARAMS:
            c = cols.get(p)
            if c is None:
                continue
            val = row[c]
            if isinstance(val, float) and (math.isnan(val) or (val == 0 and c.endswith(': Double'))):
                continue
            if c.endswith(': Double'):
                unit = {'Area': ' ft²', 'Volume': ' ft³'}.get(p, ' ft')
                val = f'{val:.2f}{unit}'
            elif isinstance(val, float) and val.is_integer():
                val = int(val)
            info[p] = str(val)
        elements[str(eid)] = {
            'category': pretty_category(cat),
            'name': str(row[cols['Name']]) if isinstance(row[cols['Name']], str) else '',
            'level': level_for(row[cols['Level']], v[:, 2].min()),
            'group': parts[0][2],
            'info': info,
        }

    # ---------- GLB: one mesh per display group, triangle ranges per element ----------
    scene = trimesh.Scene()
    ranges = {}
    for group, items in pieces.items():
        label, rgba, _ = GROUPS[group]
        items.sort(key=lambda t: t[0])
        V, N, F, rng, offset, tri_start = [], [], [], [], 0, 0
        for eid, verts, normals, faces in items:
            V.append(to_gl(verts))
            N.append(to_gl_dir(normals) if normals is not None else np.zeros_like(verts))
            F.append(faces + offset)
            if rng and rng[-1][0] == eid and rng[-1][1] + rng[-1][2] == tri_start:
                rng[-1][2] += len(faces)
            else:
                rng.append([eid, tri_start, len(faces)])
            offset += len(verts)
            tri_start += len(faces)
        mesh = trimesh.Trimesh(np.vstack(V).astype(np.float32), np.vstack(F), process=False,
                               vertex_normals=np.vstack(N).astype(np.float32))
        mat = trimesh.visual.material.PBRMaterial(
            name=group, baseColorFactor=[int(c * 255) for c in rgba], metallicFactor=0.0,
            roughnessFactor=0.85, doubleSided=True, alphaMode='BLEND' if rgba[3] < 1 else 'OPAQUE')
        mesh.visual = trimesh.visual.TextureVisuals(material=mat)
        scene.add_geometry(mesh, geom_name=group, node_name=group)
        ranges[group] = rng
        print(f'  {group:15s} {len(items):5d} pieces {tri_start:7d} tris')
    glb = scene.export(file_type='glb')
    (ASSETS / 'model.glb').write_bytes(glb)
    print(f'model.glb {len(glb)/1e6:.1f} MB')

    # ---------- 2D plans ----------
    xmin, xmax = allv[:, 0].min() - cx, allv[:, 0].max() - cx
    ymin, ymax = allv[:, 1].min() - cy, allv[:, 1].max() - cy
    pad = 6.0
    plan_geom = {'bounds_ft': [xmin, ymin, xmax, ymax], 'levels': []}
    levels_out = []
    for li, (lname, elev) in enumerate(LEVELS):
        cut = elev + (2.0 if lname == 'Roof' else CUT_HEIGHT_FT)  # parapet is only 4 ft tall
        top = LEVELS[li + 1][1] if li + 1 < len(LEVELS) else elev + 6
        cut_items, proj_items = [], []
        for eid, parts in per_element.items():
            e = elements[str(eid)]
            for verts, faces, group in parts:
                zmin, zmax = verts[:, 2].min(), verts[:, 2].max()
                if zmin <= cut <= zmax:
                    tm = trimesh.Trimesh(verts, faces, process=False)
                    segs = trimesh.intersections.mesh_plane(tm, [0, 0, 1], [0, 0, cut])
                    if len(segs):
                        cut_items.append((eid, group, segs[:, :, :2]))
                elif group in PROJECT_GROUPS and elev - 1.5 <= zmax <= cut and \
                        (e['level'] == lname or group == 'floors'):
                    tris = [Polygon(t) for t in verts[faces][:, :, :2] if Polygon(t).area > 1e-4]
                    if tris:
                        proj_items.append((eid, group, unary_union(tris).buffer(0.02).buffer(-0.02)))
        svg, geom = render_plan_svg(lname, cut_items, proj_items, cx, cy, (xmin, ymin, xmax, ymax), pad)
        fname = f'plans/{lname.lower().replace(" ", "-")}.svg'
        (ASSETS / fname).write_text(svg, encoding='utf-8')
        plan_geom['levels'].append({'name': lname, **geom})
        levels_out.append({'name': lname, 'elevation_m': elev * FT, 'cut_m': cut * FT,
                           'top_m': top * FT, 'plan': f'assets/{fname}'})
        print(f'  plan {lname}: {len(cut_items)} cut, {len(proj_items)} projected')

    index = {
        'source': Path(dae_path).name.replace('_rvt.dae', '.rvt'),
        'units': 'meters', 'up': 'Y', 'north': '-Z',
        'origin_revit_ft': [cx, cy, 0.0],
        'bounds_m': {'min': [xmin * FT, allv[:, 2].min() * FT, -ymax * FT],
                     'max': [xmax * FT, allv[:, 2].max() * FT, -ymin * FT]},
        'levels': levels_out,
        'groups': {g: {'label': GROUPS[g][0], 'color': GROUPS[g][2]} for g in pieces},
        'ranges': ranges,
        'elements': elements,
    }
    (ASSETS / 'model-index.json').write_text(json.dumps(index, separators=(',', ':')), encoding='utf-8')
    (OUT / 'plan_geom.json').write_text(json.dumps(plan_geom), encoding='utf-8')
    print('elements', len(elements))


def fmt(x):
    return f'{x:.2f}'.rstrip('0').rstrip('.')


def ring_path(coords, cx, cy):
    pts = [(x - cx, -(y - cy)) for x, y in coords]
    return 'M' + 'L'.join(f'{fmt(x)},{fmt(y)}' for x, y in pts) + 'Z'


def poly_path(geom, cx, cy):
    polys = [geom] if geom.geom_type == 'Polygon' else [g for g in getattr(geom, 'geoms', []) if g.geom_type == 'Polygon']
    d = ''
    for p in polys:
        d += ring_path(p.exterior.coords, cx, cy)
        for r in p.interiors:
            d += ring_path(r.coords, cx, cy)
    return d


def render_plan_svg(lname, cut_items, proj_items, cx, cy, bounds, pad):
    xmin, ymin, xmax, ymax = bounds
    vb = (xmin - pad, -ymax - pad, xmax - xmin + 2 * pad, ymax - ymin + 2 * pad)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{" ".join(fmt(v) for v in vb)}" '
           f'data-level="{lname}">',
           '<style>.p{fill:none;stroke:#8d969e;stroke-width:.12}.f{fill:#f1eee7;stroke:#c9c3b7;stroke-width:.1}'
           '.c{stroke:#222;stroke-width:.18;fill:none;stroke-linecap:round}'
           '.k{fill:#3b3b3b;stroke:#222;stroke-width:.08}</style>']
    geom = {'projected': [], 'cut': []}
    # floors first (background), then projections, then cut elements on top
    for eid, group, shape in sorted(proj_items, key=lambda t: t[1] != 'floors'):
        d = poly_path(shape, cx, cy)
        if not d:
            continue
        cls = 'f' if group == 'floors' else 'p'
        out.append(f'<g data-id="{eid}" data-group="{group}"><path class="{cls}" d="{d}"/></g>')
        geom['projected'].append({'id': eid, 'group': group, 'd': d})
    for eid, group, segs in cut_items:
        lines = [((a[0], a[1]), (b[0], b[1])) for a, b in segs]
        filled = ''
        if group in CUT_FILL_GROUPS:
            polys = list(polygonize(unary_union(MultiLineString(
                [[(round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))] for a, b in lines]))))
            if polys:
                filled = poly_path(unary_union(polys), cx, cy)
        color = GROUPS[group][2]
        if filled:
            out.append(f'<g data-id="{eid}" data-group="{group}"><path class="k" style="fill:{color}" d="{filled}"/></g>')
            geom['cut'].append({'id': eid, 'group': group, 'd': filled, 'fill': True})
        else:
            d = ''.join(f'M{fmt(a[0]-cx)},{fmt(-(a[1]-cy))}L{fmt(b[0]-cx)},{fmt(-(b[1]-cy))}' for a, b in lines)
            out.append(f'<g data-id="{eid}" data-group="{group}"><path class="c" style="stroke:{color}" d="{d}"/></g>')
            geom['cut'].append({'id': eid, 'group': group, 'd': d, 'fill': False})
    out.append('</svg>')
    return '\n'.join(out), geom



if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
