"""Export the construction sequence as one animated GLB for the AR app.

Run:  blender -b out/cmu_wall_sequence.blend -P export_glb.py -- out/cmu_wall.raw.glb

Drops what only the Blender movie needs (grade, camera, lights, captions) and flattens the
procedural materials to plain PBR colours; the app adds the concrete grain and the X-ray fade
(material keys do not travel in glTF). Animation is exported as one clip sampled from the scene.
"""
import bpy, os, sys

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(ARGV[0]) if ARGV else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "cmu_wall.raw.glb")

DROP_OBJECTS = {"Grass", "Soil"}
DROP_COLLECTIONS = {"Camera + Captions"}
# linear base colour, roughness, metallic per material (matches the Blender look at tabletop scale)
FLAT = {
    "CMU": ((0.36, 0.36, 0.35), 0.92, 0.0),
    "Mortar": ((0.58, 0.56, 0.52), 0.95, 0.0),
    "Footing concrete": ((0.45, 0.45, 0.43), 0.85, 0.0),
    "Grout": ((0.26, 0.255, 0.25), 0.5, 0.0),
    "Rebar": ((0.22, 0.12, 0.08), 0.55, 0.6),
    "Dowel": ((0.20, 0.13, 0.09), 0.55, 0.6),
}

for coll in [c for c in bpy.data.collections if c.name in DROP_COLLECTIONS]:
    for o in list(coll.all_objects):
        bpy.data.objects.remove(o)
    bpy.data.collections.remove(coll)
for name in DROP_OBJECTS:
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name])

for m in bpy.data.materials:
    nt = m.node_tree
    if nt is None:
        continue
    if nt.animation_data:
        nt.animation_data_clear()
    bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is None:
        continue
    for sock in ("Base Color", "Normal", "Alpha", "Roughness", "Metallic"):
        for link in list(bsdf.inputs[sock].links):
            nt.links.remove(link)
    if m.name in FLAT:
        col, rough, metal = FLAT[m.name]
        bsdf.inputs["Base Color"].default_value = (*col, 1)
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Alpha"].default_value = 1.0
    try:
        m.surface_render_method = 'DITHERED'
    except (AttributeError, TypeError):
        pass

os.makedirs(os.path.dirname(OUT), exist_ok=True)
bpy.ops.export_scene.gltf(
    filepath=OUT, export_format='GLB',
    export_apply=True, export_cameras=False, export_lights=False, export_yup=True,
    export_animations=True, export_animation_mode='SCENE', export_frame_range=True,
    export_force_sampling=True, export_optimize_animation_size=True,
    export_def_bones=False, export_materials='EXPORT',
)
print("[cmu] exported", OUT, os.path.getsize(OUT) // 1024, "KB")
