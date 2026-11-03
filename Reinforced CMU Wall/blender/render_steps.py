"""Render one still per construction step for the printed sheet (transparent background, no grade,
no captions), at frames picked from src/assets/steps.json.

Run:  blender -b out/cmu_wall_sequence.blend -P render_steps.py
Writes pipeline/out/steps/step_<n>.png
"""
import bpy, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "pipeline", "out", "steps")
steps = json.load(open(os.path.join(ROOT, "src", "assets", "steps.json")))["steps"]

# where in each step the still is taken (fraction of the step), chosen so the step's action shows
PICK = {1: 0.95, 2: 0.93, 3: 0.55, 4: 0.82, 5: 0.80, 6: 0.80, 7: 0.62, 8: 0.36, 9: 0.97}

sc = bpy.context.scene
sc.render.film_transparent = True
sc.render.resolution_x, sc.render.resolution_y = 1180, 740
sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = 'PNG'
sc.render.image_settings.color_mode = 'RGBA'
for o in sc.objects:
    if o.type == 'FONT' or o.name in ("Caption plate", "Grass", "Soil"):
        o.hide_render = True
os.makedirs(OUT, exist_ok=True)
for s in steps:
    f = round(s["start"] + PICK[s["n"]] * (s["end"] - s["start"]))
    sc.frame_set(f)
    sc.render.filepath = os.path.join(OUT, f"step_{s['n']}.png")
    bpy.ops.render.render(write_still=True)
    print("[cmu] step", s["n"], "frame", f)
