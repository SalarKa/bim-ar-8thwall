# Augmented Reality Demo — School of Construction and Design, USM

A Revit BIM model shown as **4D WebAR** on phones and tablets, built with the open-source
[8th Wall engine](https://8thwall.org) and A-Frame. No app install and no 8th Wall account are needed.
Styling follows the USM career fair app: black, usm.edu gold, warm gray, and the Archivo typeface.

**How it works:** print `print/poster.pdf` and scan its QR code. When the camera sees the sheet,
the building rises out of the printed Level 1 plan.

- **4D timeline:** drag the slider, or press play, to move through the Jan–May 2025 schedule.
  - Elements that haven't started yet are hidden.
  - Elements under construction rise from their base in USM gold.
  - Finished elements show in their normal color.
- **4D panel:** shows the date, the week, the percent of the model built, each active task with
  its progress, and what starts next.
- **Schedule tab:** a Gantt chart with a "today" line. Tap a row to jump to that work.
- **Cost estimate tab:** a quantity takeoff from the Revit model, priced at US national-average
  2025 unit costs and grouped by CSI division, with general conditions, fee and contingency.
  - It shows the total and $/SF, plus the cost to date at the slider's date and a cost S-curve.
  - Tap a line item to highlight all of its elements in 3D and see its quantity, unit cost,
    price range, basis and sources.
- **Tap to identify:** tap any element in 3D. It highlights, and a card shows its Revit data,
  estimated cost and the schedule task that builds it.
- **Ambient occlusion:** a baked AO lightmap (`pipeline/ao.py`). The model is unwrapped into a
  4096 px UV atlas at 12 texels/m, about 8 cm per texel.
  - Every face is turned outward first: a face whose normal ray re-enters its own element is
    flipped.
  - Each texel then traces 48 hemisphere rays against the building and a ground plane, within
    2.5 m.
  - The result gives crisp contact shading in corners, at slab edges, in window reveals and
    under eaves. It costs nothing on phones, and "AO on/off" toggles it instantly.
- **Wireframe:** "Wireframe on/off" draws each element's crease edges over the shaded model.
  The edges follow the 4D schedule.
- **Desktop preview:** add `?mode=3d` to the URL for a mouse-driven view with no camera.

## 4D schedule

`data/Timeline2-1.csv` is the AEC 450 schedule, which originally ran from Dec 2011 to May 2012.
`pipeline/schedule.py` shifts it by whole weeks so every task keeps its weekday. Preconstruction
now starts on Monday, Jan 6, 2025, and the work finishes on May 25, 2025. The result is written
to `data/schedule-2025.csv`.

Each Revit element is linked to a task by level, zone and trade:

- **Zones:** A, B and C are the west, center and east thirds of the building.
- **Framing:** W12 members are joists, and the heavier W14 and W16 sections are beams.
- **Multi-story elements:** core walls and curtain walls rise across several tasks, from their
  base story to their top story.
- **Fit-out with no task of its own:** plumbing fixtures and ceilings follow the interior walls
  on their level.

## Cost estimate

`pipeline/qto.py` takes quantities off the model:

- **Areas, volumes and lengths:** read from Revit parameters.
- **Steel tonnage:** member volume × 490 lb/ft³.
- **Stair risers:** counted from the stair's height in the geometry.

It prices them with `data/unit_costs.json`, then writes `src/assets/cost.json` and
`data/cost-estimate.csv`:

- **Unit costs:** installed costs incl. subcontractor O&P at national average (index 1.00).
  They're mostly anchored on the Linbeck Q3 2025 market report and RSMeans-typical values,
  with a source or comparable named for each item.
- **Markups:** general conditions 10%, overhead and profit 7%, contingency 12%.
- **Hattiesburg, MS:** about 0.84 of national average.
- **Not modeled, so excluded:** footings, HVAC, electrical, fire protection, plumbing rough-in
  and sitework.

Each line item's cost is spread over its elements, so cost to date follows the 4D schedule.

## Pipeline

```
Revit .rvt ──DDC converter──> .dae (geometry, element IDs) + .xlsx (parameters)
   pipeline/build_assets.py ─> src/assets/model.glb        meshes grouped by category, Y-up, meters
                               src/assets/ao.png            baked ambient-occlusion lightmap (GLB UVs = atlas)
                               src/assets/model-index.json  element data, task links, picking ranges
                               src/assets/plans/*.svg       per-level cut plans with grids (poster)
                               src/assets/schedule.json     2025 schedule (+ data/schedule-2025.csv)
   pipeline/make_poster.py ──> print/poster.{pdf,png}       printable sheet = AR image target, with QR
                               src/target-placement.json    where the plan sits on the sheet
   npx @8thwall/image-target-cli ──> image-targets/bim-poster*
```

To rebuild after the model or the schedule changes, run:

```bash
npm run assets
printf '%s\n' print/poster.png 1 y image-targets bim-poster | OVERWRITE_FILES=true npx @8thwall/image-target-cli@latest
```

## Develop and deploy

```bash
npm install
npm run serve      # http://localhost:8080 (add ?mode=3d on desktop)
npm run build      # -> dist/
```

`npm run deploy` builds the site and force-pushes `dist/` to the `gh-pages` branch, which
GitHub Pages serves. Phones need HTTPS for camera access, which Pages provides.

## Printing

Print `print/poster.pdf` at 100 % on Letter or A4, and keep the sheet flat and well lit.
The whole sheet is the image target, so a poster printed before a redesign stops tracking
once the new version is deployed. Reprint after any poster change.
