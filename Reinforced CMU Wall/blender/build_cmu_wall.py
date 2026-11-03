"""Build an animated construction sequence for a reinforced, partially grouted CMU wall.

Sequence (low-lift grouting, CMHA TEK 03-02A / TMS 602):
  1. Footing poured, vertical dowels set in line with the cells that will be reinforced
  2. Corner leads laid up at both ends, racked back (first course on a full mortar bed)
  3. Courses filled in between the leads along a mason's line (face-shell bedding)
  4. Leads raised and filled to the 5'-4" grout lift height (8 courses, no cleanouts needed)
  5. Vertical bars lowered into the cells and lapped with the dowels
  6. Bars secured with rebar positioners at the top
  7. Grout poured into the reinforced cells
  8. Grout consolidated with a mechanical vibrator
  9. Grout stopped 1-1/2" below the top as a key for the next pour

Run:  blender -b -P build_cmu_wall.py -- [output.blend]

All dimensions are written in inches and converted to metres. Animation uses only object
location/scale keys (glTF-friendly) except the CMU "x-ray" fade, which keys material alpha.
"""
import bpy, bmesh, math, os, sys
from bpy_extras import anim_utils
from mathutils import Vector

import json

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(ARGV[0]) if ARGV else os.path.join(HERE, "out", "cmu_wall_sequence.blend")
STEPS_JSON = os.path.join(HERE, "..", "src", "assets", "steps.json")   # step names + timing for the AR app

# Short step names for the AR overlay and the printed sheet (same order as the captions)
STEP_TITLES = ["Footing & dowels", "Corner leads", "Fill to the line", "Lift height 5'-4\"",
               "Vertical bars", "Rebar positioners", "Grout the cells", "Consolidate", "Grout key"]

IN = 0.0254
def v(x, y, z):
    return Vector((x * IN, y * IN, z * IN))

# ---- Unit and wall dimensions (inches) -------------------------------------------------
L, W, H = 15.625, 7.625, 7.625      # 8x8x16 stretcher, actual size
HL = 7.625                          # half unit length
J = 0.375                           # mortar joint
MOD, CRS = 16.0, 8.0                # nominal module and course height
FS, WEB = 1.25, 1.0                 # face shell and web thickness
CORE_W = W - 2 * FS                 # core width across the wall
RECESS = 0.12                       # tooled joints sit slightly behind the face
N_MOD, N_CRS = 9, 8                 # 12 ft long, 8 courses = 5'-4" (one low-lift pour)
WALL_L = N_MOD * MOD - J
WALL_H = N_CRS * CRS
OX = -WALL_L / 2                    # wall centred on the origin

BAR_CELLS = [0, 6, 12, 17]          # cell n sits at x = 3.8125 + 8n -> 48" o.c. + end cell
BAR_DIA = 0.625                     # #5 bar
DOWEL_TOP = 30.0                    # 48 bar diameters of lap above the footing
BAR_TOP = WALL_H + 24.0             # bars project for the next lift's splice
GROUT_TOP = WALL_H - 1.5            # grout key for the next pour

def cell_x(n):
    return 3.8125 + 8 * n

# ---- Scene reset ----------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = 'IMPERIAL'
scene.render.fps = 24
scene.render.resolution_x, scene.render.resolution_y = 1280, 720
for eng in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
    try:
        scene.render.engine = eng
        break
    except TypeError:
        pass
try:
    scene.eevee.taa_render_samples = 48
except AttributeError:
    pass
try:
    scene.view_settings.view_transform = 'AgX'
except TypeError:
    pass

def collection(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or scene.collection).children.link(c)
    return c

C_SITE = collection("Site")
C_MASON = collection("Masonry")
C_UNITS = collection("CMU Units", C_MASON)
C_MORTAR = collection("Mortar", C_MASON)
C_REBAR = collection("Reinforcement")
C_GROUT = collection("Grout")
C_TOOLS = collection("Tools")
C_CAM = collection("Camera + Captions")

# ---- Materials -------------------------------------------------------------------------
def new_material(name):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except AttributeError:
        pass
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        nt.nodes.clear()
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs[0], out.inputs[0])
    return m, nt, bsdf

def set_transparent(m):
    for attr, val in (("surface_render_method", 'DITHERED'), ("blend_method", 'HASHED')):
        try:
            setattr(m, attr, val)
            break
        except (AttributeError, TypeError):
            pass
    try:
        m.use_transparent_shadow = True
    except AttributeError:
        pass

def textured(name, dark, light, rough, noise_scale=60.0, bump=0.2, per_object=0.25, metal=0.0):
    """Principled material with world-space noise colour, bump and per-object tint variation."""
    m, nt, bsdf = new_material(name)
    N, Lk = nt.nodes, nt.links
    geo = N.new("ShaderNodeNewGeometry")
    noise = N.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 8.0
    Lk.new(geo.outputs["Position"], noise.inputs["Vector"])
    info = N.new("ShaderNodeObjectInfo")
    scl = N.new("ShaderNodeMath"); scl.operation = 'MULTIPLY'; scl.inputs[1].default_value = per_object
    Lk.new(info.outputs["Random"], scl.inputs[0])
    add = N.new("ShaderNodeMath"); add.operation = 'ADD'
    Lk.new(noise.outputs["Fac"], add.inputs[0]); Lk.new(scl.outputs[0], add.inputs[1])
    ramp = N.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = 0.3, (*dark, 1)
    ramp.color_ramp.elements[1].position, ramp.color_ramp.elements[1].color = 0.75 + per_object, (*light, 1)
    Lk.new(add.outputs[0], ramp.inputs["Fac"])
    Lk.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bmp = N.new("ShaderNodeBump")
    bmp.inputs["Strength"].default_value = bump
    bmp.inputs["Distance"].default_value = 0.002
    Lk.new(noise.outputs["Fac"], bmp.inputs["Height"])
    Lk.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    return m, bsdf

def rebar_material(name, color):
    """Rusty steel with bump ribs (deformations) running along the bar's length."""
    m, nt, bsdf = new_material(name)
    N, Lk = nt.nodes, nt.links
    geo = N.new("ShaderNodeNewGeometry")
    sep = N.new("ShaderNodeSeparateXYZ")
    Lk.new(geo.outputs["Position"], sep.inputs[0])
    mul = N.new("ShaderNodeMath"); mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = 2 * math.pi / (0.45 * IN)
    Lk.new(sep.outputs["Z"], mul.inputs[0])
    sin = N.new("ShaderNodeMath"); sin.operation = 'SINE'
    Lk.new(mul.outputs[0], sin.inputs[0])
    bmp = N.new("ShaderNodeBump")
    bmp.inputs["Strength"].default_value = 0.8
    bmp.inputs["Distance"].default_value = 0.001
    Lk.new(sin.outputs[0], bmp.inputs["Height"])
    Lk.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = 0.7
    bsdf.inputs["Roughness"].default_value = 0.55
    return m

def flat_material(name, color, rough=0.6, metal=0.0, emission=0.0):
    m, nt, bsdf = new_material(name)
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1)
        bsdf.inputs["Emission Strength"].default_value = emission
    return m

def emission_material(name, color, strength=1.0, alpha=1.0):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except AttributeError:
        pass
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if alpha < 1.0:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.inputs["Fac"].default_value = alpha
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs[0])
        set_transparent(m)
    else:
        nt.links.new(em.outputs[0], out.inputs[0])
    return m

MAT_CMU, BSDF_CMU = textured("CMU", (0.30, 0.30, 0.29), (0.46, 0.46, 0.44), 0.92, noise_scale=90, bump=0.35)
MAT_MORTAR, BSDF_MORTAR = textured("Mortar", (0.52, 0.50, 0.46), (0.66, 0.64, 0.60), 0.95, noise_scale=150, bump=0.2, per_object=0.05)
MAT_FOOTING, _ = textured("Footing concrete", (0.38, 0.38, 0.37), (0.55, 0.55, 0.53), 0.85, noise_scale=40, per_object=0.0)
MAT_GROUT, _ = textured("Grout", (0.20, 0.20, 0.20), (0.34, 0.33, 0.32), 0.45, noise_scale=70, per_object=0.0)
MAT_GRASS, _ = textured("Grass", (0.05, 0.16, 0.03), (0.16, 0.34, 0.07), 0.9, noise_scale=300, bump=0.8, per_object=0.0)
MAT_SOIL, _ = textured("Soil", (0.13, 0.08, 0.05), (0.24, 0.16, 0.10), 0.95, noise_scale=80, bump=0.5, per_object=0.0)
MAT_REBAR = rebar_material("Rebar", (0.22, 0.12, 0.08))
MAT_DOWEL = rebar_material("Dowel", (0.20, 0.13, 0.09))
MAT_WIRE = flat_material("Galvanized wire", (0.75, 0.76, 0.78), rough=0.35, metal=1.0)
MAT_LINE = flat_material("Mason line", (1.0, 0.1, 0.55), rough=0.5, emission=1.5)
MAT_VIB = flat_material("Vibrator head", (0.85, 0.65, 0.05), rough=0.35, metal=0.6)
MAT_HOSE = flat_material("Vibrator hose", (0.03, 0.03, 0.03), rough=0.5)
MAT_TEXT = emission_material("Caption text", (1.0, 1.0, 1.0), strength=1.4)
MAT_TITLE = emission_material("Caption title", (1.0, 0.78, 0.25), strength=1.4)
MAT_PLATE = emission_material("Caption plate", (0.01, 0.015, 0.03), strength=1.0, alpha=0.62)
for m in (MAT_CMU, MAT_MORTAR):
    set_transparent(m)

# ---- Geometry helpers ------------------------------------------------------------------
def boxes_mesh(name, boxes, mat_index=None):
    """One mesh from axis-aligned boxes given as (x0, x1, y0, y1, z0, z1) in inches."""
    bm = bmesh.new()
    for i, (x0, x1, y0, y1, z0, z1) in enumerate(boxes):
        verts = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        for p in verts:
            p.co = Vector(((x0 if p.co.x < 0 else x1) * IN,
                           (y0 if p.co.y < 0 else y1) * IN,
                           (z0 if p.co.z < 0 else z1) * IN))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me

def cylinders_mesh(name, cyls, seg=12):
    """Vertical cylinders (x, y, r, z0, z1, material_index) in inches."""
    bm = bmesh.new()
    for x, y, r, z0, z1, mi in cyls:
        res = bmesh.ops.create_cone(bm, cap_ends=True, segments=seg,
                                    radius1=r * IN, radius2=r * IN, depth=(z1 - z0) * IN)
        bmesh.ops.translate(bm, verts=res["verts"], vec=v(x, y, (z0 + z1) / 2))
        for f in {f for vt in res["verts"] for f in vt.link_faces}:
            f.material_index = mi
            f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me

def add_obj(name, me, coll, loc, mats, shadow=True):
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    o.location = loc
    if not me.materials:
        for m in mats:
            me.materials.append(m)
    if not shadow:
        o.visible_shadow = False
    return o

def cmu_mesh(name, length, cores):
    """Hollow CMU unit with rounded cores and eased edges. Origin at centre of the bed face.
    cores: list of (centre_x, core_length) in unit-local inches."""
    tmp = []
    outer = bpy.data.objects.new(name + "_outer", boxes_mesh(name + "_outer", [(-length / 2, length / 2, -W / 2, W / 2, 0, H)]))
    scene.collection.objects.link(outer)
    tmp.append(outer)
    for i, (cx, cl) in enumerate(cores):
        cut = bpy.data.objects.new(f"{name}_core{i}", boxes_mesh("cut", [(cx - cl / 2, cx + cl / 2, -CORE_W / 2, CORE_W / 2, -2, H + 2)]))
        scene.collection.objects.link(cut)
        tmp.append(cut)
        b = cut.modifiers.new("round", 'BEVEL')
        b.width, b.segments, b.limit_method = 0.8 * IN, 4, 'NONE'
        boo = outer.modifiers.new(f"core{i}", 'BOOLEAN')
        boo.operation, boo.object = 'DIFFERENCE', cut
    ease = outer.modifiers.new("ease", 'BEVEL')
    ease.width, ease.segments, ease.limit_method = 0.12 * IN, 2, 'ANGLE'
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(outer.evaluated_get(dg))
    me.name = name
    me.materials.clear()            # the boolean leaves an empty slot 0 behind
    me.materials.append(MAT_CMU)
    for poly in me.polygons:
        poly.material_index = 0
    for o in tmp:
        old = o.data
        bpy.data.objects.remove(o)
        bpy.data.meshes.remove(old)
    return me

CORE_L = (L - 3 * WEB) / 2
ME_FULL = cmu_mesh("CMU 8x8x16", L, [(-(L / 2 - WEB - CORE_L / 2), CORE_L), (L / 2 - WEB - CORE_L / 2, CORE_L)])
ME_HALF = cmu_mesh("CMU 8x8x8 half", HL, [(0.0, HL - 2 * WEB)])

# ---- Animation helpers -----------------------------------------------------------------
def key(target, path, frame, interp='BEZIER'):
    target.keyframe_insert(path, frame=frame)
    if interp == 'BEZIER':
        return
    ad = target.id_data.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    full = target.path_from_id(path)
    for fc in bag.fcurves:
        if fc.data_path == full:
            for kp in fc.keyframe_points:
                if abs(kp.co[0] - frame) < 0.5:
                    kp.interpolation = interp

def show_between(o, f_on, f_off=None):
    """Toggle visibility with constant scale keys (exports cleanly to glTF)."""
    o.scale = (0, 0, 0); key(o, "scale", f_on - 1, 'CONSTANT')
    o.scale = (1, 1, 1); key(o, "scale", f_on, 'CONSTANT')
    if f_off is not None:
        key(o, "scale", f_off - 1, 'CONSTANT')
        o.scale = (0, 0, 0); key(o, "scale", f_off, 'CONSTANT')

def drop_in(o, f0, f1, rise, f_off=None):
    """Appear at f0 `rise` inches above the final spot and settle into place by f1."""
    final = o.location.copy()
    o.location = final + v(0, 0, rise); key(o, "location", f0)
    o.location = final; key(o, "location", f1)
    show_between(o, f0, f_off)

def grow(o, axis, f0, f1, interp='BEZIER'):
    """Grow from zero along one local axis (origin must sit at the growing object's start)."""
    o.scale = (0, 0, 0); key(o, "scale", f0 - 1, 'CONSTANT')
    s = [1.0, 1.0, 1.0]; s[axis] = 0.0
    o.scale = s; key(o, "scale", f0, interp)
    o.scale = (1, 1, 1); key(o, "scale", f1, interp)

CAPTIONS = []   # (text, f_on, f_off)

# ---- Site: footing, grade, dowels ------------------------------------------------------
t = 1
FOOT_W, FOOT_D = 20.0, 12.0
footing = add_obj("Footing", boxes_mesh("Footing", [(-6, WALL_L + 6, -FOOT_W / 2, FOOT_W / 2, 0, FOOT_D)]),
                  C_SITE, v(OX, 0, -FOOT_D), [MAT_FOOTING])
grass = add_obj("Grass", boxes_mesh("Grass", [(-30, WALL_L + 30, -70, -FOOT_W / 2, -1.5, 0)]), C_SITE, v(OX, 0, -0.5), [MAT_GRASS])
soil = add_obj("Soil", boxes_mesh("Soil", [(-30, WALL_L + 30, -70, -FOOT_W / 2, -FOOT_D - 6, -1.5)]), C_SITE, v(OX, 0, -0.5), [MAT_SOIL])
grow(footing, 2, t + 4, t + 34)
f_cap = t
t += 40
for i, n in enumerate(BAR_CELLS):
    d = add_obj(f"Dowel {i + 1}", cylinders_mesh("Dowel", [(0, 0, BAR_DIA / 2, -FOOT_D + 3, DOWEL_TOP, 0)]),
                C_REBAR, v(OX + cell_x(n), -0.45, 0), [MAT_DOWEL])
    drop_in(d, t + i * 8, t + i * 8 + 16, 40)
t += len(BAR_CELLS) * 8 + 30
CAPTIONS.append(("1. Footing poured; dowels set in line with the reinforced cells", f_cap, t))

# ---- Masonry layout --------------------------------------------------------------------
class Piece:
    def __init__(self, k, x, length):
        self.k, self.x0, self.length = k, x, length
        self.x1 = x + length
        self.order = None
        self.objs = []

courses = []
for k in range(N_CRS):
    if k % 2 == 0:
        row = [Piece(k, MOD * i, L) for i in range(N_MOD)]
    else:
        row = [Piece(k, 0.0, HL)] + [Piece(k, MOD / 2 + MOD * i, L) for i in range(N_MOD - 1)] + [Piece(k, N_MOD * MOD - MOD / 2, HL)]
    courses.append(row)

# Build order: leads at both ends racked back half a unit per course, then fill along the line.
phases = []   # (label, [pieces], mason_line_course or None)
LEAD_COURSES = 4
for s in range(N_CRS // LEAD_COURSES):
    ks = range(s * LEAD_COURSES, (s + 1) * LEAD_COURSES)
    left, right = [], []
    for kl, k in enumerate(ks):
        reach = 48 - 8 * kl
        left += [p for p in courses[k] if p.x1 <= reach + 0.01]
        right += sorted([p for p in courses[k] if p.x0 >= WALL_L - reach - 0.01], key=lambda p: -p.x0)
    phases.append((f"lead{s}", left + right, None))
    for k in ks:
        fill = [p for p in courses[k] if p not in left and p not in right]
        phases.append((f"fill{s}", fill, k))
seq = [p for _, ps, _ in phases for p in ps]
for i, p in enumerate(seq):
    p.order = i

# ---- Masonry objects + animation --------------------------------------------------------
STEP, PAUSE = 5, 14
def face_shell_strips(x0, x1, z0, z1, full=False):
    if full:
        return [(x0, x1, -W / 2 + RECESS, W / 2 - RECESS, z0, z1)]
    return [(x0, x1, -W / 2 + RECESS, -W / 2 + FS, z0, z1), (x0, x1, W / 2 - FS, W / 2 - RECESS, z0, z1)]

phase_spans = {}
for label, pieces, line_k in phases:
    start = t
    if line_k is not None:
        z_line = line_k * CRS + CRS - 0.1
        line = add_obj(f"Mason line c{line_k + 1}", cylinders_mesh("line", [(0, 0, 0.09, 0, WALL_L + 8, 0)], seg=6),
                       C_TOOLS, v(OX - 4, -W / 2 - 0.25, z_line), [MAT_LINE], shadow=False)
        line.rotation_euler = (0, math.radians(90), 0)
        show_between(line, t, t + len(pieces) * STEP + 12)
        t += 8
    for p in pieces:
        k = p.k
        zb = k * CRS
        # bed joint spread just ahead of the unit (full bed on the first course)
        bx0, bx1 = max(0.0, p.x0 - J / 2), min(WALL_L, p.x1 + J / 2)
        bed = add_obj(f"Bed joint c{k + 1} @{p.x0:g}", boxes_mesh("bed", face_shell_strips(0, bx1 - bx0, 0, J, full=(k == 0))),
                      C_MORTAR, v(OX + bx0, 0, zb), [MAT_MORTAR])
        grow(bed, 0, t, t + 6)
        unit = add_obj(f"CMU c{k + 1} @{p.x0:g}", ME_FULL if p.length == L else ME_HALF, C_UNITS,
                       v(OX + p.x0 + p.length / 2, 0, zb + J), [])
        drop_in(unit, t + 3, t + 13, 18)
        # head joints are buttered on whichever unit of a pair goes in second
        row = courses[k]
        idx = row.index(p)
        for nb, gx in ((row[idx - 1] if idx > 0 else None, p.x0 - J), (row[idx + 1] if idx + 1 < len(row) else None, p.x1)):
            if nb is not None and nb.order < p.order:
                hj = add_obj(f"Head joint c{k + 1} @{gx:g}", boxes_mesh("head", face_shell_strips(0, J, J, CRS)),
                             C_MORTAR, v(OX + gx, 0, zb), [MAT_MORTAR])
                drop_in(hj, t + 3, t + 13, 18)
        t += STEP
    t += PAUSE
    phase_spans.setdefault(label, [start, t])[1] = t

CAPTIONS.append(("2. Lay up corner leads, racked back (first course on a full mortar bed)",
                 phase_spans["lead0"][0], phase_spans["lead0"][1]))
CAPTIONS.append(("3. Fill in between the leads along a mason's line",
                 phase_spans["fill0"][0], phase_spans["fill0"][1]))
CAPTIONS.append(("4. Raise leads and fill to the 5'-4\" grout lift height (8 courses)",
                 phase_spans["lead1"][0], phase_spans["fill1"][1] + 50))
t += 20

# ---- X-ray the masonry so the cells are visible -----------------------------------------
XRAY = 0.2
def fade(bsdfs, f0, f1, a0, a1):
    for b in bsdfs:
        a = b.inputs["Alpha"]
        a.default_value = a0; key(a, "default_value", f0)
        a.default_value = a1; key(a, "default_value", f1)
        a.default_value = 1.0
fade((BSDF_CMU, BSDF_MORTAR), t, t + 24, 1.0, XRAY)
XRAY_IN = (t, t + 24)
t += 30

# ---- Vertical reinforcement ------------------------------------------------------------
f_cap = t
for i, n in enumerate(BAR_CELLS):
    bar = add_obj(f"Vertical bar {i + 1}", cylinders_mesh("Bar", [(0, 0, BAR_DIA / 2, 0, BAR_TOP - 0.5, 0)]),
                  C_REBAR, v(OX + cell_x(n), 0.45, 0.5), [MAT_REBAR])
    drop_in(bar, t, t + 34, 110)
    t += 22
t += 30
CAPTIONS.append(("5. Lower vertical bars into the cells and lap them with the dowels", f_cap, t))

f_cap = t
for i, n in enumerate(BAR_CELLS):
    xc = cell_x(n)
    w = 0.15
    pos = add_obj(f"Rebar positioner {i + 1}", boxes_mesh("Positioner", [
        (-0.6 - w, -0.6, -W / 2 + 0.3, W / 2 - 0.3, 0, w),
        (0.6, 0.6 + w, -W / 2 + 0.3, W / 2 - 0.3, 0, w),
        (-0.6 - w, 0.6 + w, 0.45 - 0.45 - w, 0.45 - 0.45, 0, w),
        (-0.6 - w, 0.6 + w, 0.45 + 0.45, 0.45 + 0.45 + w, 0, w),
    ]), C_REBAR, v(OX + xc, 0, WALL_H - 0.05), [MAT_WIRE])
    drop_in(pos, t, t + 12, 14)
    t += 10
t += 40
CAPTIONS.append(("6. Secure the bars with rebar positioners at the top of the lift", f_cap, t))

# ---- Grout ------------------------------------------------------------------------------
STREAM_TOP = 104.0
GROUT_L, GROUT_W = 5.4, CORE_W - 0.1
f_cap = t
POUR = 54
for i, n in enumerate(BAR_CELLS):
    xc = cell_x(n)
    g = add_obj(f"Grout cell {i + 1}", boxes_mesh("Grout", [(-GROUT_L / 2, GROUT_L / 2, -GROUT_W / 2, GROUT_W / 2, 0, GROUT_TOP - J)]),
                C_GROUT, v(OX + xc, 0, J), [MAT_GROUT])
    st = add_obj(f"Grout stream {i + 1}", cylinders_mesh("Stream", [(0, -1.2, 1.0, -(STREAM_TOP - J), 0, 0)], seg=10),
                 C_TOOLS, v(OX + xc, 0, STREAM_TOP), [MAT_GROUT], shadow=False)
    f_fill0, f_fill1 = t + 6, t + POUR
    # stream falls to the bottom of the cell, then shortens as the grout rises
    st.scale = (0, 0, 0); key(st, "scale", t - 1, 'CONSTANT')
    st.scale = (1, 1, 0.05); key(st, "scale", t, 'LINEAR')
    st.scale = (1, 1, 1); key(st, "scale", f_fill0, 'LINEAR')
    st.scale = (1, 1, (STREAM_TOP - GROUT_TOP) / (STREAM_TOP - J)); key(st, "scale", f_fill1, 'CONSTANT')
    st.scale = (0, 0, 0); key(st, "scale", f_fill1 + 1, 'CONSTANT')
    grow(g, 2, f_fill0, f_fill1, 'LINEAR')
    t += POUR - 14
t += 40
CAPTIONS.append(("7. Pour grout into the reinforced cells (one lift, no cleanouts)", f_cap, t))

f_cap = t
vib = add_obj("Concrete vibrator", cylinders_mesh("Vibrator", [(0, 0, 0.7, 0, 15, 0), (0, 0, 0.42, 15, 95, 1)], seg=12),
              C_TOOLS, v(0, 0, 0), [MAT_VIB, MAT_HOSE], shadow=True)
VY, ZUP, ZDOWN = -1.6, WALL_H + 12, 10.0
cur = None
for i, n in enumerate(BAR_CELLS):
    xc = OX + cell_x(n)
    if cur is None:
        vib.location = v(xc, VY, ZUP + 40); key(vib, "location", t)
        show_between(vib, t)
        t += 12
    else:
        vib.location = v(cur, VY, ZUP); key(vib, "location", t)
        t += 16
    vib.location = v(xc, VY, ZUP); key(vib, "location", t)
    t += 14
    vib.location = v(xc, VY, ZDOWN); key(vib, "location", t)
    t += 10   # dwell while vibrating
    vib.location = v(xc, VY, ZDOWN + 1.0); key(vib, "location", t)
    t += 14
    vib.location = v(xc, VY, ZUP); key(vib, "location", t)
    cur = xc
vib.location = v(cur, VY, ZUP + 60); key(vib, "location", t + 14)
vib.scale = (1, 1, 1); key(vib, "scale", t + 13, 'CONSTANT')
vib.scale = (0, 0, 0); key(vib, "scale", t + 14, 'CONSTANT')
t += 30
CAPTIONS.append(("8. Consolidate the grout with a mechanical vibrator", f_cap, t))

f_cap = t
fade((BSDF_CMU, BSDF_MORTAR), t + 10, t + 40, XRAY, 1.0)
XRAY_OUT = (t + 10, t + 40)
t += 150
CAPTIONS.append(("9. Stop grout 1-1/2\" below the top as a key for the next pour", f_cap, t))
END = t
scene.frame_start, scene.frame_end = 1, END

# ---- Camera, lights, world ---------------------------------------------------------------
target = bpy.data.objects.new("Camera target", None)
C_CAM.objects.link(target)
target.location = (0, 0, 0.8)
rig = bpy.data.objects.new("Camera rig", None)
C_CAM.objects.link(rig)
cam_data = bpy.data.cameras.new("Camera")
cam_data.lens = 38
cam_data.clip_start = 0.05
cam_data.shift_y = -0.05           # keep the wall clear of the caption plate
cam = bpy.data.objects.new("Camera", cam_data)
C_CAM.objects.link(cam)
cam.parent = rig
tr = cam.constraints.new('TRACK_TO')
tr.target, tr.track_axis, tr.up_axis = target, 'TRACK_NEGATIVE_Z', 'UP_Y'
scene.camera = cam

rig.rotation_euler = (0, 0, math.radians(-10)); key(rig, "rotation_euler", 1, 'LINEAR')
rig.rotation_euler = (0, 0, math.radians(18)); key(rig, "rotation_euler", END, 'LINEAR')
f_rebar = CAPTIONS[4][1]
f_grout = CAPTIONS[6][1]
f_final = CAPTIONS[8][1]
for f, pos, tz in ((1, (-3.0, -4.8, 2.2), 0.35),
                   (phase_spans["lead1"][0], (-3.1, -5.1, 2.5), 0.6),
                   (f_rebar, (-3.4, -5.8, 3.0), 1.0),
                   (f_grout, (-3.1, -5.3, 3.1), 0.95),
                   (f_final, (-3.1, -5.3, 3.1), 0.9),
                   (END - 30, (-2.9, -4.8, 3.9), 0.75)):
    cam.location = pos; key(cam, "location", f)
    target.location = (0, 0, tz); key(target, "location", f)

sun_data = bpy.data.lights.new("Sun", 'SUN')
sun_data.energy = 2.6
sun_data.angle = math.radians(3)
sun = bpy.data.objects.new("Sun", sun_data)
C_CAM.objects.link(sun)
sun.rotation_euler = (math.radians(55), 0, math.radians(-60))
fill_data = bpy.data.lights.new("Fill", 'AREA')
fill_data.energy = 120
fill_data.size = 4
fill = bpy.data.objects.new("Fill", fill_data)
C_CAM.objects.link(fill)
fill.location = (3.5, -3.0, 3.0)
fill.rotation_euler = (math.radians(55), 0, math.radians(50))

world = bpy.data.worlds.new("Sky")
scene.world = world
try:
    world.use_nodes = True
except AttributeError:
    pass
bg = world.node_tree.nodes.get("Background")
if bg is None:
    world.node_tree.nodes.clear()
    bg = world.node_tree.nodes.new("ShaderNodeBackground")
    out = world.node_tree.nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(bg.outputs[0], out.inputs[0])
bg.inputs["Color"].default_value = (0.62, 0.70, 0.80, 1)
bg.inputs["Strength"].default_value = 0.5

# ---- Captions (camera-attached HUD; exclude this collection for AR export) ----------------
def hud(name, body, x, y, size, mat):
    cu = bpy.data.curves.new(name, 'FONT')
    cu.body = body
    cu.size = size
    cu.align_x, cu.align_y = 'LEFT', 'CENTER'
    o = bpy.data.objects.new(name, cu)
    C_CAM.objects.link(o)
    o.parent = cam
    o.location = (x, y, -1.0)
    o.data.materials.append(mat)
    o.visible_shadow = False
    return o

HALF_W = 18 / cam_data.lens          # half-width of the frame at 1 m (36 mm sensor)
HALF_H = HALF_W * 9 / 16
BOTTOM = -HALF_H +cam_data.shift_y * 2 * HALF_W   # lens shift moves the frame edge
plate = add_obj("Caption plate", boxes_mesh("Plate", [(-HALF_W / IN, HALF_W / IN, -0.05 / IN, 0.05 / IN, 0, 0.001 / IN)]),
                C_CAM, (0, BOTTOM + 0.05, -1.002), [MAT_PLATE], shadow=False)
plate.parent = cam
title = hud("Caption title", "REINFORCED CMU WALL  -  CONSTRUCTION SEQUENCE", -HALF_W + 0.03, BOTTOM + 0.075, 0.018, MAT_TITLE)
for i, (body, f_on, f_off) in enumerate(CAPTIONS):
    c = hud(f"Caption {i + 1}", body, -HALF_W + 0.03, BOTTOM + 0.04, 0.027, MAT_TEXT)
    show_between(c, max(f_on, 1), f_off if i < len(CAPTIONS) - 1 else None)

scene.frame_set(1)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT)
steps = [{"n": i + 1, "title": STEP_TITLES[i], "caption": body.split(". ", 1)[1], "start": a, "end": b}
         for i, (body, a, b) in enumerate(CAPTIONS)]
with open(STEPS_JSON, "w") as f:
    json.dump({"fps": scene.render.fps, "frame_end": END, "xray": [*XRAY_IN, *XRAY_OUT], "xray_alpha": XRAY,
               "footing_depth_m": FOOT_D * IN, "steps": steps}, f, indent=1)
print(f"[cmu] saved {OUT}  frames 1-{END}  units={len(seq)}  captions:")
for body, a, b in CAPTIONS:
    print(f"   {a:5d}-{b:5d}  {body}")
