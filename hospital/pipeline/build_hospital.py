"""Convert the hospital Revit models (DDC export: Collada + element spreadsheet) into
per-discipline AR assets.

Each source element is sorted into a discipline (Architecture, Structure, HVAC, Piping & Plumbing,
Electrical, Fire Alarm, Site & Landscape) and, per face, into a "look", the enhanced PBR material
it gets in the app. Every vertex also keeps its original Revit shading colour (COLOR_0), so the app
can switch between the raw Revit export ("Original") and the enhanced materials, or show both
side by side.

Outputs (src/assets):
  <disc>.glb        one mesh per look; Y-up, metres, origin = building centre at Level 1;
                    meshopt-compressed with gltfpack; custom attributes _EL (element index for
                    tap-to-identify) and _SYS (MEP system index for "colour by system")
  <disc>.json       element table for that file: [revit id, category, family, type, level, system]
  scene.json        disciplines, looks (PBR parameters), MEP systems, levels, site extent,
                    and where the planting / vehicles stand (for make_entourage.py)
Also writes pipeline/out/plan.json (footprint outlines per level, for the poster).

Usage: python -I pipeline/build_hospital.py [source dir with 2018_Hospital_<Model>_rvt.dae/.xlsx]
"""
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import collada
import fast_simplification
import numpy as np
import pandas as pd
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I
from fast_xlsx import read_columns  # noqa: E402
import ao_bake  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
OUT = ROOT / 'pipeline' / 'out'
CACHE = ROOT / 'pipeline' / 'cache'
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / 'DDC_Tools' / 'work' / 'hospital'
GLTFPACK = ROOT / 'node_modules' / '.bin' / 'gltfpack.cmd'
FT = 0.3048

MODELS = ['Architectural', 'Structural', 'HVAC']
COLUMNS = {'ID', 'Category', 'Family Name', 'Type Name', 'Level', 'Name', 'BoundingBoxMin_X', 'BoundingBoxMin_Y',
           'BoundingBoxMin_Z', 'BoundingBoxMax_X', 'BoundingBoxMax_Y', 'BoundingBoxMax_Z'}

DISCIPLINES = [  # key, label, legend colour
    ('arch', 'Architecture', '#d9d3c7'),
    ('struct', 'Structure', '#c8553d'),
    ('hvac', 'HVAC', '#3f8fd2'),
    ('piping', 'Piping & Plumbing', '#2fa36b'),
]
LEVELS = [('Level 1', 0), ('Level 2', 20), ('Level 3', 36), ('Level 4', 52), ('Level 5', 68),
          ('Level 6', 84), ('Level 7A', 100), ('Roof', 111)]

# Enhanced PBR looks. tex = texture key (src/assets/tex/<key>_diff/_nor.jpg), tinted by color.
LOOKS = {
    'paint': dict(color=[0.9, 0.89, 0.86], rough=0.9),
    'brick': dict(tex='brick', color=[1.0, 0.96, 0.94], rough=0.92, normal=1.0),
    # metal panels and mullions keep the designer's Revit colour (vertex colour) under the PBR finish
    'metal_panel': dict(tex='metal_panel', vcolor=True, color=[1.55, 1.55, 1.55], rough=0.4, metal=0.4, normal=0.7, env=0.9),
    'cmu': dict(tex='cmu', color=[1.55, 1.5, 1.42], rough=0.95, normal=0.8),
    'limestone': dict(tex='concrete', color=[1.12, 1.03, 0.88], rough=0.8, normal=0.5),
    'concrete': dict(tex='concrete', color=[1.0, 0.99, 0.97], rough=0.85, normal=0.6),
    'glass': dict(color=[0.16, 0.24, 0.3], rough=0.02, opacity=0.55, env=2.2),
    'spandrel': dict(color=[0.1, 0.13, 0.16], rough=0.05, metal=0.3, env=1.8),
    'channel_glass': dict(color=[0.82, 0.85, 0.86], rough=0.3, opacity=0.78, env=1.2),
    'mullion': dict(vcolor=True, color=[0.92, 0.92, 0.92], rough=0.3, metal=0.75),
    'aluminum': dict(color=[0.75, 0.77, 0.79], rough=0.3, metal=0.9),
    'steel_brushed': dict(color=[0.8, 0.8, 0.8], rough=0.25, metal=1.0),
    'door_metal': dict(color=[0.55, 0.57, 0.6], rough=0.4, metal=0.6),
    'wood': dict(tex='wood', rough=0.5, normal=0.5),
    'wood_rough': dict(tex='wood_rough', rough=0.8, normal=0.6),
    'grass': dict(tex='grass', color=[0.72, 0.95, 0.5], rough=1.0, normal=0.8),
    'pavers': dict(tex='pavers', rough=0.9, normal=0.8),
    'asphalt': dict(tex='asphalt', color=[0.36, 0.36, 0.38], rough=0.9, normal=0.8),
    'gravel': dict(tex='gravel', rough=1.0, normal=1.0),
    'membrane': dict(color=[0.55, 0.56, 0.57], rough=0.85),
    'ceiling': dict(tex='ceiling', rough=0.95, normal=0.5),
    'porcelain': dict(color=[0.96, 0.96, 0.95], rough=0.1),
    'chrome': dict(color=[0.92, 0.92, 0.92], rough=0.1, metal=1.0),
    'solar': dict(tex='solar', rough=0.3, metal=0.2, env=0.7),
    'tinted': dict(vcolor=True, rough=0.6),
    'steel': dict(color=[0.47, 0.49, 0.51], rough=0.5, metal=0.7),
    'column_steel': dict(color=[0.45, 0.27, 0.16], rough=0.55, metal=0.25),       # brown primed steel columns
    'concrete_gray': dict(tex='concrete', color=[0.74, 0.76, 0.79], rough=0.88, normal=0.5),  # neutral grey structure
    'duct': dict(color=[0.78, 0.8, 0.82], rough=0.38, metal=0.95),
    'duct_flex': dict(color=[0.85, 0.85, 0.83], rough=0.45, metal=0.8),
    'pipe_jacket': dict(color=[0.93, 0.93, 0.9], rough=0.55),
    'copper': dict(color=[0.86, 0.52, 0.34], rough=0.28, metal=1.0),
    'pipe_steel': dict(color=[0.3, 0.31, 0.33], rough=0.5, metal=0.7),
    'pvc': dict(color=[0.9, 0.9, 0.88], rough=0.4),
    'equipment': dict(color=[0.74, 0.75, 0.72], rough=0.45, metal=0.4),
    'diffuser': dict(color=[0.94, 0.94, 0.93], rough=0.4, metal=0.2),
    'light_fixture': dict(color=[0.96, 0.96, 0.95], rough=0.35, emissive=[1.0, 0.97, 0.9], emissive_k=0.35),
    'plastic_white': dict(color=[0.93, 0.93, 0.92], rough=0.45),
    'cable_tray': dict(color=[0.72, 0.74, 0.76], rough=0.42, metal=0.9),
    'conduit': dict(color=[0.7, 0.72, 0.74], rough=0.35, metal=0.9),
    'panel_gray': dict(color=[0.6, 0.62, 0.62], rough=0.45, metal=0.5),
    'fire_red': dict(color=[0.78, 0.08, 0.07], rough=0.35),
    'helipad': dict(tex='concrete', color=[0.72, 0.72, 0.72], rough=0.8, normal=0.4),
}

SKIP_CATEGORIES = {'OST_Grids', 'OST_Levels', 'OST_Lines', 'OST_SketchLines', 'OST_MEPSpaces', 'OST_Rooms',
                   'OST_Mass', 'OST_SectionBox', 'OST_CLines', 'OST_IOSSketchGrid'}
# Element triangle budgets: over-detailed families are decimated down to this (never below 25 %).
SIMPLIFY_BUDGET = {
    'OST_PlumbingFixtures': 300, 'OST_Furniture': 260, 'OST_SpecialityEquipment': 150,
    'OST_StairsRailing': 1500, 'OST_GenericModel': 300, 'OST_LightingFixtures': 60,
    'OST_MechanicalEquipment': 500, 'OST_Planting': 300, 'OST_Casework': 400,
    'OST_ElectricalFixtures': 60, 'OST_FireAlarmDevices': 60, 'OST_DuctTerminal': 120,
    'OST_Entourage': 2000,
}
# Looks big enough for the AO atlas; everything else gets per-vertex AO (see ao_bake.py)
ATLAS_LOOKS = {'paint', 'brick', 'metal_panel', 'cmu', 'limestone', 'concrete', 'glass', 'spandrel', 'channel_glass',
               'grass', 'pavers', 'gravel', 'membrane', 'ceiling', 'helipad', 'steel', 'column_steel',
               'concrete_gray'}
TOP_OTHER = {'solar': 'aluminum'}   # 'top:' looks: what the sides and undersides get (default concrete)
# Round ducts and pipes are exported with dense cylinders; keep this share of their triangles.
SIMPLIFY_RATIO = {'OST_DuctSystem': 0.5, 'OST_PipingSystem': 0.45, 'OST_PipeCurves': 0.45,
                  'OST_DuctCurves': 0.5, 'OST_FlexDuctCurves': 0.5}
MEP_SYSTEM_COLORS = {  # "colour by system", by keyword in the Revit system type
    'SUPPLY AIR': '#2f80ed', 'RETURN AIR': '#b14fc5', 'EXHAUST AIR': '#2fa36b',
    'HOT WATER SUPPLY': '#e5484d', 'HOT WATER RETURN': '#f08a8d', 'CHILLED WATER SUPPLY': '#1f6feb',
    'CHILLED WATER RETURN': '#79b8ff', 'MEDICAL GAS': '#8a63d2', 'OXYGEN': '#3fb950', 'VACUUM': '#f2cc60',
    'COOLING TOWER SUPPLY': '#0e9aa7', 'COOLING TOWER RETURN': '#6fd3d9', 'SANITARY': '#8b5a2b',
    'DOMESTIC HOT WATER': '#ff7b72', 'DOMESTIC COLD WATER': '#58a6ff',
}


# ---------------------------------------------------------------- classification
def wall_exterior(type_name):
    t = (type_name or '').lower()
    if 'brick' in t:
        return 'brick'
    if 'metal panel' in t:
        return 'metal_panel'
    if 'cmu' in t and 'exterior' in t:
        return 'cmu'
    if 'exterior' in t:
        return 'concrete'
    return 'paint'


def classify(model, cat, fam, typ, rgba):
    """-> (discipline, look) or None. Looks prefixed 'ext:' are decided per face later
    (outward faces get the named exterior finish, inward faces paint)."""
    fam_l, typ_l = (fam or '').lower(), (typ or '').lower()
    gray = abs(rgba[0] - rgba[1]) < 0.06 and abs(rgba[1] - rgba[2]) < 0.06
    transparent = rgba[3] < 0.98
    if cat in SKIP_CATEGORIES or cat is None:
        return None
    if model == 'Architectural':
        if cat == 'OST_Walls':
            return 'arch', 'ext:' + wall_exterior(typ)
        if cat == 'OST_CurtainWallPanels':
            if 'spandrel' in typ_l:
                return 'arch', 'spandrel'
            if 'profilit' in typ_l or 'profilit' in fam_l:
                return 'arch', 'channel_glass'
            return 'arch', 'glass' if transparent or 'glaz' in typ_l else 'metal_panel'
        if cat == 'OST_CurtainWallMullions':
            return 'arch', 'mullion'
        if cat == 'OST_Windows':
            return 'arch', 'glass' if transparent else 'aluminum'
        if cat == 'OST_Doors':
            if transparent:
                return 'arch', 'glass'
            warm = rgba[0] > rgba[2] + 0.12
            return 'arch', 'wood' if warm else 'door_metal'
        if cat == 'OST_Columns':
            if 'limestone' in fam_l:
                return 'arch', 'ext:limestone'
            if 'brick' in fam_l:
                return 'arch', 'ext:brick'
            return 'arch', 'paint'
        if cat == 'OST_Roofs':
            if 'glazing' in fam_l or transparent:
                return 'arch', 'glass'
            for key, look in (('grass', 'grass'), ('paver', 'pavers'), ('ballast', 'gravel'),
                              ('epdm', 'membrane'), ('concrete', 'concrete')):
                if key in typ_l:
                    return 'arch', 'top:' + look
            return 'arch', 'top:membrane'
        if cat in ('OST_RoofSoffit', 'OST_Fascia'):
            return 'arch', 'aluminum'
        if cat == 'OST_Ceilings':
            return 'arch', 'ceiling' if 'grid' in typ_l else 'paint'
        if cat == 'OST_Stairs':
            return 'arch', 'concrete' if gray else 'tinted'
        if cat in ('OST_StairsRailing', 'OST_StairsRailingBaluster', 'OST_RailingTopRail', 'OST_RailingHandRail'):
            return 'arch', 'glass' if transparent else ('wood' if rgba[0] > rgba[2] + 0.15 else 'steel_brushed')
        if cat == 'OST_GenericModel':
            if 'solar' in fam_l:
                return 'arch', 'top:solar'
            if 'louver' in fam_l:
                return 'arch', 'aluminum'
            if 'elevator' in fam_l:
                return 'arch', 'steel_brushed'
            return 'arch', 'tinted'
        if cat == 'OST_PlumbingFixtures':
            return 'piping', 'porcelain' if rgba[0] > 0.85 else 'chrome'
        if cat == 'OST_SpecialityEquipment':
            if 'helipad' in fam_l:
                return 'arch', 'helipad'
            if 'grab' in fam_l:
                return 'arch', 'steel_brushed'
            return 'arch', 'tinted'
        if cat in ('OST_Planting', 'OST_Entourage', 'OST_Site'):
            return None                     # site & landscape is not part of this demo
        if cat in ('OST_Floors', 'OST_BuildingPad'):
            return 'arch', 'concrete'
        return 'arch', 'tinted'
    if model == 'Structural':
        if cat == 'OST_StructuralColumns':
            return 'struct', 'column_steel'
        if cat in ('OST_StructuralFraming', 'OST_StructuralTruss'):
            return 'struct', 'steel' if gray or rgba[2] > rgba[0] else 'concrete_gray'
        if cat in ('OST_StructuralFoundation', 'OST_Floors', 'OST_Walls'):
            return 'struct', 'concrete_gray'
        if cat == 'OST_Rebar':
            return 'struct', 'steel'
        return None  # stairs/railings duplicate the architectural ones
    if model == 'HVAC':
        if cat == 'OST_PipingSystem' or cat.startswith('OST_Pipe'):
            return 'piping', 'pipe'          # decided by system below
        if cat in ('OST_DuctSystem', 'OST_DuctCurves', 'OST_DuctFitting', 'OST_DuctAccessory'):
            return 'hvac', 'duct'
        if cat == 'OST_FlexDuctCurves':
            return 'hvac', 'duct_flex'
        if cat == 'OST_DuctTerminal':
            return 'hvac', 'diffuser'
        if cat == 'OST_MechanicalEquipment':
            return 'hvac', 'equipment'
        return None
    return None


def pipe_look(system):
    s = (system or '').upper()
    if any(k in s for k in ('MEDICAL', 'OXYGEN', 'VACUUM')):
        return 'copper'
    if 'SANITARY' in s:
        return 'pvc'
    if 'COOLING TOWER' in s:
        return 'pipe_steel'
    return 'pipe_jacket'


def system_label(name):
    """'DUCT - SUPPLY AIR' and 'Supply Air' are the same system in the legend."""
    s = name.strip()
    for prefix in ('DUCT - ', 'PIPING - ', 'PLUMBING - '):
        if s.upper().startswith(prefix):
            s = s[len(prefix):]
    return ' '.join(w.capitalize() for w in s.lower().split())


def system_color(system):
    s = (system or '').upper()
    for key, col in MEP_SYSTEM_COLORS.items():
        if key in s:
            return col
    if 'SUPPLY' in s and ('DUCT' in s or 'AIR' in s):
        return MEP_SYSTEM_COLORS['SUPPLY AIR']
    if 'RETURN' in s and 'AIR' in s:
        return MEP_SYSTEM_COLORS['RETURN AIR']
    if 'EXHAUST' in s:
        return MEP_SYSTEM_COLORS['EXHAUST AIR']
    return None


# ---------------------------------------------------------------- reading
def load_table(model):
    cache = CACHE / f'{model}.pkl'
    if cache.exists():
        df = pd.read_pickle(cache)
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        df = read_columns(SRC / f'2018_Hospital_{model}_rvt.xlsx', COLUMNS)
        df.to_pickle(cache)
    df['ID'] = pd.to_numeric(df['ID'], errors='coerce')
    df = df.dropna(subset=['ID'])
    df['ID'] = df['ID'].astype(np.int64)
    for c in COLUMNS - {'ID', 'Category', 'Family Name', 'Type Name', 'Level', 'Name'}:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.drop_duplicates('ID').set_index('ID')


def to_app(v):
    """Revit feet, Z-up -> metres, Y-up (x, z, -y). (The DDC Collada says millimetre, but the
    coordinates are Revit's internal feet.)"""
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], 1) * FT


def read_dae(model):
    """Yield (element id, rgba, verts[N,3] m Y-up, normals[N,3] or None, faces[M,3])."""
    dae = collada.Collada(str(SRC / f'2018_Hospital_{model}_rvt.dae'),
                          ignore=[collada.common.DaeUnsupportedError, collada.common.DaeBrokenRefError])

    def rgba_of(mat):
        eff = mat.effect if mat is not None else None
        d = eff.diffuse if eff is not None and isinstance(eff.diffuse, tuple) else (0.6, 0.6, 0.6, 1)
        a = eff.transparency if eff is not None and isinstance(eff.transparency, float) else 1.0
        return (float(d[0]), float(d[1]), float(d[2]), float(min(1.0, a)))

    def walk(node, matrix, eid):
        m = matrix @ node.matrix if hasattr(node, 'matrix') else matrix
        nid = getattr(node, 'id', None)
        if nid and str(nid).isdigit():
            eid = int(nid)
        for child in getattr(node, 'children', []):
            if isinstance(child, collada.scene.GeometryNode):
                bound = {mn.symbol: mn.target for mn in child.materials}
                for prim in child.geometry.primitives:
                    if not isinstance(prim, (collada.triangleset.TriangleSet, collada.polylist.Polylist,
                                             collada.polygons.Polygons)):
                        continue
                    tri = prim if isinstance(prim, collada.triangleset.TriangleSet) else prim.triangleset()
                    if len(tri.vertex_index) == 0:
                        continue
                    mat = bound.get(tri.material) or dae.materials.get(tri.material)
                    yield (eid, rgba_of(mat), *bake(tri, m))
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
        normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        normals = to_app(normals) / FT
    return to_app(verts), normals, inv.reshape(-1, 3)


def simplify(verts, faces, budget, floor=0.25):
    n = len(faces)
    keep = max(budget, int(n * floor))
    if n <= keep or n < 60:
        return verts, faces, False
    try:
        v2, f2 = fast_simplification.simplify(verts.astype(np.float32), faces.astype(np.int32),
                                              target_reduction=1 - keep / n)
    except Exception:
        return verts, faces, False
    if len(f2) < 4:
        return verts, faces, False
    return np.asarray(v2, np.float64), np.asarray(f2, np.int64), True


def crease_normals(verts, faces, angle=35):
    mesh = trimesh.Trimesh(verts, faces, process=False)
    sm = trimesh.graph.smooth_shade(mesh, angle=math.radians(angle))
    return np.asarray(sm.vertices), np.asarray(sm.vertex_normals), np.asarray(sm.faces)


# ---------------------------------------------------------------- assembling
class Bucket:
    """Accumulates triangles of one (discipline, look)."""

    def __init__(self):
        self.v, self.n, self.c, self.el, self.sys, self.f = [], [], [], [], [], []
        self.count = 0

    def add(self, v, n, f, rgba, el, sys_idx):
        self.v.append(v.astype(np.float32)); self.n.append(n.astype(np.float32))
        self.c.append(np.tile(np.asarray(rgba, np.float32), (len(v), 1)))
        self.el.append(np.full(len(v), el, np.float32)); self.sys.append(np.full(len(v), sys_idx, np.float32))
        self.f.append(f + self.count)
        self.count += len(v)

    def arrays(self):
        return (np.concatenate(self.v), np.concatenate(self.n), np.concatenate(self.c),
                np.concatenate(self.el), np.concatenate(self.sys), np.concatenate(self.f).astype(np.uint32))


def srgb_to_linear(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def element_info(row, sys_name=''):
    def s(x):
        return '' if x is None or (isinstance(x, float) and math.isnan(x)) else str(x)
    return [s(row.get('Category', '')).replace('OST_', ''), s(row.get('Family Name', '')),
            s(row.get('Type Name', '')), s(row.get('Level', '')), sys_name]


def collect():
    buckets = defaultdict(Bucket)          # (disc, look) -> Bucket
    tables = defaultdict(list)             # disc -> element rows
    el_index = defaultdict(dict)           # disc -> (model, eid) -> index
    systems = []                           # MEP systems: [name, colour]
    sys_index = {}
    grids = defaultdict(list)              # Revit grid name -> bubble vertices (for the 2D plans)
    seen_geo = set()
    stats = defaultdict(lambda: [0, 0])
    for model in MODELS:
        df = load_table(model)
        n_in = 0
        for eid, rgba, verts, normals, faces in read_dae(model):
            row = df.loc[eid] if eid is not None and eid in df.index else None
            if row is None:
                continue
            cat, fam, typ = row['Category'], row['Family Name'], row['Type Name']
            if model == 'Architectural' and cat == 'OST_Grids' and isinstance(row.get('Name'), str):
                grids[row['Name']].append(verts)
                continue
            cls = classify(model, cat, fam if isinstance(fam, str) else '', typ if isinstance(typ, str) else '', rgba)
            if cls is None:
                continue
            disc, look = cls
            # identical duplicates (same element exported twice, or phase/option copies) z-fight
            key = hash(np.round(verts, 3).tobytes()) ^ hash(faces.tobytes())
            if key in seen_geo:
                continue
            seen_geo.add(key)
            sys_name = ''
            sidx = 0
            if cat in ('OST_DuctSystem', 'OST_PipingSystem'):
                sys_name = system_label(typ if isinstance(typ, str) else '')
                col = system_color(sys_name)
                if col:
                    if sys_name not in sys_index:
                        systems.append([sys_name, col])
                        sys_index[sys_name] = len(systems)
                    sidx = sys_index[sys_name]
            if look == 'pipe':
                look = pipe_look(sys_name)
            n_in += len(faces)
            budget = SIMPLIFY_BUDGET.get(cat)
            simplified = False
            if budget:
                verts, faces, simplified = simplify(verts, faces, budget)
            elif cat in SIMPLIFY_RATIO and len(faces) > 48:
                verts, faces, simplified = simplify(verts, faces, int(len(faces) * SIMPLIFY_RATIO[cat]), floor=0.3)
            if simplified or normals is None:
                verts, normals, faces = crease_normals(verts, faces)
            elk = (model, eid)
            if elk not in el_index[disc]:
                el_index[disc][elk] = len(tables[disc])
                tables[disc].append([int(eid), *element_info(row, sys_name)])
            lin = (*srgb_to_linear(rgba[:3]), rgba[3])   # glTF vertex colours are linear
            buckets[(disc, look)].add(verts, normals, faces, lin, el_index[disc][elk], sidx)
            stats[(disc, look)][0] += len(faces)
        print(f'{model}: {n_in:,} source triangles')
    return buckets, tables, systems, grids, stats


# ---------------------------------------------------------------- per-face looks
def sees_sky(ray, cen, n):
    """True where a fan of rays leaving the face along n (tilted up to 55° and ±35° sideways)
    escapes the building: an exterior face. A single ray along n would hit the next wing."""
    h = n.copy()
    h[:, 1] = 0
    hl = np.linalg.norm(h, axis=1, keepdims=True)
    flat = hl[:, 0] < 0.2                       # caps and soffits: just look along n
    h = np.where(flat[:, None], 0, h / np.maximum(hl, 1e-9))
    escaped = np.zeros(len(n), bool)
    for elev in (0.0, 25.0, 55.0):
        for az in (-35.0, 0.0, 35.0):
            a, e = math.radians(az), math.radians(elev)
            hx = h[:, 0] * math.cos(a) - h[:, 2] * math.sin(a)
            hz = h[:, 0] * math.sin(a) + h[:, 2] * math.cos(a)
            d = np.stack([hx * math.cos(e), np.full(len(n), math.sin(e)), hz * math.cos(e)], 1)
            d = np.where(flat[:, None], n, d)
            todo = ~escaped
            if not todo.any():
                return escaped
            hit = ray.intersects_any(cen[todo] + n[todo] * 0.03, d[todo])
            escaped[np.flatnonzero(todo)[~hit]] = True
    return escaped


def resolve_face_looks(buckets):
    """'ext:<finish>' -> outward faces get <finish>, the rest paint (ray escapes the building).
    'top:<finish>' -> upward faces get <finish>, edges/undersides concrete."""
    occl = [b.arrays() for (d, lk), b in buckets.items() if d in ('arch', 'struct') and lk not in ('glass',)]
    allv, allf, off = [], [], 0
    for v, _, _, _, _, f in occl:
        allv.append(v); allf.append(f.astype(np.int64) + off); off += len(v)
    scene = trimesh.Trimesh(np.concatenate(allv), np.concatenate(allf), process=False)
    ray = trimesh.ray.ray_pyembree.RayMeshIntersector(scene)
    out = defaultdict(Bucket)
    for (disc, look), b in list(buckets.items()):
        if not (look.startswith('ext:') or look.startswith('top:')):
            v, n, c, el, sy, f = b.arrays()
            nb = out[(disc, look)]
            nb.v.append(v); nb.n.append(n); nb.c.append(c); nb.el.append(el); nb.sys.append(sy)
            nb.f.append(f.astype(np.int64) + nb.count); nb.count += len(v)
            continue
        kind, finish = look.split(':')
        v, n, c, el, sy, f = b.arrays()
        f = f.astype(np.int64)
        tri = v[f]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        if kind == 'ext':
            if finish == 'paint':
                outward = np.zeros(len(f), bool)
            else:
                outward = sees_sky(ray, tri.mean(1), fn) | sees_sky(ray, tri.mean(1), -fn)
        else:
            outward = np.abs(fn[:, 1]) > 0.6
        other = 'paint' if kind == 'ext' else TOP_OTHER.get(finish, 'concrete')
        for mask, lk in ((outward, finish), (~outward, other)):
            if not mask.any():
                continue
            sub = f[mask]
            used, inv = np.unique(sub, return_inverse=True)
            nb = out[(disc, lk)]
            nb.v.append(v[used]); nb.n.append(n[used]); nb.c.append(c[used]); nb.el.append(el[used])
            nb.sys.append(sy[used]); nb.f.append(inv.reshape(-1, 3) + nb.count); nb.count += len(used)
    return out


# ---------------------------------------------------------------- UVs, site, writing
def box_uv(v, n, tile):
    a = np.abs(n)
    ax = np.argmax(a, 1)
    uv = np.empty((len(v), 2), np.float32)
    m = ax == 1  # horizontal faces
    uv[m] = v[m][:, [0, 2]]
    m = ax == 0
    uv[m] = np.stack([v[m][:, 2] * np.sign(n[m][:, 0] + 1e-9), v[m][:, 1]], 1)
    m = ax == 2
    uv[m] = np.stack([-v[m][:, 0] * np.sign(n[m][:, 2] + 1e-9), v[m][:, 1]], 1)
    return uv / tile


def write_glb(path, prims):
    """prims: list of (name, attrs{name: array}, indices). Minimal glTF 2.0 writer."""
    bin_parts, views, accessors, meshes, nodes, materials = [], [], [], [], [], []
    offset = 0

    def add(arr, target, kind):
        nonlocal offset
        data = np.ascontiguousarray(arr).tobytes()
        pad = (-len(data)) % 4
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(data), 'target': target})
        bin_parts.append(data + b'\0' * pad)
        offset += len(data) + pad
        comp = {np.dtype('float32'): 5126, np.dtype('uint32'): 5125}[arr.dtype]
        acc = {'bufferView': len(views) - 1, 'componentType': comp, 'count': int(arr.shape[0]),
               'type': kind}
        if kind == 'VEC3' and comp == 5126:
            acc['min'] = arr.min(0).tolist(); acc['max'] = arr.max(0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    kinds = {1: 'SCALAR', 2: 'VEC2', 3: 'VEC3', 4: 'VEC4'}
    for name, attrs, idx in prims:
        a = {}
        for k, arr in attrs.items():
            a[k] = add(arr, 34962, kinds[1 if arr.ndim == 1 else arr.shape[1]])
        ii = add(idx.reshape(-1), 34963, 'SCALAR')
        materials.append({'name': name, 'pbrMetallicRoughness': {'metallicFactor': 0, 'roughnessFactor': 1}})
        meshes.append({'name': name, 'primitives': [{'attributes': a, 'indices': ii, 'material': len(materials) - 1}]})
        nodes.append({'name': name, 'mesh': len(meshes) - 1})
    blob = b''.join(bin_parts)
    gltf = {'asset': {'version': '2.0', 'generator': 'bim-ar hospital pipeline'}, 'scene': 0,
            'scenes': [{'nodes': list(range(len(nodes)))}], 'nodes': nodes, 'meshes': meshes,
            'materials': materials, 'accessors': accessors, 'bufferViews': views,
            'buffers': [{'byteLength': len(blob)}]}
    js = json.dumps(gltf, separators=(',', ':')).encode()
    js += b' ' * ((-len(js)) % 4)
    with open(path, 'wb') as fh:
        fh.write(b'glTF' + (2).to_bytes(4, 'little') + (12 + 8 + len(js) + 8 + len(blob)).to_bytes(4, 'little'))
        fh.write(len(js).to_bytes(4, 'little') + b'JSON' + js)
        fh.write(len(blob).to_bytes(4, 'little') + b'BIN\0' + blob)


def pack(raw, final):
    # 14-bit positions = 6 mm over the 100 m site; 8-bit normals. (-cz would emit KHR_meshopt_compression,
    # which the three.js r158 inside 8frame cannot decode.)
    cmd = [str(GLTFPACK), '-i', str(raw), '-o', str(final), '-cc', '-kn', '-km', '-kv', '-vp', '14', '-vt', '12',
           '-vn', '8']
    subprocess.run(cmd, check=True, capture_output=True)


def footprint(buckets, origin):
    """Building outline at Level 1 from the structural slabs and the walls (metres, app XZ)."""
    polys = []
    for (disc, look), b in buckets.items():
        if disc not in ('arch', 'struct'):
            continue
        v, _, _, _, _, f = b.arrays()
        tri = v[f.astype(np.int64)]
        low = (tri[:, :, 1].max(1) < 6.5) & (tri[:, :, 1].min(1) > -1.5)   # Levels 1-2
        tri = tri[low]
        if not len(tri):
            continue
        xz = tri[:, :, [0, 2]]
        area = np.abs(np.cross(xz[:, 1] - xz[:, 0], xz[:, 2] - xz[:, 0])) / 2
        for t in xz[area > 0.02][:200000]:
            polys.append(Polygon(t))
    fp = unary_union([p.buffer(0.3) for p in polys]).buffer(-0.3)
    return fp


def grid_lines(grids, origin):
    """Revit grids exported as bubble geometry -> [{name, a, b}] line ends in plan (app XZ, m).
    Each grid's vertices fall in one or two bubble clusters; the line runs between their centres."""
    out = []
    for name, parts in grids.items():
        xz = np.concatenate(parts)[:, [0, 2]] - origin[[0, 2]]
        a = xz[np.argmax(np.linalg.norm(xz - xz.mean(0), axis=1))]
        b = xz[np.argmax(np.linalg.norm(xz - a, axis=1))]
        half = np.linalg.norm(b - a) / 2
        ca = xz[np.linalg.norm(xz - a, axis=1) < half].mean(0)
        cb = xz[np.linalg.norm(xz - b, axis=1) <= half].mean(0)
        out.append({'name': name, 'a': ca.round(3).tolist(), 'b': cb.round(3).tolist()})
    return out


def main():
    ASSETS.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True); CACHE.mkdir(exist_ok=True)
    tex_sizes = json.loads((ASSETS / 'tex' / 'textures.json').read_text())
    buckets, tables, systems, grids, stats = collect()
    buckets = resolve_face_looks(buckets)

    # origin: centre of the architectural walls in plan, Level 1 (z = 0) as ground
    wv = np.concatenate([b.arrays()[0] for (d, lk), b in buckets.items() if d == 'arch'
                         and lk in ('paint', 'brick', 'metal_panel', 'cmu', 'concrete', 'glass', 'mullion')])
    lo, hi = np.percentile(wv, 0.5, axis=0), np.percentile(wv, 99.5, axis=0)
    origin = np.array([(lo[0] + hi[0]) / 2, 0.0, (lo[2] + hi[2]) / 2], np.float32)
    print('origin (m):', origin.round(2), 'extent (m):', (hi - lo).round(1))

    prims = defaultdict(list)          # disc -> [{look, v, n, c, el, sy, f}]
    for (disc, look), b in sorted(buckets.items()):
        v, n, c, el, sy, f = b.arrays()
        prims[disc].append(dict(look=look, v=v - origin, n=n, c=c, el=el, sy=sy, f=f.astype(np.int64)))

    # geometry for the 2D drawings (make_plans.py)
    looks = sorted({p['look'] for ps in prims.values() for p in ps})
    cache = {}
    for disc, ps in prims.items():
        cache[f'{disc}_tri'] = np.concatenate([p['v'][p['f']] for p in ps]).astype(np.float32)
        cache[f'{disc}_look'] = np.concatenate([np.full(len(p['f']), looks.index(p['look']), np.int16) for p in ps])
        cache[f'{disc}_el'] = np.concatenate([p['el'][p['f'][:, 0]].astype(np.int32) for p in ps])
        cache[f'{disc}_sys'] = np.concatenate([p['sy'][p['f'][:, 0]].astype(np.int16) for p in ps])
    np.savez_compressed(CACHE / 'plan_tris.npz', **cache)
    gl = grid_lines(grids, origin)
    fp = footprint(buckets, origin)
    fp_local = [[(round(x - float(origin[0]), 3), round(z - float(origin[2]), 3)) for x, z in p.exterior.coords]
                for p in (fp.geoms if hasattr(fp, 'geoms') else [fp]) if p.area > 30]
    levels = [{'name': n, 'y': round(e * FT, 3)} for n, e in LEVELS]
    (CACHE / 'plan_meta.json').write_text(json.dumps({'looks': looks, 'grids': gl, 'footprint': fp_local,
                                                      'levels': levels, 'systems': systems,
                                                      'origin': origin.tolist()}))

    # ---- ambient occlusion: atlas for Architecture + Structure, per vertex for HVAC + Piping
    dkey = {k: i for i, (k, _, _) in enumerate(DISCIPLINES)}
    occ_t, occ_e = [], []
    for disc in ('arch', 'struct'):
        for p in prims.get(disc, []):
            if p['look'] in ('glass', 'channel_glass'):
                continue
            occ_t.append(p['v'][p['f']])
            occ_e.append(dkey[disc] * 1_000_000 + p['el'][p['f'][:, 0]].astype(np.int64))
    ray, _ = ao_bake.caster(np.concatenate(occ_t))
    occ_el = np.concatenate(occ_e)
    for disc in ('arch', 'struct'):
        ps = [p for p in prims.get(disc, []) if p['look'] in ATLAS_LOOKS]
        print(f'AO {disc}:')
        unw, size = ao_bake.unwrap([(p['v'], p['n'], p['f']) for p in ps])
        for p, (vmap, f2, uv) in zip(ps, unw):
            for k in ('v', 'n', 'c', 'el', 'sy'):
                p[k] = p[k][vmap]
            p['f'] = f2
            p['uv1'] = uv.astype(np.float32)
        face_el = np.concatenate([dkey[disc] * 1_000_000 + p['el'][p['f'][:, 0]].astype(np.int64) for p in ps])
        img = ao_bake.bake_atlas([(p['v'], p['f'], p['uv1']) for p in ps], size, face_el, ray, occ_el)
        ao_bake.save_jpg(img, ASSETS / 'tex' / f'ao_{disc}.jpg')
    mep_t = [p['v'][p['f']] for d in ('hvac', 'piping') for p in prims.get(d, [])]
    ray_mep, _ = ao_bake.caster(np.concatenate(occ_t + mep_t))
    for disc, ps in prims.items():
        for p in ps:
            if 'uv1' in p:
                continue
            print(f'AO {disc}/{p["look"]} (per vertex)')
            if disc == 'piping':      # pipes are a few pixels wide on the sheet: AO is invisible there, but costs MBs
                ao = np.ones(len(p['v']), np.float32)
            else:
                ao = np.round(ao_bake.bake_vertices(p['v'], p['n'], ray_mep) * 31) / 31   # 32 levels compress better
            p['uv1'] = np.stack([ao, np.full(len(ao), 0.5, np.float32)], 1)
            p['ao_ramp'] = True
    ao_bake.save_ramp(ASSETS / 'tex' / 'ao_ramp.png')

    files = {}
    for disc, ps in prims.items():
        out = []
        for p in ps:
            attrs = {'POSITION': p['v'], 'NORMAL': p['n'], 'COLOR_0': p['c'], '_FEATURE_ID_0': p['el'],
                     'TEXCOORD_1': p['uv1']}
            tex = LOOKS[p['look']].get('tex')
            if tex:
                attrs['TEXCOORD_0'] = box_uv(p['v'], p['n'], tex_sizes[tex])
            if disc in ('hvac', 'piping'):
                attrs['_FEATURE_ID_1'] = p['sy']
            out.append((p['look'], attrs, p['f'].astype(np.uint32)))
        raw = OUT / f'{disc}.raw.glb'
        final = ASSETS / f'{disc}.glb'
        write_glb(raw, out)
        pack(raw, final)
        tris = sum(len(p['f']) for p in ps)
        files[disc] = {'glb': f'{disc}.glb', 'bytes': final.stat().st_size, 'triangles': tris,
                       'looks': [p['look'] for p in ps],
                       'ao': f'tex/ao_{disc}.jpg' if disc in ('arch', 'struct') else 'tex/ao_ramp.png',
                       'ao_ramp_looks': sorted(p['look'] for p in ps if p.get('ao_ramp'))}
        (ASSETS / f'{disc}.json').write_text(json.dumps(tables[disc], separators=(',', ':')))
        print(f'{disc:7s} {tris:>10,} tris  {final.stat().st_size / 1e6:6.2f} MB')

    scene = {
        'disciplines': [{'key': k, 'label': lbl, 'color': col, **files.get(k, {})} for k, lbl, col in DISCIPLINES
                        if k in files],
        'looks': LOOKS,
        'textures': tex_sizes,
        'systems': systems,
        'levels': levels,
        'footprint': fp_local,
        'bounds': {'min': (lo - origin).round(2).tolist(), 'max': (hi - origin).round(2).tolist()},
    }
    (ASSETS / 'scene.json').write_text(json.dumps(scene, indent=1))
    total = sum(f['bytes'] for f in files.values())
    print(f"total {sum(f['triangles'] for f in files.values()):,} triangles, {total / 1e6:.1f} MB")


if __name__ == '__main__':
    main()
