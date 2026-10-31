"""Printable sheet for the hospital AR demo; the whole sheet is the AR image target.

The anchor is a true-scale orthographic aerial render of the enhanced model (pipeline/out/shots/
plan.png, rendered by the app's ?shot=plan&ortho=1 mode), so in AR the building rises exactly out
of its own picture. Around it: the seven sub-models, Original Revit vs Enhanced, and the QR code.

The image is 3:4 (2400 x 3200 px = 8 x 10.67 in at 300 dpi) so 8th Wall's default crop uses the
whole sheet; it prints at 100 % on both US Letter and A4.

Outputs: print/hospital-poster.png (image target source), print/hospital-poster-letter.pdf,
print/hospital-poster-a4.pdf, src/assets/target-placement.json, src/assets/poster-preview.jpg

Usage: python -I pipeline/make_poster.py [app url]
"""
import json
import sys
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps, JpegImagePlugin  # noqa: F401  (PDF pages embed JPEG)

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
ASSETS = ROOT / 'src' / 'assets'
SHOTS = ROOT / 'pipeline' / 'out' / 'shots'
PRINT = ROOT / 'print'
FONTS = REPO / 'pipeline' / 'fonts'
URL = sys.argv[1] if len(sys.argv) > 1 else 'https://salarka.github.io/bim-ar-8thwall/hospital/'
W, H, DPI = 2400, 3200, 300
M = 70

BLACK, GOLD, GOLD_INK, BG, INK, MUTED, LINE = '#1A1A1A', '#FFD046', '#8a6a00', '#F5F5F2', '#333333', '#6b6b6b', '#dcdcd6'


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


HERO = lambda s: font('Archivo-Black-Italic.ttf', s)   # noqa: E731
WIDE = lambda s: font('Archivo-ExtraBold-Wide.ttf', s)  # noqa: E731
BOLD = lambda s: font('Archivo-Bold.ttf', s)           # noqa: E731
SEMI = lambda s: font('Archivo-SemiBold.ttf', s)       # noqa: E731
REG = lambda s: font('Archivo-Regular.ttf', s)         # noqa: E731

SUBMODELS = [  # shot, discipline key, label, what it shows
    ('t_arch', 'arch', 'Architecture', 'Brick podium, metal-panel tower, curtain wall, roof garden'),
    ('t_struct', 'struct', 'Structure', 'Steel frame, composite slabs, footings'),
    ('t_hvac', 'hvac', 'HVAC', 'Supply, return & exhaust air ductwork, AHUs'),
    ('t_piping', 'piping', 'Piping & Plumbing', 'Hot & chilled water, med gas, fixtures'),
    ('t_elec', 'elec', 'Electrical', 'Lighting, devices, cable tray, panels'),
    ('t_fire', 'fire', 'Fire Alarm', 'Detectors, horn/strobes, pull stations'),
    ('t_site', 'site', 'Site & Landscape', 'Trees, shrubs, trucks, air ambulance'),
]


def spaced(d, xy, text, f, fill, tracking=0.12):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + f.size * tracking
    return x


def trim(img, pad=0.04):
    """Crop a render on a white background to its content (plus a little air)."""
    from PIL import ImageChops
    diff = ImageChops.difference(img.convert('RGB'), Image.new('RGB', img.size, 'white')).convert('L')
    box = diff.point(lambda v: 255 if v > 12 else 0).getbbox() or (0, 0, img.width, img.height)
    px, py = int((box[2] - box[0]) * pad), int((box[3] - box[1]) * pad)
    return img.crop((max(0, box[0] - px), max(0, box[1] - py), min(img.width, box[2] + px), min(img.height, box[3] + py)))


def cover(img, w, h):
    return ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.5))


def rounded(img, r):
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], r, fill=255)
    out = Image.new('RGBA', img.size)
    out.paste(img, (0, 0), mask)
    return out


def main():
    PRINT.mkdir(exist_ok=True)
    scene = json.loads((ASSETS / 'scene.json').read_text())
    colors = {d['key']: d['color'] for d in scene['disciplines']}
    x0b, z0b, x1b, z1b = json.loads((ASSETS / 'entourage.json').read_text())['base_bounds']

    sheet = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(sheet)

    # ---------------------------------------------------------------- masthead
    d.rectangle([0, 0, W, 300], fill='#000000')
    d.rectangle([0, 300, W, 312], fill=GOLD)
    d.rounded_rectangle([M, -40, M + 440, 360], 46, fill=GOLD)
    logo = Image.open(REPO / 'external' / 'img' / 'usm-logo.png').convert('RGBA')
    logo.thumbnail((370, 300))
    sheet.paste(logo, (M + 220 - logo.width // 2, 160 - logo.height // 2), logo)
    tx = M + 500
    spaced(d, (tx, 52), 'SCHOOL OF CONSTRUCTION AND DESIGN', WIDE(30), GOLD, 0.1)
    d.text((tx, 92), 'University of Southern Mississippi', font=REG(30), fill='#d6d6d6')
    d.text((tx - 6, 132), 'HOSPITAL BIM IN AR', font=HERO(108), fill='#ffffff')
    d.text((tx, 252), 'Seven Revit sub-models on one printed sheet  ·  Original Revit vs. enhanced realism',
           font=SEMI(28), fill=GOLD)

    # ---------------------------------------------------------------- site plan (the AR anchor)
    plan = Image.open(SHOTS / 'plan.png').convert('RGBA')
    py0 = 400
    pw = 1540
    ph = round(pw * plan.height / plan.width)
    ppm = pw / (x1b - x0b)
    plan = plan.resize((pw, ph), Image.LANCZOS)
    card = Image.new('RGB', (pw, ph), '#ffffff')
    card.paste(plan, (0, 0), plan)
    sheet.paste(card, (M, py0))
    d.rectangle([M - 2, py0 - 2, M + pw + 1, py0 + ph + 1], outline=LINE, width=3)
    origin_px = (M + (0 - x0b) * ppm, py0 + (0 - z0b) * ppm)
    # caption, scale bar, north arrow
    spaced(d, (M, py0 - 58), 'SITE PLAN  ·  AR ANCHOR', WIDE(30), GOLD_INK, 0.1)
    d.text((M + 610, py0 - 56), 'Point your camera at this sheet: the hospital rises out of its own plan.',
           font=REG(28), fill=MUTED)
    sb_x, sb_y = M + 40, py0 + ph - 70
    d.rounded_rectangle([sb_x - 20, sb_y - 46, sb_x + 20 * ppm + 120, sb_y + 40], 14, fill=(255, 255, 255))
    for i in range(4):
        d.rectangle([sb_x + i * 5 * ppm, sb_y, sb_x + (i + 1) * 5 * ppm, sb_y + 14], fill=BLACK if i % 2 == 0 else '#ffffff',
                    outline=BLACK, width=2)
    d.text((sb_x, sb_y - 40), '0', font=BOLD(24), fill=BLACK)
    d.text((sb_x + 20 * ppm - 20, sb_y - 40), '20 m', font=BOLD(24), fill=BLACK)
    nx, ny = M + pw - 80, py0 + 90
    d.ellipse([nx - 48, ny - 48, nx + 48, ny + 48], fill=(255, 255, 255), outline=BLACK, width=3)
    d.polygon([(nx, ny - 36), (nx + 18, ny + 24), (nx, ny + 12), (nx - 18, ny + 24)], fill=BLACK)
    d.text((nx - 11, ny + 52), 'N', font=WIDE(28), fill=BLACK)

    # ---------------------------------------------------------------- sub-models column
    cx = M + pw + 50
    cw = W - M - cx
    spaced(d, (cx, py0 - 58), 'SUB-MODELS', WIDE(30), GOLD_INK, 0.1)
    n = len(SUBMODELS)
    gap = 16
    ch = (ph - gap * (n - 1)) // n
    for i, (shot, key, label, desc) in enumerate(SUBMODELS):
        y = py0 + i * (ch + gap)
        tile = Image.new('RGB', (cw, ch), '#161616')
        thumb = cover(Image.open(SHOTS / f'{shot}.png').convert('RGB'), int(cw * 0.5), ch)
        tile.paste(thumb, (cw - thumb.width, 0))
        td = ImageDraw.Draw(tile)
        td.rectangle([0, 0, 10, ch], fill=colors[key])
        td.text((34, 30), f'{i + 1:02d}', font=WIDE(26), fill=GOLD)
        td.text((34, 68), label, font=BOLD(36 if len(label) < 14 else 31), fill='#ffffff')
        words, lines, line = desc.split(), [], ''
        for w_ in words:
            test = (line + ' ' + w_).strip()
            if td.textlength(test, font=REG(23)) > cw * 0.5 - 50:
                lines.append(line); line = w_
            else:
                line = test
        lines.append(line)
        for k, ln in enumerate(lines[:3]):
            td.text((34, 122 + k * 29), ln, font=REG(23), fill='#c9c9c9')
        sheet.paste(rounded(tile, 18), (cx, y), rounded(tile, 18))

    # ---------------------------------------------------------------- original vs enhanced
    by = py0 + ph + 120
    spaced(d, (M, by - 62), 'ORIGINAL REVIT  vs.  ENHANCED', WIDE(30), GOLD_INK, 0.1)
    d.text((M + 830, by - 60), 'In the app: Original · Enhanced · Compare (drag the divider)', font=REG(28), fill=MUTED)
    iw = (1680 - 24) // 2
    ih = H - 92 - 90 - by - 50          # fill the space down to the footer
    for k, (shot, tag, sub, dark) in enumerate((
            ('cmp_original', 'ORIGINAL REVIT EXPORT', 'flat Revit shading, RPC placeholders', False),
            ('cmp_enhanced', 'ENHANCED', 'PBR textures, sky reflections, sun shadows, 3D trees & vehicles', True))):
        x = M + k * (iw + 24)
        img = trim(Image.open(SHOTS / f'{shot}.png').convert('RGB'))
        img.thumbnail((iw - 40, ih - 90), Image.LANCZOS)
        frame = Image.new('RGB', (iw, ih), '#ffffff')
        frame.paste(img, ((iw - img.width) // 2, 80 + (ih - 80 - img.height) // 2))
        fd = ImageDraw.Draw(frame)
        tw = fd.textlength(tag, font=WIDE(24)) + 40
        fd.rounded_rectangle([20, 20, 20 + tw, 66], 23, fill=GOLD if dark else BLACK)
        fd.text((40, 29), tag, font=WIDE(24), fill=BLACK if dark else '#ffffff')
        sheet.paste(rounded(frame, 18), (x, by), rounded(frame, 18))
        d.rounded_rectangle([x, by, x + iw, by + ih], 18, outline=LINE, width=3)
        d.text((x + 6, by + ih + 16), sub, font=REG(25), fill=MUTED)

    # ---------------------------------------------------------------- QR card
    qx0 = M + 1680 + 40
    qx1 = W - M
    qy0, qy1 = by - 10, by + ih + 60
    d.rounded_rectangle([qx0, qy0, qx1, qy1], 24, fill=BLACK)
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(URL)
    qr.make(fit=True)
    qimg = qr.make_image(fill_color=BLACK, back_color='white').convert('RGB')
    qs = qx1 - qx0 - 170
    qimg = qimg.resize((qs, qs), Image.NEAREST)
    sheet.paste(qimg, (qx0 + 85, qy0 + 50))
    spaced(d, (qx0 + 50, qy0 + 50 + qs + 24), 'SCAN TO LAUNCH', WIDE(30), GOLD, 0.1)
    steps = ['1  Scan with the phone camera', '2  Allow camera access', '3  Aim at this whole sheet']
    for k, s in enumerate(steps):
        d.text((qx0 + 50, qy0 + 50 + qs + 78 + k * 36), s, font=SEMI(25), fill='#ffffff')

    # ---------------------------------------------------------------- footer
    fy = H - 92
    d.line([M, fy - 22, W - M, fy - 22], fill=LINE, width=3)
    d.text((M, fy), 'Built by Dr. Salar Kamari', font=BOLD(27), fill=BLACK)
    d.text((M + 380, fy), '·  Revit sample model: Autodesk hospital (Architecture, Structure, HVAC, Electrical, '
           'Fire Alarm; Revit 2018)  ·  Print at 100 % on Letter or A4, keep flat and well lit', font=REG(25), fill=MUTED)
    d.text((W - M - d.textlength(URL, font=SEMI(25)), fy + 40), URL, font=SEMI(25), fill=GOLD_INK)

    # ---------------------------------------------------------------- outputs
    sheet.save(PRINT / 'hospital-poster.png', dpi=(DPI, DPI))
    for name, (pw_in, ph_in) in (('letter', (8.5, 11.0)), ('a4', (8.27, 11.69))):
        page = Image.new('RGB', (round(pw_in * DPI), round(ph_in * DPI)), 'white')
        page.paste(sheet, ((page.width - W) // 2, (page.height - H) // 2))
        page.save(PRINT / f'hospital-poster-{name}.pdf', resolution=DPI)
    prev = sheet.resize((900, 1200), Image.LANCZOS)
    prev.save(ASSETS / 'poster-preview.jpg', quality=82)
    placement = {
        'poster_px': [W, H],
        'plan_origin_px': [round(origin_px[0], 2), round(origin_px[1], 2)],
        'px_per_m': round(ppm, 4),
        'model_scale': round(ppm / H, 7),     # 1 m of model = this many sheet heights
        'image': 'assets/poster-preview.jpg',
        'url': URL,
    }
    (ASSETS / 'target-placement.json').write_text(json.dumps(placement, indent=1))
    print('placement:', placement)


if __name__ == '__main__':
    main()
