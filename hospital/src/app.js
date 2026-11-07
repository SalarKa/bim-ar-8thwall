require('./style.css')
const {PoseFilter} = require('./pose-filter')

// Hospital BIM in AR: the Autodesk hospital sample (Architecture, Structure, HVAC, Piping &
// Plumbing) rises out of its printed Level 1 plan. Every sub-model can be toggled, ghosted or cut
// by level, and the model can be shown as the raw Revit export ("Original"), in the original
// demo's simple flat materials ("Simple"), or both side by side. Both carry the baked AO.

const QUERY = new URLSearchParams(location.search)
const PREVIEW = QUERY.get('mode') === '3d'
const SHOT = QUERY.get('shot')            // dev: render a poster image and upload it (see poster script)
const TARGET_NAME = 'hospital-poster'
const HIGHLIGHT = 0xff7a00
const DEFAULT_ON = ['arch']
const MEP = new Set(['hvac', 'piping'])
const GHOSTED = new Set(['arch'])       // what X-ray turns see-through

const state = {
  scene: null,         // scene.json
  placement: null,     // target-placement.json (where the plan sits on the sheet)
  disc: {},            // key -> {on, loading, loaded, group, meshes[], table}
  look: 'enhanced',    // original | enhanced (the Simple materials) | compare
  split: 0.5,          // compare divider (fraction of the width)
  xray: false,
  sys: false,
  shadows: false,      // off by default, as in the original demo: a shadow pass costs frame rate in AR
  cutIndex: null,      // null = full height
  clip: null,          // world-space clipping plane
  aoOn: true,
  aoTex: {},
  sysU: {value: 0},
  sysPal: {value: []},
  highlight: null,
  root: null,          // model root (THREE.Group)
  renderer: null,
}

// ---------------------------------------------------------------- scene markup
// Image-target space: sheet height = 1 unit (scaled by 8th Wall), centre at origin, +Y up the sheet,
// +Z out of the paper. The anchor's 90° X rotation stands the Y-up model on the sheet.
const modelMarkup = `
  <a-entity id="anchor" rotation="90 0 0">
    <a-entity id="bim" hospital-model></a-entity>
  </a-entity>
  <a-entity id="shadow-catcher" shadow-catcher></a-entity>`

const lights = `
  <a-entity id="hemi" light="type: hemisphere; color: #ffffff; groundColor: #6d737a; intensity: 0.65"></a-entity>
  <a-entity id="sun" light="type: directional; intensity: 1.1; castShadow: true; shadowMapWidth: 2048; shadowMapHeight: 2048; shadowBias: -0.0004; shadowNormalBias: 0.002"
            position="0 0 0" sun-rig></a-entity>`

const sceneMarkup = PREVIEW ? `
  <a-scene renderer="colorManagement: true; antialias: true; maxCanvasWidth: 2560; maxCanvasHeight: 2560"
           shadow="type: pcfsoft" vr-mode-ui="enabled: false" background="color: #dcded9" preview-orbit>
    ${lights}
    <a-entity id="camera" camera="fov: 40; near: 0.005; far: 50"></a-entity>
    <a-entity id="target" rotation="-90 0 0" sheet-preview>
      <a-plane id="sheet-plane" width="0.75" height="1" material="shader: flat; side: double; color: #fafaf7"></a-plane>
      ${modelMarkup}
    </a-entity>
  </a-scene>` : `
  <a-scene landing-page xrextras-loading xrextras-runtime-error
           renderer="colorManagement: true"
           shadow="type: pcfsoft" xrweb="disableWorldTracking: true">
    <a-camera id="camera" position="0 4 10"></a-camera>
    ${lights}
    <a-entity id="target" sheet-tracker>
      ${modelMarkup}
    </a-entity>
  </a-scene>`

// ---------------------------------------------------------------- helpers
const $ = id => document.getElementById(id)
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]))
const discMeta = key => state.scene.disciplines.find(d => d.key === key)

const setStatus = (text, cls = '') => {
  const el = $('status')
  el.textContent = text
  el.className = `status ${cls}`
}

// ---------------------------------------------------------------- materials
// Colour by system: ducts and pipes carry a system index (_sys); the vertex shader looks its colour
// up in a uniform palette (dynamic indexing is legal there, not in WebGL1 fragment shaders).
// Ambient occlusion, as in the original demo: the baked value multiplies the surface colour. Every
// sub-model has an AO texture on uv1 (atlases for big surfaces, a grey ramp indexed by per-vertex
// AO for thin parts, and the ramp's white end for interiors and MEP, which get none); aoMap is
// only the route to it, aoMapIntensity the on/off.
const patchShader = (material, {sys, inflate = 0}) => {
  const prev = material.onBeforeCompile
  material.onBeforeCompile = (shader, r) => {
    if (prev) prev(shader, r)
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <aomap_fragment>', '')
      .replace('#include <color_fragment>', `#include <color_fragment>
#ifdef USE_AOMAP
  diffuseColor.rgb *= mix(1.0, texture2D(aoMap, vAoMapUv).r, aoMapIntensity);
#endif`)
    if (inflate) {                // pipes: thickened for display so 2-4 in lines read at tabletop scale
      // positions are quantized (gltfpack), so the offset is in the mesh's own units
      const k = (inflate / (material.userData.meshScale || 1)).toFixed(4)
      shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>',
        `#include <begin_vertex>
  transformed += normalize(objectNormal) * ${k};`)
    }
    if (!sys) return
    shader.uniforms.uSysOn = state.sysU
    shader.uniforms.uSysPal = state.sysPal
    shader.vertexShader = `attribute float _feature_id_1;\nuniform float uSysOn;\nuniform vec3 uSysPal[24];\nvarying vec3 vSysCol;\nvarying float vSysK;\n${shader.vertexShader}`
      .replace('#include <begin_vertex>', `#include <begin_vertex>
  int si = int(_feature_id_1 + 0.5);
  vSysK = (uSysOn > 0.5 && si > 0) ? 1.0 : 0.0;
  vSysCol = uSysPal[si];`)
    shader.fragmentShader = `varying vec3 vSysCol;\nvarying float vSysK;\n${shader.fragmentShader}`
      .replace('#include <color_fragment>', '#include <color_fragment>\n  diffuseColor.rgb = mix(diffuseColor.rgb, vSysCol, vSysK);')
  }
  material.customProgramCacheKey = () => `hosp-${sys ? 'sys' : 'plain'}-${inflate}-${material.userData.meshScale || 1}`
}

const aoTexture = (path) => {
  if (!state.aoTex[path]) {
    const t = new THREE.TextureLoader().load(`assets/${path}`)
    t.flipY = false                 // glTF UV convention (atlas rows are written top-down)
    t.channel = 1                   // sampled through TEXCOORD_1 / uv1
    t.colorSpace = THREE.NoColorSpace
    state.aoTex[path] = t
  }
  return state.aoTex[path]
}

// Ducts and pipes sit inside the building, so its occlusion would mute their system colours
const aoStrength = m => (state.aoOn ? (m.userData.mep ? 0.35 : 1) : 0)

const withAO = (m, ao, mep) => {
  if (ao) {
    m.aoMap = aoTexture(ao)
    m.userData.mep = !!mep
    m.aoMapIntensity = aoStrength(m)
  }
  return m
}

// Pipe runs (not fixtures) are drawn thicker than modelled; see patchShader
const PIPE_LOOKS = new Set(['copper', 'pipe_jacket', 'pipe_steel', 'pvc'])
const PIPE_INFLATE_M = 0.09

// The Simple look: the original demo's flat group colours (linear), roughness 0.85, no metal or maps
const enhancedMaterial = (look, {sys, ao}) => {
  const L = state.scene.looks[look] || {}
  const m = new THREE.MeshStandardMaterial({
    color: L.hex ? new THREE.Color(L.hex) : new THREE.Color().setRGB(...(L.color || [0.63, 0.63, 0.63])),
    roughness: 0.85,
    metalness: 0,
    side: THREE.DoubleSide,
  })
  if (L.opacity !== undefined && L.opacity < 1) {
    m.transparent = true
    m.opacity = L.opacity
    m.depthWrite = false
  }
  m.clippingPlanes = [state.clip]
  patchShader(m, {sys, inflate: PIPE_LOOKS.has(look) ? PIPE_INFLATE_M : 0})
  return withAO(m, ao, sys)
}

// The raw Revit export: flat Revit shading colours, simple lighting, no reflections or shadows.
const originalMaterial = (mesh, {sys, ao, look}) => {
  const col = mesh.geometry.getAttribute('color')
  let minA = 1
  if (col && col.itemSize === 4) {
    for (let i = 3; i < col.array.length; i += 4 * 97) minA = Math.min(minA, col.getW(Math.floor(i / 4)))
  }
  // Structure keeps its colour coding in both looks: brown steel columns, grey concrete
  const fixed = state.scene.looks[look] && state.scene.looks[look].hex
  const m = fixed
    ? new THREE.MeshLambertMaterial({color: new THREE.Color(fixed), side: THREE.DoubleSide})
    : new THREE.MeshLambertMaterial({vertexColors: true, side: THREE.DoubleSide})
  if (minA < 0.97 && !fixed) {
    m.transparent = true
    m.depthWrite = false
  }
  m.clippingPlanes = [state.clip]
  patchShader(m, {sys, inflate: PIPE_LOOKS.has(look) ? PIPE_INFLATE_M : 0})
  return withAO(m, ao, sys)
}

const ghostMaterial = () => {
  const m = new THREE.MeshLambertMaterial({
    color: 0xe3eaef, transparent: true, opacity: Number(QUERY.get('ghost') || 0.22), depthWrite: false, side: THREE.DoubleSide,
  })
  m.clippingPlanes = [state.clip]
  return m
}

// ---------------------------------------------------------------- applying view state
const lookFor = (pass) => (pass || (state.look === 'original' ? 'original' : 'enhanced'))

const applyMaterials = (pass) => {
  const look = lookFor(pass)
  Object.entries(state.disc).forEach(([key, d]) => {
    d.meshes.forEach((mesh) => {
      const mats = mesh.userData.mats
      mesh.material = state.xray && GHOSTED.has(key) ? mats.ghost : mats[look]
      mesh.castShadow = look === 'enhanced' && !(state.xray && GHOSTED.has(key)) && !MEP.has(key)
      // X-ray: the ghost draws first, then ducts and pipes on top of it, so they stay crisp
      mesh.renderOrder = MEP.has(key) ? 2 : (state.xray && GHOSTED.has(key) ? 1 : 0)
    })
  })
  const sun = document.getElementById('sun')
  if (sun && sun.getObject3D('light')) sun.getObject3D('light').castShadow = look === 'enhanced' && state.shadows
}

const applyVisibility = () => {
  Object.entries(state.disc).forEach(([key, d]) => {
    if (d.group) d.group.visible = d.on
    const chip = document.querySelector(`.disc button[data-key="${key}"]`)
    if (chip) {
      chip.setAttribute('aria-pressed', String(d.on))
      chip.classList.toggle('loading', d.on && !d.loaded)
    }
  })
  applyMaterials()
  const anyMep = [...MEP].some(k => state.disc[k] && state.disc[k].on)
  $('systems').classList.toggle('hidden', !(state.sys && anyMep && (state.disc.hvac?.on || state.disc.piping?.on)))
}

const setDisc = (key, on) => {
  const d = state.disc[key]
  if (!d) return
  d.on = on
  if (on && !d.loaded && !d.loading) loadDiscipline(key)
  // an MEP sub-model inside an opaque building is invisible: ghost the architecture for it
  if (on && MEP.has(key) && state.disc.arch.on && !state.xray && state.cutIndex === null) setXray(true)
  if (on && MEP.has(key) && !state.sys) setSys(true)          // HVAC and Piping open coloured by system
  applyVisibility()
}

const setXray = (on) => {
  state.xray = on
  $('xray').setAttribute('aria-pressed', String(on))
  applyVisibility()
}

const setSys = (on) => {
  state.sys = on
  state.sysU.value = on ? 1 : 0
  $('sys').setAttribute('aria-pressed', String(on))
  applyVisibility()
}

const setShadows = (on) => {
  state.shadows = on
  $('shadow').setAttribute('aria-pressed', String(on))
  applyMaterials()
}

const setAO = (on) => {
  state.aoOn = on
  $('ao').setAttribute('aria-pressed', String(on))
  Object.values(state.disc).forEach(d => d.meshes.forEach((m) => {
    m.userData.mats.enhanced.aoMapIntensity = aoStrength(m.userData.mats.enhanced)
    m.userData.mats.original.aoMapIntensity = aoStrength(m.userData.mats.original)
  }))
}

const setLook = (look) => {
  state.look = look
  document.querySelectorAll('.seg button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.look === look)))
  $('compare').classList.toggle('hidden', look !== 'compare')
  $('shadow').disabled = look === 'original'
  applyMaterials()
}

const levelCut = i => state.scene.levels[i].y + 1.2   // plan cut 1.2 m above the level, like a Revit plan

const setCut = (value) => {
  const n = state.scene.levels.length
  const v = Number(value)
  state.cutIndex = v >= n ? null : v
  $('cut').value = v
  $('cut').style.setProperty('--p', `${(v / n) * 100}%`)
  $('cut-label').textContent = state.cutIndex === null ? 'Full height' : `${state.scene.levels[v].name} cut`
}

// ---------------------------------------------------------------- loading
const gltfLoader = () => {
  const l = new THREE.GLTFLoader()
  l.setMeshoptDecoder(window.MeshoptDecoder)
  return l
}

const loadDiscipline = async (key) => {
  const d = state.disc[key]
  d.loading = true
  applyVisibility()
  const meta = discMeta(key)
  const [gltf, table] = await Promise.all([
    window.MeshoptDecoder.ready.then(() => gltfLoader().loadAsync(`assets/${meta.glb}`)),
    fetch(`assets/${key}.json`).then(r => r.json()),
  ])
  d.table = table
  const sys = MEP.has(key)
  gltf.scene.traverse((o) => {
    if (!o.isMesh) return
    const name = o.material.name        // gltfpack renames meshes but keeps material names (-km)
    const look = name.split('@')[0]     // 'paint@in' = interior faces (no AO), 'paint@v' = per-vertex AO
    const ao = meta.ao[name]
    o.userData.look = look
    o.userData.disc = key
    o.userData.mats = {
      enhanced: enhancedMaterial(look, {sys, ao}),
      original: originalMaterial(o, {sys, ao, look}),
      ghost: ghostMaterial(),
    }
    o.userData.mats.enhanced.userData.meshScale = o.scale.x
    o.userData.mats.original.userData.meshScale = o.scale.x
    if (sys) {                    // in the transparent pass, after the ghost (opacity stays 1)
      o.userData.mats.enhanced.transparent = true
      o.userData.mats.original.transparent = true
    }
    o.receiveShadow = true
    o.frustumCulled = true
    d.meshes.push(o)
  })
  d.group = gltf.scene
  d.group.name = key
  state.root.add(d.group)
  d.loaded = true
  d.loading = false
  applyVisibility()
  return d
}

// ---------------------------------------------------------------- components
const placeOnSheet = (anchorEl, w = 0.75, h = 1) => {
  const pl = state.placement
  const [W, H] = pl.poster_px
  const [ox, oy] = pl.plan_origin_px
  anchorEl.object3D.position.set((ox / W - 0.5) * w, (0.5 - oy / H) * h, 0)
  anchorEl.object3D.scale.setScalar(pl.model_scale * h)
  anchorEl.object3D.rotation.set(Math.PI / 2, pl.plan_rotation || 0, 0)
}

AFRAME.registerComponent('sheet-preview', {
  init() {
    const go = () => {
      placeOnSheet(this.el.querySelector('#anchor'))
      const plane = this.el.querySelector('#sheet-plane')
      if (state.placement.image) plane.setAttribute('material', 'src', `url(${state.placement.image})`)
    }
    placementReady.then(go)
  },
})

// Follows the printed sheet: image tracking only, as in the original demo, so no motion-sensor
// permission. The pose goes through PoseFilter (see pose-filter.js). Close to the paper the tracker
// drops out for moments, so the model is only hidden if the sheet stays lost.
const LOST_HOLD_MS = 600

AFRAME.registerComponent('sheet-tracker', {
  init() {
    const {object3D} = this.el
    const anchor = this.el.querySelector('#anchor')
    const scene = this.el.sceneEl
    object3D.visible = false
    this.filter = new PoseFilter()
    this.camera = new THREE.Vector3()
    this.raw = null
    this.lostAt = 0
    this.lastUpdate = 0
    let placed = false
    const found = ({detail}) => {
      if (detail.name !== TARGET_NAME) return
      if (!placed) {
        placeOnSheet(anchor, detail.scaledWidth || 0.75, detail.scaledHeight || 1)
        placed = true
      }
      setStatus('Tracking sheet', 'ok')
      const {position: p, rotation: q} = detail
      this.raw = {p: [p.x, p.y, p.z], q: [q.x, q.y, q.z, q.w], s: detail.scale}
      this.lostAt = 0
    }
    scene.addEventListener('xrimagefound', found)
    scene.addEventListener('xrimageupdated', found)
    scene.addEventListener('xrimagelost', ({detail}) => {
      if (detail.name === TARGET_NAME) this.lostAt = performance.now()
    })
  },
  tick(time) {
    const {object3D, sceneEl} = this.el
    if (this.lostAt && performance.now() - this.lostAt > LOST_HOLD_MS) {
      object3D.visible = false
      this.filter.reset()
      this.lostAt = 0
      setStatus('Point at the sheet', 'warn')
      return
    }
    if (!this.raw) return
    sceneEl.camera.getWorldPosition(this.camera)
    const dt = Math.min(Math.max(time - this.lastUpdate, 1), 200) / 1000
    this.lastUpdate = time
    const {p, q, s} = this.filter.update(this.raw, this.camera.toArray(), dt)
    this.raw = null                      // one filter step per tracker update
    object3D.position.set(p[0], p[1], p[2])
    object3D.quaternion.set(q[0], q[1], q[2], q[3])
    object3D.scale.setScalar(s)
    object3D.visible = true
  },
})

// A transparent plane on the sheet that only shows the model's shadow (contact shadows on paper).
AFRAME.registerComponent('shadow-catcher', {
  init() {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.75, 1), new THREE.ShadowMaterial({opacity: 0.32}))
    mesh.position.z = 0.0008
    mesh.receiveShadow = true
    this.el.setObject3D('mesh', mesh)
    this.mesh = mesh
  },
  tick() { this.mesh.visible = lookFor() === 'enhanced' && state.shadows },
})

// Keeps the sun's shadow camera wrapped tightly around the model wherever the sheet is.
AFRAME.registerComponent('sun-rig', {
  init() {
    this.box = new THREE.Box3()
    this.size = new THREE.Vector3()
    this.center = new THREE.Vector3()
    this.dir = new THREE.Vector3(-1.5, 2, 1).normalize()         // the original demo's light direction
    this.frame = 0
  },
  tick() {
    const light = this.el.getObject3D('light')
    if (!light || !state.root) return
    if (!state.shadows) {                  // no shadow pass: the light only needs its direction
      light.position.copy(this.dir)
      light.target.position.set(0, 0, 0)
      return
    }
    if (this.frame++ % 15) return
    this.box.setFromObject(state.root, false)
    if (this.box.isEmpty()) return
    this.box.getCenter(this.center)
    this.box.getSize(this.size)
    const r = Math.max(this.size.x, this.size.y, this.size.z) * 0.75
    light.position.copy(this.center).addScaledVector(this.dir, r * 3)
    light.target.position.copy(this.center)
    if (!light.target.parent) this.el.sceneEl.object3D.add(light.target)
    const cam = light.shadow.camera
    cam.left = -r; cam.right = r; cam.top = r; cam.bottom = -r
    cam.near = r * 0.5; cam.far = r * 6
    cam.updateProjectionMatrix()
  },
})

AFRAME.registerComponent('hospital-model', {
  init() {
    state.clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e6)
    this.localPlane = new THREE.Plane()
    state.root = new THREE.Group()
    state.root.name = 'hospital'
    this.el.setObject3D('mesh', state.root)
    const sceneEl = this.el.sceneEl
    const start = async () => {
      await sceneReady
      state.renderer = sceneEl.renderer
      state.renderer.localClippingEnabled = true
      installCompare(sceneEl)
      setStatus('Loading model…')
      await Promise.all(DEFAULT_ON.map(k => loadDiscipline(k)))
      setStatus(PREVIEW ? 'Desktop preview' : 'Point at the sheet', PREVIEW ? 'ok' : '')
      // fetch the remaining sub-models quietly so their toggles are instant
      for (const d of state.scene.disciplines) {
        if (!state.disc[d.key].loaded && !state.disc[d.key].loading) {
          // eslint-disable-next-line no-await-in-loop
          await loadDiscipline(d.key).catch(e => console.warn(d.key, e))
        }
      }
      if (SHOT) runShot(sceneEl)
    }
    sceneEl.hasLoaded ? start() : sceneEl.addEventListener('loaded', start)
  },
  tick() {
    if (!state.root) return
    const h = state.cutIndex === null ? 1e6 : levelCut(state.cutIndex)
    this.localPlane.set(new THREE.Vector3(0, -1, 0), h)
    state.clip.copy(this.localPlane).applyMatrix4(state.root.matrixWorld)
  },
})

// Side-by-side comparison: the frame is drawn twice with scissor rectangles, Original on the left
// of the divider and Enhanced on the right.
const installCompare = (sceneEl) => {
  const r = sceneEl.renderer
  const render = r.render.bind(r)
  const size = new THREE.Vector2()
  r.render = (scene, camera) => {
    if (state.look !== 'compare' || scene !== sceneEl.object3D) return render(scene, camera)
    r.getSize(size)
    const x = Math.round(size.x * state.split)
    r.setScissorTest(true)
    applyMaterials('original')
    r.setScissor(0, 0, x, size.y)
    render(scene, camera)
    applyMaterials('enhanced')
    r.setScissor(x, 0, size.x - x, size.y)
    render(scene, camera)
    r.setScissorTest(false)
    return undefined
  }
}

// Desktop preview: drag to orbit, wheel to zoom, right-drag or shift-drag to pan.
AFRAME.registerComponent('preview-orbit', {
  init() {
    const cam = () => document.getElementById('camera').object3D
    let yaw = QUERY.has('yaw') ? Number(QUERY.get('yaw')) : 0.62
    let pitch = QUERY.has('pitch') ? Number(QUERY.get('pitch')) : 0.6
    let dist = QUERY.has('dist') ? Number(QUERY.get('dist')) : 1.2
    const target = QUERY.has('t') ? new THREE.Vector3(...QUERY.get('t').split(',').map(Number)) : new THREE.Vector3(0, 0.06, 0.02)
    let drag = null
    const update = () => {
      const c = cam()
      const aspect = innerWidth / Math.max(innerHeight, 1)
      const d = dist * (aspect > 0 ? Math.max(1, 0.85 / aspect) : 1)
      c.position.set(target.x + Math.sin(yaw) * Math.cos(pitch) * d, target.y + Math.sin(pitch) * d,
        target.z + Math.cos(yaw) * Math.cos(pitch) * d)
      const m = new THREE.Matrix4().lookAt(c.position, target, c.up)
      c.quaternion.setFromRotationMatrix(m)
    }
    this.el.addEventListener('loaded', update)
    window.addEventListener('resize', update)
    const canvas = () => this.el.canvas
    window.addEventListener('contextmenu', (e) => { if (e.target === canvas()) e.preventDefault() })
    window.addEventListener('pointerdown', (e) => {
      if (e.target === canvas()) drag = {x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey}
    })
    window.addEventListener('pointermove', (e) => {
      if (!drag) return
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y
      if (drag.pan) {
        const right = new THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw))
        const fwd = new THREE.Vector3(Math.sin(yaw), 0, Math.cos(yaw))
        target.addScaledVector(right, -dx * 0.0012 * dist).addScaledVector(fwd, -dy * 0.0012 * dist)
      } else {
        yaw -= dx * 0.006
        pitch = Math.min(1.52, Math.max(0.05, pitch + dy * 0.006))
      }
      drag.x = e.clientX; drag.y = e.clientY
      update()
    })
    window.addEventListener('pointerup', () => { drag = null })
    window.addEventListener('wheel', (e) => {
      if (e.target !== canvas()) return
      dist = Math.min(4, Math.max(0.05, dist * (1 + Math.sign(e.deltaY) * 0.1)))
      update()
    }, {passive: true})
    window.__orbit = {set: (o) => { if (o.yaw !== undefined) yaw = o.yaw; if (o.pitch !== undefined) pitch = o.pitch; if (o.dist !== undefined) dist = o.dist; if (o.target) target.set(...o.target); update() }}
  },
})

// ---------------------------------------------------------------- tap to identify
const clearHighlight = () => {
  if (state.highlight) {
    state.highlight.parent.remove(state.highlight)
    state.highlight.geometry.dispose()
    state.highlight = null
  }
}

const highlightElement = (mesh, el) => {
  clearHighlight()
  const src = mesh.geometry
  const elAttr = src.getAttribute('_feature_id_0')
  const idx = src.index.array
  const out = []
  for (let t = 0; t < idx.length; t += 3) {
    if (Math.round(elAttr.getX(idx[t])) === el) out.push(idx[t], idx[t + 1], idx[t + 2])
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', src.getAttribute('position'))
  geo.setIndex(out)
  const hl = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
    color: HIGHLIGHT, transparent: true, opacity: 0.85, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, clippingPlanes: [state.clip],
  }))
  hl.renderOrder = 10
  mesh.add(hl)
  state.highlight = hl
}

const showInfo = (key, el) => {
  const row = state.disc[key].table[el]
  if (!row) return
  const [rid, cat, fam, typ, level, system] = row
  const meta = discMeta(key)
  const title = typ || fam || cat
  const sys = system ? state.scene.systems.find(s => s[0] === system) : null
  $('info-body').innerHTML = `
    <h3>${esc(title)}</h3>
    <div class="sub">${esc(cat.replace(/([a-z])([A-Z])/g, '$1 $2'))}${fam && fam !== title ? ` · ${esc(fam)}` : ''}</div>
    <span class="chip"><i style="background:${meta.color}"></i>${esc(meta.label)}</span>
    <dl>
      ${system ? `<dt>System</dt><dd>${sys ? `<span style="color:${sys[1]}">■</span> ` : ''}${esc(system)}</dd>` : ''}
      ${level ? `<dt>Level</dt><dd>${esc(level)}</dd>` : ''}
      <dt>Revit ID</dt><dd>${esc(rid)}</dd>
    </dl>`
  $('info').classList.remove('hidden')
}

const setupPicking = (sceneEl) => {
  const raycaster = new THREE.Raycaster()
  let down = null
  sceneEl.canvas.addEventListener('pointerdown', (e) => { down = {x: e.clientX, y: e.clientY} })
  sceneEl.canvas.addEventListener('pointerup', (e) => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 8) return
    const rect = sceneEl.canvas.getBoundingClientRect()
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1)
    raycaster.setFromCamera(ndc, sceneEl.camera)
    const targets = []
    Object.entries(state.disc).forEach(([key, d]) => {
      if (!d.on || !d.loaded || (state.xray && GHOSTED.has(key))) return
      d.meshes.forEach((m) => { if (m.visible) targets.push(m) })
    })
    const hit = raycaster.intersectObjects(targets, false)
      .find(h => state.clip.distanceToPoint(h.point) >= 0 && h.object.geometry.getAttribute('_feature_id_0'))
    if (!hit) {
      clearHighlight()
      $('info').classList.add('hidden')
      return
    }
    const el = Math.round(hit.object.geometry.getAttribute('_feature_id_0').getX(hit.face.a))
    highlightElement(hit.object, el)
    showInfo(hit.object.userData.disc, el)
  })
}

// ---------------------------------------------------------------- UI
const buildUI = () => {
  const wrap = $('disc')
  state.scene.disciplines.forEach((d) => {
    state.disc[d.key] = {on: DEFAULT_ON.includes(d.key), loading: false, loaded: false, group: null, meshes: [], table: []}
    const b = document.createElement('button')
    b.dataset.key = d.key
    b.style.setProperty('--c', d.color)
    b.innerHTML = `<i style="background:${d.color}"></i>${esc(d.label)}`
    let last = 0
    b.addEventListener('click', () => {
      const now = Date.now()
      if (now - last < 320) {             // double tap: isolate this sub-model
        state.scene.disciplines.forEach(x => setDisc(x.key, x.key === d.key))
        if (MEP.has(d.key) || d.key === 'struct') setXray(false)
      } else {
        setDisc(d.key, !state.disc[d.key].on)
      }
      last = now
    })
    wrap.appendChild(b)
  })
  const legend = $('systems')
  legend.innerHTML = '<div class="eyebrow">MEP systems</div>' + state.scene.systems
    .map(([name, col]) => `<span><i style="background:${col}"></i>${esc(name.replace(/^(DUCT|PIPING|PLUMBING) - /i, ''))}</span>`).join('')
  state.sysPal.value = Array.from({length: 24}, (_, i) => {
    const s = state.scene.systems[i - 1]
    return s ? new THREE.Color(s[1]) : new THREE.Color(0xffffff)
  })

  document.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => setLook(b.dataset.look)))
  $('xray').addEventListener('click', () => setXray(!state.xray))
  $('sys').addEventListener('click', () => setSys(!state.sys))
  $('shadow').addEventListener('click', () => setShadows(!state.shadows))
  $('ao').addEventListener('click', () => setAO(!state.aoOn))
  $('all').addEventListener('click', () => {
    state.scene.disciplines.forEach(d => setDisc(d.key, true))
    setXray(true)
  })
  $('cut').max = state.scene.levels.length
  $('cut').addEventListener('input', e => setCut(e.target.value))
  setCut(state.scene.levels.length)
  setLook(state.look)
  $('panel-toggle').addEventListener('click', () => $('panel').classList.toggle('collapsed'))
  $('info-close').addEventListener('click', () => { clearHighlight(); $('info').classList.add('hidden') })

  // compare divider
  const handle = $('compare-handle')
  const setSplit = (x) => {
    state.split = Math.min(0.95, Math.max(0.05, x / innerWidth))
    $('compare').style.setProperty('--split', `${state.split * 100}%`)
  }
  let dragging = false
  handle.addEventListener('pointerdown', (e) => { dragging = true; handle.setPointerCapture(e.pointerId) })
  handle.addEventListener('pointermove', (e) => { if (dragging) setSplit(e.clientX) })
  handle.addEventListener('pointerup', () => { dragging = false })
  handle.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') setSplit((state.split - 0.05) * innerWidth)
    if (e.key === 'ArrowRight') setSplit((state.split + 0.05) * innerWidth)
  })
  setSplit(innerWidth / 2)
  window.addEventListener('resize', () => setSplit(state.split * innerWidth))
}

// Dev helper for the poster: ?mode=3d&shot=<name>&look=..&disc=a,b&xray=1 renders one frame
// at the current view and posts it to the dev server (pipeline/out/shots/<name>.png).
// &w=&h= set the image size; &ortho=1 renders the true-scale top view used as the printed AR anchor
// (model metres, image x = +X, image down = +Z, framed on the site base).
const runShot = async (sceneEl) => {
  const discs = (QUERY.get('disc') || 'arch,site').split(',')
  state.scene.disciplines.forEach(d => setDisc(d.key, discs.includes(d.key)))
  setXray(QUERY.get('xray') === '1')
  setSys(QUERY.get('sys') === '1')
  if (QUERY.get('cut')) setCut(QUERY.get('cut'))
  if (QUERY.get('ao') === '0') setAO(false)
  setLook(QUERY.get('look') || 'enhanced')
  sceneEl.object3D.background = new THREE.Color(QUERY.get('bg') || '#ffffff')
  document.getElementById('sheet-plane').object3D.visible = false
  document.getElementById('shadow-catcher').object3D.visible = !!QUERY.get('catcher')
  document.getElementById('ui').style.display = 'none'
  const r = sceneEl.renderer
  const W = Number(QUERY.get('w') || 1600)
  let H = Number(QUERY.get('h') || 1200)
  let camera = sceneEl.camera
  if (QUERY.get('ortho')) {
    const anchor = document.getElementById('anchor').object3D
    anchor.position.set(0, 0, 0)
    anchor.scale.setScalar(1)
    const bb = state.scene.bounds
    const [x0, z0, x1, z1] = [bb.min[0] - 6, bb.min[2] - 6, bb.max[0] + 6, bb.max[2] + 6]
    H = Math.round(W * (z1 - z0) / (x1 - x0))
    camera = new THREE.OrthographicCamera(x0, x1, -z0, -z1, 1, 600)
    camera.position.set(0, 300, 0)
    camera.up.set(0, 0, -1)
    camera.lookAt(0, 0, 0)
    document.getElementById('sun').components['sun-rig'].dir.set(-0.35, 0.9, -0.25).normalize()  // high sun: short shadows on the plan
    document.title = `bounds:${[x0, z0, x1, z1].join(',')}`
  }
  r.setPixelRatio(1)
  r.setSize(W, H, false)
  if (camera.isPerspectiveCamera) { camera.aspect = W / H; camera.updateProjectionMatrix() }
  await new Promise(res => setTimeout(res, Number(QUERY.get('wait') || 2500)))
  const sun = document.getElementById('sun').components['sun-rig']
  sun.frame = 0
  sun.tick()
  r.render(sceneEl.object3D, camera)
  const url = sceneEl.canvas.toDataURL('image/png')
  await fetch(`/__shot?name=${encodeURIComponent(SHOT)}`, {method: 'POST', body: url})
  document.title = `shot:${SHOT}:done`
}

// ---------------------------------------------------------------- boot
let placementResolve
const placementReady = new Promise((r) => { placementResolve = r })
let sceneResolve
const sceneReady = new Promise((r) => { sceneResolve = r })

const boot = async () => {
  $('brand').innerHTML = '<img src="external/img/usm-logo.svg" alt="">'
  const [scene, placement] = await Promise.all([
    fetch('assets/scene.json').then(r => r.json()),
    fetch('assets/target-placement.json').then(r => r.json()),
  ])
  state.scene = scene
  state.placement = placement
  placementResolve()
  buildUI()
  sceneResolve()

  if (!PREVIEW) {
    const target = await fetch(`image-targets/${TARGET_NAME}.json`).then(r => r.json())
    const configure = () => XR8.XrController.configure({imageTargetData: [target]})
    window.XR8 ? configure() : window.addEventListener('xrloaded', configure)
  }

  $('scene-root').innerHTML = sceneMarkup
  const sceneEl = document.querySelector('a-scene')
  const ready = () => setupPicking(sceneEl)
  sceneEl.hasLoaded ? ready() : sceneEl.addEventListener('loaded', ready)
}

boot()
