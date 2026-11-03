"""Render the animation to an MP4 preview next to the .blend.

Run:  blender -b cmu_wall_sequence.blend -P render_preview.py -- [percent] [samples]
"""
import bpy, sys

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
pct = int(ARGV[0]) if ARGV else 75
samples = int(ARGV[1]) if len(ARGV) > 1 else 24

sc = bpy.context.scene
sc.render.resolution_percentage = pct
try:
    sc.eevee.taa_render_samples = samples
except AttributeError:
    pass
img = sc.render.image_settings
try:
    img.media_type = 'VIDEO'          # Blender 5.x
except (AttributeError, TypeError):
    pass
img.file_format = 'FFMPEG'
ff = sc.render.ffmpeg
ff.format = 'MPEG4'
ff.codec = 'H264'
ff.constant_rate_factor = 'HIGH'
sc.render.filepath = bpy.path.abspath("//renders/cmu_wall_sequence_preview.mp4")
bpy.ops.render.render(animation=True)
print("[cmu] wrote", sc.render.filepath)
