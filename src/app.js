require('./style.css')

const placement = require('./target-placement.json')
const posterTarget = require('../image-targets/bim-poster.json')

const PREVIEW = new URLSearchParams(location.search).get('mode') === '3d'
const TARGET_NAME = posterTarget.name
const HIGHLIGHT = 0xff7a00
const DAY_MS = 86400000
const PLAY_SECONDS = 24          // a full schedule pass when playing
const GANTT_ROWS = [             // same grouping as the poster's schedule strip
  ['Foundations', ['110']],
  ['Level 1 structure', ['120', '130', '140']],
  ['Level 2 structure', ['210', '220', '230', '240']],
  ['Level 3 structure', ['310', '320', '330', '340']],
  ['Exterior walls & core', ['150', '250', '350']],
  ['Roof', ['410', '420']],
  ['Stairs & railings', ['510', '810']],
  ['Curtain walls & windows', ['710', '610']],
  ['Interior walls & doors', ['910', '620']],
]

const state = {
  index: null,       // model-index.json
  sched: null,       // schedule.json (2025)
  tasks: {},         // id -> task with s/e day numbers (e exclusive)
  elTime: {},        // element id -> {s, e} day numbers
  total: 1,          // schedule length in days
  day: 0,
  dayU: {value: 1e6},  // shader uniform shared by every model material
  model: null,
  meshes: {},
  level: null,
  clip: null,
  selected: null,
  highlight: [],
  playing: false,
  aoOn: true,
  aoBin: null,      // baked ambient occlusion, one byte per vertex
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
           background="color: #dcdcd6" preview-orbit>
    <a-entity light="type: hemisphere; color: #ffffff; groundColor: #6d737a; intensity: 0.65"></a-entity>
    <a-entity light="type: directional; intensity: 1.1" position="-1.5 2 1"></a-entity>
    <a-entity id="camera" camera="fov: 45; userHeight: 0"></a-entity>
    <a-entity id="target" rotation="-90 0 0" target-geometry>
      <a-plane width="0.75" height="1" src="url(image-targets/${posterTarget.resources.originalImage})"
               material="shader: flat; side: double"></a-plane>
      ${modelMarkup}
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

// ---------------------------------------------------------------- 4D schedule
const dayOf = iso => (Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${state.sched.start}T00:00:00Z`)) / DAY_MS
const dateOf = day => new Date(Date.parse(`${state.sched.start}T00:00:00Z`) + Math.floor(day) * DAY_MS)
const fmtDate = (d, opts) => d.toLocaleDateString('en-US', {timeZone: 'UTC', ...opts})

const prepareSchedule = () => {
  state.sched.tasks.forEach((t) => {
    state.tasks[t.id] = {...t, s: dayOf(t.start), e: dayOf(t.end) + 1}
  })
  state.total = dayOf(state.sched.end) + 1
  Object.entries(state.index.elements).forEach(([id, el]) => {
    const a = state.tasks[el.task]
    const b = state.tasks[el.task_end] || a
    state.elTime[id] = a ? {s: a.s, e: Math.max(a.e, b.e)} : {s: -1, e: -1}
  })
  state.day = state.total
}

const statusAt = (t, day) => (day >= t.e ? 'done' : day >= t.s ? 'active' : 'planned')

// Per-vertex timing for the 4D shader: a4d = (start day, end day, element min y, element max y).
// Only walls rise from their base while being built; everything else appears whole when its task
// starts (a reveal height far above the model).
const GROWING_GROUPS = new Set(['walls'])
const bake4D = (mesh) => {
  const grows = GROWING_GROUPS.has(mesh.name)
  const ranges = state.index.ranges[mesh.name]
  const geo = mesh.geometry
  const idx = geo.index.array
  const pos = geo.getAttribute('position')
  const a4d = new Float32Array(pos.count * 4).fill(-1)
  ranges.forEach(([eid, start, count]) => {
    const t = state.elTime[eid] || {s: -1, e: -1}
    let y0 = 1e6, y1 = 1e6
    if (grows) {
      y0 = Infinity; y1 = -Infinity
      for (let i = start * 3; i < (start + count) * 3; i++) {
        const y = pos.getY(idx[i]); if (y < y0) y0 = y; if (y > y1) y1 = y
      }
    }
    for (let i = start * 3; i < (start + count) * 3; i++) {
      const v = idx[i] * 4
      a4d[v] = t.s; a4d[v + 1] = t.e; a4d[v + 2] = y0; a4d[v + 3] = y1
    }
  })
  geo.setAttribute('a4d', new THREE.BufferAttribute(a4d, 4))
}

// Not started: hidden. In progress: USM gold (walls rise from their base). Done: normal colour.
const patch4D = (material) => {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uDay = state.dayU
    shader.uniforms.uGold = {value: new THREE.Color(0xffd046)}
    shader.vertexShader = `attribute vec4 a4d;\nvarying vec4 v4d;\nvarying float vY4;\n${shader.vertexShader}`
      .replace('#include <begin_vertex>', '#include <begin_vertex>\n  v4d = a4d;\n  vY4 = position.y;')
    shader.fragmentShader = `uniform float uDay;\nuniform vec3 uGold;\nvarying vec4 v4d;\nvarying float vY4;\n${shader.fragmentShader}`
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
  float act4 = 0.0;
  if (uDay < v4d.x) discard;
  if (uDay < v4d.y) {
    float p4 = clamp((uDay - v4d.x) / max(v4d.y - v4d.x, 0.001), 0.0, 1.0);
    if (vY4 > mix(v4d.z, v4d.w, p4) + 0.03) discard;
    act4 = 1.0;
  }`)
      .replace('#include <color_fragment>', '#include <color_fragment>\n  diffuseColor.rgb = mix(diffuseColor.rgb, uGold, act4 * 0.7);')
  }
  material.customProgramCacheKey = () => 'bim4d'
  material.needsUpdate = true
}

let renderQueued = false
const setDay = (day, {fromSlider = false} = {}) => {
  state.day = Math.max(0, Math.min(state.total, day))
  state.dayU.value = state.day
  const slider = document.getElementById('slider')
  if (!fromSlider) slider.value = state.day
  slider.style.setProperty('--p', `${(state.day / state.total) * 100}%`)
  if (!renderQueued) {
    renderQueued = true
    requestAnimationFrame(() => { renderQueued = false; renderTimeUI() })
  }
}

const renderTimeUI = () => {
  const day = state.day
  const d = dateOf(Math.min(day, state.total - 1))
  const done = day >= state.total
  document.getElementById('hud-date').textContent = done ? 'Complete' : fmtDate(d, {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'})
  document.getElementById('tl-date').textContent = fmtDate(d, {month: 'short', day: 'numeric', year: 'numeric'})
  const week = Math.floor(Math.min(day, state.total - 1) / 7) + 1
  const tasks = Object.values(state.tasks)
  const finished = tasks.filter(t => day >= t.e).length
  document.getElementById('hud-meta').textContent = `Week ${week} of ${Math.ceil(state.total / 7)} · ${finished}/${tasks.length} tasks done`

  // percent complete, weighted by elements (partial credit while a task is running)
  let sum = 0, n = 0
  Object.values(state.elTime).forEach(({s, e}) => {
    if (s < 0) return
    n++; sum += Math.max(0, Math.min(1, (day - s) / Math.max(e - s, 1e-3)))
  })
  const pct = Math.round((sum / Math.max(n, 1)) * 100)
  document.getElementById('hud-pct').textContent = pct
  document.getElementById('hud-bar').style.width = `${pct}%`

  const active = tasks.filter(t => statusAt(t, day) === 'active').sort((a, b) => a.s - b.s)
  const list = active.map(t => `<li><span class="tid">${t.id}</span><span class="tname">${esc(t.title)}</span>` +
    `<span class="tpct">${Math.round(((day - t.s) / (t.e - t.s)) * 100)}%</span></li>`)
  if (!active.length) {
    const next = tasks.filter(t => t.s > day).sort((a, b) => a.s - b.s)[0]
    list.push(next
      ? `<li class="next"><span class="tid">${next.id}</span><span class="tname">Next: ${esc(next.title)}</span><span class="tpct">${fmtDate(dateOf(next.s), {month: 'short', day: 'numeric'})}</span></li>`
      : '<li class="next"><span class="tid">✓</span><span class="tname">All 50 tasks complete</span><span class="tpct"></span></li>')
  }
  document.getElementById('hud-tasks').innerHTML = list.join('')

  // plan + gantt status
  document.querySelectorAll('#plan g[data-id]').forEach((g) => {
    const t = state.elTime[g.dataset.id]
    const st = !t || t.s < 0 ? 'done' : statusAt(t, day)
    g.classList.toggle('s-planned', st === 'planned')
    g.classList.toggle('s-active', st === 'active')
  })
  document.querySelectorAll('#gantt .g-bar').forEach((b) => {
    b.className = `g-bar ${statusAt(state.tasks[b.dataset.task], day)}`
  })
  const now = document.querySelector('#gantt .g-now-wrap .g-now')
  if (now) now.style.left = `${(day / state.total) * 100}%`
}

const buildTimeline = () => {
  const slider = document.getElementById('slider')
  slider.max = state.total
  slider.value = state.day
  slider.addEventListener('input', () => { stopPlay(); setDay(Number(slider.value), {fromSlider: true}) })
  document.getElementById('tl-start').textContent = fmtDate(dateOf(0), {month: 'short', day: 'numeric'})
  document.getElementById('tl-end').textContent = fmtDate(dateOf(state.total - 1), {month: 'short', day: 'numeric', year: 'numeric'})
  const ticks = document.getElementById('tl-ticks')
  const s0 = new Date(Date.parse(`${state.sched.start}T00:00:00Z`))
  for (let m = 0; m < 6; m++) {
    const first = new Date(Date.UTC(s0.getUTCFullYear(), s0.getUTCMonth() + m, 1))
    const day = (first - s0) / DAY_MS
    if (day > state.total) break
    const x = `${(Math.max(day, 0) / state.total) * 100}%`
    if (day > 0) ticks.insertAdjacentHTML('beforeend', `<span class="tk" style="left:${x}"></span>`)
    ticks.insertAdjacentHTML('beforeend', `<span class="tl" style="left:${x}">${fmtDate(first, {month: 'short'}).toUpperCase()}</span>`)
  }
  document.getElementById('play').addEventListener('click', () => (state.playing ? stopPlay() : startPlay()))
}

let lastT = 0
const startPlay = () => {
  if (state.day >= state.total) setDay(0)
  state.playing = true
  const btn = document.getElementById('play')
  btn.classList.add('on'); btn.textContent = '❚❚'
  lastT = performance.now()
  const step = (t) => {
    if (!state.playing) return
    setDay(state.day + ((t - lastT) / 1000) * (state.total / PLAY_SECONDS))
    lastT = t
    if (state.day >= state.total) { stopPlay(); return }
    requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}
const stopPlay = () => {
  state.playing = false
  const btn = document.getElementById('play')
  btn.classList.remove('on'); btn.textContent = '▶'
}

const buildGantt = () => {
  const s0 = new Date(Date.parse(`${state.sched.start}T00:00:00Z`))
  let months = ''
  for (let m = 0; m < 6; m++) {
    const first = new Date(Date.UTC(s0.getUTCFullYear(), s0.getUTCMonth() + m, 1))
    const day = Math.max(0, (first - s0) / DAY_MS)
    if (day > state.total) break
    months += `<span style="left:${(day / state.total) * 100}%">${fmtDate(first, {month: 'short'}).toUpperCase()}</span>`
  }
  const rows = GANTT_ROWS.map(([label, prefixes]) => {
    const tasks = Object.values(state.tasks).filter(t => prefixes.some(p => t.id.startsWith(p)))
    const bars = tasks.map(t => `<span class="g-bar" data-task="${t.id}" title="${t.id} ${esc(t.title)}"
      style="left:${(t.s / state.total) * 100}%;width:${((t.e - t.s) / state.total) * 100}%"></span>`).join('')
    const first = Math.min(...tasks.map(t => t.s))
    return `<div class="g-row" data-day="${first}"><span class="g-label">${label}</span><span class="g-track">${bars}</span></div>`
  }).join('')
  const gantt = document.getElementById('gantt')
  gantt.innerHTML = `<div class="g-head"><span></span><span class="g-months">${months}</span></div>${rows}
    <div class="g-row g-now-wrap" style="position:absolute;inset:0 10px 0 128px;height:auto;pointer-events:none;display:block">
    <span class="g-now"></span></div>`
  gantt.addEventListener('click', (e) => {
    const row = e.target.closest('.g-row[data-day]')
    if (!row) return
    stopPlay()
    setDay(Number(row.dataset.day) + 0.5)   // halfway through the group's first day
  })
}

// ---------------------------------------------------------------- ambient occlusion
// Baked in the pipeline (pipeline/ao.py): a grey vertex colour that multiplies the material colour.
const applyAO = (mesh) => {
  const span = state.index.ao && state.index.ao[mesh.name]
  const pos = mesh.geometry.getAttribute('position')
  if (!span || !state.aoBin || span[1] !== pos.count) return
  const src = new Uint8Array(state.aoBin, span[0], span[1])
  const rgb = new Uint8Array(span[1] * 3)
  for (let i = 0; i < span[1]; i++) rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = src[i]
  mesh.geometry.setAttribute('color', new THREE.BufferAttribute(rgb, 3, true))
  mesh.material.vertexColors = state.aoOn
  mesh.material.needsUpdate = true
}

const setAO = (on) => {
  state.aoOn = on
  Object.values(state.meshes).forEach((m) => {
    if (!m.geometry.getAttribute('color')) return
    m.material.vertexColors = on
    m.material.needsUpdate = true
  })
  const btn = document.getElementById('ao-toggle')
  btn.setAttribute('aria-pressed', String(on))
  btn.textContent = on ? 'AO on' : 'AO off'
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
    state.clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e6)
    this.localPlane = new THREE.Plane()
    this.el.addEventListener('model-loaded', async () => {
      await scheduleReady
      state.model = this.el.getObject3D('mesh')
      state.model.traverse((o) => {
        if (!o.isMesh) return
        state.meshes[o.name] = o
        o.material.clippingPlanes = [state.clip]
        o.material.side = THREE.DoubleSide
        bake4D(o)
        applyAO(o)
        patch4D(o.material)
      })
      this.el.sceneEl.renderer.localClippingEnabled = true
      setStatus(PREVIEW ? 'Desktop preview' : 'Point at the poster', PREVIEW ? 'ok' : '')
      setDay(state.day)
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

// Desktop preview: drag to orbit, wheel to zoom.
AFRAME.registerComponent('preview-orbit', {
  init() {
    const cam = () => document.getElementById('camera').object3D
    let yaw = 0.35, pitch = 0.72, dist = 1.25, drag = null
    const update = () => {
      const c = cam()
      const d = dist * Math.max(1, 0.85 / (innerWidth / innerHeight))  // back off on portrait screens
      c.position.set(Math.sin(yaw) * Math.cos(pitch) * d, Math.sin(pitch) * d,
        Math.cos(yaw) * Math.cos(pitch) * d)
      // entity lookAt would aim +Z at the target; cameras look down -Z
      const m = new THREE.Matrix4().lookAt(c.position, new THREE.Vector3(0, -0.06, 0.12), c.up)
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
  const a = state.tasks[e.task]
  const b = state.tasks[e.task_end] || a
  const when = a
    ? `<span class="task-chip">${a.id}${b !== a ? `→${b.id}` : ''} · ${fmtDate(dateOf(a.s), {month: 'short', day: 'numeric'})} – ${fmtDate(dateOf(b.e - 1), {month: 'short', day: 'numeric', year: 'numeric'})}</span>`
    : ''
  document.getElementById('info-body').innerHTML = `
    <h3>${esc(title)}</h3>
    <div class="sub">${esc(e.category)} · ${esc(e.level)} · Revit ID ${esc(id)}</div>
    ${when}
    <dl>${a ? `<dt>Schedule</dt><dd>${esc(a.title)}</dd>` : ''}${rows}</dl>`
  document.getElementById('info').classList.remove('hidden')
}

// Tap on the 3D model: raycast, skip hits above the cut plane or not yet built, map triangle -> element.
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
    const hit = raycaster.intersectObjects(Object.values(state.meshes), false)
      .filter(h => state.clip.distanceToPoint(h.point) >= 0)
      .map(h => ({h, id: findElement(h.object.name, h.faceIndex)}))
      .find(({id}) => id && (state.elTime[id] || {s: -1}).s <= state.day)
    selectElement(hit ? hit.id : null)
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
  renderTimeUI()
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

  document.querySelectorAll('.seg button').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.seg button').forEach(x => x.setAttribute('aria-selected', String(x === b)))
      document.getElementById('tab-plan').classList.toggle('hidden', b.dataset.tab !== 'plan')
      document.getElementById('tab-schedule').classList.toggle('hidden', b.dataset.tab !== 'schedule')
      document.getElementById('panel').classList.remove('collapsed')
    })
  })
}

const setStatus = (text, cls = '') => {
  const el = document.getElementById('status')
  el.textContent = text
  el.className = `status ${cls}`
}

// ---------------------------------------------------------------- boot
let scheduleReadyResolve
const scheduleReady = new Promise((r) => { scheduleReadyResolve = r })

const boot = async () => {
  document.getElementById('brand').innerHTML = '<img src="external/img/usm-logo.svg" alt="">'
  const [index, sched, aoBin] = await Promise.all([
    fetch('assets/model-index.json').then(r => r.json()),
    fetch('assets/schedule.json').then(r => r.json()),
    fetch('assets/ao.bin').then(r => (r.ok ? r.arrayBuffer() : null)).catch(() => null),
  ])
  state.aoBin = aoBin
  state.index = index
  state.sched = sched
  prepareSchedule()
  scheduleReadyResolve()
  buildTabs()
  buildTimeline()
  buildGantt()
  selectLevel(null)
  setDay(state.day)

  document.getElementById('plan').addEventListener('click', (e) => {
    const g = e.target.closest('g[data-id]')
    if (g) selectElement(g.dataset.id)
  })
  document.getElementById('info-close').addEventListener('click', () => selectElement(null))
  document.getElementById('ao-toggle').addEventListener('click', () => setAO(!state.aoOn))
  document.getElementById('hud').addEventListener('click', () => document.getElementById('hud').classList.toggle('open'))
  document.getElementById('panel-toggle').addEventListener('click', () => {
    document.getElementById('panel').classList.toggle('collapsed')
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
