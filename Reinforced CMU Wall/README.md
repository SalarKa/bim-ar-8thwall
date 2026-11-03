# Reinforced CMU Wall in AR

A third demo in this repo, separate from the original AEC 450 app and the hospital (nothing
outside this folder is changed). It plays a Blender animation of a reinforced, partially grouted
CMU wall being built, as WebAR on a printed sheet. It uses the same open-source 8th Wall engine,
A-Frame and image-target tracking as the root app.

**How it works:**
- Print `print/cmu-wall-poster-letter.pdf` or `print/cmu-wall-poster-a4.pdf` landscape, at 100 %.
- Scan the QR code.
- When the camera sees the sheet, the wall rises out of its printed 1:16 top view and the
  construction sequence plays.

## The sequence

The sequence follows low-lift grouting (CMHA TEK 03-02A, TMS 602):

1. **Footing & dowels.** The footing is poured, with dowels set in line with the reinforced cells.
2. **Corner leads.** Leads are laid up at both ends, racked back. The first course sits on a full
   mortar bed.
3. **Fill to the line.** The courses are filled in between the leads along a mason's line, with
   face-shell bedding.
4. **Lift height 5'-4".** The wall reaches 8 courses. That is one lift, so no cleanouts are needed.
5. **Vertical bars.** #5 bars at 4'-0" o.c. are lowered into the cells and lapped with the dowels.
6. **Rebar positioners.** Positioners at the top hold the bars in place.
7. **Grout the cells.** Only the reinforced cells are grouted.
8. **Consolidate.** The grout is consolidated with a mechanical vibrator.
9. **Grout key.** The grout stops 1-1/2" below the top as a key for the next pour.

## What the app shows

- **Step overlay:** a frosted-glass card in the top-left corner shows the current step's number,
  name and description.
- **Controls:** a glass dock at the bottom has:
  - the steps as chips: tap one to jump to it;
  - play/pause and a scrubber;
  - speed (0.5×, 1×, 2×);
  - **X-ray** (auto / on / off). *Auto* ghosts the block while the bars and grout go in, as the
    Blender film does.
- **Desktop preview:** add `?mode=3d` to the URL; `&t=<seconds>` opens at a given time and
  `&autoplay=0` starts paused.

## The sheet

- **The AR anchor:** a top view of the wall at 1:16, as large as a landscape Letter/A4 sheet
  allows. It shows:
  - the footing;
  - the top course of CMU, with hollow and grouted cells;
  - the bars, dimensions and callouts.
- **The nine steps,** each a still from the Blender animation.
- **The QR code.**

8th Wall image targets are portrait 3:4. The target image (`print/cmu-wall-poster.png`) is
therefore the landscape sheet turned 90° clockwise. `src/assets/target-placement.json` holds the
matching `plan_rotation`, which `placeOnSheet` applies in XYZ order.

## Pipeline

```
blender/build_cmu_wall.py  ─> blender/out/cmu_wall_sequence.blend   the animated scene (also renders the film)
                               src/assets/steps.json                 step names, captions, frame ranges, X-ray timing
blender/export_glb.py      ─> blender/out/cmu_wall.raw.glb           AR content only (no grade, camera, captions)
npm run pack (gltfpack)    ─> src/assets/cmu_wall.glb                meshopt-compressed, ~1.5 MB
blender/render_steps.py    ─> pipeline/out/steps/step_<n>.png        one still per step for the sheet
pipeline/make_poster.py    ─> print/*, src/assets/target-placement.json
npx @8thwall/image-target-cli ─> image-targets/cmu-wall-poster*
```

To rebuild after a change, run (Blender 5.2):

```bash
blender -b -P blender/build_cmu_wall.py
blender -b blender/out/cmu_wall_sequence.blend -P blender/export_glb.py
npm run pack
blender -b blender/out/cmu_wall_sequence.blend -P blender/render_steps.py
python -I pipeline/make_poster.py
printf '%s\n' print/cmu-wall-poster.png 1 y image-targets cmu-wall-poster | OVERWRITE_FILES=true npx @8thwall/image-target-cli@latest
```

Rerun the last two lines whenever the sheet changes, because the QR is part of the target.
`blender/render_preview.py` renders the captioned film to an MP4.

## Develop and deploy

```bash
npm install          # gltfpack; webpack comes from the parent repo's node_modules
npm run serve        # http://localhost:8036/?mode=3d
npm run deploy       # build, then add /reinforced-cmu-wall/ to gh-pages (leaves the other demos alone)
```

Watch out: the parent repo's `npm run deploy` force-pushes the whole site and removes the
sub-folders. Run this deploy again afterward.

Built by Dr. Salar Kamari.
