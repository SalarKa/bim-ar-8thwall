"""4D schedule and 5D cost estimate for the hospital, generated from the model.

No schedule came with the Autodesk sample, so this builds a hypothetical one the way a planner would
from the model: the building is split into three zones (A west, B centre, C east) and eight levels,
every element is assigned to an activity by category, level and zone, and each activity's duration
comes from its model quantity at a typical crew production rate. A forward pass over the
dependencies (finish-to-start, or start-to-start with a lag) on a Mon-Fri calendar with US holidays
gives the dates.

The estimate is a quantity takeoff from the Revit parameters (Area, Volume, Length, counts) or, where
Revit has none, from the model geometry, priced with 2025 US national-average installed unit costs
(data/unit_costs.json), moved to Hattiesburg, MS with the RSMeans location factor, plus markups and
escalation to the construction midpoint. Scope the model doesn't contain (electrical, sprinklers,
finishes, sitework...) is carried as clearly labelled parametric allowances.

Run after build_hospital.py (it reads src/assets/<disc>.json and pipeline/out/<disc>.raw.glb):
    python -I pipeline/schedule_cost.py
Writes src/assets/schedule.json, src/assets/cost.json, data/hospital-schedule.csv,
data/hospital-cost-estimate.csv.
"""
import csv
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I
from fast_xlsx import read_columns  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
OUT = ROOT / 'pipeline' / 'out'
CACHE = ROOT / 'pipeline' / 'cache'
DATA = ROOT / 'data'
SRC = Path.home() / 'DDC_Tools' / 'work' / 'hospital'
FT = 0.3048
M2_SF = 1 / FT ** 2
DISCS = ['arch', 'struct', 'hvac', 'piping']
MODEL_OF = {'arch': 'Architectural', 'struct': 'Structural', 'hvac': 'HVAC', 'piping': 'HVAC'}
QTO_COLS = {'ID', 'Category', 'Family Name', 'Type Name', 'Level', 'Area', 'Volume', 'Length', 'Width', 'Height',
            'Thickness', 'Size', 'System Type', 'System Classification', 'Diameter', 'Unconnected Height',
            'Base Constraint', 'Top Constraint', 'Assembly Code', 'Type Mark'}

START = date(2027, 3, 1)       # hypothetical notice to proceed (a Monday)
ZONE_X_M = 16.5                # zones: thirds of the ~99 m length
ZONES = 'ABC'
ZONE_NAMES = {'A': 'West third', 'B': 'Centre third', 'C': 'East third'}
STEEL_PCF = 490.0
RISER_IN = 7.0


# ---------------------------------------------------------------- model data
def geometry(disc, n):
    """Per element row: bbox lo/hi (m, Y-up, origin at Level 1 centre) and surface area (m2), from
    the uncompressed GLB the build writes before packing."""
    b = (OUT / f'{disc}.raw.glb').read_bytes()
    jl = int.from_bytes(b[12:16], 'little')
    js = json.loads(b[20:20 + jl])
    base = 20 + jl + 8
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

    def acc(i):
        a = js['accessors'][i]
        v = js['bufferViews'][a['bufferView']]
        dt = {5126: np.float32, 5125: np.uint32}[a['componentType']]
        w = width[a['type']]
        arr = np.frombuffer(b, dt, a['count'] * w, base + v['byteOffset'])
        return arr.reshape(a['count'], w) if w > 1 else arr

    lo = np.full((n, 3), np.inf)
    hi = np.full((n, 3), -np.inf)
    area = np.zeros(n)
    for mesh in js['meshes']:
        p = mesh['primitives'][0]
        P = acc(p['attributes']['POSITION']).astype(np.float64)
        E = np.rint(acc(p['attributes']['_FEATURE_ID_0'])).astype(np.int64)
        I = acc(p['indices']).astype(np.int64).reshape(-1, 3)
        np.minimum.at(lo, E, P)
        np.maximum.at(hi, E, P)
        t = P[I]
        np.add.at(area, E[I[:, 0]], np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1) / 2)
    return lo, hi, area


def revit_quantities(model):
    cache = CACHE / f'{model}_qto.pkl'
    if cache.exists():
        df = pd.read_pickle(cache)
    else:
        df = read_columns(SRC / f'2018_Hospital_{model}_rvt.xlsx', QTO_COLS)
        df.to_pickle(cache)
    df['ID'] = pd.to_numeric(df['ID'], errors='coerce')
    df = df.dropna(subset=['ID']).drop_duplicates('ID')
    df['ID'] = df['ID'].astype(np.int64)
    for c in ('Area', 'Volume', 'Length'):
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
    return df.set_index('ID')


def load_elements(levels):
    """One dict per (disc, row): table fields, Revit quantities (ft units), geometry, level, zone."""
    qto = {m: revit_quantities(m) for m in set(MODEL_OF.values())}
    lv_y = np.array([l['y'] for l in levels])
    els = []
    for disc in DISCS:
        table = json.loads((ASSETS / f'{disc}.json').read_text(encoding='utf-8'))
        lo, hi, area = geometry(disc, len(table))
        q = qto[MODEL_OF[disc]]
        for i, (rid, cat, fam, typ, level, system) in enumerate(table):
            if not np.isfinite(lo[i, 0]):
                continue                                    # no geometry (dropped in the build)
            r = q.loc[rid] if rid in q.index else None
            base = int(max(0, np.searchsorted(lv_y, lo[i, 1] + 0.45, 'right') - 1))
            top = int(np.abs(lv_y - hi[i, 1]).argmin())
            xc = (lo[i, 0] + hi[i, 0]) / 2
            els.append({
                'disc': disc, 'idx': i, 'rid': rid, 'cat': cat, 'fam': fam or '', 'type': typ or '',
                'system': system or '',
                'area': float(r['Area']) if r is not None else 0.0,        # ft2
                'volume': float(r['Volume']) if r is not None else 0.0,    # ft3
                'length': float(r['Length']) if r is not None else 0.0,    # ft
                'geo_sf': float(area[i] * M2_SF), 'lo': lo[i], 'hi': hi[i],
                'base': base, 'top': top,
                'zone': 'A' if xc < -ZONE_X_M else ('C' if xc > ZONE_X_M else 'B'),
            })
    return els


# ---------------------------------------------------------------- activities
LEVEL_NAMES = ['Level 1', 'Level 2', 'Level 3', 'Level 4', 'Level 5', 'Level 6', 'Level 7A', 'Roof']
EXT_DOOR_KEYS = ('Store Front', 'Curtain Wall', 'Overhead', 'Automatic')


def activity(e):
    """-> (start activity id, end activity id or None, grows) for one element. A span (start..end)
    is for elements that run through the building (duct and pipe systems, the roof membrane):
    they rise from the bottom up over the span."""
    d, cat, fam, typ, b, z = e['disc'], e['cat'], e['fam'], e['type'], e['base'], e['zone']
    lvl = min(b, 6)
    if d == 'struct':
        if cat in ('StructuralFoundation', 'Walls'):
            return f'F2{z}', None, False
        if cat == 'Floors':
            if 'base' in typ and e['top'] == 0:
                return f'F3{z}', None, False
            return f'D{max(1, e["top"])}{z}', None, False
        if cat == 'StructuralColumns':
            return f'S{min(7, b + 1)}{z}', None, False
        if cat == 'StructuralFraming':
            return (f'S{e["top"]}{z}', None, False) if e['top'] >= 1 else (f'F2{z}', None, False)
    if d == 'hvac':
        if cat == 'DuctSystem':
            return 'H0', 'H6', True
        if cat == 'MechanicalEquipment':
            if any(k in fam for k in ('AHU', 'Chiller', 'Boiler', 'Cooling Tower', 'Fan')):
                return 'P1', None, False
            if 'MedGas' in fam:
                return f'T{lvl}', None, False
            return f'H{lvl}', None, False
        if cat == 'DuctTerminal':
            return f'C{lvl}', None, False
        return f'H{lvl}', None, False
    if d == 'piping':
        if cat == 'PipingSystem':
            return 'H0', 'H6', True
        if cat == 'PlumbingFixtures':
            return f'C{lvl}', None, False
        return f'H{lvl}', None, False
    # architecture
    if cat == 'Walls':
        if typ.startswith('Exterior'):
            return (f'R{b}', None, False) if b >= 7 else (f'E{lvl}', None, False)
        return f'I{lvl}', None, False
    if cat in ('CurtainWallPanels', 'CurtainWallMullions', 'Windows'):
        return f'W{lvl}', None, False
    if cat == 'Doors':
        return (f'W{lvl}', None, False) if any(k in fam or k in typ for k in EXT_DOOR_KEYS) else (f'I{lvl}', None, False)
    if cat == 'Columns':
        return (f'E{lvl}', None, False) if ('Brick' in fam or 'Limestone' in fam) else (f'I{lvl}', None, False)
    if cat == 'Ceilings':
        return f'C{lvl}', None, False
    if cat == 'Roofs':
        if e['rid'] == 0:                                  # membrane generated on every open slab
            return 'R2', 'R7', True
        return f'R{max(1, b)}', None, False
    if cat == 'RoofSoffit':
        return f'E{lvl}', None, False
    if cat == 'GenericModel':
        if 'Solar' in fam:
            return 'R9', None, False
        if 'Louver' in fam:
            return f'E{lvl}', None, False
        if 'Roof_Drain' in typ or 'Roof Drain' in fam:
            return f'R{max(1, b)}', None, False
        if 'Elevator' in fam:
            return 'V2', None, False
        return f'T{lvl}', None, False
    if cat == 'SpecialityEquipment':
        return ('R9', None, False) if 'HeliPad' in fam else (f'C{lvl}', None, False)
    if cat in ('Stairs', 'StairsRailing'):
        return 'V1', None, False
    return f'T{lvl}', None, False


# ---------------------------------------------------------------- cost items
# item -> (CSI division, description, unit)
DIVISIONS = {
    '03': 'Concrete', '04': 'Masonry', '05': 'Metals', '07': 'Thermal & Moisture Protection',
    '08': 'Openings', '09': 'Finishes', '10': 'Specialties', '11': 'Equipment', '14': 'Conveying',
    '22': 'Plumbing', '23': 'HVAC', '26': 'Electrical (allowance)', '48': 'Electrical Power Generation',
    'AL': 'Allowances: scope not in the model',
}
ITEMS = {
    'piles': ('03', 'Auger-cast piles, 18" x 60 ft (assumed; caps modeled)', 'EA'),
    'pile_caps': ('03', 'Pile caps, cast-in-place incl. forms & rebar', 'CY'),
    'footings': ('03', 'Spread & wall footings, foundation slab', 'CY'),
    'found_walls': ('03', 'Foundation & retaining walls', 'CY'),
    'sog': ('03', 'Slab on grade on 12" base', 'SF'),
    'slab_on_deck': ('03', 'Concrete slab on composite metal deck', 'SF'),
    'conc_columns': ('03', 'Cast-in-place concrete columns', 'CY'),
    'brick_wall': ('04', 'Brick veneer on metal-stud backup', 'SF'),
    'cmu_ext': ('04', 'Exterior CMU, 8" reinforced', 'SF'),
    'cmu_int': ('04', 'Interior CMU partitions, 8"', 'SF'),
    'masonry_cols': ('04', 'Brick & limestone column enclosures', 'SF'),
    'steel_framing': ('05', 'Structural steel beams & channels (W12-W40)', 'TON'),
    'steel_columns': ('05', 'Structural steel columns (W14, HSS)', 'TON'),
    'floor_deck': ('05', 'Composite floor & roof deck, 3" 20 ga', 'SF'),
    'stair': ('05', 'Steel pan stairs, concrete-filled treads', 'RISER'),
    'cable_rail': ('05', 'Cable railings with top rail', 'LF'),
    'pipe_rail': ('05', 'Pipe handrails & guardrails', 'LF'),
    'metal_panel': ('07', 'Insulated metal panel wall on metal studs', 'SF'),
    'roofing': ('07', 'Membrane roofing, tapered polyiso & cover board', 'SF'),
    'ballast_roof': ('07', 'Ballasted roof', 'SF'),
    'green_roof': ('07', 'Extensive green roof over membrane', 'SF'),
    'paver_roof': ('07', 'Paver roof on pedestals over membrane', 'SF'),
    'soffit': ('07', 'Metal panel soffits', 'SF'),
    'roof_drain': ('22', 'Roof drains with overflow', 'EA'),
    'curtain_wall': ('08', 'Aluminum curtain wall, insulated glass (incl. SSG)', 'SF'),
    'spandrel': ('08', 'Curtain wall spandrel panels', 'SF'),
    'channel_glass': ('08', 'Channel glass wall (Profilit)', 'SF'),
    'storefront': ('08', 'Aluminum storefront & fixed windows', 'SF'),
    'louver': ('08', 'Architectural louvres', 'SF'),
    'door_single': ('08', 'Interior single doors, frame & hardware', 'EA'),
    'door_double': ('08', 'Interior double doors, frame & hardware', 'PR'),
    'door_auto': ('08', 'Automatic sliding entrance doors', 'EA'),
    'door_ext_pair': ('08', 'Curtain-wall entrance door pairs', 'PR'),
    'door_overhead': ('08', 'Overhead dock doors', 'EA'),
    'part_1hr': ('09', 'Metal stud & gypsum partitions, 1-hr', 'SF'),
    'part_2hr': ('09', 'Metal stud & gypsum partitions, 2-hr', 'SF'),
    'feature_wall': ('09', 'Feature walls (donor wall, water wall)', 'SF'),
    'act_ceiling': ('09', '2x2 acoustical ceilings, suspended grid', 'SF'),
    'gyp_ceiling': ('09', 'Gypsum board ceilings & soffits', 'SF'),
    'column_cover': ('09', 'Column enclosures (gypsum / metal)', 'SF'),
    'toilet_part': ('10', 'Toilet compartments & urinal screens', 'EA'),
    'grab_bar': ('10', 'Grab bars', 'EA'),
    'helipad': ('10', 'Rooftop helipad (deck, lighting, fire)', 'LS'),
    'casework': ('11', 'Nurse stations & counters', 'EA'),
    'elevator': ('14', 'Hospital traction elevators (stretcher)', 'EA'),
    'water_closet': ('22', 'Water closets, wall-hung', 'EA'),
    'lavatory': ('22', 'Lavatories / sinks, wall-hung', 'EA'),
    'shower': ('22', 'Accessible showers (valve, head, slide bar)', 'EA'),
    'urinal': ('22', 'Urinals, wall-hung', 'EA'),
    'piping_hydronic': ('23', 'Hydronic piping, insulated (CHW, HW, CT)', 'LF'),
    'piping_plumbing': ('22', 'Domestic water & sanitary piping', 'LF'),
    'piping_medgas': ('22', 'Medical gas piping (O2, vacuum, med air)', 'LF'),
    'medgas_outlet': ('22', 'Medical gas outlets', 'EA'),
    'ahu': ('23', 'Custom air-handling units (~50,000 CFM each)', 'EA'),
    'chiller': ('23', 'Water-cooled screw chillers (~500 tons)', 'EA'),
    'boiler': ('23', 'Water-tube boilers (16,750 MBH)', 'EA'),
    'cooling_tower': ('23', 'Closed-circuit cooling towers', 'EA'),
    'fan': ('23', 'Inline exhaust fans', 'EA'),
    'vav': ('23', 'VAV terminal units', 'EA'),
    'coil': ('23', 'Hot-water reheat coils', 'EA'),
    'diffuser': ('23', 'Diffusers & return grilles', 'EA'),
    'ductwork': ('23', 'Galvanized ductwork incl. fittings & insulation', 'LB'),
    'pv': ('48', 'Rooftop PV array', 'KW'),
}
ALLOWANCES = {   # $/GSF, US national 2025, scope the model does not contain
    'al_elec': ('Electrical power, lighting, emergency power & low voltage (Div 26/27/28)', ['H', 'T', 'K1']),
    'al_fp': ('Fire sprinklers & standpipes (Div 21)', ['H']),
    'al_plumb': ('Plumbing & med-gas distribution not modeled (risers, branches, zone valves)', ['H', 'C']),
    'al_hvac': ('HVAC distribution, exhaust & building automation not modeled', ['H', 'K1']),
    'al_fin': ('Flooring, wall protection, paint & interior signage', ['T']),
    'al_site': ('Sitework, paving, site utilities & landscaping', ['G020', 'K2']),
}
PIPE_SYSTEMS = {   # piping system -> (item, assumed average outside diameter in inches)
    'CHILLED WATER': ('piping_hydronic', 8.6), 'HOT WATER SUPPLY': ('piping_hydronic', 6.6),
    'HOT WATER RETURN': ('piping_hydronic', 6.6), 'COOLING TOWER': ('piping_hydronic', 8.6),
    'Domestic Hot Water': ('piping_plumbing', 2.4), 'SANITARY': ('piping_plumbing', 4.5),
    'MEDICAL GAS': ('piping_medgas', 1.1), 'OXYGEN': ('piping_medgas', 1.1), 'VACUUM': ('piping_medgas', 1.4),
}
DUCT_LB_PER_SF = 1.15      # 22-24 ga average incl. seams and stiffeners
PV_KW_EACH = 0.4


def takeoff(e):
    """-> list of (item, quantity) for one element."""
    d, cat, fam, typ = e['disc'], e['cat'], e['fam'], e['type']
    cy = e['volume'] / 27
    out = []
    if d == 'struct':
        if cat == 'StructuralFoundation':
            m = re.search(r'(\d+) Pile', fam)
            if m:
                out += [('pile_caps', cy), ('piles', int(m.group(1)))]
            else:
                out.append(('footings', cy))
        elif cat == 'Walls':
            out.append(('found_walls', cy))
        elif cat == 'Floors':
            out.append(('sog', e['area']) if 'base' in typ else ('slab_on_deck', e['area']))
            if 'Deck' in typ:
                out.append(('floor_deck', e['area']))
        elif cat == 'StructuralColumns':
            out.append(('conc_columns', cy) if fam.startswith('Concrete') else
                       ('steel_columns', e['volume'] * STEEL_PCF / 2000))
        elif cat == 'StructuralFraming':
            out.append(('steel_framing', e['volume'] * STEEL_PCF / 2000))
        return out
    if d == 'hvac':
        if cat == 'DuctSystem':
            out.append(('ductwork', e['geo_sf'] * DUCT_LB_PER_SF))
        elif cat == 'MechanicalEquipment':
            for key, item in (('AHU', 'ahu'), ('Chiller', 'chiller'), ('Boiler', 'boiler'),
                              ('Cooling Tower', 'cooling_tower'), ('Fan', 'fan'), ('VAV', 'vav'),
                              ('Coil', 'coil'), ('MedGas', 'medgas_outlet')):
                if key in fam:
                    out.append((item, 1))
                    break
        elif cat == 'DuctTerminal':
            out.append(('diffuser', 1))
        return out
    if d == 'piping':
        if cat == 'PipingSystem':
            for key, (item, od) in PIPE_SYSTEMS.items():
                if key in typ:
                    out.append((item, e['geo_sf'] / (math.pi * od / 12)))   # surface area -> length
                    break
        elif cat == 'PlumbingFixtures':
            for key, item in (('Toilet', 'water_closet'), ('Sink', 'lavatory'), ('Shower', 'shower'),
                              ('Urinal', 'urinal')):
                if key in fam:
                    out.append((item, 1))
                    break
        return out
    # architecture
    if cat == 'Walls':
        if 'Brick' in typ:
            out.append(('brick_wall', e['area']))
        elif 'Metal Panel' in typ:
            out.append(('metal_panel', e['area']))
        elif 'CMU' in typ:
            out.append(('cmu_ext' if typ.startswith('Exterior') else 'cmu_int', e['area']))
        elif 'Donor' in typ or 'Water Wall' in typ:
            out.append(('feature_wall', e['area']))
        elif '2-hr' in typ:
            out.append(('part_2hr', e['area']))
        else:
            out.append(('part_1hr', e['area']))
    elif cat == 'CurtainWallPanels':
        out.append(('channel_glass', e['area']) if 'Profilit' in fam else
                   ('spandrel', e['area']) if 'Spandrel' in typ else ('curtain_wall', e['area']))
    elif cat == 'Windows':
        out.append(('storefront', e['area']))
    elif cat == 'Doors':
        if 'Automatic' in fam:
            out.append(('door_auto', 1))
        elif 'Curtain Wall' in fam:
            out.append(('door_ext_pair', 1))
        elif 'Overhead' in fam:
            out.append(('door_overhead', 1))
        elif fam.startswith('Double'):
            out.append(('door_double', 1))
        else:
            out.append(('door_single', 1))
    elif cat == 'Ceilings':
        out.append(('act_ceiling', e['area']) if 'grid' in typ else ('gyp_ceiling', e['area']))
    elif cat == 'Columns':
        if 'Brick' in fam or 'Limestone' in fam:
            out.append(('masonry_cols', e['geo_sf'] / 2))      # outer face of a closed solid
        elif 'Elevator' not in fam:
            out.append(('column_cover', e['geo_sf'] / 2))
    elif cat == 'Roofs':
        if e['rid'] == 0:
            out.append(('roofing', e['geo_sf']))                # one-sided membrane
        elif 'Grass' in typ:
            out.append(('green_roof', e['area']))
        elif 'Paver' in typ:
            out.append(('paver_roof', e['area']))
        elif 'Ballast' in typ:
            out.append(('ballast_roof', e['area']))
        else:
            out.append(('roofing', e['area']))
    elif cat == 'RoofSoffit':
        out.append(('soffit', e['area']))
    elif cat == 'GenericModel':
        if 'Solar' in fam:
            out.append(('pv', PV_KW_EACH))
        elif 'Louver' in fam:
            out.append(('louver', e['area'] / 2))                # Revit reports both faces
        elif 'Roof Drain' in fam:
            out.append(('roof_drain', 1))
        elif 'CabinFront' in fam:
            out.append(('elevator', 1))
        elif 'Counter' in fam:
            out.append(('casework', e['length'] / 12))          # ~12 LF of counter = one station
    elif cat == 'SpecialityEquipment':
        if 'HeliPad' in fam:
            out.append(('helipad', 1))
        elif 'Grab' in fam:
            out.append(('grab_bar', 1))
        elif 'Stall Door' in fam or 'Urinal Screen' in fam:
            out.append(('toilet_part', 1))
    elif cat == 'Stairs':
        rise_ft = (e['hi'][1] - e['lo'][1]) / FT
        out.append(('stair', round(rise_ft * 12 / RISER_IN)))
    elif cat == 'StairsRailing':
        out.append(('cable_rail' if 'cable' in typ.lower() else 'pipe_rail', e['length']))
    elif cat == 'Casework':
        out.append(('casework', 1))
    return out


# ---------------------------------------------------------------- the schedule
# activity prefix -> (title template, trade, driver item(s) or 'gsf', production per working day, min, max)
def build_activities(els, level_gsf):
    qty = defaultdict(lambda: defaultdict(float))        # activity -> item -> quantity
    count = defaultdict(int)
    for e in els:
        a, b, _ = e['act']
        count[a] += 1
        if b:
            count[b] += 0
        for item, q in e['items']:
            qty[a][item] += q
    n_piles = defaultdict(float)
    for e in els:
        for item, q in e['items']:
            if item == 'piles':
                n_piles[e['zone']] += q

    def dur(x, rate, lo, hi):
        return int(min(hi, max(lo, math.ceil(x / rate))))

    def sum_items(a, *items):
        return sum(qty[a][i] for i in items)

    acts = []

    def add(aid, title, trade, duration, preds, level=None, zone=None):
        acts.append({'id': aid, 'title': title, 'trade': trade, 'duration': int(duration), 'preds': preds,
                     'level': level, 'zone': zone})

    add('G010', 'Notice to proceed, mobilization & site setup', 'General', 15, [])
    add('G020', 'Site clearing, mass excavation & dewatering', 'Sitework', 30, [('G010', 'FS', 0)])
    for k, z in enumerate(ZONES):
        prev = ZONES[k - 1] if k else None
        add(f'F1{z}', f'Zone {z}: Auger-cast piles', 'Piles', dur(n_piles[z], 18, 8, 40),
            [('G020', 'FS', 0)] + ([(f'F1{prev}', 'FS', 0)] if prev else []), 0, z)
        a = f'F2{z}'
        add(a, f'Zone {z}: Pile caps, grade beams & foundation walls', 'Foundations',
            dur(sum_items(a, 'pile_caps', 'footings', 'found_walls'), 45, 10, 40),
            [(f'F1{z}', 'FS', 0)] + ([(f'F2{prev}', 'SS', 10)] if prev else []), 0, z)
        a = f'F3{z}'
        add(a, f'Zone {z}: Under-slab utilities & slab on grade', 'Slab on grade',
            dur(qty[a]['sog'], 3500, 8, 30), [(f'F2{z}', 'FS', 0)] + ([(f'F3{prev}', 'FS', 0)] if prev else []), 0, z)
    for L in range(1, 8):
        for k, z in enumerate(ZONES):
            prev = ZONES[k - 1] if k else None
            s, dk = f'S{L}{z}', f'D{L}{z}'
            preds = [(f'F2{z}', 'FS', 0)] if L == 1 else [(f'S{L - 1}{z}', 'FS', 0), (f'D{L - 1}{z}', 'SS', 3)]
            if prev:
                preds.append((f'S{L}{prev}', 'FS', 0))          # one raising gang moves A -> B -> C
            add(s, f'{LEVEL_NAMES[L]}{z}: Steel erection', 'Structural steel',
                dur(sum_items(s, 'steel_framing', 'steel_columns') + sum_items(s, 'conc_columns') / 4, 9, 5, 25),
                preds, L, z)
            add(dk, f'{LEVEL_NAMES[L]}{z}: Metal deck & concrete slab', 'Deck & slab',
                dur(qty[dk]['slab_on_deck'], 2500, 6, 25) + 3,
                [(s, 'FS', 0)] + ([(f'D{L}{prev}', 'FS', 0)] if prev else []), L, z)
    deck_all = lambda L: [(f'D{L}{z}', 'FS', 0) for z in ZONES]
    for L in range(0, 7):
        up = min(L + 1, 7)
        a = f'E{L}'
        add(a, f'{LEVEL_NAMES[L]}: Exterior walls, cladding & louvres', 'Envelope',
            dur(sum_items(a, 'metal_panel', 'brick_wall', 'cmu_ext', 'masonry_cols', 'soffit', 'louver'), 700, 10, 45),
            deck_all(up) + ([(f'E{L - 1}', 'SS', 10)] if L else []), L)
        a = f'W{L}'
        add(a, f'{LEVEL_NAMES[L]}: Curtain wall, storefront & windows', 'Glazing',
            dur(sum_items(a, 'curtain_wall', 'spandrel', 'channel_glass', 'storefront'), 600, 10, 45),
            [(f'E{L}', 'SS', 10)] + ([(f'W{L - 1}', 'SS', 10)] if L else []), L)
    for L in range(1, 8):
        a = f'R{L}'
        area = sum_items(a, 'roofing', 'green_roof', 'paver_roof', 'ballast_roof')
        if L == 7 or area > 0 or count[a]:
            add(a, f'{LEVEL_NAMES[L]}: Roofing & roof drains', 'Roofing', dur(area, 1500, 8, 30), deck_all(L), L)
    roofs = [a['id'] for a in acts if a['trade'] == 'Roofing']
    add('R9', 'Rooftop PV arrays & helipad', 'Roof equipment', 30, [(r, 'FS', 0) for r in roofs], 7)
    add('M1', 'Building dried in (milestone)', 'Milestone', 0,
        [(a['id'], 'FS', 0) for a in acts if a['trade'] in ('Envelope', 'Glazing', 'Roofing')])
    add('P1', 'Central plant: chillers, boilers, cooling towers & AHUs set', 'Mechanical equipment', 30,
        deck_all(7) + [('R7', 'FS', 0)], 7)
    for L in range(0, 7):
        g = level_gsf[L]
        h, i, c, t = f'H{L}', f'I{L}', f'C{L}', f'T{L}'
        add(h, f'{LEVEL_NAMES[L]}: Overhead MEP rough-in (duct, pipe, conduit)', 'MEP rough-in',
            dur(g, 450, 40, 110), deck_all(min(L + 1, 7)) + ([(f'H{L - 1}', 'SS', 15)] if L else [('F3C', 'FS', 0)]), L)
        add(i, f'{LEVEL_NAMES[L]}: Framing, in-wall rough-in, drywall & doors', 'Interiors',
            dur(sum_items(i, 'part_1hr', 'part_2hr', 'cmu_int', 'feature_wall'), 650, 35, 100),
            [(h, 'SS', 15), (f'W{L}', 'FS', 0), (f'E{L}', 'FS', 0)], L)
        add(c, f'{LEVEL_NAMES[L]}: Ceilings, fixtures, diffusers & specialties', 'Ceilings & fixtures',
            dur(sum_items(c, 'act_ceiling', 'gyp_ceiling'), 900, 25, 70), [(i, 'SS', 25), (h, 'FS', 10)], L)
        add(t, f'{LEVEL_NAMES[L]}: Flooring, finishes, MEP trim & med-gas outlets', 'Finishes & trim',
            dur(g, 1100, 25, 60), [(c, 'SS', 20)], L)
    add('V1', 'Stairs & railings', 'Stairs', 45, deck_all(2))
    add('V2', 'Elevators: install, test & state inspection', 'Elevators', 120, [('M1', 'FS', 0)])
    add('K1', 'Testing, balancing & commissioning (MEP, med gas, life safety)', 'Commissioning', 100,
        [(f'T{L}', 'FS', 0) for L in range(7)] + [('P1', 'FS', 0), ('V2', 'FS', 0), ('R9', 'FS', 0)])
    add('K2', 'Punch list, MSDH licensure & fire marshal inspections', 'Closeout', 30, [('K1', 'FS', 0)])
    add('K3', 'Substantial completion (milestone)', 'Milestone', 0, [('K2', 'FS', 0)])
    return acts, count


def holidays(y):
    def nth(month, weekday, n):
        d = date(y, month, 1)
        d += timedelta((weekday - d.weekday()) % 7)
        return d + timedelta(7 * (n - 1))

    def last(month, weekday):
        d = date(y, month + 1, 1) - timedelta(1)
        return d - timedelta((d.weekday() - weekday) % 7)

    def observed(d):
        return d - timedelta(1) if d.weekday() == 5 else d + timedelta(1) if d.weekday() == 6 else d
    thanks = nth(11, 3, 4)
    return {observed(date(y, 1, 1)), last(5, 0), observed(date(y, 7, 4)), nth(9, 0, 1), thanks,
            thanks + timedelta(1), observed(date(y, 12, 24)), observed(date(y, 12, 25))}


def calendar(n_days):
    hol = set().union(*(holidays(y) for y in range(START.year, START.year + 6)))
    days, d = [], START
    while len(days) < n_days:
        if d.weekday() < 5 and d not in hol:
            days.append(d)
        d += timedelta(1)
    return days


def forward_pass(acts):
    """Earliest start/finish in working-day numbers (finish exclusive) for FS and SS links."""
    by = {a['id']: a for a in acts}
    done = {}

    def es(a):
        if a['id'] in done:
            return done[a['id']]
        s = 0
        for pid, kind, lag in a['preds']:
            p = by[pid]
            ps, pf = es(p)
            s = max(s, (pf if kind == 'FS' else ps) + lag)
        done[a['id']] = (s, s + a['duration'])
        return done[a['id']]
    for a in acts:
        a['es'], a['ef'] = es(a)
    return acts


def critical_path(acts):
    by = {a['id']: a for a in acts}
    end = max(acts, key=lambda a: a['ef'])
    path, cur = [end['id']], end
    while cur['preds']:
        drivers = []
        for pid, kind, lag in cur['preds']:
            p = by[pid]
            if (p['ef'] if kind == 'FS' else p['es']) + lag == cur['es']:
                drivers.append(p)
        if not drivers:
            break
        cur = max(drivers, key=lambda p: p['ef'])
        path.append(cur['id'])
    return set(path)


# ---------------------------------------------------------------- cost
def price(els, acts, prices, gsf, cal):
    items, element_cost = [], defaultdict(dict)
    by_item = defaultdict(list)
    for e in els:
        for item, q in e['items']:
            if q > 0:
                by_item[item].append((e, q))
    lf = prices['location_factor']['index']
    act_days = {a['id']: (cal_day(cal, a['es']), cal_day(cal, a['ef'])) for a in acts}
    task_amount = defaultdict(float)              # activity -> direct cost (local)
    for iid, (div, desc, unit) in ITEMS.items():
        rows = by_item.get(iid, [])
        qty = sum(q for _, q in rows)
        if qty <= 0:
            continue
        p = prices['items'][iid]
        total = qty * p['unit_cost'] * lf
        for e, q in rows:
            c = total * q / qty
            element_cost[e['disc']][e['idx']] = element_cost[e['disc']].get(e['idx'], 0) + c
            act = f'F1{e["zone"]}' if iid == 'piles' else e['act'][0]
            if e['act'][1]:                       # spans: spread over the whole run
                span = [a['id'] for a in acts if a['id'][0] == e['act'][0][0] and
                        e['act'][0][1:] <= a['id'][1:] <= e['act'][1][1:] and len(a['id']) == len(e['act'][0])]
                for s in span:
                    task_amount[s] += c / len(span)
            else:
                task_amount[act] += c
        items.append({
            'id': iid, 'div': div, 'division': DIVISIONS[div], 'desc': desc, 'unit': unit,
            'qty': round(qty, 1), 'unit_cost': p['unit_cost'], 'local_unit_cost': round(p['unit_cost'] * lf, 2),
            'low': p.get('low'), 'high': p.get('high'), 'total': round(total, 2), 'note': p.get('note', ''),
            'sources': p.get('sources', []), 'elements': {d: sorted(v) for d, v in
                                                          group_elements(rows).items()},
        })
    for aid, (desc, prefixes) in ALLOWANCES.items():
        p = prices['items'][aid]
        total = gsf * p['unit_cost'] * lf
        targets = [a['id'] for a in acts if any(a['id'] == x or (len(x) == 1 and a['id'][0] == x and
                                                                 a['trade'] != 'Roofing') for x in prefixes)]
        for t in targets:
            task_amount[t] += total / len(targets)
        items.append({'id': aid, 'div': 'AL', 'division': DIVISIONS['AL'], 'desc': desc, 'unit': 'GSF',
                      'qty': round(gsf), 'unit_cost': p['unit_cost'], 'local_unit_cost': round(p['unit_cost'] * lf, 2),
                      'low': p.get('low'), 'high': p.get('high'), 'total': round(total, 2),
                      'note': p.get('note', ''), 'sources': p.get('sources', []), 'elements': {}, 'allowance': True})
    m = prices['markups']
    direct = sum(i['total'] for i in items)
    modeled = sum(i['total'] for i in items if not i.get('allowance'))
    gc = direct * m['general_conditions_pct'] / 100
    bonds = (direct + gc) * m['bonds_insurance_pct'] / 100
    fee = (direct + gc + bonds) * m['overhead_profit_pct'] / 100
    cont = (direct + gc + bonds + fee) * m['contingency_pct'] / 100
    sub = direct + gc + bonds + fee + cont
    first, last = cal[0], cal_day(cal, max(a['ef'] for a in acts))
    mid = first + (last - first) / 2
    years = (mid - date.fromisoformat(m['price_date'])).days / 365.25
    esc = sub * ((1 + m['escalation_pct_per_year'] / 100) ** years - 1)
    grand = sub + esc
    k = grand / (direct + gc)                 # direct-work markups (bonds, fee, contingency, escalation)
    total_days = (last - first).days
    spans = [[(act_days[t][0] - first).days, (act_days[t][1] - first).days, round(v * k, 2)]
             for t, v in task_amount.items() if v > 0]
    spans.append([0, total_days, round(gc * k, 2)])          # general conditions: evenly over the job
    return {
        'basis': prices['basis'], 'as_of': prices['as_of'], 'location_factor': prices['location_factor'],
        'markups': m, 'gsf': round(gsf), 'direct': round(direct, 2), 'modeled_direct': round(modeled, 2),
        'general_conditions': round(gc, 2), 'bonds_insurance': round(bonds, 2), 'overhead_profit': round(fee, 2),
        'contingency': round(cont, 2), 'escalation': round(esc, 2), 'escalation_years': round(years, 2),
        'midpoint': mid.isoformat(), 'total': round(grand, 2), 'per_sf': round(grand / gsf, 2),
        'markup_factor': grand / direct, 'benchmark': prices.get('benchmark', {}),
        'items': items, 'spans': spans,
        'element_cost': {d: {str(i): round(c, 2) for i, c in v.items()} for d, v in element_cost.items()},
        'exclusions': prices['exclusions'],
    }


def group_elements(rows):
    g = defaultdict(list)
    for e, _ in rows:
        g[e['disc']].append(e['idx'])
    return g


def cal_day(cal, n):
    """Working-day number (n = finish, exclusive) -> calendar date of that working day."""
    return cal[min(n, len(cal) - 1)]


# ---------------------------------------------------------------- main
def main():
    scene = json.loads((ASSETS / 'scene.json').read_text(encoding='utf-8'))
    levels = scene['levels']
    prices = json.loads((DATA / 'unit_costs.json').read_text(encoding='utf-8'))
    els = load_elements(levels)
    for e in els:
        e['act'] = activity(e)
        e['items'] = takeoff(e)

    # gross floor area per level: structural floor plates (slab on grade + slabs on deck), less the
    # roof areas, which are slabs with sky above
    level_gsf = defaultdict(float)
    roof_sf = defaultdict(float)
    for e in els:
        if e['disc'] == 'struct' and e['cat'] == 'Floors':
            level_gsf[e['top']] += e['area']
        if e['disc'] == 'arch' and e['cat'] == 'Roofs':
            roof_sf[max(1, e['base'])] += e['area'] if e['rid'] else 0
    membrane = sum(q for e in els if e['rid'] == 0 and e['disc'] == 'arch' for _, q in e['items'])
    floors = {L: max(0.0, level_gsf[L] - roof_sf[L]) for L in range(8)}
    gsf = sum(v for L, v in floors.items() if L < 7) - membrane * 0.85   # most of the open slabs are wing roofs
    level_area = {L: floors[L] for L in range(7)}

    acts, count = build_activities(els, level_area)
    acts = forward_pass(acts)
    cp = critical_path(acts)
    cal = calendar(max(a['ef'] for a in acts) + 5)
    ids = [a['id'] for a in acts]
    index = {a: i for i, a in enumerate(ids)}

    tasks = []
    for a in acts:
        s, f = cal_day(cal, a['es']), cal_day(cal, max(a['es'], a['ef'] - 1))
        tasks.append({'id': a['id'], 'title': a['title'], 'trade': a['trade'], 'level': a['level'],
                      'zone': a['zone'], 'start': s.isoformat(), 'end': f.isoformat(), 'duration': a['duration'],
                      'predecessors': [f'{p}{"" if k == "FS" and not l else f" {k}+{l}d" if l else f" {k}"}'
                                       for p, k, l in a['preds']],
                      'critical': a['id'] in cp, 'elements': count.get(a['id'], 0)})
    el_task = {}
    for disc in DISCS:
        n = len(json.loads((ASSETS / f'{disc}.json').read_text(encoding='utf-8')))
        s_arr, e_arr, g_arr = [-1] * n, [-1] * n, [0] * n
        for e in els:
            if e['disc'] != disc:
                continue
            a, b, grow = e['act']
            s_arr[e['idx']] = index[a]
            e_arr[e['idx']] = index[b] if b else index[a]
            g_arr[e['idx']] = int(grow)
        el_task[disc] = {'start': s_arr, 'end': e_arr, 'grow': g_arr}
    start, end = tasks[0]['start'], max(t['end'] for t in tasks)
    sched = {
        'start': start, 'end': end, 'hypothetical': True,
        'basis': ('Generated from the model: activities by category, level and zone; durations from model '
                  'quantities at typical crew production rates; Mon-Fri, US holidays off.'),
        'zones': ZONE_NAMES, 'levels': LEVEL_NAMES, 'working_days': max(a['ef'] for a in acts),
        'tasks': tasks, 'element_task': el_task,
    }
    (ASSETS / 'schedule.json').write_text(json.dumps(sched, separators=(',', ':')), encoding='utf-8')
    with open(DATA / 'hospital-schedule.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['TaskID', 'Title', 'Trade', 'Level', 'Zone', 'Duration (working days)', 'Start', 'Finish',
                    'Predecessors', 'Critical', 'Model elements'])
        for t in tasks:
            w.writerow([t['id'], t['title'], t['trade'], LEVEL_NAMES[t['level']] if t['level'] is not None else '',
                        t['zone'] or '', t['duration'], t['start'], t['end'], '; '.join(t['predecessors']),
                        'yes' if t['critical'] else '', t['elements']])

    cost = price(els, acts, prices, gsf, cal)
    (ASSETS / 'cost.json').write_text(json.dumps(cost, separators=(',', ':')), encoding='utf-8')
    with open(DATA / 'hospital-cost-estimate.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Division', 'Item', 'Quantity', 'Unit', 'National unit cost (USD)',
                    'Hattiesburg unit cost (USD)', 'Total (USD)', 'Basis'])
        for i in cost['items']:
            w.writerow([f"{i['div']} {i['division']}", i['desc'], i['qty'], i['unit'], i['unit_cost'],
                        i['local_unit_cost'], i['total'], i['note']])
        m = cost['markups']
        for label, v in (('Direct cost (Hattiesburg)', cost['direct']),
                         (f"General conditions {m['general_conditions_pct']}%", cost['general_conditions']),
                         (f"Bonds & insurance {m['bonds_insurance_pct']}%", cost['bonds_insurance']),
                         (f"Overhead & profit {m['overhead_profit_pct']}%", cost['overhead_profit']),
                         (f"Contingency {m['contingency_pct']}%", cost['contingency']),
                         (f"Escalation {m['escalation_pct_per_year']}%/yr to midpoint {cost['midpoint']}", cost['escalation']),
                         ('Total estimate', cost['total'])):
            w.writerow(['', label, '', '', '', '', round(v, 2), ''])

    # report
    print(f'{len(els):,} elements, {len(tasks)} activities, {start} -> {end} '
          f'({sched["working_days"]} working days, {(date.fromisoformat(end) - date.fromisoformat(start)).days / 30.44:.1f} months)')
    print('critical path:', ' > '.join(a['id'] for a in acts if a['id'] in cp))
    unassigned = [e for e in els if not e['items'] and e['cat'] not in
                  ('CurtainWallMullions', 'PipeFitting', 'PipeCurves', 'PipeAccessory', 'DuctFitting',
                   'DuctAccessory', 'Furniture')]
    by_cat = defaultdict(int)
    for e in unassigned:
        by_cat[(e['disc'], e['cat'], e['fam'][:30])] += 1
    print('not priced:', sorted(by_cat.items(), key=lambda kv: -kv[1])[:12])
    for i in cost['items']:
        print(f"{i['div']:2s} {i['desc'][:52]:52s} {i['qty']:>12,.1f} {i['unit']:5s} x {i['local_unit_cost']:>9,.2f} = {i['total']:>14,.0f}")
    print(f"GSF {gsf:,.0f}; modeled direct {cost['modeled_direct']:,.0f}; direct {cost['direct']:,.0f}; "
          f"total {cost['total']:,.0f} ({cost['per_sf']:,.0f} $/SF); midpoint {cost['midpoint']}")


if __name__ == '__main__':
    main()
