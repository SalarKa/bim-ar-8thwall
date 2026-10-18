"""Printable poster = AR image target, styled like the USM career fair app (black, USM gold, warm
gray, Archivo). Level 1 plan with structural grids (the AR anchor), a 4D construction sequence,
a schedule strip, and the QR code that launches the app.

The poster is exactly 3:4 portrait so 8th Wall's default crop uses the whole sheet.
Writes print/poster.png + print/poster.pdf and src/target-placement.json, which tells the
app where the Level 1 plan sits on the sheet so the 3D model lands exactly on top of it.

Usage: python -I pipeline/make_poster.py <app_url>
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import qrcode
import trimesh
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, PathPatch, Polygon as MPolygon, Rectangle
from matplotlib.path import Path as MPath
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from svgpath2mpl import parse_path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
PRINT = ROOT / 'print'
OUT = ROOT / 'pipeline' / 'out'
FONTS = ROOT / 'pipeline' / 'fonts'
W, H = 2400, 3200  # px, 3:4
DPI = 300
FT = 0.3048

# career fair app tokens
BLACK, GOLD, GOLD_INK, BG, INK, MUTED, LINE = '#1A1A1A', '#FFD046', '#8a6a00', '#F5F5F2', '#333333', '#6b6b6b', '#e8e8e8'
F_HERO = FontProperties(fname=FONTS / 'Archivo-Black-Italic.ttf')
F_WIDE = FontProperties(fname=FONTS / 'Archivo-ExtraBold-Wide.ttf')
F_BOLD = FontProperties(fname=FONTS / 'Archivo-Bold.ttf')
F_SEMI = FontProperties(fname=FONTS / 'Archivo-SemiBold.ttf')
F_REG = FontProperties(fname=FONTS / 'Archivo-Regular.ttf')

MODEL_CREDIT = ('Revit model & 4D schedule: AEC 450 course materials, School of Construction and Design, '
                'University of Southern Mississippi · used for educational purposes')
SNAPSHOT_DATES = [date(2025, 2, 10), date(2025, 3, 10), date(2025, 4, 7), date(2025, 5, 26)]
GANTT_ROWS = [  # label, task-id prefixes
    ('Foundations', ['110']),
    ('Level 1 structure', ['120', '130', '140']),
    ('Level 2 structure', ['210', '220', '230', '240']),
    ('Level 3 structure', ['310', '320', '330', '340']),
    ('Exterior walls & core', ['150', '250', '350']),
    ('Roof', ['410', '420']),
    ('Stairs & railings', ['510', '810']),
    ('Curtain walls & windows', ['710', '610']),
    ('Interior walls & doors', ['910', '620']),
]


def parse_d(d):
    """SVG path with M/L/Z only -> matplotlib Path."""
    verts, codes = [], []
    for cmd, body in re.findall(r'([MLZ])([^MLZ]*)', d):
        if cmd == 'Z':
            if verts:
                verts.append(verts[-1]); codes.append(MPath.CLOSEPOLY)
            continue
        x, y = map(float, body.split(','))
        verts.append((x, y)); codes.append(MPath.MOVETO if cmd == 'M' else MPath.LINETO)
    return MPath(verts, codes) if verts else None


def text(ax, x, y, s, fp, size, color=INK, **kw):
    return ax.text(x, y, s, fontproperties=fp, fontsize=size, color=color, **kw)


def spaced(s, n=1):
    """Letter-spaced uppercase, the app's eyebrow style."""
    return (' ' * n).join(s.upper())


def draw_logo(ax, svg_path, x, y, width, zorder=6):
    root = ET.parse(svg_path).getroot()
    vb = [float(v) for v in root.get('viewBox').split()]
    s = width / vb[2]
    default_fill = root.get('fill', '#000')
    tf = matplotlib.transforms.Affine2D().translate(-vb[0], -vb[1]).scale(s).translate(x, y) + ax.transData
    for el in root.iter():
        if not el.tag.endswith('path'):
            continue
        fill = el.get('fill', default_fill)
        if fill == 'none':
            continue
        ax.add_patch(PathPatch(parse_path(el.get('d')), transform=tf, fc=fill, ec='none', zorder=zorder))
    return vb[3] * s


def draw_grids(ax, grids, tf_pt, k, lw=1.0):
    """Grid lines, bubbles and dimension strings from plan_geom (plan ft coords)."""
    r = 2.1
    for ln in grids['lines']:
        (x0, y0), (x1, y1) = tf_pt(ln['a']), tf_pt(ln['b'])
        ax.plot([x0, x1], [y0, y1], color='#9aa3ab', lw=0.7 * lw, dashes=(10, 3, 2, 3), zorder=1.5)
        for b in ln['bubbles']:
            bx, by = tf_pt(b)
            ax.add_patch(matplotlib.patches.Circle((bx, by), r * k, fc='white', ec=BLACK, lw=1.1 * lw, zorder=5))
            text(ax, bx, by + 1, ln['name'], F_BOLD, 2.3 * k / DPI * 72 * 0.85, BLACK, ha='center',
                 va='center', zorder=6)
    for d in grids['dims']:
        (x0, y0), (x1, y1) = tf_pt(d['a']), tf_pt(d['b'])
        ax.plot([x0, x1], [y0, y1], color=MUTED, lw=0.6 * lw, zorder=1.6)
        for x, y in ((x0, y0), (x1, y1)):
            ax.plot([x - 0.5 * k, x + 0.5 * k], [y + 0.5 * k, y - 0.5 * k], color=MUTED, lw=0.9 * lw, zorder=1.6)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        size = 1.9 * k / DPI * 72 * 0.8
        if abs(x1 - x0) < 1e-6:
            text(ax, mx - 0.6 * k, my, d['label'], F_SEMI, size, MUTED, ha='center', va='bottom',
                 rotation=90, zorder=6)
        else:
            text(ax, mx, my - 0.6 * k, d['label'], F_SEMI, size, MUTED, ha='center', va='bottom', zorder=6)


def draw_plan(ax, level, groups, grids, ox, oy, k, lw=1.0):
    """Draw a level plan with its origin at pixel (ox, oy), k px per ft."""
    tf = matplotlib.transforms.Affine2D().scale(k).translate(ox, oy) + ax.transData
    draw_grids(ax, grids, lambda p: (ox + p[0] * k, oy + p[1] * k), k, lw)
    for item in level['projected']:
        p = parse_d(item['d'])
        if p is None:
            continue
        floor = item['group'] == 'floors'
        ax.add_patch(PathPatch(p, transform=tf, lw=0.6 * lw, ec='#c9c3b7' if floor else '#7d8790',
                               fc='#f6f3ec' if floor else 'none', zorder=1 if floor else 2))
    for item in level['cut']:
        p = parse_d(item['d'])
        if p is None:
            continue
        color = groups[item['group']]['color']
        if item['fill']:
            ax.add_patch(PathPatch(p, transform=tf, lw=0.4 * lw, ec=BLACK, fc=color, zorder=3))
        else:
            ax.add_patch(PathPatch(p, transform=tf, lw=1.6 * lw, ec=color, fc='none',
                                   capstyle='round', zorder=4))


def element_dates(index, sched):
    tasks = {t['id']: t for t in sched['tasks']}
    out = {}
    for eid, e in index['elements'].items():
        if e.get('task'):
            out[int(eid)] = date.fromisoformat(tasks[e['task']]['start'])
    return out


def render_axon(path_png, index, sched, upto=None, size=(7, 3.6), zoom=0.95, hide=('ceilings',)):
    """Axonometric of the model (optionally only elements started by `upto`).

    All triangles go into ONE collection so matplotlib depth-sorts faces individually; separate
    collections are sorted as whole groups, which buried the walls behind other groups."""
    scene = trimesh.load(ASSETS / 'model.glb')
    starts = element_dates(index, sched)
    light = np.array([-0.35, -0.55, 0.75]); light /= np.linalg.norm(light)
    all_tris, all_cols = [], []
    lo_all, hi_all = None, None
    for name, geom in scene.geometry.items():
        if name in hide:
            continue
        T = scene.graph.get(name)[0]
        v = trimesh.transformations.transform_points(geom.vertices, T)
        faces = geom.faces
        lo_g, hi_g = v.min(0), v.max(0)
        lo_all = lo_g if lo_all is None else np.minimum(lo_all, lo_g)
        hi_all = hi_g if hi_all is None else np.maximum(hi_all, hi_g)
        if upto is not None:
            keep = np.zeros(len(faces), bool)
            for eid, start, count in index['ranges'][name]:
                d = starts.get(eid)
                if d is not None and d <= upto:
                    keep[start:start + count] = True
            faces = faces[keep]
        if not len(faces):
            continue
        tris = v[faces][:, :, [0, 2, 1]] * [1, -1, 1]  # x, north, up
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        shade = 0.5 + 0.5 * np.abs(n @ light)
        rgba = np.array(geom.visual.material.baseColorFactor) / 255
        cols = np.clip(rgba[None, :3] * shade[:, None], 0, 1)
        all_tris.append(tris)
        all_cols.append(np.c_[cols, np.full(len(cols), max(rgba[3], 0.55))])
    fig = plt.figure(figsize=size, dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1], projection='3d', proj_type='ortho')
    # Painter's sorting by face centroid lets the big slab faces sort in front of the columns standing
    # on them (steel appeared under the foundation). Draw the ground and anything at or below the
    # Level 1 slab top as a fixed base layer, then the building on top, sorted per face.
    ax.computed_zorder = False
    lo, hi = lo_all, hi_all
    ground = [[(lo[0], -hi[2], -0.2), (hi[0], -hi[2], -0.2), (hi[0], -lo[2], -0.2), (lo[0], -lo[2], -0.2)]]
    ax.add_collection3d(Poly3DCollection(ground, facecolors=(0.88, 0.87, 0.84, 1), edgecolors=(0.7, 0.7, 0.68, 1),
                                         linewidths=0.4, zorder=0))
    if all_tris:
        tris, cols = np.vstack(all_tris), np.vstack(all_cols)
        base = tris[:, :, 2].max(1) <= 0.05          # slab on grade and foundation (top at Level 1)
        if base.any():
            ax.add_collection3d(Poly3DCollection(tris[base], facecolors=cols[base], edgecolors='none', zorder=1))
        ax.add_collection3d(Poly3DCollection(tris[~base], facecolors=cols[~base], edgecolors='none', zorder=2))
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(-hi[2], -lo[2]); ax.set_zlim(lo[1], hi[1])
    ax.set_box_aspect((hi[0] - lo[0], hi[2] - lo[2], hi[1] - lo[1]), zoom=zoom)
    ax.view_init(elev=26, azim=-56)
    ax.set_axis_off()
    fig.savefig(path_png, transparent=True)
    plt.close(fig)


def trim(img):
    rows, cols = np.where(img[:, :, 3] > 0.02)
    return img[rows.min():rows.max() + 1, cols.min():cols.max() + 1]


def card(ax, x, y, w, h, fc='white', ec=LINE, r=26, z=0.6, lw=1.2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}', fc=fc, ec=ec,
                                lw=lw, zorder=z))


def main(url):
    index = json.loads((ASSETS / 'model-index.json').read_text(encoding='utf-8'))
    sched = json.loads((ASSETS / 'schedule.json').read_text(encoding='utf-8'))
    plan = json.loads((OUT / 'plan_geom.json').read_text(encoding='utf-8'))
    groups = index['groups']
    xmin, ymin, xmax, ymax = plan['bounds_ft']
    pad = plan['pad_ft']
    levels = {lv['name']: lv for lv in plan['levels']}
    PRINT.mkdir(exist_ok=True)

    snaps = []
    for i, d in enumerate(SNAPSHOT_DATES):
        p = OUT / f'axon_{i}.png'
        render_axon(p, index, sched, upto=d, size=(6, 3.4))
        snaps.append(p)

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.set_aspect('equal'); ax.axis('off')
    ax.add_patch(Rectangle((0, 0), W, H, fc=BG, zorder=0))

    # ---------------------------------------------------------------- masthead
    HB = 470
    ax.add_patch(Rectangle((0, 0), W, HB, fc='#000', zorder=1))
    tex = plt.imread(ROOT / 'external' / 'img' / 'dark-texture.jpg')
    ax.imshow(tex, extent=(1180, W, HB, 0), aspect='auto', alpha=0.55, zorder=1.1)
    # banner diagonal: textured panel ends on a 70% slant with a gold seam behind it
    ax.add_patch(MPolygon([(1960, 0), (W, 0), (W, HB), (2110, HB)], closed=True, fc=GOLD, zorder=1.2))
    ax.add_patch(MPolygon([(1930, 0), (1960, 0), (2110, HB), (2080, HB)], closed=True, fc='#000', zorder=1.3))
    # gold logo block hanging below the bar, rounded bottom corners
    bx, bw, bh = 100, 700, 420
    ax.add_patch(FancyBboxPatch((bx, -60), bw, bh + 60, boxstyle='round,pad=0,rounding_size=46',
                                fc=GOLD, ec='none', zorder=2))
    # logo pre-rendered from the SVG by librsvg (sharp) so its knocked-out white areas survive; centred
    logo = trim(plt.imread(ROOT / 'external' / 'img' / 'usm-logo.png'))
    lw_px = bw - 120
    lh_px = round(lw_px * logo.shape[0] / logo.shape[1])
    # resample with Pillow (alpha-aware) to the exact pixel size; matplotlib's resampling of
    # RGBA left the letter strokes hollow
    from PIL import Image
    logo = np.asarray(Image.fromarray((logo * 255).astype(np.uint8)).resize((lw_px, lh_px), Image.LANCZOS)) / 255
    lx, ly = round(bx + (bw - lw_px) / 2), round(bh / 2 - lh_px / 2)
    ax.imshow(logo, extent=(lx, lx + lw_px, ly + lh_px, ly), interpolation='none', zorder=6)
    # copy column
    cx0 = 880
    text(ax, cx0, 92, spaced('School of Construction and Design'), F_BOLD, 8.4, GOLD, va='center')
    text(ax, cx0, 150, 'University of Southern Mississippi', F_SEMI, 10, (1, 1, 1, 0.88), va='center')
    text(ax, cx0, 262, 'AUGMENTED', F_HERO, 33, 'white', va='center')
    text(ax, cx0, 380, 'REALITY DEMO', F_HERO, 33, 'white', va='center')
    # gold corner tag, centred in the slanted panel
    tag_x = (1960 + W + W + 2110) / 4 + 10
    text(ax, tag_x, 205, 'BIM', F_WIDE, 24, BLACK, ha='center', va='center', zorder=3)
    text(ax, tag_x, 320, '4D · AR', F_BOLD, 15, BLACK, ha='center', va='center', zorder=3)

    # ---------------------------------------------------------------- Level 1 plan (AR anchor)
    margin = 90
    plan_w_ft = xmax - xmin + 2 * pad
    plan_h_ft = ymax - ymin + 2 * pad
    k1 = (W - 2 * margin - 40) / plan_w_ft
    top = 560
    text(ax, margin, top - 30, spaced('Level 1 plan  ·  AR anchor'), F_BOLD, 9, GOLD_INK, va='bottom')
    card_h = plan_h_ft * k1 + 40
    card(ax, margin, top, W - 2 * margin, card_h)
    ox1 = W / 2 - (xmin + xmax) / 2 * k1
    oy1 = top + 20 + (ymax + pad) * k1
    draw_plan(ax, levels['Level 1'], groups, plan['grids'], ox1, oy1, k1, lw=1.15)
    # north arrow + scale bar on the section header row, clear of the grid bubbles
    # bar first, labels underneath, so nothing reaches up into the banner (which ends at HB)
    sb_x, sb_y = W - margin - 40 * k1 - 190, HB + 32
    for i in range(4):
        ax.add_patch(Rectangle((sb_x + i * 10 * k1, sb_y), 10 * k1, 13, fc=BLACK if i % 2 == 0 else 'white',
                               ec=BLACK, lw=0.8, zorder=6))
    for i in range(5):
        text(ax, sb_x + i * 10 * k1, sb_y + 36, f'{i * 10}', F_SEMI, 6.3, MUTED, ha='center', va='center')
    text(ax, sb_x + 40 * k1 + 30, sb_y + 7, 'ft', F_SEMI, 6.3, MUTED, va='center')
    nx, ny = W - margin - 30, HB + 50
    ax.annotate('', xy=(nx, ny - 22), xytext=(nx, ny + 30),
                arrowprops=dict(arrowstyle='-|>', color=BLACK, lw=2.4, mutation_scale=26), zorder=6)
    text(ax, nx - 34, ny + 4, 'N', F_BOLD, 9, BLACK, ha='center', va='center')

    # ---------------------------------------------------------------- 4D sequence
    y4 = top + card_h + 110
    text(ax, margin, y4 - 30, spaced('4D construction sequence  ·  2025'), F_BOLD, 9, GOLD_INK, va='bottom')
    sw = (W - 2 * margin - 3 * 30) / 4
    sh = 380
    captions = ['Level 1 frame', 'Level 2 & 3 frame', 'Roof & envelope', 'Complete']
    for i, (p, d) in enumerate(zip(snaps, SNAPSHOT_DATES)):
        x = margin + i * (sw + 30)
        card(ax, x, y4, sw, sh, fc='white')
        img = trim(plt.imread(p))
        iw = sw - 50; ih = iw * img.shape[0] / img.shape[1]
        if ih > sh - 120:
            ih = sh - 120; iw = ih * img.shape[1] / img.shape[0]
        ax.imshow(img, extent=(x + (sw - iw) / 2, x + (sw + iw) / 2, y4 + 25 + ih, y4 + 25), zorder=2)
        ax.add_patch(Rectangle((x, y4 + sh - 78), sw, 78, fc=BLACK, zorder=1.8))
        ax.add_patch(FancyBboxPatch((x, y4 + sh - 78), sw, 78, boxstyle='round,pad=0,rounding_size=26',
                                    fc=BLACK, ec='none', zorder=1.8))
        text(ax, x + 26, y4 + sh - 39, d.strftime('%b %d').upper(), F_WIDE, 9.5, GOLD, va='center', zorder=3)
        text(ax, x + sw - 24, y4 + sh - 39, captions[i], F_SEMI, 7.5, 'white', ha='right', va='center', zorder=3)

    # ---------------------------------------------------------------- schedule strip + QR
    yg = y4 + sh + 110
    qr_w = 640
    gx0, gx1 = margin, W - margin - qr_w - 50
    text(ax, gx0, yg - 30, spaced('Schedule  ·  Jan – May 2025'), F_BOLD, 9, GOLD_INK, va='bottom')
    gh = 70 * len(GANTT_ROWS) + 110
    card(ax, gx0, yg, gx1 - gx0, gh)
    t0 = date.fromisoformat(sched['start']); t1 = date.fromisoformat(sched['end']) + timedelta(days=1)
    lab_w = 470
    bx0, bx1 = gx0 + lab_w, gx1 - 40
    span = (t1 - t0).days

    def X(d):
        return bx0 + (d - t0).days / span * (bx1 - bx0)

    m = date(t0.year, t0.month, 1)
    while m <= t1:
        if m >= t0:
            ax.plot([X(m), X(m)], [yg + 60, yg + gh - 30], color=LINE, lw=1, zorder=1)
        text(ax, X(max(m, t0)) + 8, yg + 42, m.strftime('%b').upper(), F_BOLD, 7, MUTED, va='center')
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    tasks = sched['tasks']
    for r, (label, prefixes) in enumerate(GANTT_ROWS):
        y = yg + 90 + r * 70
        text(ax, gx0 + 30, y + 22, label, F_SEMI, 7.6, INK, va='center')
        for t in tasks:
            if any(t['id'].startswith(pf) for pf in prefixes):
                a = X(date.fromisoformat(t['start'])); b = X(date.fromisoformat(t['end']) + timedelta(days=1))
                ax.add_patch(FancyBboxPatch((a, y + 6), max(b - a, 8), 32, boxstyle='round,pad=0,rounding_size=8',
                                            fc=GOLD if r % 2 == 0 else BLACK, ec='none', zorder=2))

    qx, qy = W - margin - qr_w, yg - 70
    qh = gh + 70
    card(ax, qx, qy, qr_w, qh, fc=BLACK, ec=BLACK)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=10)
    qr.add_data(url); qr.make(fit=True)
    q = np.array(qr.make_image(fill_color='black', back_color='white').convert('L')) / 255
    qs = qr_w - 120
    ax.imshow(q, cmap='gray', extent=(qx + 60, qx + 60 + qs, qy + 60 + qs, qy + 60), interpolation='nearest',
              zorder=3)
    text(ax, qx + qr_w / 2, qy + qs + 125, 'SCAN TO LAUNCH', F_HERO, 15, GOLD, ha='center', va='center')
    text(ax, qx + qr_w / 2, qy + qs + 195, 'Phones & tablets  ·  no app needed', F_SEMI, 7.5, 'white',
         ha='center', va='center')
    text(ax, qx + qr_w / 2, qy + qh - 50, url.replace('https://', ''), F_REG, 6.5, (1, 1, 1, 0.7),
         ha='center', va='center')

    # ---------------------------------------------------------------- legend + footer
    ax.add_patch(Rectangle((0, H - 80), W, 80, fc='#000', zorder=1))
    ax.add_patch(Rectangle((0, H - 86), W, 6, fc=GOLD, zorder=1))
    text(ax, margin, H - 54, 'Built by Dr. Salar Kamari', F_BOLD, 8, GOLD, va='center')
    text(ax, W / 2, H - 54, 'School of Construction and Design  ·  University of Southern Mississippi',
         F_SEMI, 7, 'white', ha='center', va='center')
    text(ax, W - margin, H - 54, 'Print at 100% · keep flat & well lit',
         F_REG, 7, (1, 1, 1, 0.7), ha='right', va='center')
    # model credit: no original author could be traced, so it is credited as course material
    text(ax, W / 2, H - 20, MODEL_CREDIT, F_REG, 5.6, (1, 1, 1, 0.72), ha='center', va='center')

    fig.savefig(PRINT / 'poster.png', dpi=DPI)
    try:
        fig.savefig(PRINT / 'poster.pdf')
    except PermissionError:  # the PDF is open in a viewer on Windows; don't lose the new version
        fig.savefig(PRINT / 'poster-new.pdf')
        print('poster.pdf is open elsewhere; wrote print/poster-new.pdf instead')
    plt.close(fig)

    placement = {
        'poster_px': [W, H],
        'plan_origin_px': [ox1, oy1],
        'px_per_ft': k1,
        # 8th Wall image-target space: target height = 1 unit, centre at origin, +Y up the sheet
        'model_position': [(ox1 - W / 2) / H, (H / 2 - oy1) / H, 0],
        'model_scale': k1 / FT / H,
        'url': url,
    }
    (ROOT / 'src' / 'target-placement.json').write_text(json.dumps(placement, indent=2), encoding='utf-8')
    print(json.dumps(placement, indent=2))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'https://salarka.github.io/bim-ar-8thwall/')
