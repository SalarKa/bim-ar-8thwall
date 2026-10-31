"""Realistic replacements for the hospital's RPC planting and entourage, plus a site base.

Revit draws RPC trees, shrubs and vehicles as photo billboards; exported to geometry they become
coarse placeholder solids. This script models proper 3D stand-ins, one prototype per species or
vehicle, and lists every placement from the Revit bounding boxes so the app can draw them as
instanced meshes:

  trees    Japanese cherry (in bloom), blue elderberry, golden chain: branching trunks with
           leaf-cluster cards (alpha-cut foliage atlas), normals bent outward for soft volume
  shrubs   boxwood, yew, rhododendron, oleander, magnolia, viburnum, forsythia 'Meadowlark',
           switchgrass (blade clumps), century plant (agave rosette)
  vehicles tractor-trailer (two liveries), EC135-class air-ambulance helicopter
  site     a model base: lawn, paving apron around the building, and the sunken loading dock

Outputs: src/assets/entourage.glb (meshopt-compressed), src/assets/entourage.json (instances).
Materials named 'look:<key>' are replaced in the app by the shared textured looks.

Usage: python -I pipeline/make_entourage.py
"""
import json
import math
import subprocess
from io import BytesIO
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import Polygon, box as sbox
from shapely.ops import unary_union
from trimesh.creation import triangulate_polygon

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src' / 'assets'
OUT = ROOT / 'pipeline' / 'out'
GLTFPACK = ROOT / 'node_modules' / '.bin' / 'gltfpack.cmd'
FT = 0.3048
RNG = np.random.default_rng(7)


# ================================================================ mesh building
class Part:
    """Triangles for one material."""

    def __init__(self):
        self.v, self.n, self.uv, self.c, self.f, self.count = [], [], [], [], [], 0

    def add(self, v, f, n=None, uv=None, c=None):
        v = np.asarray(v, np.float64)
        f = np.asarray(f, np.int64)
        if n is None and c is not None:      # per-vertex colours: keep the vertices, smooth normals
            n = np.asarray(trimesh.Trimesh(v, f, process=False).vertex_normals)
        elif n is None:                      # split hard edges (>40°) for crisp bevels
            m = trimesh.Trimesh(v, f, process=False)
            sm = trimesh.graph.smooth_shade(m, angle=math.radians(40))
            v, f, n = np.asarray(sm.vertices), np.asarray(sm.faces), np.asarray(sm.vertex_normals)
        self.v.append(v); self.n.append(np.asarray(n, np.float64))
        self.uv.append(np.zeros((len(v), 2)) if uv is None else np.asarray(uv, np.float64))
        self.c.append(np.ones((len(v), 4)) if c is None else np.asarray(c, np.float64))
        self.f.append(f + self.count)
        self.count += len(v)

    def empty(self):
        return self.count == 0

    def arrays(self):
        return (np.concatenate(self.v).astype(np.float32), np.concatenate(self.n).astype(np.float32),
                np.concatenate(self.uv).astype(np.float32), np.concatenate(self.c).astype(np.float32),
                np.concatenate(self.f).astype(np.uint32))


class Model:
    def __init__(self):
        self.parts = {}

    def part(self, mat):
        return self.parts.setdefault(mat, Part())


def frame(axis):
    a = np.asarray(axis, float)
    a /= np.linalg.norm(a)
    t = np.array([0, 1, 0]) if abs(a[1]) < 0.9 else np.array([1, 0, 0])
    u = np.cross(a, t); u /= np.linalg.norm(u)
    w = np.cross(a, u)
    return a, u, w


def tube(p0, p1, r0, r1, sides=7, cap=True):
    a, u, w = frame(np.subtract(p1, p0))
    ang = np.linspace(0, 2 * math.pi, sides, endpoint=False)
    ring = np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * w
    v = np.vstack([p0 + ring * r0, p1 + ring * r1])
    f = []
    for i in range(sides):
        j = (i + 1) % sides
        f += [[i, j, sides + j], [i, sides + j, sides + i]]
    if cap:
        v = np.vstack([v, p1])
        c = len(v) - 1
        f += [[sides + i, sides + (i + 1) % sides, c] for i in range(sides)]
    return v, np.array(f)


def lathe(profile, axis_origin, axis, segments=24):
    """Revolve (radius, along-axis) profile points around an axis."""
    a, u, w = frame(axis)
    v, f = [], []
    n = len(profile)
    for s in range(segments):
        t = 2 * math.pi * s / segments
        d = math.cos(t) * u + math.sin(t) * w
        for r, h in profile:
            v.append(np.asarray(axis_origin) + a * h + d * r)
    for s in range(segments):
        s2 = (s + 1) % segments
        for k in range(n - 1):
            p, q = s * n + k, s2 * n + k
            f += [[p, q, q + 1], [p, q + 1, p + 1]]
    return np.array(v), np.array(f)


def rrect(w, h, r, n=4, cy=0.0):
    """Rounded rectangle loop in (y, z); w along z, h along y."""
    r = min(r, w / 2 - 1e-4, h / 2 - 1e-4)
    pts = []
    for cx_, cz_, a0 in ((h / 2 - r, w / 2 - r, 0), (h / 2 - r, -w / 2 + r, 90),
                         (-h / 2 + r, -w / 2 + r, 180), (-h / 2 + r, w / 2 - r, 270)):
        for k in range(n + 1):
            t = math.radians(a0 + 90 * k / n)
            pts.append((cy + cx_ + r * math.sin(t), cz_ + r * math.cos(t)))
    return pts


def loft(sections, cap0=True, cap1=True):
    """sections: [(x, [(y, z), ...])] with equal point counts -> closed body along +x."""
    m = len(sections[0][1])
    v = [(x, y, z) for x, loop in sections for (y, z) in loop]
    f = []
    for s in range(len(sections) - 1):
        for i in range(m):
            j = (i + 1) % m
            a, b, c, d = s * m + i, s * m + j, (s + 1) * m + j, (s + 1) * m + i
            f += [[a, d, c], [a, c, b]]
    v = np.array(v, float)
    if cap0:
        c0 = len(v); v = np.vstack([v, v[:m].mean(0)])
        f += [[c0, i, (i + 1) % m] for i in range(m)]
    if cap1:
        c1 = len(v); v = np.vstack([v, v[(len(sections) - 1) * m:(len(sections)) * m].mean(0)])
        base = (len(sections) - 1) * m
        f += [[c1, base + (i + 1) % m, base + i] for i in range(m)]
    return v, np.array(f)


def rbox(x0, x1, w, h, r, y0=0.0, z0=0.0, bevel=None):
    """Rounded-section box along x from x0 to x1, bottom at y0, centred on z0."""
    bev = bevel if bevel is not None else min(r, (x1 - x0) / 4)
    full = [(y + y0 + h / 2, z + z0) for y, z in rrect(w, h, r)]
    inset = [(y + y0 + h / 2, z + z0) for y, z in rrect(w - 2 * bev, h - 2 * bev, max(r - bev, 0.001))]
    return loft([(x0, inset), (x0 + bev, full), (x1 - bev, full), (x1, inset)])


def transform(v, rot_y=0.0, scale=(1, 1, 1), offset=(0, 0, 0)):
    c, s = math.cos(rot_y), math.sin(rot_y)
    v = np.asarray(v, float) * scale
    x = v[:, 0] * c + v[:, 2] * s
    z = -v[:, 0] * s + v[:, 2] * c
    return np.stack([x, v[:, 1], z], 1) + offset


# ================================================================ foliage atlas
TILE = 512
ATLAS = {'broadleaf': (0, 0), 'smallleaf': (1, 0), 'blossom': (0, 1), 'yellow': (1, 1)}


def leaf_shape(d, cx, cy, length, width, ang, fill, rib):
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    for k in range(14):
        t = k / 13
        x = length * t
        y = width * math.sin(math.pi * t) ** 0.8 * 0.5
        pts.append((x, y))
    pts = pts + [(x, -y) for x, y in reversed(pts[1:-1])]
    poly = [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]
    d.polygon(poly, fill=fill)
    d.line([(cx, cy), (cx + length * 0.9 * ca, cy + length * 0.9 * sa)], fill=rib, width=1)


def green(rng, light=0.0, hue=(78, 112), sat=(0.42, 0.68), val=(0.22, 0.48)):
    h = rng.uniform(*hue) / 360
    s = rng.uniform(*sat)
    v = min(1, rng.uniform(*val) + light)
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255), 255


def foliage_tile(kind, seed):
    rng = np.random.default_rng(seed)
    S = TILE * 2
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    if kind == 'smallleaf':
        n, L, W = 1700, (24, 40), (11, 18)
    else:
        n, L, W = 420, (70, 120), (30, 52)
    # twigs
    for k in range(9):
        a = rng.uniform(0, 2 * math.pi)
        d.line([(c, c), (c + math.cos(a) * S * 0.36, c + math.sin(a) * S * 0.36)], fill=(70, 55, 40, 255), width=5)
    for k in range(n):
        r = S * 0.43 * math.sqrt(rng.uniform(0.02, 1))
        a = rng.uniform(0, 2 * math.pi)
        x, y = c + math.cos(a) * r, c + math.sin(a) * r
        depth = 1 - r / (S * 0.43)                      # inner leaves darker (self-shadowed)
        col = green(rng, light=0.12 * (1 - depth) - 0.08 * depth)
        if kind == 'smallleaf':
            col = green(rng, light=0.05 * (1 - depth) - 0.1 * depth, hue=(90, 125), sat=(0.5, 0.75), val=(0.16, 0.36))
        rib = tuple(min(255, int(ch * 1.35)) for ch in col[:3]) + (255,)
        leaf_shape(d, x, y, rng.uniform(*L), rng.uniform(*W), a + rng.uniform(-0.9, 0.9), col, rib)
    if kind == 'blossom':
        for k in range(260):
            r = S * 0.42 * math.sqrt(rng.uniform(0.05, 1))
            a = rng.uniform(0, 2 * math.pi)
            x, y = c + math.cos(a) * r, c + math.sin(a) * r
            base = np.array([248, 196, 214]) * rng.uniform(0.85, 1.05)
            for p in range(5):
                pa = 2 * math.pi * p / 5 + rng.uniform(0, 1)
                px, py = x + math.cos(pa) * 9, y + math.sin(pa) * 9
                col = tuple(int(min(255, ch)) for ch in base) + (255,)
                d.ellipse([px - 9, py - 9, px + 9, py + 9], fill=col)
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(200, 80, 120, 255))
    if kind == 'yellow':
        for k in range(70):                       # hanging racemes of yellow flowers
            x = c + rng.uniform(-0.36, 0.36) * S
            y = c + rng.uniform(-0.25, 0.3) * S
            ln = rng.uniform(60, 150)
            for t in np.linspace(0, 1, 14):
                rr = 11 * (1 - 0.6 * t)
                col = (int(250 - 20 * t), int(206 - 30 * t), 40, 255)
                d.ellipse([x - rr, y + ln * t - rr, x + rr, y + ln * t + rr], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    # soften the alpha edge of the whole cluster so cards blend into each other
    return img.resize((TILE, TILE), Image.LANCZOS)


def foliage_atlas():
    atlas = Image.new('RGBA', (TILE * 2, TILE * 2), (0, 0, 0, 0))
    for i, (kind, (tx, ty)) in enumerate(ATLAS.items()):
        atlas.paste(foliage_tile(kind, 11 + i), (tx * TILE, ty * TILE))
    # bleed colour into transparent texels so mipmaps don't fringe dark
    arr = np.array(atlas).astype(np.float32)
    rgb, a = arr[..., :3], arr[..., 3:] / 255
    blur = np.array(Image.fromarray(np.uint8(np.clip(rgb * a, 0, 255))).filter(ImageFilter.GaussianBlur(10))).astype(np.float32)
    wa = np.array(Image.fromarray(np.uint8(a[..., 0] * 255)).filter(ImageFilter.GaussianBlur(10))).astype(np.float32)[..., None] / 255
    fill = blur / np.maximum(wa, 1e-3)
    arr[..., :3] = np.where(a > 0.5, rgb, fill)
    out = Image.fromarray(np.uint8(np.clip(arr, 0, 255)))
    buf = BytesIO()
    out.save(buf, 'PNG', optimize=True)
    out.save(OUT / 'foliage_atlas.png')
    return buf.getvalue()


def tile_uv(kind, quad_uv):
    tx, ty = ATLAS[kind]
    return (np.asarray(quad_uv) * 0.5 + [tx * 0.5, ty * 0.5])


# ================================================================ plants (unit height, unit width)
def leaf_cards(part, centers, size, kind, crown_c, crown_r, rng, n_bend=0.75):
    for p in centers:
        a = rng.uniform(0, 2 * math.pi)
        tilt = rng.uniform(-0.6, 0.6)
        u = np.array([math.cos(a), tilt * 0.3, math.sin(a)]); u /= np.linalg.norm(u)
        w = np.cross(u, rng.normal(size=3)); w /= np.linalg.norm(w)
        s = size * rng.uniform(0.75, 1.25)
        quad = np.array([p - u * s / 2 - w * s / 2, p + u * s / 2 - w * s / 2, p + u * s / 2 + w * s / 2,
                         p - u * s / 2 + w * s / 2])
        out = (quad - crown_c) / np.maximum(crown_r, 1e-3)
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-6)
        cn = np.cross(u, w)
        if np.dot(cn, (p - crown_c)) < 0:
            cn = -cn
        nrm = out * n_bend + cn * (1 - n_bend)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        uv = tile_uv(kind, [[0, 1], [1, 1], [1, 0], [0, 0]])
        shade = 0.82 + 0.18 * np.clip((p[1] - crown_c[1]) / max(crown_r[1], 1e-3) * 0.5 + 0.5, 0, 1)
        col = np.tile([shade, shade, shade, 1], (4, 1))
        part.add(quad, [[0, 1, 2], [0, 2, 3]], n=nrm, uv=uv, c=col)


def branch(model, p0, d, length, r, depth, rng, tips):
    p1 = p0 + d * length
    v, f = tube(p0, p1, r, r * 0.68, sides=6 if depth < 2 else 5, cap=False)
    model.part('bark').add(v, f)
    if depth == 0 or length < 0.05:
        tips.append(p1)
        return
    for k in range(rng.integers(2, 4)):
        nd = d + rng.normal(size=3) * 0.55 + np.array([0, 0.35, 0])
        nd /= np.linalg.norm(nd)
        branch(model, p1, nd, length * rng.uniform(0.6, 0.78), r * 0.62, depth - 1, rng, tips)


def tree(kind, crown_w=0.85, trunk_h=0.33, cards=170, seed=1):
    """Deciduous tree, 1 unit tall, crown ~crown_w wide."""
    rng = np.random.default_rng(seed)
    m = Model()
    tips = []
    base = np.array([0.0, 0.0, 0.0])
    top = np.array([0.0, trunk_h, 0.0])
    m.part('bark').add(*tube(base, top, 0.035, 0.028, sides=8, cap=False))
    for k in range(4):
        a = 2 * math.pi * k / 4 + rng.uniform(-0.4, 0.4)
        d = np.array([math.cos(a) * 0.6, 0.8, math.sin(a) * 0.6]); d /= np.linalg.norm(d)
        branch(m, top, d, 0.22, 0.022, 3, rng, tips)
    crown_c = np.array([0, trunk_h + (1 - trunk_h) * 0.52, 0])
    crown_r = np.array([crown_w / 2, (1 - trunk_h) * 0.5, crown_w / 2])
    pts = []
    for t in tips:
        pts.append(t + rng.normal(size=3) * 0.03)
    while len(pts) < cards:
        q = rng.normal(size=3); q /= np.linalg.norm(q)
        q *= rng.uniform(0.55, 1.0) ** 0.5
        pts.append(crown_c + q * crown_r)
    pts = np.array(pts)
    pts[:, 1] = np.clip(pts[:, 1], trunk_h * 0.9, 0.98)
    leaf_cards(m.part('foliage'), pts, 0.24, kind, crown_c, crown_r, rng)
    return m


def shrub(kind, height_ratio=0.7, cards=60, seed=2, core_rgb=(0.12, 0.2, 0.08)):
    """Mounded shrub, 1 unit tall, 1 unit wide (scaled per instance)."""
    rng = np.random.default_rng(seed)
    m = Model()
    c = np.array([0, 0.5, 0])
    r = np.array([0.5, 0.5, 0.5])
    core = trimesh.creation.icosphere(subdivisions=1)
    cv = np.asarray(core.vertices) * (r * 0.78) + c - [0, 0.06, 0]
    cv[:, 1] = np.maximum(cv[:, 1], 0.02)
    m.part('leafcore').add(cv, np.asarray(core.faces), c=np.tile([*core_rgb, 1], (len(cv), 1)))
    pts = []
    while len(pts) < cards:
        q = rng.normal(size=3); q /= np.linalg.norm(q)
        if q[1] < -0.25:
            continue
        pts.append(c + q * r * rng.uniform(0.75, 1.0))
    pts = np.array(pts)
    pts[:, 1] = np.clip(pts[:, 1], 0.12, 0.97)
    leaf_cards(m.part('foliage'), pts, 0.32, kind, c, r, rng, n_bend=0.85)
    return m


def grass_clump(seed=3, blades=70, tint=(0.45, 0.56, 0.4), tip=(0.78, 0.7, 0.5)):
    rng = np.random.default_rng(seed)
    m = Model()
    p = m.part('blade')
    for b in range(blades):
        a = rng.uniform(0, 2 * math.pi)
        lean = rng.uniform(0.05, 0.45)
        h = rng.uniform(0.6, 1.0)
        wbase = rng.uniform(0.012, 0.022)
        d = np.array([math.cos(a), 0, math.sin(a)])
        side = np.array([-d[2], 0, d[0]])
        root = d * rng.uniform(0, 0.12)
        vs, cs = [], []
        for k in range(5):
            t = k / 4
            pos = root + d * lean * h * t ** 1.6 + np.array([0, h * t * (1 - 0.15 * lean * t), 0])
            w = wbase * (1 - t * 0.92)
            vs += [pos - side * w, pos + side * w]
            col = np.array(tint) * (0.75 + 0.35 * t) * (1 - t ** 3) + np.array(tip) * t ** 3
            cs += [[*col, 1], [*col, 1]]
        f = [[2 * k, 2 * k + 1, 2 * k + 3] for k in range(4)] + [[2 * k, 2 * k + 3, 2 * k + 2] for k in range(4)]
        vs = np.array(vs)
        n = np.tile(np.cross(side, [0, 1, 0]) * 0.4 + np.array([0, 0.9, 0]), (len(vs), 1))
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        p.add(vs, f, n=n, c=np.array(cs))
    return m


def agave(seed=4, leaves=28):
    rng = np.random.default_rng(seed)
    m = Model()
    p = m.part('agave')
    for k in range(leaves):
        a = k * 2.39996 + rng.uniform(-0.1, 0.1)       # golden-angle rosette
        ring = k / leaves
        el = math.radians(80 - 55 * ring)               # inner leaves upright, outer spread
        L = 0.55 + 0.35 * ring
        d = np.array([math.cos(a) * math.cos(el), math.sin(el), math.sin(a) * math.cos(el)])
        side = np.array([-math.sin(a), 0, math.cos(a)])
        vs = []
        for t in np.linspace(0, 1, 6):
            w = 0.07 * (1 - t) ** 0.7 + 0.003
            pos = d * L * t - np.array([0, 0.18 * (t ** 2) * ring, 0])
            vs += [pos - side * w, pos + side * w, pos + np.cross(d, side) * w * 0.35]
        vs = np.array(vs) + [0, 0.02, 0]
        f = []
        for s in range(5):
            a0, b0, c0 = 3 * s, 3 * s + 1, 3 * s + 2
            a1, b1, c1 = a0 + 3, b0 + 3, c0 + 3
            f += [[a0, c0, c1], [a0, c1, a1], [c0, b0, b1], [c0, b1, c1], [a0, a1, b1], [a0, b1, b0]]
        col = np.tile([0.36, 0.52, 0.5, 1], (len(vs), 1)) * (0.85 + 0.25 * rng.random())
        col[:, 3] = 1
        p.add(vs, f, c=col)
    return m


# ================================================================ vehicles (metres, +x forward, +y up)
def wheel(m, cx, cz, r, width, rim='rim', y=None, dual=False):
    y = r if y is None else y
    axis = np.array([0, 0, 1.0])
    for off in ([-width * 0.55, width * 0.55] if dual else [0.0]):
        o = np.array([cx, y, cz + off])
        tire = [(r * 0.62, -width / 2), (r * 0.9, -width / 2), (r * 0.98, -width * 0.42), (r, -width * 0.2),
                (r, width * 0.2), (r * 0.98, width * 0.42), (r * 0.9, width / 2), (r * 0.62, width / 2)]
        m.part('rubber').add(*lathe(tire, o - axis * 0, axis, 22))
        disc = [(0.0, width * 0.36), (r * 0.18, width * 0.38), (r * 0.5, width * 0.3), (r * 0.62, width * 0.44),
                (r * 0.62, -width * 0.44), (r * 0.5, -width * 0.3), (r * 0.18, -width * 0.38), (0.0, -width * 0.36)]
        m.part(rim).add(*lathe(disc, o, axis, 18))


def semi_truck(paint='paint_red'):
    """Class 8 tractor (aero sleeper) + 40 ft dry van: 16.8 m overall, 4.0 m tall, the Revit
    entourage envelope (55 ft)."""
    m = Model()
    # ----- trailer: x from -16.76 (rear) to -4.55 (nose, just behind the sleeper), floor 1.2 m
    L0, L1 = -16.76, -4.55
    tw, th, ty = 2.59, 2.75, 1.22
    m.part('trailer').add(*rbox(L0, L1, tw, th, 0.05, y0=ty))
    for x in np.arange(L0 + 0.6, L1 - 0.3, 1.22):               # side posts
        for side in (-1, 1):
            m.part('trailer_trim').add(*rbox(x - 0.03, x + 0.03, 0.02, th - 0.1, 0.005, y0=ty + 0.05, z0=side * (tw / 2 + 0.005)))
    m.part('trailer_trim').add(*rbox(L0 - 0.04, L0 + 0.02, tw + 0.02, th + 0.02, 0.02, y0=ty - 0.01))    # rear frame
    m.part('chrome').add(*rbox(L0 - 0.05, L0 - 0.02, 0.02, th - 0.2, 0.005, y0=ty + 0.1, z0=0))          # door seam
    for side in (-1, 1):                                                                               # side skirts
        m.part('plastic_black').add(*rbox(-13.4, -7.2, 0.03, 0.75, 0.01, y0=0.42, z0=side * (tw / 2 - 0.05)))
        m.part('light_tail').add(*rbox(L0 - 0.06, L0 - 0.02, 0.18, 0.12, 0.03, y0=1.0, z0=side * 0.95))
    m.part('chassis').add(*rbox(L0 + 0.2, L0 + 0.3, tw - 0.2, 0.12, 0.01, y0=0.55))                       # underride guard
    m.part('chassis').add(*rbox(-16.2, -13.8, 1.0, 0.3, 0.02, y0=0.92))                                   # slider
    m.part('chassis').add(*rbox(-7.3, -7.1, 1.9, 0.6, 0.02, y0=0.35))                                     # landing gear
    for x in (-15.55, -14.3):
        for side in (-1, 1):
            wheel(m, x, side * 0.93, 0.51, 0.28, dual=True)
    # ----- tractor: x from -3.3 (fifth wheel) to 0 (bumper)
    m.part('chassis').add(*rbox(-6.6, -0.3, 0.95, 0.32, 0.02, y0=0.62))                                   # frame rails
    m.part('chassis').add(*rbox(-5.6, -3.7, 2.2, 0.12, 0.02, y0=1.02))                                    # fifth wheel deck
    # cab + sleeper with roof fairing: loft of rounded sections along x
    secs = []
    for x, w, h, y0, r in ((-4.25, 2.3, 2.25, 1.15, 0.25), (-4.15, 2.44, 2.8, 1.1, 0.3), (-2.6, 2.48, 2.85, 1.1, 0.32),
                           (-1.9, 2.46, 2.55, 1.1, 0.34), (-1.45, 2.36, 1.95, 1.1, 0.32)):
        secs.append((x, [(yy + y0 + h / 2, zz) for yy, zz in rrect(w, h, r, n=5)]))
    m.part(paint).add(*loft(secs))
    # sloped windshield and side windows
    ws = [(-1.47, [(y + 1.1 + 1.95 / 2, z) for y, z in rrect(2.2, 0.62, 0.15, n=3, cy=0.55)]),
          (-1.95, [(y + 1.1 + 2.55 / 2, z) for y, z in rrect(2.3, 0.62, 0.15, n=3, cy=0.62)])]
    m.part('glass_dark').add(*loft([(ws[0][0] + 0.03, ws[0][1]), (ws[1][0] + 0.03, ws[1][1])]))
    for side in (-1, 1):
        m.part('glass_dark').add(*rbox(-2.55, -1.75, 0.02, 0.62, 0.08, y0=2.55, z0=side * 1.235))
        m.part('chrome').add(*rbox(-1.55, -1.45, 0.06, 0.42, 0.02, y0=2.35, z0=side * 1.45))              # mirrors
        m.part('chrome').add(*rbox(-1.62, -1.5, 0.22, 0.03, 0.01, y0=2.55, z0=side * 1.33))
        m.part('chrome').add(*tube(np.array([-3.0, 0.55, side * 0.85]), np.array([-3.0, 0.55, side * 0.85]) + [1.1, 0, 0], 0.3, 0.3, 16)[0:2])
        m.part('chrome').add(*tube(np.array([-4.1, 1.5, side * 1.05]), np.array([-4.1, 4.15, side * 1.05]), 0.065, 0.06, 10))  # stacks
        m.part(paint).add(*rbox(-4.15, -1.6, 0.06, 0.6, 0.03, y0=0.45, z0=side * 1.2))                     # side fairing
    # hood: sloped aerodynamic nose
    hood = []
    for x, w, h, y0, r in ((-1.47, 2.3, 1.55, 0.75, 0.3), (-0.8, 2.25, 1.35, 0.72, 0.36), (-0.25, 2.15, 1.05, 0.6, 0.4),
                           (0.0, 1.95, 0.85, 0.55, 0.36)):
        hood.append((x, [(yy + y0 + h / 2, zz) for yy, zz in rrect(w, h, r, n=5)]))
    m.part(paint).add(*loft(hood))
    m.part('chrome').add(*rbox(-0.02, 0.06, 1.15, 0.75, 0.08, y0=0.72))                                   # grille
    m.part('plastic_black').add(*rbox(-0.12, 0.18, 2.4, 0.38, 0.1, y0=0.38))                              # bumper
    for side in (-1, 1):
        m.part('light_head').add(*rbox(-0.25, 0.04, 0.42, 0.16, 0.06, y0=0.98, z0=side * 0.82))
        m.part('light_amber').add(*rbox(-1.5, -1.42, 0.12, 0.05, 0.02, y0=4.02, z0=side * 0.9))
    for x in (-0.95,):
        for side in (-1, 1):
            wheel(m, x, side * 1.0, 0.51, 0.3)
    for x in (-4.0, -5.3):
        for side in (-1, 1):
            wheel(m, x, side * 0.93, 0.51, 0.28, dual=True)
    return m


def helicopter():
    """EC135-class air ambulance: 10.2 m rotor, 12.2 m long; white with red livery band."""
    m = Model()
    secs = []
    for x, w, h, y0, r in ((2.05, 0.25, 0.3, 1.25, 0.12), (1.85, 1.0, 1.05, 0.88, 0.48), (1.3, 1.5, 1.55, 0.62, 0.62),
                           (0.0, 1.62, 1.68, 0.55, 0.6), (-1.6, 1.58, 1.7, 0.6, 0.55), (-2.4, 1.1, 1.25, 0.95, 0.45),
                           (-3.0, 0.42, 0.5, 1.45, 0.2), (-6.2, 0.26, 0.32, 1.62, 0.12), (-6.9, 0.22, 0.28, 1.66, 0.1)):
        secs.append((x, [(yy + y0 + h / 2, zz) for yy, zz in rrect(w, h, r, n=5)]))
    v, f = loft(secs)
    c = np.ones((len(v), 4))
    band = (v[:, 1] > 1.05) & (v[:, 1] < 1.35) & (v[:, 0] < 1.6)
    c[band] = [0.72, 0.05, 0.06, 1]
    lower = v[:, 1] < 0.85
    c[lower] = [0.72, 0.05, 0.06, 1]
    m.part('paint_livery').add(v, f, c=c)
    # canopy glazing
    gl = []
    for x, w, h, y0, r in ((1.92, 0.7, 0.45, 1.25, 0.2), (1.55, 1.36, 0.75, 1.3, 0.3), (0.85, 1.6, 0.62, 1.52, 0.28)):
        gl.append((x, [(yy + y0 + h / 2, zz) for yy, zz in rrect(w, h, r, n=4)]))
    m.part('glass_dark').add(*loft(gl))
    for side in (-1, 1):
        m.part('glass_dark').add(*rbox(-1.1, 0.4, 0.02, 0.5, 0.1, y0=1.4, z0=side * 0.81))
    # engine cowling + mast
    m.part('paint_livery').add(*rbox(-2.1, 0.5, 1.05, 0.48, 0.2, y0=2.15), c=None)
    m.part('chassis').add(*tube(np.array([-0.4, 2.55, 0]), np.array([-0.4, 2.85, 0]), 0.09, 0.08, 10))
    # main rotor: 4 blades
    for k in range(4):
        a = math.radians(45 + 90 * k)
        d = np.array([math.cos(a), 0, math.sin(a)])
        side = np.array([-d[2], 0, d[0]])
        p0 = np.array([-0.4, 2.86, 0]) + d * 0.3
        p1 = np.array([-0.4, 2.86, 0]) + d * 5.1
        quad = [p0 - side * 0.14, p1 - side * 0.12, p1 + side * 0.12, p0 + side * 0.14]
        q = np.array(quad)
        top = q + [0, 0.03, 0]
        vv = np.vstack([q, top])
        ff = [[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6], [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2], [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0]]
        m.part('rotor').add(vv, ff)
    m.part('chassis').add(*tube(np.array([-0.4, 2.8, 0]), np.array([-0.4, 2.95, 0]), 0.22, 0.18, 12))
    # fenestron tail + stabiliser
    fin = [(-6.3, 1.4), (-7.15, 1.4), (-7.45, 3.0), (-7.0, 3.05), (-6.55, 2.0)]
    pv = np.array([[x, y, -0.05] for x, y in fin] + [[x, y, 0.05] for x, y in fin])
    n5 = len(fin)
    ff = [[0, i, i + 1] for i in range(1, n5 - 1)] + [[n5, n5 + i + 1, n5 + i] for i in range(1, n5 - 1)]
    ff += [[i, (i + 1) % n5, n5 + (i + 1) % n5] for i in range(n5)] + [[i, n5 + (i + 1) % n5, n5 + i] for i in range(n5)]
    m.part('paint_white').add(pv, ff)
    m.part('chassis').add(*lathe([(0.48, -0.16), (0.55, -0.1), (0.55, 0.1), (0.48, 0.16)], np.array([-6.75, 1.85, 0]),
                                 np.array([0, 0, 1.0]), 20))
    m.part('paint_white').add(*rbox(-6.0, -5.4, 1.8, 0.06, 0.02, y0=1.62))
    # skids
    for side in (-1, 1):
        z = side * 1.05
        m.part('chassis').add(*tube(np.array([-2.0, 0.08, z]), np.array([1.7, 0.08, z]), 0.05, 0.05, 8))
        m.part('chassis').add(*tube(np.array([1.7, 0.08, z]), np.array([2.0, 0.28, z]), 0.05, 0.05, 8))
        for x in (-1.2, 0.8):
            m.part('chassis').add(*tube(np.array([x, 0.08, z]), np.array([x, 0.65, side * 0.65]), 0.045, 0.045, 8))
    m.part('light_tail').add(*rbox(-7.0, -6.9, 0.08, 0.06, 0.02, y0=3.02))
    return m


# ================================================================ site base
def site_base(scene, trucks):
    m = Model()
    tex = scene['textures']
    fps = [Polygon(p) for p in scene['footprint'] if len(p) >= 4]
    fp = unary_union([p.buffer(0) for p in fps])
    minx, minz, maxx, maxz = fp.bounds
    for t in trucks:
        minx, maxx = min(minx, t['min'][0]), max(maxx, t['max'][0])
        minz, maxz = min(minz, t['min'][2]), max(maxz, t['max'][2])
    margin = 9.0
    base = sbox(minx - margin, minz - margin, maxx + margin, maxz + margin)
    base = base.buffer(-3, join_style=1).buffer(3, join_style=1)        # rounded corners
    dock = unary_union([sbox(t['min'][0] - 2.2, t['min'][2] - 0.2, t['max'][0] + 2.2, t['max'][2] + 1.5) for t in trucks]) \
        if trucks else Polygon()
    if trucks:
        # the drive leaves the dock on the side away from the building, out to the edge of the base
        dmin, dmax = dock.bounds[1], dock.bounds[3]
        cz = (fp.bounds[1] + fp.bounds[3]) / 2
        z_out = (minz - margin - 1) if (dmin + dmax) / 2 < cz else (maxz + margin + 1)
        z_a, z_b = sorted([z_out, (dmin + dmax) / 2])
        drive = sbox(dock.bounds[0] - 1.5, z_a, dock.bounds[2] + 1.5, z_b).intersection(base).difference(fp)
    else:
        drive = Polygon()
    apron = fp.buffer(4.0, join_style=2).difference(fp.buffer(-0.05))
    lawn = base.difference(fp.buffer(4.0, join_style=2)).difference(dock).difference(drive)
    apron = apron.intersection(base).difference(dock).difference(drive)
    dock_floor_y = (min(t['min'][1] for t in trucks) if trucks else 0.0)
    top_y = -0.02

    def flat(poly, look, y):
        polys = poly.geoms if hasattr(poly, 'geoms') else [poly]
        for p in polys:
            if p.is_empty or p.area < 0.5:
                continue
            v2, f = triangulate_polygon(p)
            v = np.column_stack([v2[:, 0], np.full(len(v2), y), v2[:, 1]])
            uv = v2 / tex.get(look.split(':')[-1].replace('look:', ''), 2.0)
            n = np.tile([0, 1, 0], (len(v), 1))
            m.part(look).add(v, f[:, ::-1], n=n, uv=uv)

    def walls(poly, look, y0, y1, outward=True):
        polys = poly.geoms if hasattr(poly, 'geoms') else [poly]
        for p in polys:
            for ring in [p.exterior, *p.interiors]:
                pts = np.array(ring.coords)
                for a, b in zip(pts[:-1], pts[1:]):
                    v = np.array([[a[0], y0, a[1]], [b[0], y0, b[1]], [b[0], y1, b[1]], [a[0], y1, a[1]]])
                    d = b - a
                    nn = np.array([d[1], 0, -d[0]]); nn /= max(np.linalg.norm(nn), 1e-9)
                    L = np.linalg.norm(d)
                    uv = np.array([[0, y0], [L, y0], [L, y1], [0, y1]]) / tex.get('concrete', 2.7)
                    m.part(look).add(v, [[0, 1, 2], [0, 2, 3]], n=np.tile(nn, (4, 1)), uv=uv)

    flat(fp.buffer(-0.05), 'look:concrete', top_y - 0.03)      # building pad under the footprint
    flat(lawn, 'look:grass', top_y)
    flat(apron, 'look:pavers', top_y + 0.01)
    flat(drive.difference(dock), 'look:asphalt', top_y + 0.005)
    if not dock.is_empty:
        flat(dock.intersection(base), 'look:asphalt', dock_floor_y)
        walls(dock.intersection(base).difference(fp), 'look:concrete', dock_floor_y, top_y)
        ramp = drive.difference(dock)
        if not ramp.is_empty:
            pass
    # plinth: underside and dark edge band like an architectural model base
    walls(base, 'plinth', -1.6, top_y)
    v2, f = triangulate_polygon(base)
    m.part('plinth').add(np.column_stack([v2[:, 0], np.full(len(v2), -1.6), v2[:, 1]]), f,
                         n=np.tile([0, -1, 0], (len(v2), 1)))
    return m, {'base_bounds': list(base.bounds)}


# ================================================================ placements
PLANTS = {   # Revit type keyword -> prototype
    'japanese cherry': 'tree_cherry', 'blue berry elder': 'tree_elder', 'golden chain': 'tree_goldenchain',
    'boxwood': 'shrub_boxwood', 'yew': 'shrub_yew', 'rhododendron': 'shrub_rhododendron',
    'oleander': 'shrub_oleander', 'magnolia': 'shrub_magnolia', 'vibernum': 'shrub_viburnum',
    'viburnum': 'shrub_viburnum', 'meadowlark': 'shrub_forsythia', 'switchgrass': 'grass_switch',
    'century': 'agave_century',
}


def plant_proto(typ):
    t = (typ or '').lower()
    for k, v in PLANTS.items():
        if k in t:
            return v
    return 'shrub_viburnum'


def placements(scene):
    inst = {}
    trucks = []
    seen = set()
    for p in scene['placements']:
        lo, hi = np.array(p['min']), np.array(p['max'])
        key = tuple(np.round(lo, 2)) + tuple(np.round(hi, 2))
        if key in seen:
            continue
        seen.add(key)
        c = (lo + hi) / 2
        size = hi - lo
        if abs(c[0]) > 75 or abs(c[2]) > 75:     # family definitions parked at the Revit origin
            continue
        if p['cat'] == 'OST_Planting':
            proto = plant_proto(p['type'])
            h = size[1]
            w = min(max(size[0], size[2]), h * (1.9 if proto.startswith('shrub') else 1.4)) \
                if not proto.startswith('tree') else max(size[0], size[2]) * 0.92
            if proto == 'agave_century':
                w = h * 1.9
            jitter = RNG.uniform(0.9, 1.1)
            inst.setdefault(proto, []).append({'p': [round(c[0], 3), round(lo[1], 3), round(c[2], 3)],
                                               'yaw': round(float(RNG.uniform(0, 2 * math.pi)), 3),
                                               's': [round(w * jitter, 3), round(h * jitter, 3), round(w * jitter, 3)]})
        elif 'truck' in (p['family'] or '').lower():
            trucks.append({'min': lo.tolist(), 'max': hi.tolist(), 'c': c.tolist()})
        elif 'helicopter' in (p['family'] or '').lower():
            yaw = 0.0 if size[0] > size[2] else math.pi / 2
            inst.setdefault('helicopter', []).append({'p': [round(c[0], 3), round(lo[1], 3), round(c[2], 3)], 'yaw': yaw})
    fp = unary_union([Polygon(q).buffer(0) for q in scene['footprint'] if len(q) >= 4])
    for i, t in enumerate(trucks):
        lo, hi, c = np.array(t['min']), np.array(t['max']), np.array(t['c'])
        along_x = (hi[0] - lo[0]) > (hi[2] - lo[2])
        ends = ([lo[0], c[2]], [hi[0], c[2]]) if along_x else ([c[0], lo[2]], [c[0], hi[2]])
        from shapely.geometry import Point
        d0, d1 = fp.distance(Point(ends[0])), fp.distance(Point(ends[1]))
        cab = np.array(ends[1] if d1 > d0 else ends[0])     # cab faces away from the dock wall
        rear = np.array(ends[0] if d1 > d0 else ends[1])
        fwd = cab - rear
        yaw = math.atan2(-fwd[1], fwd[0])
        name = 'semi_truck_a' if i % 2 == 0 else 'semi_truck_b'
        inst.setdefault(name, []).append({'p': [round(cab[0], 3), round(lo[1], 3), round(cab[1], 3)],
                                          'yaw': round(yaw, 4)})
    return inst, trucks


# ================================================================ writing
MATERIALS = {
    'foliage': dict(color=[1, 1, 1, 1], rough=0.78, metal=0, texture='atlas', alpha='MASK', cutoff=0.42),
    'leafcore': dict(color=[1, 1, 1, 1], rough=0.9, metal=0, vcolor=True),
    'bark': dict(color=[0.24, 0.19, 0.15, 1], rough=0.95, metal=0),
    'blade': dict(color=[1, 1, 1, 1], rough=0.75, metal=0, vcolor=True),
    'agave': dict(color=[1, 1, 1, 1], rough=0.5, metal=0, vcolor=True),
    'paint_red': dict(color=[0.42, 0.02, 0.02, 1], rough=0.22, metal=0.35, clearcoat=1.0),
    'paint_blue': dict(color=[0.02, 0.08, 0.3, 1], rough=0.22, metal=0.35, clearcoat=1.0),
    'paint_white': dict(color=[0.85, 0.86, 0.87, 1], rough=0.25, metal=0.1, clearcoat=1.0),
    'paint_livery': dict(color=[1, 1, 1, 1], rough=0.25, metal=0.1, clearcoat=1.0, vcolor=True),
    'trailer': dict(color=[0.86, 0.87, 0.88, 1], rough=0.38, metal=0.2),
    'trailer_trim': dict(color=[0.7, 0.72, 0.74, 1], rough=0.3, metal=0.8),
    'glass_dark': dict(color=[0.03, 0.04, 0.05, 1], rough=0.04, metal=0.4),
    'chrome': dict(color=[0.92, 0.92, 0.92, 1], rough=0.08, metal=1.0),
    'rim': dict(color=[0.82, 0.83, 0.85, 1], rough=0.22, metal=1.0),
    'rubber': dict(color=[0.025, 0.025, 0.028, 1], rough=0.88, metal=0),
    'chassis': dict(color=[0.06, 0.06, 0.065, 1], rough=0.6, metal=0.4),
    'plastic_black': dict(color=[0.04, 0.04, 0.045, 1], rough=0.55, metal=0),
    'rotor': dict(color=[0.1, 0.1, 0.11, 1], rough=0.4, metal=0.3),
    'light_head': dict(color=[0.9, 0.9, 0.95, 1], rough=0.05, metal=0, emissive=[1.0, 0.96, 0.85]),
    'light_tail': dict(color=[0.5, 0.0, 0.0, 1], rough=0.1, metal=0, emissive=[0.9, 0.02, 0.02]),
    'light_amber': dict(color=[0.8, 0.4, 0.0, 1], rough=0.1, metal=0, emissive=[1.0, 0.45, 0.0]),
    'plinth': dict(color=[0.16, 0.16, 0.17, 1], rough=0.85, metal=0),
}


def write_glb(path, protos, atlas_png):
    """protos: {name: Model}. One node per prototype, one child mesh per material."""
    bin_parts, views, accessors, meshes, nodes, materials, mat_index = [], [], [], [], [], [], {}
    offset = 0

    def view(data, target=None):
        nonlocal offset
        pad = (-len(data)) % 4
        v = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)}
        if target:
            v['target'] = target
        views.append(v)
        bin_parts.append(data + b'\0' * pad)
        offset += len(data) + pad
        return len(views) - 1

    def acc(arr, kind, target):
        bv = view(np.ascontiguousarray(arr).tobytes(), target)
        comp = 5126 if arr.dtype == np.float32 else 5125
        a = {'bufferView': bv, 'componentType': comp, 'count': int(arr.shape[0]), 'type': kind}
        if kind == 'VEC3' and comp == 5126:
            a['min'] = arr.min(0).tolist(); a['max'] = arr.max(0).tolist()
        accessors.append(a)
        return len(accessors) - 1

    img_view = view(atlas_png)
    images = [{'bufferView': img_view, 'mimeType': 'image/png'}]
    textures = [{'source': 0, 'sampler': 0}]
    samplers = [{'magFilter': 9729, 'minFilter': 9987, 'wrapS': 33071, 'wrapT': 33071}]

    def material(name):
        if name in mat_index:
            return mat_index[name]
        if name.startswith('look:'):
            mat = {'name': name, 'pbrMetallicRoughness': {'metallicFactor': 0, 'roughnessFactor': 1}}
        else:
            d = MATERIALS[name]
            pbr = {'baseColorFactor': d['color'], 'metallicFactor': d['metal'], 'roughnessFactor': d['rough']}
            if d.get('texture') == 'atlas':
                pbr['baseColorTexture'] = {'index': 0}
            mat = {'name': name, 'pbrMetallicRoughness': pbr, 'doubleSided': True}
            if d.get('alpha'):
                mat['alphaMode'] = d['alpha']; mat['alphaCutoff'] = d['cutoff']
            if d.get('emissive'):
                mat['emissiveFactor'] = d['emissive']
            if d.get('clearcoat'):
                mat['extensions'] = {'KHR_materials_clearcoat': {'clearcoatFactor': d['clearcoat'],
                                                                 'clearcoatRoughnessFactor': 0.06}}
        materials.append(mat)
        mat_index[name] = len(materials) - 1
        return mat_index[name]

    root_nodes = []
    for pname, model in protos.items():
        children = []
        for mname, part in model.parts.items():
            if part.empty():
                continue
            v, n, uv, c, f = part.arrays()
            attrs = {'POSITION': acc(v, 'VEC3', 34962), 'NORMAL': acc(n, 'VEC3', 34962)}
            needs_uv = mname == 'foliage' or mname.startswith('look:')
            if needs_uv:
                attrs['TEXCOORD_0'] = acc(uv, 'VEC2', 34962)
            if MATERIALS.get(mname, {}).get('vcolor'):
                attrs['COLOR_0'] = acc(c, 'VEC4', 34962)
            prim = {'attributes': attrs, 'indices': acc(f.reshape(-1), 'SCALAR', 34963), 'material': material(mname)}
            meshes.append({'name': f'{pname}:{mname}', 'primitives': [prim]})
            nodes.append({'name': f'{pname}:{mname}', 'mesh': len(meshes) - 1})
            children.append(len(nodes) - 1)
        nodes.append({'name': pname, 'children': children})
        root_nodes.append(len(nodes) - 1)
    blob = b''.join(bin_parts)
    gltf = {'asset': {'version': '2.0', 'generator': 'bim-ar hospital entourage'}, 'scene': 0,
            'scenes': [{'nodes': root_nodes}], 'nodes': nodes, 'meshes': meshes, 'materials': materials,
            'accessors': accessors, 'bufferViews': views, 'buffers': [{'byteLength': len(blob)}],
            'images': images, 'textures': textures, 'samplers': samplers,
            'extensionsUsed': ['KHR_materials_clearcoat']}
    js = json.dumps(gltf, separators=(',', ':')).encode()
    js += b' ' * ((-len(js)) % 4)
    with open(path, 'wb') as fh:
        fh.write(b'glTF' + (2).to_bytes(4, 'little') + (12 + 8 + len(js) + 8 + len(blob)).to_bytes(4, 'little'))
        fh.write(len(js).to_bytes(4, 'little') + b'JSON' + js)
        fh.write(len(blob).to_bytes(4, 'little') + b'BIN\0' + blob)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    scene = json.loads((ASSETS / 'scene.json').read_text())
    inst, trucks = placements(scene)
    protos = {
        'tree_cherry': tree('blossom', crown_w=0.95, trunk_h=0.3, cards=190, seed=21),
        'tree_elder': tree('broadleaf', crown_w=1.0, trunk_h=0.28, cards=200, seed=22),
        'tree_goldenchain': tree('yellow', crown_w=0.8, trunk_h=0.34, cards=170, seed=23),
        'shrub_boxwood': shrub('smallleaf', cards=88, seed=31, core_rgb=(0.05, 0.1, 0.03)),
        'shrub_yew': shrub('smallleaf', cards=88, seed=32, core_rgb=(0.03, 0.07, 0.02)),
        'shrub_rhododendron': shrub('blossom', cards=96, seed=33),
        'shrub_oleander': shrub('blossom', cards=112, seed=34),
        'shrub_magnolia': shrub('broadleaf', cards=88, seed=35),
        'shrub_viburnum': shrub('broadleaf', cards=88, seed=36),
        'shrub_forsythia': shrub('yellow', cards=96, seed=37),
        'grass_switch': grass_clump(seed=41),
        'agave_century': agave(seed=42),
        'semi_truck_a': semi_truck('paint_red'),
        'semi_truck_b': semi_truck('paint_blue'),
        'helicopter': helicopter(),
    }
    base, base_info = site_base(scene, trucks)
    protos['site_base'] = base
    raw = OUT / 'entourage.raw.glb'
    write_glb(raw, protos, foliage_atlas())
    final = ASSETS / 'entourage.glb'
    subprocess.run([str(GLTFPACK), '-i', str(raw), '-o', str(final), '-cc', '-kn', '-km', '-kv',
                    '-vp', '16', '-vt', '14'], check=True, capture_output=True)
    manifest = {'instances': {k: v for k, v in inst.items() if k in protos}, 'static': ['site_base'],
                **base_info}
    (ASSETS / 'entourage.json').write_text(json.dumps(manifest, separators=(',', ':')))
    for k, v in inst.items():
        print(f'{k:20s} x{len(v)}')
    tris = {k: sum(len(np.concatenate(p.f)) for p in m.parts.values() if not p.empty()) for k, m in protos.items()}
    print('prototype triangles:', tris)
    print(f'entourage.glb {final.stat().st_size / 1e6:.2f} MB')


if __name__ == '__main__':
    main()
