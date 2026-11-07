# Hospital BIM in AR

A second demo in this repo, separate from the original AEC 450 app (nothing outside `hospital/`
is changed). It shows the Autodesk hospital sample (Revit 2018) as WebAR on a printed sheet, with
the same open-source 8th Wall engine and A-Frame as the parent app.

**How it works:** print `print/hospital-poster-letter.pdf` or `print/hospital-poster-a4.pdf` at
100 % and scan the QR code. When the camera sees the sheet, the hospital rises out of its printed
Level 1 plan, at true scale.

## What the app shows

- **Four sub-models:**
  - Architecture, Structure, HVAC, and Piping & Plumbing, each one a toggle.
  - Double-tap a sub-model to isolate it.
  - Turning on HVAC or Piping ghosts the architecture automatically (**X-ray**).
- **Systems:** colours ducts and pipes by system: supply, return and exhaust air, hot and chilled
  water, medical gas, oxygen, vacuum, cooling tower, sanitary and domestic hot water.
- **Section:** cuts the model 1.2 m above any level, the way a Revit plan view does.
- **AO:** baked ambient occlusion, used in both looks and baked the way the parent app does it.
  "AO" toggles it on and off.
- **Original / Simple / Compare:**
  - *Original* is the raw Revit export, with Revit shading colours.
  - *Simple* uses the parent app's flat group colours: roughness 0.85, no metal, no textures, and
    its hemisphere and sun lights. Structure keeps brown columns and grey concrete.
  - *Compare* draws both at once, with a draggable divider between them.
- **Tap to identify:** tap any element to see its type, category, discipline, level, system and
  Revit ID.
- **Desktop preview:** add `?mode=3d` to the URL. Drag to orbit, scroll to zoom, and right-drag to
  pan.

## The sheet

Everything on the sheet is a 2D drawing generated from the model geometry. There are no
screenshots of the 3D model. The layout follows the original poster:

- **The AR anchor:** the Level 1 plan, cut at 1.2 m.
  - Walls and columns are drawn in poché, glazing in blue and doors in brown, with fixtures below
    the cut.
  - Revit exports only one bubble per grid, so each grid line is rebuilt from the structural
    columns it runs through.
- **One drawing per sub-model:**
  - Architecture: Level 4 plan.
  - Structure: Level 3 framing plan.
  - HVAC: Level 4 ductwork, coloured by system.
  - Piping: Level 4 piping, coloured by system.
- **In the app** and the MEP system legend.
- **The QR code.**

## Pipeline

```
2018_Hospital_<Model>.rvt ──DDC RvtExporter──> .dae + .xlsx  (~/DDC_Tools/work/hospital)
  pipeline/build_hospital.py ─> src/assets/<disc>.glb    one file per sub-model, meshopt-compressed (gltfpack)
                                src/assets/<disc>.json   element table for tap-to-identify
                                src/assets/tex/ao_*      baked AO (see pipeline/ao_bake.py)
                                src/assets/scene.json    looks (materials), MEP systems, levels
                                pipeline/cache/plan_*    geometry for the 2D drawings
  pipeline/make_poster.py    ─> print/*, src/assets/target-placement.json
  npx @8thwall/image-target-cli ─> image-targets/hospital-poster*
```

- **Ambient occlusion** (`ao_bake.py`, same method and constants as the parent app's `pipeline/ao.py`):
  - **Exterior:** everything visible from outside goes into one 4096 px atlas at the parent app's
    12 texels/m where it fits, and every texel traces 48 hemisphere rays (Embree) within 2.5 m.
  - **Only what is seen:** Architecture gets AO only on faces that can see the sky (facades, curtain
    walls, roofs, courtyards); interiors, HVAC and piping stay open.
  - **Structure:** baked against itself only, so it isn't darkened by walls that aren't shown.
  - **Thin parts:** exterior mullions, louvres and PV, and small structural members, get per-vertex
    AO through a grey ramp.
  - **Flipped normals:** every sample is traced from both sides and the lighter side kept, so a face
    Revit exported inside-out is never shaded from inside its own solid. Triangles whose winding
    disagrees with their normal are flipped, so lighting never sees them from behind.
  - **Per flat face, not per triangle:** facade finish vs interior paint, exterior vs interior, and
    atlas vs per-vertex are decided for each flat face of a Revit element, so the two triangles of
    one panel can't disagree. Soffits and canopy undersides look out sideways, not only straight
    down, when deciding whether they are exterior.
  - **In the app:** the value multiplies the surface colour, as in the parent app.
- **Tracking:** image tracking only, with no motion-sensor prompt. `src/pose-filter.js` holds tilt,
  position and depth inside small dead bands that widen near the paper;
  `node scripts/test-pose-filter.js` compares it with no filter on simulated noise.
- **Mesh reduction:** over-detailed families (toilets, shower heads, grab bars, chairs, louvers)
  are reduced to triangle budgets that are invisible at tabletop scale. Round ducts and pipes keep
  45–50 % of their triangles.

To rebuild after a model change, run:

```bash
python -I pipeline/build_hospital.py
python -I pipeline/make_poster.py
printf '%s\n' print/hospital-poster.png 1 y image-targets hospital-poster | OVERWRITE_FILES=true npx @8thwall/image-target-cli@latest
```

## Develop and deploy

```bash
cd hospital
npm install          # gltfpack; webpack comes from the parent repo's node_modules
npm run serve        # http://localhost:8034/?mode=3d
npm run deploy       # build, then add /hospital/ to gh-pages (leaves the original demo alone)
```

Watch out: the parent repo's `npm run deploy` force-pushes the whole site and removes
`/hospital/`. Run this deploy again afterward.

## Credits

- **Model:** Autodesk hospital sample (Revit 2018 files, obtained via mepwork.com).

Built by Dr. Salar Kamari.
