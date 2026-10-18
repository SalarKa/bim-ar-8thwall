"""18" x 24" easel sign for the FORGE Your Path Career Expo booth.

Meets the expo's minimum branding: trade partner (School of Construction and Design, USM) name and
logo prominent at the top, and the activity name in bold. Same visual language as the AR poster
(black / USM gold / warm gray, Archivo) and it features the demo: hero render, 4D sequence,
how-it-works steps and the QR code that launches the app. The tabletop poster stays the AR target.

Usage: python -I pipeline/make_easel_sign.py [app_url]
Writes print/forge-easel-sign-18x24.pdf and .png
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import qrcode
from matplotlib.patches import Circle, FancyBboxPatch, Polygon as MPolygon, Rectangle
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I
import make_poster as mp  # noqa: E402

ROOT = mp.ROOT
OUT = mp.OUT
PRINT = mp.PRINT
W, H = 3600, 4800            # drawing units: 200 px per inch on an 18 x 24 in sheet
SIGN_IN = (18, 24)
PNG_DPI = 200
EXPO = 'Proud trade partner of FORGE Your Path'
FORGE_ORANGE = '#E24307'
text, spaced, card, trim = mp.text, mp.spaced, mp.card, mp.trim
BLACK, GOLD, GOLD_INK, BG, INK, MUTED = mp.BLACK, mp.GOLD, mp.GOLD_INK, mp.BG, mp.INK, mp.MUTED


def place_image(ax, img, x, y, w, zorder=5, resample=True):
    """Place an RGBA array with its top-left at (x, y), width w (drawing units)."""
    h = w * img.shape[0] / img.shape[1]
    if resample:  # Pillow resamples alpha correctly; matplotlib's resampling leaves halos on logos
        px_w = max(1, round(w / W * SIGN_IN[0] * PNG_DPI * 1.5))
        px_h = max(1, round(px_w * img.shape[0] / img.shape[1]))
        img = np.asarray(Image.fromarray((img * 255).astype(np.uint8)).resize((px_w, px_h), Image.LANCZOS)) / 255
    ax.imshow(img, extent=(x, x + w, y + h, y), interpolation='none' if resample else 'antialiased', zorder=zorder)
    return h


EXPLODE_M = 17.0   # how far the plan floats below the building in the exploded axonometric


def exploded_plan(plan_geom, groups, level='Level 1', depth=EXPLODE_M):
    """extra(ax) callback for mp.render_axon: the 2D plan drawn flat under the building, in the same
    projection, with gold tie lines from the corner grid intersections up to the model."""
    from matplotlib.path import Path as MPath
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
    FT = mp.FT
    lv = next(l for l in plan_geom['levels'] if l['name'] == level)
    z = -depth

    def to3(x, y):                      # plan ft (x east, y = -north) -> x, north, up (m)
        return (x * FT, -y * FT, z)

    def subpaths(d):
        path = mp.parse_d(d)
        if path is None:
            return []
        out, cur = [], []
        for (x, y), code in zip(path.vertices, path.codes):
            if code == MPath.MOVETO and cur:
                out.append(cur); cur = []
            if code != MPath.CLOSEPOLY:
                cur.append(to3(x, y))
        if cur:
            out.append(cur)
        return out

    def draw(ax):
        xs = [l['a'][0] for l in plan_geom['grids']['lines']] + [l['b'][0] for l in plan_geom['grids']['lines']]
        ys = [l['a'][1] for l in plan_geom['grids']['lines']] + [l['b'][1] for l in plan_geom['grids']['lines']]
        x0, x1, y0, y1 = min(xs) - 7, max(xs) + 7, min(ys) - 7, max(ys) + 7
        sheet = [[to3(x0, y0), to3(x1, y0), to3(x1, y1), to3(x0, y1)]]
        ax.add_collection3d(Poly3DCollection(sheet, facecolors=(1, 1, 1, 1), edgecolors=(0.35, 0.35, 0.32, 1),
                                             linewidths=1.4, zorder=-3))
        floors = [sp for it in lv['projected'] if it['group'] == 'floors' for sp in subpaths(it['d']) if len(sp) > 2]
        if floors:
            ax.add_collection3d(Poly3DCollection(floors, facecolors=(0.96, 0.95, 0.92, 1), edgecolors=(0.8, 0.78, 0.72, 1),
                                                 linewidths=0.3, zorder=-2.8))
        lines = []
        for ln in plan_geom['grids']['lines']:
            lines.append([to3(*ln['a']), to3(*ln['b'])])
        ax.add_collection3d(Line3DCollection(lines, colors=(0.45, 0.5, 0.55, 1), linewidths=0.9,
                                             linestyles=(0, (6, 2, 1, 2)), zorder=-2.6))
        fills, strokes, fcols, scols = [], [], [], []
        for it in lv['cut']:
            col = groups[it['group']]['color']
            for sp in subpaths(it['d']):
                if it['fill'] and len(sp) > 2:
                    fills.append(sp); fcols.append(col)
                elif len(sp) > 1:
                    strokes.append(sp); scols.append(col)
        if fills:
            ax.add_collection3d(Poly3DCollection(fills, facecolors=fcols, edgecolors=mp.BLACK, linewidths=0.5,
                                                 zorder=-2.4))
        if strokes:
            ax.add_collection3d(Line3DCollection(strokes, colors=scols, linewidths=1.5, zorder=-2.3))
        # grid bubbles
        t = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        r = 2.1
        for ln in plan_geom['grids']['lines']:
            for bx, by in ln['bubbles']:
                ring = [to3(bx + r * np.cos(a), by + r * np.sin(a)) for a in t]
                ax.add_collection3d(Poly3DCollection([ring], facecolors='white', edgecolors=mp.BLACK,
                                                     linewidths=0.9, zorder=-2.2))
                ax.text(*to3(bx, by), ln['name'], fontproperties=mp.F_BOLD, fontsize=6.2, color=mp.BLACK,
                        ha='center', va='center', zorder=-2.1)
        # tie lines: corner grid intersections up to the building
        gx = sorted(l['a'][0] for l in plan_geom['grids']['lines'] if l['axis'] == 'x')
        gy = sorted(l['a'][1] for l in plan_geom['grids']['lines'] if l['axis'] == 'y')
        ties = []
        for x in (gx[0], gx[-1]):
            for y in (gy[0], gy[-1]):
                bx_, by_, _ = to3(x, y)
                ties.append([(bx_, by_, z), (bx_, by_, 0.0)])
        ax.add_collection3d(Line3DCollection(ties, colors=mp.GOLD, linewidths=2.6, linestyles=(0, (5, 3)), zorder=3))
        for (p0, _) in ties:
            ax.scatter([p0[0]], [p0[1]], [p0[2]], s=30, c=mp.GOLD, edgecolors=mp.BLACK, linewidths=0.4, zorder=3,
                       depthshade=False)
    return draw


def main(url):
    index = json.loads((mp.ASSETS / 'model-index.json').read_text(encoding='utf-8'))
    sched = json.loads((mp.ASSETS / 'schedule.json').read_text(encoding='utf-8'))

    plan = json.loads((OUT / 'plan_geom.json').read_text(encoding='utf-8'))
    hero_png = OUT / 'sign_exploded.png'
    mp.render_axon(hero_png, index, sched, size=(12, 10), zoom=1.15, hide=('ceilings', 'framing'),
                   below=EXPLODE_M, pad=4.0, extra=exploded_plan(plan, index['groups']), refine=1.5)
    snaps = []
    for i, d in enumerate(mp.SNAPSHOT_DATES):
        sp = OUT / f'sign_axon_{i}.png'
        mp.render_axon(sp, index, sched, upto=d, size=(6, 3.4), refine=1.5)
        snaps.append(sp)

    fig = plt.figure(figsize=SIGN_IN, dpi=PNG_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.set_aspect('equal'); ax.axis('off')
    ax.add_patch(Rectangle((0, 0), W, H, fc=BG, zorder=0))
    m = 150  # outer margin

    # ------------------------------------------------------------ trade partner band (top)
    HB = 960
    ax.add_patch(Rectangle((0, 0), W, HB, fc='#000', zorder=1))
    tex = plt.imread(ROOT / 'external' / 'img' / 'dark-texture.jpg')
    ax.imshow(tex, extent=(1500, W, HB, 0), aspect='auto', alpha=0.5, zorder=1.1)
    ax.add_patch(MPolygon([(3060, 0), (W, 0), (W, HB), (3330, HB)], closed=True, fc=GOLD, zorder=1.2))
    ax.add_patch(MPolygon([(3010, 0), (3060, 0), (3330, HB), (3280, HB)], closed=True, fc='#000', zorder=1.3))
    ax.add_patch(Rectangle((0, HB), W, 16, fc=GOLD, zorder=1.4))
    bx, bw, bh = m, 1180, 860
    ax.add_patch(FancyBboxPatch((bx, -90), bw, bh + 90, boxstyle='round,pad=0,rounding_size=70',
                                fc=GOLD, ec='none', zorder=2))
    logo = trim(plt.imread(ROOT / 'external' / 'img' / 'usm-logo.png'))
    lw = bw - 170
    lh = lw * logo.shape[0] / logo.shape[1]
    place_image(ax, logo, bx + (bw - lw) / 2, bh / 2 - lh / 2, lw, zorder=6)
    cx0 = bx + bw + 110
    text(ax, cx0, 170, spaced('Trade partner'), mp.F_BOLD, 22, GOLD, va='center')
    text(ax, cx0, 380, 'SCHOOL OF', mp.F_HERO, 66, 'white', va='center')
    text(ax, cx0, 560, 'CONSTRUCTION', mp.F_HERO, 66, 'white', va='center')
    text(ax, cx0, 740, 'AND DESIGN', mp.F_HERO, 66, 'white', va='center')
    text(ax, cx0, 880, 'The University of Southern Mississippi', mp.F_SEMI, 30, GOLD, va='center')
    text(ax, 3390, 360, 'BIM', mp.F_WIDE, 44, BLACK, ha='center', va='center', zorder=3)
    text(ax, 3400, 560, '4D·AR', mp.F_BOLD, 30, BLACK, ha='center', va='center', zorder=3)

    # ------------------------------------------------------------ activity name
    ay = HB + 140
    text(ax, m, ay, spaced('Activity'), mp.F_BOLD, 22, GOLD_INK, va='center')
    text(ax, m - 6, ay + 205, 'BUILD A BUILDING', mp.F_HERO, 86, BLACK, va='center')
    text(ax, m - 6, ay + 440, 'IN AUGMENTED REALITY', mp.F_HERO, 86, BLACK, va='center')
    ax.add_patch(Rectangle((m, ay + 575), 900, 20, fc=GOLD, zorder=2))
    text(ax, m, ay + 670, 'Watch a real BIM model go up week by week, right on your phone.',
         mp.F_REG, 26, INK, va='center')
    cost = json.loads((mp.ASSETS / 'cost.json').read_text(encoding='utf-8'))
    facts = (f"{cost['gsf']:,} SF  ·  3 storeys  ·  {len(index['elements']):,} BIM elements  ·  "
             f"{len(sched['tasks'])} scheduled tasks  ·  ${cost['total'] / 1e6:.2f}M estimate")
    text(ax, m, ay + 760, facts, mp.F_BOLD, 19, GOLD_INK, va='center')

    # ------------------------------------------------------------ hero: 2D map tied to the 3D model
    hy = ay + 850
    hh = 1560
    hw = 2020
    card(ax, m, hy, hw, hh, r=60, lw=2)
    hero = trim(plt.imread(hero_png))
    iw = hw - 80
    ih = iw * hero.shape[0] / hero.shape[1]
    if ih > hh - 310:
        ih = hh - 310; iw = ih * hero.shape[1] / hero.shape[0]
    ix, iy = m + (hw - iw) / 2, hy + 140
    ax.imshow(hero, extent=(ix, ix + iw, iy + ih, iy), zorder=3)

    def pill(x, y, label, w):
        ax.add_patch(FancyBboxPatch((x, y - 48), w, 96, boxstyle='round,pad=0,rounding_size=48',
                                    fc=BLACK, ec='none', zorder=5))
        text(ax, x + w / 2, y, label, mp.F_BOLD, 15, GOLD, ha='center', va='center', zorder=6)

    pill(m + 50, hy + 90, '3D MODEL  ·  REVIT', 640)
    pill(m + 50, hy + hh - 95, '2D MAP  ·  LEVEL 1 PLAN', 760)
    ax.plot([m + 860, m + 960], [hy + hh - 95, hy + hh - 95], color=GOLD, lw=5, ls=(0, (4, 3)), zorder=5)
    text(ax, m + 985, hy + hh - 95, 'gold lines tie the map to the model', mp.F_SEMI, 16, MUTED,
         va='center', zorder=5)

    # 4D sequence, stacked to the right of the hero
    rx = m + hw + 60
    rw = W - m - rx
    text(ax, rx, hy + 25, spaced('4D sequence'), mp.F_BOLD, 22, GOLD_INK, va='center')
    gap = 40
    ch = (hh - 90 - 3 * gap) / 4
    captions = ['Level 1 frame', 'Upper floors', 'Roof & envelope', 'Complete']
    for i, sp in enumerate(snaps):
        y = hy + 90 + i * (ch + gap)
        card(ax, rx, y, rw, ch, r=40, lw=2)
        lab = 400
        ax.add_patch(FancyBboxPatch((rx, y), lab, ch, boxstyle='round,pad=0,rounding_size=40',
                                    fc=BLACK, ec='none', zorder=2))
        ax.add_patch(Rectangle((rx + lab - 60, y), 60, ch, fc=BLACK, zorder=2))
        text(ax, rx + 40, y + ch / 2 - 40, f'STEP {i + 1}', mp.F_WIDE, 24, GOLD, va='center', zorder=4)
        text(ax, rx + 40, y + ch / 2 + 40, captions[i], mp.F_SEMI, 14, 'white', va='center', zorder=4)
        img = trim(plt.imread(sp))
        aw, ah = rw - lab - 60, ch - 40
        if aw * img.shape[0] / img.shape[1] > ah:
            aw = ah * img.shape[1] / img.shape[0]
        else:
            ah = aw * img.shape[0] / img.shape[1]
        ax0 = rx + lab + (rw - lab - aw) / 2
        ax.imshow(img, extent=(ax0, ax0 + aw, y + (ch + ah) / 2, y + (ch - ah) / 2), zorder=3)
    sy, sh = hy, hh   # the how-it-works row starts below the hero

    # ------------------------------------------------------------ how it works + careers + QR
    fy = H - 280
    by = sy + sh + 150
    qx1 = W - m
    qtop, qbot = by - 50, fy - 90
    qr_w = qbot - qtop - 290 + 140         # card width so the code is as large as the space allows
    qx = qx1 - qr_w
    text(ax, m, by, spaced('How it works'), mp.F_BOLD, 22, GOLD_INK, va='center')
    steps = [('Scan the QR code', 'with your phone or tablet camera, no app needed'),
             ('Point at the floor plan', 'on our table: the building rises out of the page'),
             ('Slide through time', 'watch it get built and tap any part to see its cost')]
    for i, (h1, h2) in enumerate(steps):
        y = by + 120 + i * 175
        ax.add_patch(Circle((m + 58, y + 22), 58, fc=GOLD, ec='none', zorder=3))
        text(ax, m + 58, y + 26, str(i + 1), mp.F_HERO, 32, BLACK, ha='center', va='center', zorder=4)
        text(ax, m + 160, y - 6, h1, mp.F_WIDE, 26, BLACK, va='center')
        text(ax, m + 160, y + 62, h2, mp.F_REG, 19, INK, va='center')
    cy = by + 120 + 3 * 175 + 10
    text(ax, m, cy, spaced('Careers you can explore'), mp.F_BOLD, 17, GOLD_INK, va='center')
    chips = ['Construction Management', 'BIM & VDC', 'Estimating', 'Scheduling']
    x = m
    yy = cy + 85
    for c in chips:
        tw = 70 + len(c) * 23
        ax.add_patch(FancyBboxPatch((x, yy - 34), tw, 68, boxstyle='round,pad=0,rounding_size=34',
                                    fc=BLACK, ec='none', zorder=3))
        text(ax, x + tw / 2, yy, c, mp.F_SEMI, 14.5, GOLD, ha='center', va='center', zorder=4)
        x += tw + 18

    card(ax, qx, qtop, qr_w, qbot - qtop, fc=BLACK, ec=BLACK, r=60)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=10)
    qr.add_data(url); qr.make(fit=True)
    q = np.array(qr.make_image(fill_color='black', back_color='white').convert('L')) / 255
    qs = qr_w - 140
    ax.imshow(q, cmap='gray', extent=(qx + 70, qx + 70 + qs, qtop + 70 + qs, qtop + 70),
              interpolation='nearest', zorder=3)
    text(ax, qx + qr_w / 2, qtop + qs + 150, 'SCAN TO TRY IT', mp.F_HERO, 25, GOLD, ha='center', va='center')
    text(ax, qx + qr_w / 2, qtop + qs + 230, url.replace('https://', ''), mp.F_REG, 14, (1, 1, 1, 0.75),
         ha='center', va='center')

    # ------------------------------------------------------------ footer: FORGE + credits
    ax.add_patch(Rectangle((0, fy - 14), W, 14, fc=GOLD, zorder=1))
    ax.add_patch(Rectangle((0, fy), W, H - fy, fc='#000', zorder=1))
    forge = plt.imread(ROOT / 'pipeline' / 'brand' / 'forge-logo.png')
    lh = place_image(ax, forge, m, fy + 40, 500, zorder=3)
    text(ax, m + 580, fy + 40 + lh / 2, EXPO, mp.F_SEMI, 22, 'white', va='center')
    text(ax, W - m, fy + 40 + lh / 2, 'Built by Dr. Salar Kamari', mp.F_BOLD, 22, GOLD, ha='right', va='center')
    text(ax, W / 2, H - 36, mp.MODEL_CREDIT, mp.F_REG, 13, (1, 1, 1, 0.65), ha='center', va='center')

    base = PRINT / 'forge-easel-sign-18x24'
    fig.savefig(f'{base}.png', dpi=PNG_DPI)
    try:
        fig.savefig(f'{base}.pdf')
    except PermissionError:
        fig.savefig(f'{base}-new.pdf')
        print('PDF is open elsewhere; wrote -new.pdf instead')
    plt.close(fig)
    print('wrote', base)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'https://salarka.github.io/bim-ar-8thwall/')
