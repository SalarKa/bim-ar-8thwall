"""Printable sheet for the reinforced CMU wall AR demo; the whole sheet is the AR image target.

Landscape layout (3200 x 2400 px = 10.67 x 8 in at 300 dpi, prints at 100 % on Letter or A4
landscape) so the 12 ft wall runs along the long side of the paper:
  - top view of the wall at 1:16 (the AR anchor): footing, top course of CMU with hollow and
    grouted cells, vertical bars, dimensions and callouts. The animated wall rises out of it.
  - the nine construction steps, rendered from the Blender animation (blender/render_steps.py)
  - the QR code

8th Wall image targets are portrait 3:4, so the target image is this sheet turned 90 degrees
clockwise; target-placement.json carries the matching plan_rotation.

Outputs: print/cmu-wall-poster.png (target image, portrait), print/cmu-wall-poster-{letter,a4}.pdf,
         src/assets/target-placement.json
Usage: python -I pipeline/make_poster.py [app url]
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import qrcode
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, FancyBboxPatch, Polygon as MPolygon, Rectangle
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
ASSETS = ROOT / 'src' / 'assets'
PRINT = ROOT / 'print'
STEP_IMG = ROOT / 'pipeline' / 'out' / 'steps'
FONTS = REPO / 'pipeline' / 'fonts'
URL = sys.argv[1] if len(sys.argv) > 1 else 'https://salarka.github.io/bim-ar-8thwall/reinforced-cmu-wall/'
W, H, DPI = 3200, 2400, 300            # landscape sheet
SCALE = 16                             # plan scale 1:16
IN = 0.0254
K = DPI / IN / SCALE / 1.0             # px per model metre on the sheet

BLACK, GOLD, GOLD_INK, BG, INK, MUTED, LINE = '#1A1A1A', '#FFD046', '#8a6a00', '#F5F5F2', '#333333', '#6b6b6b', '#e8e8e8'
CMU, CMU_EDGE, MORTAR, CORE, GROUT, REBAR, FOOTING = '#a6a6a1', '#4a4a48', '#d9d4c8', '#efeeea', '#4d4d4b', '#8a3f1c', '#dcdcd8'
F_HERO = FontProperties(fname=FONTS / 'Archivo-Black-Italic.ttf')
F_WIDE = FontProperties(fname=FONTS / 'Archivo-ExtraBold-Wide.ttf')
F_BOLD = FontProperties(fname=FONTS / 'Archivo-Bold.ttf')
F_SEMI = FontProperties(fname=FONTS / 'Archivo-SemiBold.ttf')
F_REG = FontProperties(fname=FONTS / 'Archivo-Regular.ttf')

# ---- wall geometry (inches), same as blender/build_cmu_wall.py --------------------------------
L, WU, HL, J = 15.625, 7.625, 7.625, 0.375
MOD, N_MOD, N_CRS = 16.0, 9, 8
FS, WEB = 1.25, 1.0
CORE_W = WU - 2 * FS
CORE_L = (L - 3 * WEB) / 2
WALL_L = N_MOD * MOD - J
OX = -WALL_L / 2
FOOT_W, FOOT_EXT = 20.0, 6.0
BAR_CELLS = [0, 6, 12, 17]
BAR_DIA = 0.625


def cell_x(n):
    return 3.8125 + 8 * n


def top_course():
    """Pieces of the top course (course 8 starts and ends with a half unit): (x0, length)."""
    k = N_CRS - 1
    if k % 2 == 0:
        return [(MOD * i, L) for i in range(N_MOD)]
    return [(0.0, HL)] + [(MOD / 2 + MOD * i, L) for i in range(N_MOD - 1)] + [(N_MOD * MOD - MOD / 2, HL)]


def cores_of(x0, length):
    if length == L:
        c = x0 + L / 2
        d = L / 2 - WEB - CORE_L / 2
        return [(c - d, CORE_L), (c + d, CORE_L)]
    return [(x0 + length / 2, HL - 2 * WEB)]


# ---- drawing helpers -------------------------------------------------------------------------
def text(ax, x, y, s, fp, size, color=INK, **kw):
    return ax.text(x, y, s, fontproperties=fp, fontsize=size, color=color, **kw)


def spaced(s, n=1):
    return (' ' * n).join(s.upper())


def card(ax, x, y, w, h, fc='white', ec=LINE, r=26, z=0.6, lw=1.2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}', fc=fc, ec=ec, lw=lw, zorder=z))


def ft_in(inches):
    inches = round(inches * 8) / 8
    f, i = divmod(inches, 12)
    whole = int(i)
    frac = i - whole
    fr = {0: '', 0.125: ' 1/8', 0.25: ' 1/4', 0.375: ' 3/8', 0.5: ' 1/2', 0.625: ' 5/8', 0.75: ' 3/4', 0.875: ' 7/8'}[frac]
    return f"{int(f)}'-{whole}{fr}\"" if f else f'{whole}{fr}"'


def dim_h(ax, u0, u1, v, label, ext_from=None, below=False):
    """Horizontal dimension string with ticks; optional extension lines down/up to ext_from."""
    ax.plot([u0, u1], [v, v], color=INK, lw=0.8, zorder=6)
    for u in (u0, u1):
        ax.plot([u - 9, u + 9], [v + 9, v - 9], color=INK, lw=1.2, zorder=6)
        if ext_from is not None:
            ax.plot([u, u], [ext_from, v + (-14 if below else 14) * -1], color='#9a9a9a', lw=0.6, zorder=5)
    text(ax, (u0 + u1) / 2, v + (22 if below else -16), label, F_SEMI, 6.4, INK, ha='center',
         va='top' if below else 'bottom', zorder=6)


def dim_v(ax, u, v0, v1, label, ext_from=None):
    ax.plot([u, u], [v0, v1], color=INK, lw=0.8, zorder=6)
    for v in (v0, v1):
        ax.plot([u - 9, u + 9], [v + 9, v - 9], color=INK, lw=1.2, zorder=6)
        if ext_from is not None:
            ax.plot([ext_from, u + 14], [v, v], color='#9a9a9a', lw=0.6, zorder=5)
    text(ax, u + 22, (v0 + v1) / 2, label, F_SEMI, 6.4, INK, ha='left', va='center', zorder=6)


def callout(ax, xy, txy, label, ha='left'):
    ax.annotate('', xy=xy, xytext=txy, arrowprops=dict(arrowstyle='-|>', color=INK, lw=0.9, mutation_scale=12,
                                                       shrinkA=0, shrinkB=0), zorder=8)
    ax.add_patch(Circle(xy, 4.5, fc=INK, ec='none', zorder=8))
    text(ax, txy[0] + (10 if ha == 'left' else -10), txy[1], label, F_SEMI, 6.6, INK, ha=ha, va='center', zorder=8)


def rrect(ax, u0, v0, w, h, r, **kw):
    ax.add_patch(FancyBboxPatch((u0 + r, v0 + r), w - 2 * r, h - 2 * r, boxstyle=f'round,pad={r}', **kw))


# ---- sheet -------------------------------------------------------------------------------------
def main():
    steps = json.loads((ASSETS / 'steps.json').read_text())['steps']
    PRINT.mkdir(exist_ok=True)
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.set_aspect('equal'); ax.axis('off')
    ax.add_patch(Rectangle((0, 0), W, H, fc=BG, zorder=0))
    margin = 70

    # ------------------------------------------------------------ masthead
    HB = 300
    ax.add_patch(Rectangle((0, 0), W, HB, fc='#000', zorder=1))
    tex = plt.imread(REPO / 'external' / 'img' / 'dark-texture.jpg')
    ax.imshow(tex, extent=(1500, W, HB, 0), aspect='auto', alpha=0.55, zorder=1.1)
    ax.add_patch(MPolygon([(2560, 0), (W, 0), (W, HB), (2660, HB)], closed=True, fc=GOLD, zorder=1.2))
    ax.add_patch(MPolygon([(2530, 0), (2560, 0), (2660, HB), (2630, HB)], closed=True, fc='#000', zorder=1.3))
    bx, bw, bh = 70, 560, 262
    ax.add_patch(FancyBboxPatch((bx, -60), bw, bh + 60, boxstyle='round,pad=0,rounding_size=40', fc=GOLD, ec='none', zorder=2))
    logo = Image.open(REPO / 'external' / 'img' / 'usm-logo.png').convert('RGBA')
    logo = logo.crop(logo.getbbox())
    lw_px = bw - 100
    lh_px = round(lw_px * logo.height / logo.width)
    logo = np.asarray(logo.resize((lw_px, lh_px), Image.LANCZOS)) / 255
    lx, ly = round(bx + (bw - lw_px) / 2), round(bh / 2 - lh_px / 2)
    ax.imshow(logo, extent=(lx, lx + lw_px, ly + lh_px, ly), interpolation='none', zorder=6)
    cx0 = 700
    text(ax, cx0, 62, spaced('School of Construction and Design'), F_BOLD, 7.6, GOLD, va='center')
    text(ax, cx0, 112, 'University of Southern Mississippi', F_SEMI, 9, (1, 1, 1, 0.88), va='center')
    hero = text(ax, cx0, 205, 'REINFORCED CMU WALL', F_HERO, 30, 'white', va='center')
    fig.canvas.draw()
    bb = hero.get_window_extent().transformed(ax.transData.inverted())
    text(ax, bb.x1 + 40, 205, 'AR', F_HERO, 30, GOLD, va='center')
    tag_x = (2560 + W + W + 2660) / 4 + 10
    text(ax, tag_x, 120, 'CMU', F_WIDE, 22, BLACK, ha='center', va='center', zorder=3)
    text(ax, tag_x, 210, 'MASONRY · AR', F_BOLD, 10.5, BLACK, ha='center', va='center', zorder=3)

    # ------------------------------------------------------------ top view (AR anchor) at 1:16
    top = 380
    card_h = 790
    text(ax, margin, top - 26, spaced('Top view  ·  AR anchor'), F_BOLD, 8.4, GOLD_INK, va='bottom')
    text(ax, margin + 980, top - 26, f'Scale 1:{SCALE}  ·  8" CMU, running bond  ·  #5 bars at 4\'-0" o.c.',
         F_SEMI, 7.4, MUTED, va='bottom')
    card(ax, margin, top, W - 2 * margin, card_h)
    k = DPI / SCALE                                           # px per inch on the sheet
    ox = W / 2                                                # wall centre (model origin) on the sheet
    oy = top + 380
    U = lambda x_in: ox + (OX + x_in) * k                     # wall-local x (0 = left end) -> sheet u
    V = lambda y_in: oy - y_in * k                            # model y (front = -y) -> sheet v (down)

    # footing (plain concrete, seen from above)
    fx0, fx1 = U(-FOOT_EXT), U(WALL_L + FOOT_EXT)
    ax.add_patch(Rectangle((fx0, V(FOOT_W / 2)), fx1 - fx0, FOOT_W * k, fc=FOOTING, ec='#8a8a86', lw=1.1, zorder=2))
    rng = np.random.default_rng(3)                            # light stipple for concrete
    pts = np.column_stack([rng.uniform(fx0, fx1, 2600), rng.uniform(V(FOOT_W / 2), V(-FOOT_W / 2), 2600)])
    ax.scatter(pts[:, 0], pts[:, 1], s=0.35, c='#a9a9a4', lw=0, zorder=2.1)
    # wall: mortar joints underneath, units on top
    ax.add_patch(Rectangle((U(0), V(WU / 2)), WALL_L * k, WU * k, fc=MORTAR, ec='none', zorder=3))
    grouted = {round(cell_x(n), 3) for n in BAR_CELLS}
    core_pos = {}
    for x0, ln in top_course():
        ax.add_patch(Rectangle((U(x0), V(WU / 2)), ln * k, WU * k, fc=CMU, ec=CMU_EDGE, lw=0.9, zorder=4))
        for cx, cl in cores_of(x0, ln):
            g = any(abs(cx - gx) < 2.0 for gx in grouted)
            rrect(ax, U(cx - cl / 2), V(CORE_W / 2), cl * k, CORE_W * k, 0.7 * k,
                  fc=GROUT if g else CORE, ec=CMU_EDGE, lw=0.7, zorder=5)
            core_pos[round(cx, 2)] = g
    for n in BAR_CELLS:                                       # bars (cut) and dowels below (hidden)
        cx = cell_x(n)
        ax.add_patch(Circle((U(cx), V(0.45)), BAR_DIA / 2 * k, fc=REBAR, ec='#2b1408', lw=0.7, zorder=7))
        ax.add_patch(Circle((U(cx), V(-0.45)), BAR_DIA / 2 * k, fc='none', ec='#d8d8d4', lw=0.8, ls=(0, (2, 1.5)), zorder=7))
    # dimensions
    v_top = V(FOOT_W / 2)
    dim_h(ax, U(0), U(WALL_L), v_top - 120, f"{ft_in(WALL_L + J)} wall ({ft_in(WALL_L)} actual)  ·  9 units long",
          ext_from=V(WU / 2) - 6)
    bars = [cell_x(n) for n in BAR_CELLS]
    for a, b in zip(bars, bars[1:]):
        dim_h(ax, U(a), U(b), v_top - 50, ft_in(b - a), ext_from=V(0.45) - 8)
    # callouts below the footing, in two staggered rows
    r1, r2 = V(-FOOT_W / 2) + 62, V(-FOOT_W / 2) + 118
    hx = sorted(cx for cx, g in core_pos.items() if not g)
    callout(ax, (U(hx[1]), V(0)), (U(hx[1]) - 40, r2), 'Hollow cell (not grouted)')
    callout(ax, (U(bars[1]), V(0.45)), (U(bars[1]) + 60, r1), '#5 vertical bar in a grouted cell, lapped with the dowel')
    callout(ax, (U(104), V(-WU / 2 + 0.4)), (U(104) + 30, r2), f'8x8x16 CMU ({ft_in(WU)} wide), top course shown, 3/8" joints')
    callout(ax, (U(122), V(-FOOT_W / 2 + 2.5)), (U(122) + 40, r1), 'Concrete footing 1\'-8" wide x 1\'-0" deep')
    # bottom row of the card: scale bar, legend, viewing side
    lx0, ly = margin + 44, top + card_h - 52
    text(ax, lx0, ly - 30, spaced('Scale') + f'   1:{SCALE}', F_BOLD, 6.4, GOLD_INK, va='center')
    step = 12 * k                                              # 1 ft segments
    for i in range(2):
        ax.add_patch(Rectangle((lx0 + i * step, ly), step, 11, fc=BLACK if i % 2 == 0 else 'white', ec=BLACK, lw=0.8, zorder=6))
    for i in (0, 1, 2):
        text(ax, lx0 + i * step, ly + 32, f'{i}', F_SEMI, 5.8, MUTED, ha='center', va='center')
    text(ax, lx0 + 2 * step + 14, ly + 6, 'ft', F_SEMI, 5.8, MUTED, va='center')
    items = [('CMU unit', CMU), ('Grouted cell', GROUT), ('Hollow cell', CORE), ('Mortar joint', MORTAR), ('Footing', FOOTING),
             ('#5 bar', None)]
    x = lx0 + 2 * step + 120
    for lab, col in items:
        if col is None:
            ax.add_patch(Circle((x + 23, ly + 6), 9, fc=REBAR, ec='#2b1408', lw=0.7, zorder=6))
        else:
            ax.add_patch(Rectangle((x, ly - 6), 46, 24, fc=col, ec=CMU_EDGE, lw=0.6, zorder=6))
        t = text(ax, x + 60, ly + 6, lab, F_SEMI, 6.2, INK, va='center', zorder=6)
        fig.canvas.draw()
        x = t.get_window_extent().transformed(ax.transData.inverted()).x1 + 56
    text(ax, W - margin - 40, ly + 6, 'Front face  ·  view the AR from this side  ↓', F_SEMI, 6.4, MUTED,
         ha='right', va='center')

    # ------------------------------------------------------------ construction steps
    y4 = top + card_h + 74
    text(ax, margin, y4 - 24, spaced('Construction sequence  ·  low-lift grouting'), F_BOLD, 8.4, GOLD_INK, va='bottom')
    gap, cols = 28, 5
    sw = (W - 2 * margin - (cols - 1) * gap) / cols
    img_h, cap_h, bar_h = 316, 76, 76
    sh = img_h + cap_h + bar_h
    for i, s in enumerate(steps + [None]):
        x = margin + (i % cols) * (sw + gap)
        y = y4 + (i // cols) * (sh + gap)
        if s is None:                                          # QR card fills the tenth slot
            card(ax, x, y, sw, sh, fc=BLACK, ec=BLACK, z=6)
            qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(URL)
            qr.make(fit=True)
            qimg = np.asarray(qr.make_image(fill_color='black', back_color='white').convert('RGB'))
            qs = 290
            ax.imshow(qimg, extent=(x + (sw - qs) / 2, x + (sw + qs) / 2, y + 30 + qs, y + 30), interpolation='nearest', zorder=7)
            text(ax, x + sw / 2, y + 30 + qs + 56, 'SCAN TO LAUNCH', F_HERO, 9, GOLD, ha='center', va='center', zorder=7)
            text(ax, x + sw / 2, y + 30 + qs + 102, 'then aim at this sheet', F_SEMI, 6.6, 'white', ha='center', va='center', zorder=7)
            continue
        card(ax, x, y, sw, sh)
        im = Image.open(STEP_IMG / f"step_{s['n']}.png").convert('RGBA')
        im = im.crop(im.getbbox())
        im = Image.alpha_composite(Image.new('RGBA', im.size, 'white'), im)   # no dark fringe on the edges
        aw, ah = sw - 40, img_h - 30
        f = min(aw / im.width, ah / im.height)
        iw, ih = im.width * f, im.height * f
        ix, iy = x + (sw - iw) / 2, y + 20 + (ah - ih) / 2
        ax.imshow(np.asarray(im) / 255, extent=(ix, ix + iw, iy + ih, iy), interpolation='lanczos', zorder=2)
        words, lines = s['caption'].split(), ['']
        for wd in words:                                       # wrap the caption to two lines
            if len(lines[-1]) + len(wd) > 44 and lines[-1]:
                lines.append('')
            lines[-1] = (lines[-1] + ' ' + wd).strip()
        for j, ln in enumerate(lines[:2]):
            text(ax, x + 24, y + img_h + 8 + j * 30, ln, F_REG, 5.6, MUTED, va='top')
        ax.add_patch(FancyBboxPatch((x, y + sh - bar_h), sw, bar_h, boxstyle='round,pad=0,rounding_size=26', fc=BLACK, ec='none', zorder=7))
        ax.add_patch(Rectangle((x, y + sh - bar_h), sw, 30, fc=BLACK, ec='none', zorder=7))
        text(ax, x + 26, y + sh - bar_h / 2, f"{s['n']:d}", F_WIDE, 10, GOLD, va='center', zorder=8)
        text(ax, x + 80, y + sh - bar_h / 2, s['title'].upper(), F_BOLD, 7.4, 'white', va='center', zorder=8)

    # ------------------------------------------------------------ in the app + footer
    yf = H - 70 - 48
    text(ax, margin, yf, spaced('In the app'), F_BOLD, 7.2, GOLD_INK, va='center')
    text(ax, margin + 290, yf, 'Play / pause  ·  jump to any step  ·  speed  ·  X-ray to see bars and grout inside the cells'
         '  ·  the wall rises out of this top view at 1:16', F_SEMI, 6.4, INK, va='center')
    ax.add_patch(Rectangle((0, H - 70), W, 70, fc=BLACK, zorder=1))
    ax.add_patch(Rectangle((0, H - 74), W, 4, fc=GOLD, zorder=1))
    text(ax, margin, H - 35, 'Built by Dr. Salar Kamari', F_BOLD, 7.2, GOLD, va='center')
    text(ax, W / 2, H - 35, 'Sequence per CMHA TEK 03-02A and TMS 602 (low-lift grouting)  ·  School of Construction and Design',
         F_SEMI, 6.4, 'white', ha='center', va='center')
    text(ax, W - margin, H - 35, 'Print at 100%, landscape  ·  keep flat & well lit', F_SEMI, 6.4, (1, 1, 1, 0.8), ha='right', va='center')

    # ------------------------------------------------------------ outputs
    tmp = PRINT / 'cmu-wall-poster-landscape.png'
    fig.savefig(tmp, dpi=DPI)
    plt.close(fig)
    sheet = Image.open(tmp).convert('RGB')
    assert sheet.size == (W, H), sheet.size
    sheet.save(tmp, dpi=(DPI, DPI))
    target = sheet.transpose(Image.Transpose.ROTATE_270)      # 90 degrees clockwise -> portrait 2400 x 3200
    target.save(PRINT / 'cmu-wall-poster.png', dpi=(DPI, DPI))
    for name, (pw_in, ph_in) in (('letter', (11.0, 8.5)), ('a4', (11.69, 8.27))):
        page = Image.new('RGB', (round(pw_in * DPI), round(ph_in * DPI)), 'white')
        page.paste(sheet, ((page.width - W) // 2, (page.height - H) // 2))
        out = PRINT / f'cmu-wall-poster-{name}.pdf'
        try:
            page.save(out, resolution=DPI)
        except PermissionError:                              # the PDF is open in a viewer: write a dated copy
            out = out.with_name(f'cmu-wall-poster-{name}-{datetime.now():%Y%m%d-%H%M}.pdf')
            page.save(out, resolution=DPI)
        print('wrote', out.name)
    # model origin on the portrait target: landscape (u, v) -> portrait (H - v, u)
    px_per_m = K
    placement = {
        'poster_px': [H, W],
        'plan_origin_px': [round(H - oy, 2), round(ox, 2)],
        'plan_rotation': -math.pi / 2,          # model +X runs down the portrait target (along the sheet)
        'px_per_m': round(px_per_m, 4),
        'model_scale': round(px_per_m / W, 7),  # 1 m of model = this many target heights
        'url': URL,
    }
    (ASSETS / 'target-placement.json').write_text(json.dumps(placement, indent=1))
    print('placement:', placement)


if __name__ == '__main__':
    main()
