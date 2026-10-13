"""Printable poster = AR image target: Level 1 plan (anchor), upper-level plans, axonometric, QR code.

The poster is exactly 3:4 portrait so 8th Wall's default crop uses the whole sheet.
Writes print/poster.png + print/poster.pdf and src/target-placement.json, which tells the
app where the Level 1 plan sits on the sheet so the 3D model lands exactly on top of it.

Usage: python -I pipeline/make_poster.py <app_url>
"""
import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import qrcode
import trimesh
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MPath

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
PRINT = ROOT / 'print'
W, H = 2400, 3200  # px, 3:4
DPI = 300
FT = 0.3048
NAVY, CYAN, INK = '#14213d', '#1fa2c7', '#222222'


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


def draw_plan(ax, level, groups, ox, oy, k, lw=1.0):
    """Draw a level plan with its origin at pixel (ox, oy), k px per ft."""
    tf = matplotlib.transforms.Affine2D().scale(k).translate(ox, oy) + ax.transData
    for item in level['projected']:
        p = parse_d(item['d'])
        if p is None:
            continue
        floor = item['group'] == 'floors'
        ax.add_patch(PathPatch(p, transform=tf, lw=0.6 * lw, ec='#c9c3b7' if floor else '#7d8790',
                               fc='#f1eee7' if floor else 'none', zorder=1 if floor else 2))
    for item in level['cut']:
        p = parse_d(item['d'])
        if p is None:
            continue
        color = groups[item['group']]['color']
        if item['fill']:
            ax.add_patch(PathPatch(p, transform=tf, lw=0.4 * lw, ec=INK, fc=color, zorder=3))
        else:
            ax.add_patch(PathPatch(p, transform=tf, lw=1.6 * lw, ec=color, fc='none',
                                   capstyle='round', zorder=4))


def render_axon(path_png):
    scene = trimesh.load(ASSETS / 'model.glb')
    fig = plt.figure(figsize=(7, 3.6), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1], projection='3d', proj_type='ortho')
    light = np.array([-0.35, -0.5, 0.8]); light /= np.linalg.norm(light)
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    for name, geom in scene.geometry.items():
        if name == 'ceilings':
            continue
        T = scene.graph.get(name)[0]
        v = trimesh.transformations.transform_points(geom.vertices, T)
        tris = v[geom.faces][:, :, [0, 2, 1]] * [1, -1, 1]  # x, north(-z), up(y)
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        shade = 0.55 + 0.45 * np.abs(n @ light)
        base = np.array(geom.visual.material.baseColorFactor[:3]) / 255
        cols = np.clip(base[None] * shade[:, None], 0, 1)
        alpha = geom.visual.material.baseColorFactor[3] / 255
        ax.add_collection3d(Poly3DCollection(tris, facecolors=np.c_[cols, np.full(len(cols), alpha)],
                                             edgecolors='none'))
    b = np.array([g.bounds for g in scene.geometry.values()])
    lo, hi = b[:, 0].min(0), b[:, 1].max(0)
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(-hi[2], -lo[2]); ax.set_zlim(lo[1], hi[1])
    ax.set_box_aspect((hi[0] - lo[0], hi[2] - lo[2], hi[1] - lo[1]), zoom=0.9)
    ax.view_init(elev=28, azim=-58)
    ax.set_axis_off()
    fig.savefig(path_png, transparent=True)
    plt.close(fig)


def main(url):
    index = json.loads((ASSETS / 'model-index.json').read_text(encoding='utf-8'))
    plan = json.loads((ROOT / 'pipeline' / 'out' / 'plan_geom.json').read_text(encoding='utf-8'))
    groups = index['groups']
    xmin, ymin, xmax, ymax = plan['bounds_ft']
    levels = {lv['name']: lv for lv in plan['levels']}
    PRINT.mkdir(exist_ok=True)

    axon_png = ROOT / 'pipeline' / 'out' / 'axon.png'
    render_axon(axon_png)

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.set_aspect('equal'); ax.axis('off')
    ax.add_patch(Rectangle((0, 0), W, H, fc='white', zorder=0))

    # header band
    ax.add_patch(Rectangle((0, 0), W, 300, fc=NAVY, zorder=1))
    ax.add_patch(Rectangle((0, 300), W, 18, fc=CYAN, zorder=1))
    ax.text(110, 135, 'AEC 450  ·  BIM in Augmented Reality', color='white', fontsize=21,
            weight='bold', va='center')
    ax.text(110, 228, f'{index["source"]}  ·  Level 1 plan is the AR anchor — scan the QR, then aim '
            'your camera at this sheet', color='#cfe6ef', fontsize=8.6, va='center')

    # Level 1 plan: the AR anchor
    margin = 110
    k1 = (W - 2 * margin) / (xmax - xmin + 12)
    ox1 = W / 2 - (xmin + xmax) / 2 * k1
    plan_top = 400
    oy1 = plan_top + ymax * k1 + 6 * k1  # SVG y = -(north); ymax north edge sits at top
    ax.add_patch(Rectangle((margin - 20, plan_top - 20), W - 2 * margin + 40, (ymax - ymin + 12) * k1 + 40,
                           fc='none', ec=NAVY, lw=1.6, zorder=0.5))
    draw_plan(ax, levels['Level 1'], groups, ox1, oy1, k1, lw=1.2)
    ax.text(margin, plan_top - 40, 'LEVEL 1  —  EL. 0\'-0"', color=NAVY, fontsize=14, weight='bold')
    # north arrow + scale bar
    nx, ny = W - margin - 70, plan_top + (ymax - ymin + 12) * k1 - 120
    ax.annotate('', xy=(nx, ny - 60), xytext=(nx, ny + 40),
                arrowprops=dict(arrowstyle='-|>', color=NAVY, lw=3, mutation_scale=40))
    ax.text(nx, ny + 85, 'N', ha='center', color=NAVY, fontsize=16, weight='bold')
    sb_y = plan_top + (ymax - ymin + 12) * k1 - 50
    for i in range(4):
        ax.add_patch(Rectangle((margin + 30 + i * 10 * k1, sb_y), 10 * k1, 18,
                               fc=NAVY if i % 2 == 0 else 'white', ec=NAVY, lw=1, zorder=5))
    ax.text(margin + 30, sb_y + 60, "0        10       20       30       40 ft", color=NAVY, fontsize=8.5)

    # upper level plans
    y2 = plan_top + (ymax - ymin + 12) * k1 + 130
    k2 = (W / 2 - margin - 40) / (xmax - xmin + 12)
    for i, name in enumerate(['Level 2', 'Level 3']):
        x0 = margin + i * (W / 2 - margin + 20)
        ox = x0 + (-xmin + 6) * k2
        oy = y2 + (ymax + 6) * k2
        ax.add_patch(Rectangle((x0, y2), (xmax - xmin + 12) * k2, (ymax - ymin + 12) * k2,
                               fc='#fafafa', ec='#b8c2cc', lw=1, zorder=0.5))
        draw_plan(ax, levels[name], groups, ox, oy, k2, lw=0.7)
        elev = next(lv['elevation_m'] for lv in index['levels'] if lv['name'] == name) / FT
        ax.text(x0, y2 - 22, f'{name.upper()}  —  EL. {elev:.0f}\'-0"', color=NAVY, fontsize=11,
                weight='bold')

    # axonometric + QR + legend
    y3 = y2 + (ymax - ymin + 12) * k2 + 90
    img = plt.imread(axon_png)
    rows, cols = np.where(img[:, :, 3] > 0.02)
    img = img[rows.min():rows.max() + 1, cols.min():cols.max() + 1]  # trim transparent margin
    aw = 1450; ah = min(aw * img.shape[0] / img.shape[1], 640); aw = ah * img.shape[1] / img.shape[0]
    ax.imshow(img, extent=(margin, margin + aw, y3 + 20 + ah, y3 + 20), zorder=2)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=10)
    qr.add_data(url); qr.make(fit=True)
    q = np.array(qr.make_image(fill_color='black', back_color='white').convert('L')) / 255
    qs = 560; qx = W - margin - qs; qy = y3 + 10
    ax.imshow(q, cmap='gray', extent=(qx, qx + qs, qy + qs, qy), interpolation='nearest', zorder=3)
    ax.text(qx + qs / 2, qy + qs + 55, 'SCAN TO LAUNCH AR', ha='center', color=NAVY, fontsize=12.5,
            weight='bold')
    ax.text(qx + qs / 2, qy + qs + 115, url.replace('https://', ''), ha='center', color='#4a5560',
            fontsize=8.5)

    shown = [g for g in ['walls', 'struct_columns', 'framing', 'columns', 'doors', 'windows',
                         'mullions', 'glass', 'plumbing', 'stairs'] if g in groups]
    for i, g in enumerate(shown):
        x = margin + (i % 5) * 440
        ly = H - 205 + (i // 5) * 62
        ax.add_patch(Rectangle((x, ly), 34, 34, fc=groups[g]['color'], ec=INK, lw=0.6, zorder=3))
        ax.text(x + 50, ly + 27, groups[g]['label'], fontsize=7.5, color=INK)
    ax.add_patch(Rectangle((0, H - 60), W, 60, fc=NAVY, zorder=1))
    ax.text(W / 2, H - 28, 'Print at 100% on Letter or A4 · keep the sheet flat and well lit · '
            'built with the open-source 8th Wall engine', ha='center', va='center', color='#cfe6ef',
            fontsize=8)

    fig.savefig(PRINT / 'poster.png', dpi=DPI)
    fig.savefig(PRINT / 'poster.pdf')
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
