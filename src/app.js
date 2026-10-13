require('./style.css')

const placement = require('./target-placement.json')
const posterTarget = require('../image-targets/bim-poster.json')

const PREVIEW = new URLSearchParams(location.search).get('mode') === '3d'
const TARGET_NAME = posterTarget.name
const HIGHLIGHT = 0xff7a00

const state = {
  index: null,       // model-index.json
  model: null,       // THREE.Object3D of the loaded glTF
  meshes: {},        // display group -> THREE.Mesh
  level: null,       // selected level name, null = full model
  clip: null,        // THREE.Plane in world space, shared by all model materials
  selected: null,
  highlight: [],
}

// ---------------------------------------------------------------- scene markup
// The model is attached to the printed poster. Image-target space: sheet height = scaledHeight,
// centre at origin, +Y up the sheet, +Z out of the paper. rotation 90° X stands the model up.
const modelMarkup = `
  <a-entity id="anchor" rotation="90 0 0">
    <a-entity id="bim" gltf-model="url(assets/model.glb)" bim-model></a-entity>
  </a-entity>`

const sceneMarkup = PREVIEW ? `
  <a-scene renderer="colorManagement: true; antialias: true" vr-mode-ui="enabled: false"
           background="color: #dfe5ea" preview-orbit>
    <a-entity light="type: hemisphere; color: #ffffff; groundColor: #6d737a; intensity: 0.65"></a-entity>
    <a-entity light="type: directional; intensity: 1.1" position="-1.5 2 1"></a-entity>
    <a-entity id="camera" camera="fov: 45; userHeight: 0"></a-entity>
    <a-entity id="turntable">
      <a-entity id="target" rotation="-90 0 0" target-geometry>
        <a-plane width="0.75" height="1" src="url(image-targets/${posterTarget.resources.originalImage})"
                 material="shader: flat; side: double"></a-plane>
        ${modelMarkup}
      </a-entity>
    </a-entity>
  </a-scene>` : `
  <a-scene landing-page xrextras-loading xrextras-runtime-error
           renderer="colorManagement: true" xrweb="disableWorldTracking: true">
    <a-camera id="camera" position="0 4 10"></a-camera>
    <a-entity light="type: hemisphere; color: #ffffff; groundColor: #6d737a; intensity: 0.65"></a-entity>
    <a-entity light="type: directional; intensity: 1.1" position="-1.5 2 1"></a-entity>
    <xrextras-named-image-target id="target" name="${TARGET_NAME}" target-geometry>
      ${modelMarkup}
    </xrextras-named-image-target>
  </a-scene>`

// ---------------------------------------------------------------- 8th Wall setup
const onxrloaded = () => {
  XR8.XrController.configure({imageTargetData: [posterTarget]})
}
if (!PREVIEW) {
  window.XR8 ? onxrloaded() : window.addEventListener('xrloaded', onxrloaded)
}

// ---------------------------------------------------------------- components
// Places the model so its Level 1 footprint sits exactly on the printed Level 1 plan.
AFRAME.registerComponent('target-geometry', {
  init() {
    const apply = (w, h) => {
      const [W, H] = placement.poster_px
      const [ox, oy] = placement.plan_origin_px
      const anchor = this.el.querySelector('#anchor')
      anchor.object3D.position.set((ox / W - 0.5) * w, (0.5 - oy / H) * h, 0)
      anchor.object3D.scale.setScalar(placement.model_scale * h)
    }
    apply(0.75, 1)
    this.el.addEventListener('xrextrasimagegeometry', e => apply(e.detail.scaledWidth, e.detail.scaledHeight))
  },
})

AFRAME.registerComponent('bim-model', {
  init() {
    const {THREE} = window
    state.clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e6)
    this.localPlane = new THREE.Plane()
    this.el.addEventListener('model-loaded', () => {
      state.model = this.el.getObject3D('mesh')
      state.model.traverse((o) => {
        if (!o.isMesh) return
        state.meshes[o.name] = o
        o.material.clippingPlanes = [state.clip]
        o.material.side = THREE.DoubleSide
      })
      this.el.sceneEl.renderer.localClippingEnabled = true
      setStatus(PREVIEW ? 'Desktop preview' : 'Point at the poster', PREVIEW ? 'ok' : '')
      selectLevel(state.level)
    })
    this.el.addEventListener('model-error', () => setStatus('Model failed to load', 'warn'))
  },
  tick() {
    // keep the cut plane at the selected level, in world space, as the target moves
    if (!state.model) return
    const lv = state.index && state.index.levels.find(l => l.name === state.level)
    const h = lv ? lv.cut_m : 1e6
    this.localPlane.set(new THREE.Vector3(0, -1, 0), h)
    state.clip.copy(this.localPlane).applyMatrix4(state.model.matrixWorld)
  },
})

// Desktop preview: drag to orbit, wheel / pinch to zoom.
AFRAME.registerComponent('preview-orbit', {
  init() {
    const cam = () => document.getElementById('camera').object3D
    let yaw = 0.35, pitch = 0.72, dist = 1.2, drag = null
    const update = () => {
      const c = cam()
      const d = dist * Math.max(1, 0.85 / (innerWidth / innerHeight))  // back off on portrait screens
      c.position.set(Math.sin(yaw) * Math.cos(pitch) * d, Math.sin(pitch) * d,
        Math.cos(yaw) * Math.cos(pitch) * d)
      // entity lookAt would aim +Z at the target; cameras look down -Z
      const m = new THREE.Matrix4().lookAt(c.position, new THREE.Vector3(0, -0.02, 0.1), c.up)
      c.quaternion.setFromRotationMatrix(m)
    }
    this.el.addEventListener('loaded', update)
    const canvas = () => this.el.canvas
    window.addEventListener('pointerdown', (e) => { if (e.target === canvas()) drag = {x: e.clientX, y: e.clientY} })
    window.addEventListener('pointermove', (e) => {
      if (!drag) return
      yaw -= (e.clientX - drag.x) * 0.006
      pitch = Math.min(1.5, Math.max(0.1, pitch + (e.clientY - drag.y) * 0.006))
      drag = {x: e.clientX, y: e.clientY}
      update()
    })
    window.addEventListener('pointerup', () => { drag = null })
    window.addEventListener('wheel', (e) => {
      if (e.target !== canvas()) return
      dist = Math.min(3, Math.max(0.3, dist * (1 + Math.sign(e.deltaY) * 0.1)))
      update()
    }, {passive: true})
  },
})

// ---------------------------------------------------------------- selection
const findElement = (group, face) => {
  const r = state.index.ranges[group]
  if (!r) return null
  let lo = 0, hi = r.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const [eid, start, count] = r[mid]
    if (face < start) hi = mid - 1
    else if (face >= start + count) lo = mid + 1
    else return String(eid)
  }
  return null
}

const clearHighlight = () => {
  state.highlight.forEach(m => { m.parent.remove(m); m.geometry.dispose() })
  state.highlight = []
  document.querySelectorAll('.plan g.sel').forEach(g => g.classList.remove('sel'))
}

const highlightMaterial = () => new THREE.MeshBasicMaterial({
  color: HIGHLIGHT, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthTest: true,
  polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, clippingPlanes: [state.clip],
})

const selectElement = (id) => {
  clearHighlight()
  state.selected = id
  if (!id) { document.getElementById('info').classList.add('hidden'); return }
  const id_ = Number(id)
  Object.entries(state.index.ranges).forEach(([group, ranges]) => {
    const mesh = state.meshes[group]
    if (!mesh) return
    ranges.filter(r => r[0] === id_).forEach(([, start, count]) => {
      const src = mesh.geometry
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', src.getAttribute('position'))
      geo.setIndex(Array.from(src.index.array.slice(start * 3, (start + count) * 3)))
      const hl = new THREE.Mesh(geo, highlightMaterial())
      hl.renderOrder = 10
      mesh.add(hl)
      state.highlight.push(hl)
    })
  })
  document.querySelectorAll(`.plan g[data-id="${id}"]`).forEach(g => g.classList.add('sel'))
  showInfo(id)
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]))

const showInfo = (id) => {
  const e = state.index.elements[id]
  if (!e) return
  const title = e.info['Type Name'] || e.name || e.category
  const rows = Object.entries(e.info).filter(([k]) => k !== 'Type Name')
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')
  document.getElementById('info-body').innerHTML = `
    <h3>${esc(title)}</h3>
    <div class="sub">${esc(e.category)} · ${esc(e.level)} · Revit ID ${esc(id)}</div>
    <dl>${rows}</dl>`
  document.getElementById('info').classList.remove('hidden')
}

// Tap on the 3D model: raycast, skip hits above the cut plane, map triangle -> Revit element.
const setupModelPicking = (sceneEl) => {
  const raycaster = new THREE.Raycaster()
  let down = null
  sceneEl.canvas.addEventListener('pointerdown', (e) => { down = {x: e.clientX, y: e.clientY} })
  sceneEl.canvas.addEventListener('pointerup', (e) => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 8 || !state.model) return
    const rect = sceneEl.canvas.getBoundingClientRect()
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1)
    raycaster.setFromCamera(ndc, sceneEl.camera)
    const hits = raycaster.intersectObjects(Object.values(state.meshes), false)
      .filter(h => state.clip.distanceToPoint(h.point) >= 0)
    const hit = hits[0]
    selectElement(hit ? findElement(hit.object.name, hit.faceIndex) : null)
  })
}

// ---------------------------------------------------------------- 2D map
const planCache = {}
const loadPlan = async (level) => {
  if (!planCache[level.name]) {
    planCache[level.name] = await fetch(level.plan).then(r => r.text())
  }
  const host = document.getElementById('plan')
  host.innerHTML = planCache[level.name]
  const svg = host.querySelector('svg')
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet')
  // fat invisible strokes so thin cut lines are easy to tap
  svg.querySelectorAll('g[data-id] path').forEach((p) => {
    const hit = p.cloneNode()
    hit.setAttribute('class', 'hit')
    hit.removeAttribute('style')
    p.parentNode.appendChild(hit)
  })
  if (state.selected) {
    svg.querySelectorAll(`g[data-id="${state.selected}"]`).forEach(g => g.classList.add('sel'))
  }
}

const selectLevel = (name) => {
  state.level = name
  document.querySelectorAll('#level-tabs button').forEach((b) => {
    b.setAttribute('aria-selected', String(b.dataset.level === (name || '')))
  })
  const lv = state.index.levels.find(l => l.name === name) || state.index.levels[0]
  loadPlan(lv)
}

const buildTabs = () => {
  const tabs = document.getElementById('level-tabs')
  const mk = (label, level) => {
    const b = document.createElement('button')
    b.textContent = label
    b.dataset.level = level || ''
    b.setAttribute('role', 'tab')
    b.addEventListener('click', () => selectLevel(level))
    tabs.appendChild(b)
  }
  mk('Full model', null)
  state.index.levels.forEach(l => mk(l.name, l.name))
}

const setStatus = (text, cls = '') => {
  const el = document.getElementById('status')
  el.textContent = text
  el.className = `status ${cls}`
}

// ---------------------------------------------------------------- boot
const boot = async () => {
  state.index = await fetch('assets/model-index.json').then(r => r.json())
  buildTabs()
  selectLevel(null)

  document.getElementById('plan').addEventListener('click', (e) => {
    const g = e.target.closest('g[data-id]')
    if (g) selectElement(g.dataset.id)
  })
  document.getElementById('info-close').addEventListener('click', () => selectElement(null))
  document.getElementById('map-toggle').addEventListener('click', () => {
    document.getElementById('sheet').classList.toggle('expanded')
  })

  document.getElementById('scene-root').innerHTML = sceneMarkup
  const sceneEl = document.querySelector('a-scene')
  const ready = () => setupModelPicking(sceneEl)
  sceneEl.hasLoaded ? ready() : sceneEl.addEventListener('loaded', ready)

  if (!PREVIEW) {
    sceneEl.addEventListener('xrimagefound', () => setStatus('Tracking poster', 'ok'))
    sceneEl.addEventListener('xrimagelost', () => setStatus('Point at the poster', 'warn'))
  }
}

boot()
