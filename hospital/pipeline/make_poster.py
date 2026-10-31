"""Printable sheet for the hospital AR demo, drawn in the original poster's style; the whole sheet
is the AR image target.

Everything on it is a 2D drawing generated from the model geometry (pipeline/cache/plan_tris.npz,
written by build_hospital.py); there are no screenshots of the 3D model:
  - Level 1 plan with structural grids (the AR anchor; the model rises out of it at true scale)
  - one drawing per sub-model: Architecture (Level 4 plan), Structure (Level 3 framing plan),
    HVAC (Level 4 ductwork by system), Piping & Plumbing (Level 4 piping by system)
  - how to use the app, the MEP system legend, and the QR code

The sheet is 3:4 (2400 x 3200 px = 8 x 10.67 in at 300 dpi), so 8th Wall's default crop uses the
whole sheet, and it prints at 100 % on US Letter and A4.

Outputs: print/hospital-poster.png, print/hospital-poster-{letter,a4}.pdf,
         src/assets/target-placement.json, src/assets/poster-preview.jpg
Usage: python -I pipeline/make_poster.py [app url]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import qrcode
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, FancyBboxPatch, PathPatch, Polygon as MPolygon, Rectangle
from matplotlib.path import Path as MPath
from PIL import Image
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
ASSETS = ROOT / 'src' / 'assets'
CACHE = ROOT / 'pipeline' / 'cache'
PRINT = ROOT / 'print'
FONTS = REPO / 'pipeline' / 'fonts'
URL = sys.argv[1] if len(sys.argv) > 1 else 'https://salarka.github.io/bim-ar-8thwall/hospital/'
W, H, DPI = 2400, 3200, 300
FT = 0.3048
CUT = 1.2                      # plan cut height above each level (m), as in a Revit plan view

BLACK, GOLD, GOLD_INK, BG, INK, MUTED, LINE = '#1A1A1A', '#FFD046', '#8a6a00', '#F5F5F2', '#333333', '#6b6b6b', '#e8e8e8'
POCHE, GLASS, DOOR, FLOOR, FIXTURE = '#3b3b3b', '#5fa8d3', '#a8754f', '#f4f1ea', '#cfcac0'
STEEL, COLUMN, CONCRETE = '#6f7880', '#6e4126', '#c3c5c8'   # Structure colours match the app
F_HERO = FontProperties(fname=FONTS / 'Archivo-Black-Italic.ttf')
F_WIDE = FontProperties(fname=FONTS / 'Archivo-ExtraBold-Wide.ttf')
F_BOLD = FontProperties(fname=FONTS / 'Archivo-Bold.ttf')
F_SEMI = FontProperties(fname=FONTS / 'Archivo-SemiBold.ttf')
F_REG = FontProperties(fname=FONTS / 'Archivo-Regular.ttf')

POCHE_CATS = {'Walls', 'Columns', 'StructuralColumns', 'Structural Columns'}
GLASS_CATS = {'CurtainWallPanels', 'Curtain Wall Panels', 'CurtainWallMullions', 'Curtain Wall Mullions', 'Windows'}
FIXTURE_CATS = {'PlumbingFixtures', 'Plumbing Fixtures', 'Casework', 'Furniture', 'SpecialityEquipment', 'Stairs',
                'StairsRailing', 'Railings', 'MechanicalEquipment'}


# ---------------------------------------------------------------- model data
class Model:
    def __init__(self):
        z = np.load(CACHE / 'plan_tris.npz')
        self.meta = json.loads((CACHE / 'plan_meta.json').read_text())
        self.tri, self.el, self.sys, self.cat = {}, {}, {}, {}
        for disc in ('arch', 'struct', 'hvac', 'piping'):
            if f'{disc}_tri' not in z:
                continue
            self.tri[disc] = z[f'{disc}_tri']
            self.el[disc] = z[f'{disc}_el']
            self.sys[disc] = z[f'{disc}_sys']
            table = json.loads((ASSETS / f'{disc}.json').read_text())
            cats = np.array([row[1] for row in table] + [''])
            self.cat[disc] = cats[np.clip(self.el[disc], 0, len(table))]
        self.level = {lv['name']: lv['y'] for lv in self.meta['levels']}
        self.sys_colors = ['#888888'] + [c for _, c in self.meta['systems']]
        self.grids = self.grid_lines()

    def column_points(self):
        """Plan centres of the structural columns cut at Level 1."""
        seg, idx = self.section('struct', CUT, np.isin(self.cat['struct'], list(POCHE_CATS)))
        shapes, _ = poche(seg, self.el['struct'][idx])
        return np.array([[g.centroid.x, g.centroid.y] for g in shapes if not g.is_empty])

    def grid_lines(self):
        """Revit exports only each grid's bubble, so the line is recovered from the columns it runs
        through: the direction from the bubble that lines up the most column centres."""
        cols = self.column_points()
        out = []
        ang = np.radians(np.arange(0, 360, 0.25))
        dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
        for g in self.meta['grids']:
            b = (np.array(g['a']) + np.array(g['b'])) / 2
            rel = cols - b
            along = rel @ dirs.T                                          # (cols, dirs)
            off = np.abs(rel[:, :1] * dirs[:, 1] - rel[:, 1:] * dirs[:, 0])
            hits = (off < 0.35) & (along > 1.0)
            n = hits.sum(0)
            best = int(np.argmax(n))
            a = np.degrees(ang[best]) % 90
            # only grids that truly run through columns: 5+ aligned columns, roughly N-S or E-W
            # (the wings' radial grids fan out up to ~20 degrees; diagonals are coincidences)
            if n[best] < 5 or min(a, 90 - a) > 25:
                continue
            far = along[hits[:, best], best].max()
            out.append({'name': g['name'], 'bubble': b, 'end': b + dirs[best] * (far + 3.0)})
        return out

    def section(self, disc, y, mask=None):
        """Triangles crossing the plane y -> (segments (n,2,2) in plan XZ, triangle index)."""
        t = self.tri[disc]
        idx = np.arange(len(t))
        if mask is not None:
            t, idx = t[mask], idx[mask]
        d = t[:, :, 1] - y
        cross = (d.min(1) < 0) & (d.max(1) > 0)
        t, d, idx = t[cross], d[cross], idx[cross]
        pts = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            da, db = d[:, a], d[:, b]
            s = (da * db) < 0
            u = np.where(s, da / np.where(s, da - db, 1), np.nan)
            pts.append(t[:, a, :] + (t[:, b, :] - t[:, a, :]) * u[:, None])
        pts = np.stack(pts, 1)[:, :, [0, 2]]                     # (n,3,2), one edge is NaN
        ok = ~np.isnan(pts[:, :, 0])
        seg = np.array([p[o][:2] for p, o in zip(pts, ok)]) if len(pts) else np.zeros((0, 2, 2))
        return seg, idx

    def project(self, disc, mask):
        return self.tri[disc][mask][:, :, [0, 2]]

    def between(self, disc, y0, y1):
        ys = self.tri[disc][:, :, 1]
        return (ys.min(1) >= y0) & (ys.max(1) <= y1)


def poche(seg, owners):
    """Group cut segments per element and turn each closed outline into a filled shape."""
    groups = defaultdict(list)
    for s, o in zip(seg, owners):
        groups[o].append(s)
    shapes, loose = [], []
    for segs in groups.values():
        lines = [LineString(np.round(s, 4)) for s in segs if np.linalg.norm(s[1] - s[0]) > 1e-4]
        if not lines:
            continue
        faces = list(polygonize(unary_union(lines)))
        if not faces:
            loose.extend(segs)
            continue
        geom = faces[0]
        for f in faces[1:]:
            geom = geom.symmetric_difference(f)
        shapes.append(geom)
    return shapes, loose


def shape_path(geom, tf):
    verts, codes = [], []
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    for p in polys:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        for ring in [p.exterior, *p.interiors]:
            c = np.array(ring.coords)
            verts.extend(tf(c)); codes.extend([MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY])
    return MPath(verts, codes) if verts else None


# ---------------------------------------------------------------- drawing helpers
def text(ax, x, y, s, fp, size, color=INK, **kw):
    return ax.text(x, y, s, fontproperties=fp, fontsize=size, color=color, **kw)


def spaced(s, n=1):
    return (' ' * n).join(s.upper())


def card(ax, x, y, w, h, fc='white', ec=LINE, r=26, z=0.6, lw=1.2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}', fc=fc, ec=ec, lw=lw, zorder=z))


def tf_of(ox, oy, k):
    return lambda p: np.column_stack([ox + np.asarray(p)[:, 0] * k, oy + np.asarray(p)[:, 1] * k])


def polys(ax, tris_xz, tf, color, z, ec=None, lw=0.25, alpha=1.0):
    if not len(tris_xz):
        return
    v = tf(tris_xz.reshape(-1, 2)).reshape(-1, 3, 2)
    ax.add_collection(PolyCollection(v, facecolors=color, edgecolors=ec or color, linewidths=lw,
                                     alpha=alpha, zorder=z, antialiased=True))


def segs(ax, s, tf, color, lw, z):
    if not len(s):
        return
    v = tf(s.reshape(-1, 2)).reshape(-1, 2, 2)
    ax.add_collection(LineCollection(v, colors=color, linewidths=lw, zorder=z, capstyle='round'))


def draw_grids(ax, model, tf, bubble_r, lw=1.0, labels=True, color='#b9c0c6'):
    """Grid lines (light dash-dot) with a bubble at the exported end; bubbles that would overlap a
    neighbour are pushed further out along their own line, alternating like a staggered Revit sheet."""
    placed = []
    for g in sorted(model.grids, key=lambda g: (g['bubble'][1], g['bubble'][0])):
        (x0, y0), (x1, y1) = tf(np.array([g['bubble'], g['end']]))
        out = np.array([x0 - x1, y0 - y1]); out /= max(np.linalg.norm(out), 1e-9)
        bx, by = x0, y0
        if labels:
            for _ in range(3):
                if all(np.hypot(bx - px, by - py) >= 2.1 * bubble_r for px, py in placed):
                    break
                bx, by = bx + out[0] * 2.2 * bubble_r, by + out[1] * 2.2 * bubble_r
        ax.plot([bx, x1], [by, y1], color=color, lw=0.55 * lw, dashes=(10, 3, 2, 3), zorder=1.5)
        if not labels:
            continue
        placed.append((bx, by))
        ax.add_patch(Circle((bx, by), bubble_r, fc='white', ec=BLACK, lw=0.8 * lw, zorder=5))
        text(ax, bx, by + 1, g['name'], F_BOLD, 5.6 if len(g['name']) == 1 else 4.6, BLACK,
             ha='center', va='center', zorder=6)


def draw_footprint(ax, model, tf, fc=FLOOR, ec='#c9c3b7', lw=0.8):
    for ring in model.meta['footprint']:
        c = tf(np.array(ring))
        ax.add_patch(MPolygon(c, closed=True, fc=fc, ec=ec, lw=lw, zorder=1))


def plan_bounds(model, with_grids=True):
    pts = [np.array(r) for r in model.meta['footprint']]
    if with_grids:
        pts += [np.array([g['bubble'], g['end']]) for g in model.grids]
    p = np.concatenate(pts)
    return p.min(0), p.max(0)


def draw_arch_plan(ax, model, level, tf, k, detail=True):
    """Cut plan: walls/columns in poché, glazing blue, doors brown, fixtures and stairs below the cut."""
    y0 = model.level[level]
    yc = y0 + CUT
    draw_footprint(ax, model, tf)
    if detail:
        below = model.between('arch', y0 - 0.05, yc) & np.isin(model.cat['arch'], list(FIXTURE_CATS))
        polys(ax, model.project('arch', below), tf, FIXTURE, 2, lw=0.15)
        if 'piping' in model.tri:
            fx = model.between('piping', y0 - 0.05, yc) & np.isin(model.cat['piping'], list(FIXTURE_CATS))
            polys(ax, model.project('piping', fx), tf, FIXTURE, 2, lw=0.15)
    seg, idx = model.section('arch', yc)
    cats = model.cat['arch'][idx]
    owners = model.el['arch'][idx]
    is_p = np.isin(cats, list(POCHE_CATS))
    is_g = np.isin(cats, list(GLASS_CATS))
    is_d = cats == 'Doors'
    if detail:
        shapes, loose = poche(seg[is_p], owners[is_p])
        for g in shapes:
            path = shape_path(g, tf)
            if path is not None:
                ax.add_patch(PathPatch(path, fc=POCHE, ec=BLACK, lw=0.35, zorder=3))
        segs(ax, np.array(loose).reshape(-1, 2, 2), tf, POCHE, 1.4, 3)
        sseg, sidx = model.section('struct', yc, np.isin(model.cat['struct'], list(POCHE_CATS)))
        shapes, _ = poche(sseg, model.el['struct'][sidx])
        for g in shapes:
            path = shape_path(g, tf)
            if path is not None:
                ax.add_patch(PathPatch(path, fc=POCHE, ec=BLACK, lw=0.35, zorder=3))
    else:
        segs(ax, seg[is_p], tf, POCHE, max(0.45, 0.16 * k), 3)
    segs(ax, seg[is_g], tf, GLASS, max(0.5, 0.11 * k), 4)
    segs(ax, seg[is_d], tf, DOOR, max(0.4, 0.08 * k), 4)


def draw_struct_plan(ax, model, level, tf, k):
    y0 = model.level[level]
    draw_footprint(ax, model, tf, fc=CONCRETE, ec='#9c9fa3')        # concrete slab: grey, as in the app
    cat = model.cat['struct']
    beams = model.between('struct', y0 - 1.3, y0 + 0.25) & np.isin(cat, ['StructuralFraming', 'Structural Framing'])
    polys(ax, model.project('struct', beams), tf, STEEL, 3, lw=0.2)
    seg, idx = model.section('struct', y0 + CUT, np.isin(cat, list(POCHE_CATS)))
    shapes, _ = poche(seg, model.el['struct'][idx])
    for g in shapes:
        c = g.centroid
        (cx, cy), = tf(np.array([[c.x, c.y]]))
        s = max(4.0, 0.8 * k)
        ax.add_patch(Rectangle((cx - s / 2, cy - s / 2), s, s, fc=COLUMN, ec='none', zorder=4))


def draw_mep_plan(ax, model, disc, level, tf, k):
    y0 = model.level[level]
    names = [lv['name'] for lv in model.meta['levels']]
    y1 = model.level[names[names.index(level) + 1]]
    draw_footprint(ax, model, tf, fc='#f1efe9', ec='#d6d1c6')
    seg, idx = model.section('arch', y0 + CUT)
    walls = np.isin(model.cat['arch'][idx], list(POCHE_CATS | GLASS_CATS))
    segs(ax, seg[walls], tf, '#c3beb3', max(0.3, 0.08 * k), 2)
    m = model.between(disc, y0 - 0.1, y1 - 0.05)
    sysi = model.sys[disc]
    cols = np.array(model.sys_colors + ['#888888'] * 32)
    for s in np.unique(sysi[m]):
        mm = m & (sysi == s)
        color = '#8d949b' if s == 0 else cols[s]
        polys(ax, model.project(disc, mm), tf, color, 3 if s else 2.5, lw=0.25)


# ---------------------------------------------------------------- poster
def main():
    model = Model()
    PRINT.mkdir(exist_ok=True)
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.set_aspect('equal'); ax.axis('off')
    ax.add_patch(Rectangle((0, 0), W, H, fc=BG, zorder=0))

    # ---------------------------------------------------------------- masthead (as on the original poster)
    HB = 470
    ax.add_patch(Rectangle((0, 0), W, HB, fc='#000', zorder=1))
    tex = plt.imread(REPO / 'external' / 'img' / 'dark-texture.jpg')
    ax.imshow(tex, extent=(1180, W, HB, 0), aspect='auto', alpha=0.55, zorder=1.1)
    ax.add_patch(MPolygon([(1960, 0), (W, 0), (W, HB), (2110, HB)], closed=True, fc=GOLD, zorder=1.2))
    ax.add_patch(MPolygon([(1930, 0), (1960, 0), (2110, HB), (2080, HB)], closed=True, fc='#000', zorder=1.3))
    bx, bw, bh = 100, 700, 420
    ax.add_patch(FancyBboxPatch((bx, -60), bw, bh + 60, boxstyle='round,pad=0,rounding_size=46',
                                fc=GOLD, ec='none', zorder=2))
    logo = Image.open(REPO / 'external' / 'img' / 'usm-logo.png').convert('RGBA')
    logo = logo.crop(logo.getbbox())
    lw_px = bw - 120
    lh_px = round(lw_px * logo.height / logo.width)
    logo = np.asarray(logo.resize((lw_px, lh_px), Image.LANCZOS)) / 255
    lx, ly = round(bx + (bw - lw_px) / 2), round(bh / 2 - lh_px / 2)
    ax.imshow(logo, extent=(lx, lx + lw_px, ly + lh_px, ly), interpolation='none', zorder=6)
    cx0 = 880
    text(ax, cx0, 92, spaced('School of Construction and Design'), F_BOLD, 8.4, GOLD, va='center')
    text(ax, cx0, 150, 'University of Southern Mississippi', F_SEMI, 10, (1, 1, 1, 0.88), va='center')
    text(ax, cx0, 262, 'HOSPITAL BIM', F_HERO, 33, 'white', va='center')
    text(ax, cx0, 380, 'AR DEMO', F_HERO, 33, 'white', va='center')
    tag_x = (1960 + W + W + 2110) / 4 + 10
    text(ax, tag_x, 205, 'BIM', F_WIDE, 24, BLACK, ha='center', va='center', zorder=3)
    text(ax, tag_x, 320, 'MEP · AR', F_BOLD, 15, BLACK, ha='center', va='center', zorder=3)

    # ---------------------------------------------------------------- Level 1 plan (AR anchor), as large as the sheet allows
    margin = 90
    lo, hi = plan_bounds(model)
    pad = 7.0
    top = 560
    card_h = 1980
    k1 = min((W - 2 * margin - 60) / (hi[0] - lo[0] + 2 * pad), (card_h - 40) / (hi[1] - lo[1] + 2 * pad))
    text(ax, margin, top - 30, spaced('Level 1 plan  ·  AR anchor'), F_BOLD, 9, GOLD_INK, va='bottom')
    card(ax, margin, top, W - 2 * margin, card_h)
    ox1 = W / 2 - (lo[0] + hi[0]) / 2 * k1
    oy1 = top + 20 + (card_h - 40 - (hi[1] - lo[1]) * k1) / 2 - lo[1] * k1
    tf1 = tf_of(ox1, oy1, k1)
    draw_grids(ax, model, tf1, bubble_r=26, lw=1.2)
    draw_arch_plan(ax, model, 'Level 1', tf1, k1, detail=True)

    # legend, scale and north in the card's top-left corner (the plan narrows towards the top)
    lx0, ly = margin + 40, top + 60
    text(ax, lx0, ly, spaced('Legend'), F_BOLD, 7.5, GOLD_INK, va='center')
    items = [('Walls & columns (cut)', 'fill', POCHE), ('Curtain wall & windows', 'line', GLASS),
             ('Doors', 'line', DOOR), ('Fixtures, casework, stairs', 'fill', FIXTURE),
             ('Floor plate', 'fill', FLOOR), ('Structural grid', 'grid', '#9aa3ab')]
    for i, (lab, kind, col) in enumerate(items):
        y = ly + 60 + i * 52
        if kind == 'fill':
            ax.add_patch(Rectangle((lx0, y - 13), 54, 26, fc=col, ec=BLACK if col == POCHE else '#bdb7aa', lw=0.6, zorder=3))
        elif kind == 'line':
            ax.plot([lx0, lx0 + 54], [y, y], color=col, lw=3.0, zorder=3, solid_capstyle='round')
        else:
            ax.plot([lx0, lx0 + 54], [y, y], color=col, lw=1.2, dashes=(10, 3, 2, 3), zorder=3)
        text(ax, lx0 + 72, y, lab, F_SEMI, 6.2, INK, va='center')
    sb_y = ly + 60 + len(items) * 52 + 30
    text(ax, lx0, sb_y, spaced('Scale'), F_BOLD, 7.5, GOLD_INK, va='center')
    step = 25 * FT * k1                                            # 25 ft segments
    for i in range(4):
        ax.add_patch(Rectangle((lx0 + i * step, sb_y + 34), step, 12, fc=BLACK if i % 2 == 0 else 'white',
                               ec=BLACK, lw=0.8, zorder=6))
    for i in range(0, 5, 2):
        text(ax, lx0 + i * step, sb_y + 72, f'{i * 25}', F_SEMI, 6.0, MUTED, ha='center', va='center')
    text(ax, lx0 + 4 * step + 14, sb_y + 40, 'ft', F_SEMI, 6.0, MUTED, va='center')
    nx, ny = lx0 + 26, sb_y + 160
    ax.annotate('', xy=(nx, ny - 32), xytext=(nx, ny + 32),
                arrowprops=dict(arrowstyle='-|>', color=BLACK, lw=2.4, mutation_scale=26), zorder=6)
    text(ax, nx + 38, ny, 'N', F_BOLD, 9, BLACK, va='center')

    # QR in the card's top-right corner
    qr_w, qr_h = 420, 450
    qx, qy = W - margin - 30 - qr_w, top + 30
    card(ax, qx, qy, qr_w, qr_h, fc=BLACK, ec=BLACK, r=22, z=6)
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(URL)
    qr.make(fit=True)
    qimg = np.asarray(qr.make_image(fill_color='black', back_color='white').convert('RGB'))
    qs = 290
    ax.imshow(qimg, extent=(qx + (qr_w - qs) / 2, qx + (qr_w + qs) / 2, qy + 30 + qs, qy + 30),
              interpolation='nearest', zorder=7)
    text(ax, qx + qr_w / 2, qy + 30 + qs + 52, 'SCAN TO LAUNCH', F_HERO, 9, GOLD, ha='center', va='center', zorder=7)
    text(ax, qx + qr_w / 2, qy + 30 + qs + 100, 'then aim at this sheet', F_SEMI, 6.6, 'white', ha='center',
         va='center', zorder=7)

    # ---------------------------------------------------------------- sub-models in 2D
    y4 = top + card_h + 100
    text(ax, margin, y4 - 30, spaced('Sub-models  ·  2D drawings from the model'), F_BOLD, 9, GOLD_INK, va='bottom')
    gap = 30
    sw = (W - 2 * margin - 3 * gap) / 4
    sh = 360
    cards = [('ARCH', 'Level 4 plan', lambda a, t, k: draw_arch_plan(a, model, 'Level 4', t, k, detail=False)),
             ('STRUCT', 'Level 3 framing', lambda a, t, k: draw_struct_plan(a, model, 'Level 3', t, k)),
             ('HVAC', 'Level 4 ductwork', lambda a, t, k: draw_mep_plan(a, model, 'hvac', 'Level 4', t, k)),
             ('PIPING', 'Level 4 piping', lambda a, t, k: draw_mep_plan(a, model, 'piping', 'Level 4', t, k))]
    blo, bhi = plan_bounds(model, with_grids=False)
    for i, (tag, cap, fn) in enumerate(cards):
        x = margin + i * (sw + gap)
        card(ax, x, y4, sw, sh)
        aw, ah = sw - 40, sh - 70 - 30
        k = min(aw / (bhi[0] - blo[0]), ah / (bhi[1] - blo[1]))
        ox = x + 20 + (aw - (bhi[0] - blo[0]) * k) / 2 - blo[0] * k
        oy = y4 + 15 + (ah - (bhi[1] - blo[1]) * k) / 2 - blo[1] * k
        tf = tf_of(ox, oy, k)
        if tag == 'STRUCT':
            draw_grids(ax, model, tf, 0, lw=0.5, labels=False, color='#c5cbd0')
        fn(ax, tf, k)
        ax.add_patch(FancyBboxPatch((x, y4 + sh - 70), sw, 70, boxstyle='round,pad=0,rounding_size=26',
                                    fc=BLACK, ec='none', zorder=7))
        ax.add_patch(Rectangle((x, y4 + sh - 70), sw, 30, fc=BLACK, ec='none', zorder=7))
        text(ax, x + 26, y4 + sh - 35, tag, F_WIDE, 9, GOLD, va='center', zorder=8)
        text(ax, x + sw - 24, y4 + sh - 35, cap, F_SEMI, 7, 'white', ha='right', va='center', zorder=8)

    # ---------------------------------------------------------------- in the app (one line)
    yf = y4 + sh + 62
    text(ax, margin, yf, spaced('In the app'), F_BOLD, 7.5, GOLD_INK, va='center')
    text(ax, margin + 300, yf, 'Original · Enhanced · Compare   ·   ambient occlusion   ·   X-ray   ·   Section by level'
         '   ·   Systems colours   ·   tap any element for its Revit data', F_SEMI, 6.6, INK, va='center')

    # ---------------------------------------------------------------- footer
    ax.add_patch(Rectangle((0, H - 70), W, 70, fc=BLACK, zorder=1))
    ax.add_patch(Rectangle((0, H - 74), W, 4, fc=GOLD, zorder=1))
    text(ax, margin, H - 35, 'Built by Dr. Salar Kamari', F_BOLD, 7.5, GOLD, va='center')
    text(ax, W / 2, H - 35, 'Revit sample model: Autodesk hospital (Revit 2018)  ·  School of Construction and Design',
         F_SEMI, 6.6, 'white', ha='center', va='center')
    text(ax, W - margin, H - 35, 'Print at 100%  ·  keep flat & well lit', F_SEMI, 6.6, (1, 1, 1, 0.8),
         ha='right', va='center')

    out = PRINT / 'hospital-poster.png'
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    sheet = Image.open(out).convert('RGB')
    assert sheet.size == (W, H), sheet.size
    sheet.save(out, dpi=(DPI, DPI))
    for name, (pw_in, ph_in) in (('letter', (8.5, 11.0)), ('a4', (8.27, 11.69))):
        page = Image.new('RGB', (round(pw_in * DPI), round(ph_in * DPI)), 'white')
        page.paste(sheet, ((page.width - W) // 2, (page.height - H) // 2))
        target = PRINT / f'hospital-poster-{name}.pdf'
        try:
            page.save(target, resolution=DPI)
        except PermissionError:                      # the PDF is open in a viewer
            for suffix in ('-new', '-new2', '-new3'):
                target = target.with_name(f'hospital-poster-{name}{suffix}.pdf')
                try:
                    page.save(target, resolution=DPI)
                    break
                except PermissionError:
                    continue
        print('wrote', target.name)
    sheet.resize((900, 1200), Image.LANCZOS).save(ASSETS / 'poster-preview.jpg', quality=82)
    placement = {
        'poster_px': [W, H],
        'plan_origin_px': [round(ox1, 2), round(oy1, 2)],
        'px_per_m': round(k1, 4),
        'model_scale': round(k1 / H, 7),     # 1 m of model = this many sheet heights
        'image': 'assets/poster-preview.jpg',
        'url': URL,
    }
    (ASSETS / 'target-placement.json').write_text(json.dumps(placement, indent=1))
    print('placement:', placement)


if __name__ == '__main__':
    main()
