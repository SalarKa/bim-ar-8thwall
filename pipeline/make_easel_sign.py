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


def main(url):
    index = json.loads((mp.ASSETS / 'model-index.json').read_text(encoding='utf-8'))
    sched = json.loads((mp.ASSETS / 'schedule.json').read_text(encoding='utf-8'))

    hero_png = OUT / 'sign_hero.png'
    mp.render_axon(hero_png, index, sched, upto=None, size=(16, 6.6), zoom=1.05,
                   hide=('ceilings', 'framing'))  # framing under the roof sorts through it
    snaps = []
    for i, d in enumerate(mp.SNAPSHOT_DATES):
        p = OUT / f'axon_{i}.png'
        if not p.exists():
            mp.render_axon(p, index, sched, upto=d, size=(6, 3.4))
        snaps.append(p)

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
    text(ax, m, ay + 680, 'Watch a real BIM model go up week by week, right on your phone.',
         mp.F_REG, 26, INK, va='center')

    # ------------------------------------------------------------ hero render
    hy = ay + 780
    hh = 900
    card(ax, m, hy, W - 2 * m, hh, r=60, lw=2)
    hero = trim(plt.imread(hero_png))
    ih = hh - 190
    iw = ih * hero.shape[1] / hero.shape[0]
    hx = m + 70
    ax.imshow(hero, extent=(hx, hx + iw, hy + 150 + ih, hy + 150), zorder=3)
    ax.add_patch(FancyBboxPatch((m + 50, hy + 45), 980, 96, boxstyle='round,pad=0,rounding_size=48',
                                fc=BLACK, ec='none', zorder=4))
    text(ax, m + 540, hy + 93, '3-STOREY STEEL BUILDING  ·  REVIT', mp.F_BOLD, 15, GOLD, ha='center',
         va='center', zorder=5)
    # quick facts from the real model, schedule and estimate
    cost = json.loads((mp.ASSETS / 'cost.json').read_text(encoding='utf-8'))
    facts = [(f"{cost['gsf']:,}", 'square feet, 3 storeys'),
             (f"{len(index['elements']):,}", 'BIM elements you can tap'),
             (f"{len(sched['tasks'])}", 'scheduled tasks, Jan to May'),
             (f"${cost['total'] / 1e6:.2f}M", 'cost estimate from the model')]
    fx = hx + iw + 140
    ax.plot([fx - 70, fx - 70], [hy + 90, hy + hh - 90], color=mp.LINE, lw=3, zorder=3)
    colw = (W - m - 60 - fx) / 2
    for i, (big, small) in enumerate(facts):   # 2 x 2 grid across the rest of the card
        x = fx + (i % 2) * colw
        fyy = hy + 250 + (i // 2) * 360
        text(ax, x, fyy, big, mp.F_HERO, 56, BLACK, va='center', zorder=4)
        text(ax, x, fyy + 105, small, mp.F_SEMI, 19, MUTED, va='center', zorder=4)

    # ------------------------------------------------------------ 4D sequence
    sy = hy + hh + 130
    text(ax, m, sy - 45, spaced('4D construction sequence'), mp.F_BOLD, 22, GOLD_INK, va='bottom')
    gap = 50
    sw = (W - 2 * m - 3 * gap) / 4
    sh = 540
    captions = ['Level 1 frame', 'Upper floors', 'Roof & envelope', 'Complete']
    for i, (p, d) in enumerate(zip(snaps, mp.SNAPSHOT_DATES)):
        x = m + i * (sw + gap)
        card(ax, x, sy, sw, sh, r=46, lw=2)
        img = trim(plt.imread(p))
        iw = sw - 80; ih = iw * img.shape[0] / img.shape[1]
        if ih > sh - 170:
            ih = sh - 170; iw = ih * img.shape[1] / img.shape[0]
        ax.imshow(img, extent=(x + (sw - iw) / 2, x + (sw + iw) / 2, sy + 30 + ih, sy + 30), zorder=3)
        ax.add_patch(FancyBboxPatch((x, sy + sh - 115), sw, 115, boxstyle='round,pad=0,rounding_size=46',
                                    fc=BLACK, ec='none', zorder=2))
        ax.add_patch(Rectangle((x, sy + sh - 115), sw, 55, fc=BLACK, zorder=2))
        text(ax, x + 40, sy + sh - 57, f'STEP {i + 1}', mp.F_WIDE, 21, GOLD, va='center', zorder=4)
        text(ax, x + sw - 36, sy + sh - 57, captions[i], mp.F_SEMI, 16, 'white', ha='right', va='center', zorder=4)

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
