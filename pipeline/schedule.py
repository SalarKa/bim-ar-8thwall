"""4D schedule: shift the AEC 450 timeline into 2025 and link each Revit element to a task.

The source timeline (Timeline2-1.csv) runs Dec 2011 - May 2012. Dates are shifted by a whole
number of weeks so every task keeps its weekday, starting Monday 6 January 2025.

Task naming in the CSV: "Level 1A: Columns" = level 1, zone A, trade "Columns".
Zones split the building into thirds along its length: A west, B centre, C east.
"""
import csv
import re
from datetime import date, datetime, timedelta

import numpy as np

SOURCE_START = date(2011, 12, 26)
NEW_START = date(2025, 1, 6)
SHIFT = NEW_START - SOURCE_START
assert SHIFT.days % 7 == 0

LEVEL_ELEV_FT = {1: 0.0, 2: 12.0, 3: 24.0, 4: 36.0}
ZONE_SPLIT_FT = 25.0  # |x| from building centre, thirds of the ~155 ft length
DATE_COLS = ['Expected Start', 'Expected End', 'End 1', 'End 2', 'End 3', 'End 4', 'End 5', 'End 6',
             'Max End']

EXTERIOR_WALL_KEYS = ('Exterior', 'SIP', 'Core', 'Foundation', 'Retaining', 'Generic', 'Soffit')
GLAZED_WALL_KEYS = ('Translucent', 'Storefront', 'Curtain')
EXTERIOR_DOOR_KEYS = ('Curtain Wall', '72"')


def _parse(s):
    return datetime.strptime(s, '%m/%d/%Y').date()


def shift_csv(src, dst):
    """Write a copy of the schedule with every date moved into 2025."""
    with open(src, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    out = []
    for r in rows:
        if not r['TaskID']:
            continue
        for c in DATE_COLS:
            if r.get(c):
                r[c] = (_parse(r[c]) + SHIFT).strftime('%m/%d/%Y').lstrip('0').replace('/0', '/')
        out.append(r)
    with open(dst, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    return out


def load_tasks(rows):
    tasks = []
    for r in rows:
        title = r['Title'].strip()
        m = re.match(r'(Level (\d)([ABC])?|All): (.+)', title)
        level = int(m.group(2)) if m and m.group(2) else None
        zone = m.group(3) if m else None
        trade = m.group(4) if m else title
        preds = [r[f'Pred {i}'] for i in range(1, 7) if r.get(f'Pred {i}')]
        tasks.append({
            'id': r['TaskID'], 'title': title, 'level': level, 'zone': zone, 'trade': trade,
            'start': _parse(r['Expected Start']).isoformat(),
            'end': _parse(r['Expected End']).isoformat(),
            'duration': int(r['Duration']), 'predecessors': preds,
        })
    return tasks


def _nearest_level(z):
    return min(LEVEL_ELEV_FT, key=lambda k: abs(LEVEL_ELEV_FT[k] - z))


def _zone(x):
    return 'A' if x < -ZONE_SPLIT_FT else ('C' if x > ZONE_SPLIT_FT else 'B')


SPANNING = ('OST_Walls', 'OST_CurtainWallPanels', 'OST_CurtainWallMullions')


def respect_supports(members, tasks, tol_ft=1.2):
    """Move framing that would appear before the members carrying it.

    Zones split the floor plates into thirds, so a joist or beam near a zone line can bear on a
    girder or column from the next zone, which starts later; it would hang in the air. Each such
    member moves to the first task of its own trade on the same level that starts once every
    support has started (else to its latest support's task).

    members: {eid: {'kind': 'column'|'beam'|'joist', 'lo': (x, y, z), 'hi': (x, y, z), 'task': id}}
    (z up, feet). tasks: list of task dicts with 'id' and ISO 'start'. Returns {eid: new_task}."""
    start = {t['id']: t['start'] for t in tasks}

    def ends(m):
        lo, hi = np.asarray(m['lo']), np.asarray(m['hi'])
        ax = 0 if hi[0] - lo[0] >= hi[1] - lo[1] else 1
        o = 1 - ax
        pts = []
        for v in (lo[ax], hi[ax]):
            p = np.zeros(3)
            p[ax], p[o], p[2] = v, (lo[o] + hi[o]) / 2, hi[2]
            pts.append(p)
        return pts

    def supporters(eid, kinds):
        out = set()
        for e in ends(members[eid]):
            end_cols, end_beams = set(), set()
            for cid, c in members.items():
                if cid == eid or c['kind'] not in kinds:
                    continue
                lo, hi = c['lo'], c['hi']
                if not (lo[0] - tol_ft <= e[0] <= hi[0] + tol_ft and lo[1] - tol_ft <= e[1] <= hi[1] + tol_ft):
                    continue
                if c['kind'] == 'column':
                    bears = lo[2] <= e[2] - 2 and hi[2] >= e[2] - 1.5   # column rises from below the member
                else:
                    bears = abs(hi[2] - e[2]) <= 1.5                    # beam at the same top elevation
                if bears:
                    (end_cols if c['kind'] == 'column' else end_beams).add(cid)
            # an end that lands on a column bears on the column, not on the other members framing in
            out |= end_cols or end_beams
        return out

    moved = {}
    for kind, support_kinds, trade in (('beam', {'column', 'beam'}, '30'), ('joist', {'column', 'beam'}, '40')):
        for _ in range(4):  # beams can bear on beams; settle chains
            changed = False
            for eid, m in members.items():
                if m['kind'] != kind:
                    continue
                sup = supporters(eid, support_kinds)
                if not sup:
                    continue
                need = max(start[members[s]['task']] for s in sup)
                if start[m['task']] >= need:
                    continue
                level = m['task'][0]
                options = sorted((start[t], t) for t in start
                                 if t.startswith(level + trade) and start[t] >= need)
                new = options[0][1] if options else members[max(sup, key=lambda s: start[members[s]['task']])]['task']
                m['task'] = moved[eid] = new
                changed = True
            if not changed:
                break
    return moved


def task_span(category, type_name, family, bbox_min, bbox_max):
    """(start task, end task). Multi-storey walls and curtain walls rise with the floors: they
    start with their base storey's task and finish with their top storey's task."""
    start = task_for(category, type_name, family, bbox_min, bbox_max)
    if category not in SPANNING:
        return start, start
    z0, z1 = bbox_min[2], bbox_max[2]
    top_storey = max((k for k, e in LEVEL_ELEV_FT.items() if e <= max(z0, z1 - 1.0)), default=1)
    if LEVEL_ELEV_FT[top_storey] <= z0 + 0.5:
        return start, start
    lo = (bbox_min[0], bbox_min[1], LEVEL_ELEV_FT[top_storey])
    return start, task_for(category, type_name, family, lo, bbox_max)


def task_for(category, type_name, family, bbox_min, bbox_max):
    """Map one element (bbox in building-centred ft) to a schedule TaskID, or None."""
    (x0, _, z0), (x1, _, z1) = bbox_min, bbox_max
    xc = (x0 + x1) / 2
    lvl_base = min(3, _nearest_level(z0))   # storey the element starts on
    lvl_top = _nearest_level(z1)           # floor it supports
    zone = _zone(xc)
    type_name = type_name or ''
    family = family or ''

    if z1 < 0.6 and category in ('OST_Floors', 'OST_StructuralFoundation', 'OST_Walls'):
        return '110'
    if category in ('OST_StructuralColumns', 'OST_Columns'):
        return f'{lvl_base}20{zone}'
    if category == 'OST_StructuralFraming':
        lv = min(3, max(1, lvl_top - 1))   # beams under level N+1 belong to level N
        trade = '40' if type_name.startswith('W12') else '30'
        return f'{lv}{trade}{zone}'
    if category == 'OST_Floors':
        lv = _nearest_level(z1)
        return '110' if lv == 1 else ('410' if lv == 4 else f'{lv}10')
    if category in ('OST_Roofs',):
        return '420' if 'Glazing' in type_name else '410'
    if category == 'OST_Fascia':
        return '420'
    if category == 'OST_Stairs':
        return '510'
    if category == 'OST_StairsRailing':
        return '810'
    if category in ('OST_CurtainWallPanels', 'OST_CurtainWallMullions'):
        return f'710{"ABC"[lvl_base - 1]}'
    if category == 'OST_Windows':
        return f'610{"ABC"[lvl_base - 1]}'
    if category == 'OST_Doors':
        if any(k in family or k in type_name for k in EXTERIOR_DOOR_KEYS):
            return f'610{"ABC"[lvl_base - 1]}'
        return f'620{"ABC"[lvl_base - 1]}'
    if category == 'OST_Walls':
        if z0 >= LEVEL_ELEV_FT[4] - 0.5:
            return '420'                    # parapets
        if any(k in type_name for k in GLAZED_WALL_KEYS):
            return f'710{"ABC"[lvl_base - 1]}'
        if any(k in type_name for k in EXTERIOR_WALL_KEYS):
            return f'{lvl_base}50'
        return f'910{"ABC"[lvl_base - 1]}'
    # fit-out not in the schedule (plumbing, ceilings, generic models) follows interior walls
    return f'910{"ABC"[lvl_base - 1]}'
