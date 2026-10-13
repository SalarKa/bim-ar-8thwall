# BIM in AR — AEC 450

A Revit BIM model shown in WebAR on phones and tablets, using the open-source
[8th Wall engine](https://8thwall.org) with A-Frame. No app install and no 8th Wall account are needed.

**How it works:** print `print/poster.pdf` and scan its QR code. When the camera sees the sheet,
the 3D building rises out of the printed Level 1 plan.

- **Level tabs** cut the 3D model at 4 ft above the chosen level, like a Revit plan view.
- **2D map:** cut plans per level, generated from the model geometry.
- **Tap to identify:** tap any element on the map or in 3D. It highlights in both views and
  shows its Revit category, type, level and key parameters.
- **Desktop preview:** add `?mode=3d` to the URL for a mouse-driven 3D view with no camera.

## Pipeline

```
Revit .rvt ──DDC converter──> .dae (geometry, element IDs) + .xlsx (parameters)
   pipeline/build_assets.py ─> src/assets/model.glb        meshes grouped by category, Y-up, meters
                               src/assets/model-index.json  element data + triangle ranges for picking
                               src/assets/plans/*.svg       per-level cut plans (2D map)
   pipeline/make_poster.py ──> print/poster.{pdf,png}       printable sheet = AR image target, with QR
                               src/target-placement.json    where the plan sits on the sheet
   npx @8thwall/image-target-cli ──> image-targets/bim-poster*
```

To rebuild after the model changes, re-export the model with the DDC converter, then run:

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

`npm run deploy` builds and force-pushes `dist/` to the `gh-pages` branch, which GitHub Pages serves.
Phones need HTTPS for camera access, which Pages provides.

## Printing

Print `print/poster.pdf` at 100 % on Letter or A4, and keep the sheet flat and well lit.
The whole sheet is the image target. If you change anything on the poster, regenerate the
image target.
