"""Download the CC0 PBR textures and the sky HDRI used by the enhanced look, and draw the
procedural ones (metal panel joints, 2x2 ceiling grid, PV cells).

Outputs (src/assets/tex):  <key>_diff.jpg, <key>_nor.jpg  512 px, mobile-sized
                           env.jpg                         1024x512 tonemapped sky for reflections/IBL
                           textures.json                   real-world tile size of each texture (m)
All photo textures and the HDRI are from Poly Haven (CC0). Downloads are cached in pipeline/cache.

Usage: python -I pipeline/fetch_textures.py
"""
import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'pipeline' / 'cache' / 'tex'
OUT = ROOT / 'src' / 'assets' / 'tex'
SIZE = 512
API = 'https://api.polyhaven.com'
HDRI = 'kloofendal_48d_partly_cloudy_puresky'

# key -> (Poly Haven asset, tile size override in m or None to use the asset's real dimensions)
PHOTO = {
    'brick': ('brick_wall_001', None),
    'concrete': ('concrete_wall_008', None),
    'cmu': ('concrete_block_wall', None),
    'pavers': ('concrete_pavers_02', None),
    'grass': ('leafy_grass', 1.0),
    'gravel': ('gravel_floor_02', None),
    'asphalt': ('asphalt_02', None),
    'wood': ('kitchen_wood', 1.2),
    'wood_rough': ('fine_grained_wood', None),
}


def get(url, dest):
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={'User-Agent': 'bim-ar-hospital-pipeline'})
        with urllib.request.urlopen(req) as r:
            dest.write_bytes(r.read())
    return dest


def api(path):
    req = urllib.request.Request(f'{API}/{path}', headers={'User-Agent': 'bim-ar-hospital-pipeline'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def save(img, name, quality=86):
    img.convert('RGB').resize((SIZE, SIZE), Image.LANCZOS).save(OUT / name, quality=quality, optimize=True)


def flatten(img, sigma=48, keep=0.35):
    """Even out large-scale blotches (they read as dirt and as visible tiling once the texture
    repeats across a 100 m facade) while keeping the brick/stone/grain detail."""
    a = np.asarray(img.convert('RGB'), np.float32)
    low = cv2.GaussianBlur(a, (0, 0), sigma)
    mean = a.reshape(-1, 3).mean(0)
    out = a / np.maximum(low, 1) * (mean + (low - mean) * keep)
    return Image.fromarray(np.uint8(np.clip(out, 0, 255)))


def photo_textures(sizes):
    for key, (asset, tile) in PHOTO.items():
        files = api(f'files/{asset}')
        for kind, suffix in (('Diffuse', 'diff'), ('nor_gl', 'nor')):
            src = get(files[kind]['1k']['jpg']['url'], CACHE / f'{asset}_{suffix}.jpg')
            img = Image.open(src)
            if suffix == 'diff':
                img = flatten(img, keep=0.1 if key == 'grass' else 0.35)
            save(img, f'{key}_{suffix}.jpg')
        if tile is None:
            dims = api(f'info/{asset}').get('dimensions') or [2000, 2000]
            tile = dims[0] / 1000
        sizes[key] = round(tile, 3)


def normal_from_height(h, strength=2.0):
    """Tangent-space normal map (OpenGL convention) from a 0..1 height field."""
    gy, gx = np.gradient(h)
    n = np.dstack([-gx * strength, gy * strength, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8))


def noise(size, scale, seed):
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, size // scale), max(2, size // scale))).astype(np.float32)
    return cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)


def procedural(sizes):
    n = 1024
    # Composite metal panel: 1.5 m x 0.75 m modules with recessed reveals, faint brushed streaks.
    h = np.ones((n, n), np.float32)
    for k in range(0, n, n // 2):
        h[:, k:k + 6] = 0.0
    for k in range(0, n, n // 2):
        h[k:k + 6, :] = 0.0
    h = cv2.GaussianBlur(h, (0, 0), 1.2)
    base = 0.80 + 0.03 * noise(n, 64, 1) + 0.015 * cv2.resize(noise(n, 4, 2), (n, n))
    rgb = np.dstack([base * 0.93, base * 0.95, base * 0.98]) * (0.55 + 0.45 * h[..., None])
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((SIZE, SIZE), Image.LANCZOS) \
        .save(OUT / 'metal_panel_diff.jpg', quality=88)
    normal_from_height(h, 3.0).resize((SIZE, SIZE), Image.LANCZOS).save(OUT / 'metal_panel_nor.jpg', quality=90)
    sizes['metal_panel'] = 3.0

    # 2'x2' acoustic ceiling: fissured tile in a white T-bar grid.
    t = 0.86 + 0.06 * noise(n, 3, 3) + 0.03 * noise(n, 40, 4)
    grid = np.ones((n, n), np.float32)
    for k in range(0, n, n // 2):
        grid[:, k:k + 10] = 0; grid[k:k + 10, :] = 0
    rgb = np.where(grid[..., None] > 0, np.dstack([t, t, t * 0.985]), 0.97)
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((SIZE, SIZE), Image.LANCZOS) \
        .save(OUT / 'ceiling_diff.jpg', quality=88)
    normal_from_height(cv2.GaussianBlur(grid, (0, 0), 2), 2.5).resize((SIZE, SIZE), Image.LANCZOS) \
        .save(OUT / 'ceiling_nor.jpg', quality=90)
    sizes['ceiling'] = 1.2192

    # PV module: 6x10 monocrystalline cells with silver busbars, white backsheet gaps.
    img = Image.new('RGB', (n, n), (225, 228, 232))
    d = ImageDraw.Draw(img)
    cw, ch = n / 6, n / 10
    for i in range(6):
        for j in range(10):
            x0, y0 = i * cw + 4, j * ch + 4
            d.rounded_rectangle([x0, y0, x0 + cw - 8, y0 + ch - 8], radius=8, fill=(22, 30, 58))
            for b in (0.3, 0.7):
                xb = x0 + (cw - 8) * b
                d.line([xb, y0, xb, y0 + ch - 8], fill=(150, 156, 170), width=2)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.resize((SIZE, SIZE), Image.LANCZOS).save(OUT / 'solar_diff.jpg', quality=88)
    sizes['solar'] = 1.0


def environment():
    hdr = get(api(f'files/{HDRI}')['hdri']['1k']['hdr']['url'], CACHE / f'{HDRI}_1k.hdr')
    img = cv2.imread(str(hdr), cv2.IMREAD_ANYDEPTH | cv2.IMREAD_COLOR)[:, :, ::-1]
    img = img * 0.9
    ldr = img / (1 + img) * 1.6          # Reinhard, lifted so the sky stays bright after sRGB decode
    ldr = np.clip(ldr, 0, 1) ** (1 / 2.2)
    out = cv2.resize((ldr * 255).astype(np.uint8), (1024, 512), interpolation=cv2.INTER_AREA)
    Image.fromarray(out).save(OUT / 'env.jpg', quality=88)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = {}
    photo_textures(sizes)
    procedural(sizes)
    environment()
    (OUT / 'textures.json').write_text(json.dumps(sizes, indent=1))
    print('textures:', sizes)


if __name__ == '__main__':
    main()
