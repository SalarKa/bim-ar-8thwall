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
- **2D plan tab:** cut plans for each level, with structural grids A–H and 1–4 and their
  dimensions. Elements are colored by status, and the level chips cut the 3D model at that level.
- **Tap to identify:** tap any element on the plan or in 3D. It highlights in both views, and a
  card shows its Revit data and the schedule task that builds it.
- **Ambient occlusion:** soft shading in corners, under slabs and around openings is baked
  into the model ahead of time (`pipeline/ao.py`), so it costs nothing on phones. The
  "AO on/off" toggle switches it.
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

## Pipeline

```
Revit .rvt ──DDC converter──> .dae (geometry, element IDs) + .xlsx (parameters)
   pipeline/build_assets.py ─> src/assets/model.glb        meshes grouped by category, Y-up, meters
                               src/assets/ao.bin            baked ambient occlusion, 1 byte per vertex
                               src/assets/model-index.json  element data, task links, picking ranges
                               src/assets/plans/*.svg       per-level cut plans with grids
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
