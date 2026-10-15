"""Quantity takeoff + cost estimate from the Revit model, priced with US national-average unit costs.

Quantities come from Revit parameters in the DDC export (Area, Volume, Length, counts) and, where
Revit has none (stair rise), from the model geometry. Unit costs live in data/unit_costs.json
(installed cost incl. the installing subcontractor's O&P, national average = location index 1.00).
Each line item's cost is spread over its elements so the app can show cost-to-date on the 4D timeline.

Usage: python -I pipeline/qto.py <model.xlsx>
Writes src/assets/cost.json and data/cost-estimate.csv.
"""
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import trimesh

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
STEEL_PCF = 490.0          # lb per ft3
RISER_IN = 7.0             # design riser for the 8"-max stair

DIVISIONS = {
    '03': 'Concrete', '04': 'Masonry', '05': 'Metals', '07': 'Thermal & Moisture Protection',
    '08': 'Openings', '09': 'Finishes', '10': 'Specialties', '22': 'Plumbing',
}
# item id -> (CSI division, description, unit)
ITEMS = {
    'sog': ('03', 'Slab on grade, 6" concrete', 'SF'),
    'lwc_fill': ('03', 'Lightweight concrete fill on metal deck', 'SF'),
    'core_wall': ('04', 'CMU core walls, 8"', 'SF'),
    'steel_framing': ('05', 'Structural steel beams & joists (W12–W16)', 'TON'),
    'steel_columns': ('05', 'Structural steel columns (W10x49)', 'TON'),
    'floor_deck': ('05', 'Composite floor deck, 2" 20 ga', 'SF'),
    'roof_deck': ('05', 'Roof deck, 1-1/2" Type B', 'SF'),
    'stair': ('05', 'Steel pan stair, concrete-filled treads', 'RISER'),
    'railing': ('05', 'Pipe handrails & guardrails', 'LF'),
    'roofing': ('07', 'EPDM roofing with polyiso insulation', 'SF'),
    'eifs_wall': ('07', 'EIFS exterior wall on metal studs', 'SF'),
    'fascia': ('07', 'Prefinished metal roof-edge fascia', 'LF'),
    'curtain_wall': ('08', 'Aluminum curtain wall, insulated glass', 'SF'),
    'cw_vent': ('08', 'Operable awning vents in curtain wall', 'EA'),
    'window': ('08', 'Aluminum windows, insulated glass', 'SF'),
    'translucent_panel': ('08', 'Translucent insulated wall panels', 'SF'),
    'int_door': ('08', 'Interior doors, HM frame & hardware', 'EA'),
    'storefront_door': ('08', 'Aluminum entrance door leaves', 'EA'),
    'ext_pair_door': ('08', 'Exterior HM pair doors, panic hardware', 'PR'),
    'partition': ('09', 'Metal stud & gypsum partitions, 1-hr', 'SF'),
    'act_ceiling': ('09', '2x2 acoustical ceiling, suspended grid', 'SF'),
    'column_cover': ('09', '18" round column covers', 'LF'),
    'toilet_partition': ('10', 'Toilet compartments with doors', 'EA'),
    'water_closet': ('22', 'Water closets, flush valve', 'EA'),
    'lavatory': ('22', 'Lavatories with faucets', 'EA'),
    'urinal': ('22', 'Urinals, flush valve', 'EA'),
}
EXCLUSIONS = [
    'Footings and foundation walls (not modeled)', 'HVAC, electrical, fire protection, plumbing rough-in',
    'Sitework, paving, landscaping and utilities', 'Interior finishes beyond partitions and ceilings',
    'Furniture, equipment, design fees, permits and land',
]


def element_bounds(index, scene):
    """Bounding boxes (meters, model space) per element id, from the GLB triangle ranges."""
    out = {}
    for group, ranges in index['ranges'].items():
        geom = scene.geometry[group]
        T = scene.graph.get(group)[0]
        verts = trimesh.transformations.transform_points(geom.vertices, T)
        for eid, start, count in ranges:
            v = verts[np.unique(geom.faces[start:start + count])]
            lo, hi = v.min(0), v.max(0)
            if eid in out:
                lo, hi = np.minimum(lo, out[eid][0]), np.maximum(hi, out[eid][1])
            out[eid] = (lo, hi)
    return out


def takeoff(df, index, bounds):
    """-> {item: {element_id: quantity}}"""
    q = defaultdict(dict)
    num = lambda r, k: float(r.get(f'{k} : Double')) if pd.notna(r.get(f'{k} : Double')) else 0.0
    wc_count = 0
    partition_walls = []
    for sid in index['elements']:
        eid = int(sid)
        r = df.loc[eid]
        cat = r['Category : String']
        tname = r['Type Name : String'] if isinstance(r['Type Name : String'], str) else ''
        area, vol, length = num(r, 'Area'), num(r, 'Volume'), num(r, 'Length')
        if cat == 'OST_StructuralFoundation':
            q['sog'][eid] = area
        elif cat == 'OST_Floors':
            q['lwc_fill'][eid] = area
            q['floor_deck'][eid] = area
        elif cat == 'OST_Roofs':
            q['roofing'][eid] = area
            q['roof_deck'][eid] = area
        elif cat == 'OST_StructuralFraming':
            q['steel_framing'][eid] = vol * STEEL_PCF / 2000
        elif cat == 'OST_StructuralColumns':
            q['steel_columns'][eid] = vol * STEEL_PCF / 2000
        elif cat == 'OST_Columns':
            q['column_cover'][eid] = vol / (math.pi * 0.75 ** 2)  # 18" dia -> height in ft
        elif cat == 'OST_Stairs':
            lo, hi = bounds[eid]
            q['stair'][eid] = round((hi[1] - lo[1]) / 0.3048 * 12 / RISER_IN)
        elif cat == 'OST_StairsRailing':
            q['railing'][eid] = length
        elif cat == 'OST_Fascia':
            q['fascia'][eid] = length
        elif cat == 'OST_Walls':
            if 'EIFS' in tname:
                q['eifs_wall'][eid] = area
            elif 'Core' in tname:
                q['core_wall'][eid] = area
            elif 'Bathroom' in tname:
                partition_walls.append(eid)
            else:
                q['partition'][eid] = area
        elif cat == 'OST_CurtainWallPanels':
            q['curtain_wall'][eid] = area      # mullions are part of the curtain wall price
        elif cat == 'OST_Windows':
            if 'Awning' in tname:
                q['cw_vent'][eid] = 1
            elif 'Translucent' in tname:
                q['translucent_panel'][eid] = area
            else:
                q['window'][eid] = area
        elif cat == 'OST_Doors':
            if tname in ('36" x 84"', '32" x 84"'):
                q['int_door'][eid] = 1
            elif 'Dbl Glass' in tname:
                q['storefront_door'][eid] = 2
            elif 'Sgl Glass' in tname:
                q['storefront_door'][eid] = 1
            elif tname == '72" x 84"':
                q['ext_pair_door'][eid] = 1
            # 30" x 60" stall doors are part of the toilet compartments
        elif cat == 'OST_Ceilings':
            q['act_ceiling'][eid] = area
        elif cat == 'OST_PlumbingFixtures':
            if 'Urinal' in tname:
                q['urinal'][eid] = 1
            elif 'Seat' in tname:
                q['water_closet'][eid] = 1
                wc_count += 1
            else:
                q['lavatory'][eid] = 1
        # OST_GenericModel here are ADA clearance zones, OST_CurtainWallMullions are in curtain_wall
    # one compartment per water closet, carried by the bathroom partition walls
    for eid in partition_walls:
        q['toilet_partition'][eid] = wc_count / max(len(partition_walls), 1)
    return q


def main(xlsx):
    index = json.loads((ASSETS / 'model-index.json').read_text(encoding='utf-8'))
    prices = json.loads((ROOT / 'data' / 'unit_costs.json').read_text(encoding='utf-8'))
    df = pd.read_excel(xlsx).set_index('ID')
    bounds = element_bounds(index, trimesh.load(ASSETS / 'model.glb'))
    q = takeoff(df, index, bounds)

    items, element_cost = [], defaultdict(float)
    for iid, (div, desc, unit) in ITEMS.items():
        per_el = q.get(iid, {})
        qty = sum(per_el.values())
        if qty <= 0:
            continue
        p = prices['items'][iid]
        total = qty * p['unit_cost']
        for eid, v in per_el.items():
            element_cost[eid] += total * v / qty
        items.append({
            'id': iid, 'div': div, 'division': DIVISIONS[div], 'desc': desc, 'unit': unit,
            'qty': round(qty, 2), 'unit_cost': p['unit_cost'], 'low': p.get('low'), 'high': p.get('high'),
            'total': round(total, 2), 'note': p.get('note', ''), 'sources': p.get('sources', []),
            'elements': sorted(per_el),
        })
    m = prices['markups']
    direct = sum(i['total'] for i in items)
    gc = direct * m['general_conditions_pct'] / 100
    fee = (direct + gc) * m['overhead_profit_pct'] / 100
    cont = (direct + gc + fee) * m['contingency_pct'] / 100
    grand = direct + gc + fee + cont
    gsf = sum(i['qty'] for i in items if i['id'] in ('sog', 'lwc_fill'))
    out = {
        'basis': prices.get('basis', ''), 'as_of': prices.get('as_of', ''),
        'location_factor': prices.get('location_factor', {}), 'markups': m,
        'gsf': round(gsf), 'direct': round(direct, 2),
        'general_conditions': round(gc, 2), 'overhead_profit': round(fee, 2), 'contingency': round(cont, 2),
        'total': round(grand, 2), 'per_sf': round(grand / gsf, 2),
        # markups follow the direct cost through time, so element costs carry the full multiplier
        'markup_factor': grand / direct,
        'items': items, 'element_cost': {str(k): round(v, 2) for k, v in element_cost.items()},
        'exclusions': EXCLUSIONS,
    }
    (ASSETS / 'cost.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    with open(ROOT / 'data' / 'cost-estimate.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Division', 'Item', 'Quantity', 'Unit', 'Unit cost (USD)', 'Total (USD)', 'Basis'])
        for i in items:
            w.writerow([f"{i['div']} {i['division']}", i['desc'], i['qty'], i['unit'], i['unit_cost'],
                        i['total'], i['note']])
        for label, v in (('Direct cost', direct), (f"General conditions {m['general_conditions_pct']}%", gc),
                         (f"Overhead & profit {m['overhead_profit_pct']}%", fee),
                         (f"Contingency {m['contingency_pct']}%", cont), ('Total estimate', grand)):
            w.writerow(['', label, '', '', '', round(v, 2), ''])
    for i in items:
        print(f"{i['div']} {i['desc'][:44]:44s} {i['qty']:>10,.1f} {i['unit']:5s} x {i['unit_cost']:>8,.2f} = {i['total']:>12,.0f}")
    print(f'direct {direct:,.0f}  total {grand:,.0f}  ({grand / gsf:,.0f} $/SF over {gsf:,.0f} SF)')


if __name__ == '__main__':
    main(sys.argv[1])
