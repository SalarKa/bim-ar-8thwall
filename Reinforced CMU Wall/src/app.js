require('./style.css')

// Reinforced CMU wall in AR: the Blender construction sequence (footing and dowels, corner leads,
// fill to the line, bars, positioners, grout, vibration, grout key) plays on its printed 1:16 top
// view. Tracking is the root demo's: an 8th Wall image target whose pose is copied straight onto
// the sheet, with world tracking off.

const TARGET = require('../image-targets/cmu-wall-poster.json')
const PLACEMENT = require('./assets/target-placement.json')
const SEQ = require('./assets/steps.json')

const QUERY = new URLSearchParams(location.search)
const PREVIEW = QUERY.get('mode') === '3d'
const SPEEDS = [0.5, 1, 2]
const XRAY_MODES = ['auto', 'on', 'off']

const sec = frame => (frame - 1) / SEQ.fps          // Blender frame -> clip time (frame 1 = 0 s)
const STEPS = SEQ.steps.map(s => ({...s, t0: sec(s.start), t1: sec(s.end)}))
const [XI0, XI1, XO0, XO1] = SEQ.xray.map(sec)

const state = {
  mixer: null,
  duration: sec(SEQ.frame_end),
  time: 0,
  playing: false,
  speed: 1,
  xray: 'auto',
  ghost: [],          // CMU + mortar materials that fade for the X-ray
  step: -1,
  seen: false,        // sheet found at least once (AR autoplays then)
}

// ---------------------------------------------------------------- scene markup
// The model is attached to the printed sheet. Image-target space: sheet height = scaledHeight,
// centre at origin, +Y up the sheet, +Z out of the paper. rotation 90° X stands the model up;
// the inner offset lifts it by the footing depth so the footing sits on the paper.
const modelMarkup = `
  <a-entity id="anchor" rotation="90 0 0">
    <a-entity id="wall" position="0 ${SEQ.footing_depth_m} 0" cmu-wall></a-entity>
  </a-entity>`

const lights = `
    <a-entity light="type: hemisphere; color: #ffffff; groundColor: #6d737a; intensity: 0.75"></a-entity>
    <a-entity light="type: directional; intensity: 1.25" position="-1.2 2 1.4"></a-entity>`

const sceneMarkup = PREVIEW ? `
  <a-scene renderer="colorManagement: true; antialias: true" vr-mode-ui="enabled: false"
           background="color: #d9dad5" preview-orbit>
    ${lights}
    <a-entity id="camera" camera="fov: 45; userHeight: 0"></a-entity>
    <a-entity id="target" rotation="-90 0 0" sheet-preview>
      <a-plane width="0.75" height="1" src="url(image-targets/${TARGET.resources.originalImage})"
               material="shader: flat; side: double"></a-plane>
      ${modelMarkup}
    </a-entity>
  </a-scene>` : `
  <a-scene landing-page xrextras-loading xrextras-runtime-error
           renderer="colorManagement: true" xrweb="disableWorldTracking: true">
    <a-camera id="camera" position="0 4 10"></a-camera>
    ${lights}
    <a-entity id="target" sheet-tracker>
      ${modelMarkup}
    </a-entity>
  </a-scene>`

// ---------------------------------------------------------------- 8th Wall setup
const onxrloaded = () => {
  XR8.XrController.configure({imageTargetData: [TARGET]})
}
if (!PREVIEW) {
  window.XR8 ? onxrloaded() : window.addEventListener('xrloaded', onxrloaded)
}

// ---------------------------------------------------------------- helpers
const $ = id => document.getElementById(id)
const setStatus = (text, cls = '') => {
  const el = $('status')
  el.textContent = text
  el.className = `glass status ${cls}`
}
const fmt = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`
const smooth = x => x * x * (3 - 2 * x)
const ramp = (t, a, b) => smooth(Math.min(1, Math.max(0, (t - a) / Math.max(b - a, 1e-6))))

// opacity of the masonry: the animation fades it while the bars and grout go in (material keys
// do not travel in glTF, so the fade is driven here from the Blender timing)
const ghostOpacity = (t) => {
  if (state.xray === 'on') return SEQ.xray_alpha
  if (state.xray === 'off') return 1
  const a = SEQ.xray_alpha
  return 1 - (1 - a) * (ramp(t, XI0, XI1) - ramp(t, XO0, XO1))
}

// ---------------------------------------------------------------- components
// Places the model so its footprint sits exactly on the sheet's printed top view.
// Image-target space: sheet height = h, width = w, centre at origin, +Y up the sheet.
// The sheet is printed landscape, so the target image is the layout turned 90°; plan_rotation
// turns the model the same way.
const placeOnSheet = (anchorEl, w = 0.75, h = 1) => {
  const pl = PLACEMENT
  const [W, H] = pl.poster_px
  const [ox, oy] = pl.plan_origin_px
  anchorEl.object3D.position.set((ox / W - 0.5) * w, (0.5 - oy / H) * h, 0)
  anchorEl.object3D.scale.setScalar(pl.model_scale * h)
  anchorEl.object3D.rotation.set(Math.PI / 2, pl.plan_rotation || 0, 0, 'XYZ')   // stand up, then turn about up
}

AFRAME.registerComponent('sheet-preview', {
  init() { placeOnSheet(this.el.querySelector('#anchor')) },
})

// Follows the printed sheet (same as the root demo's tracker).
AFRAME.registerComponent('sheet-tracker', {
  init() {
    const {object3D} = this.el
    const anchor = this.el.querySelector('#anchor')
    const scene = this.el.sceneEl
    this.current = null
    object3D.visible = false
    const show = ({detail}) => {
      if (detail.name !== TARGET.name) return
      if (this.current !== detail.name) {
        this.current = detail.name
        placeOnSheet(anchor, detail.scaledWidth || 0.75, detail.scaledHeight || 1)
        setStatus('Tracking sheet', 'ok')
        if (!state.seen) {
          state.seen = true
          setPlaying(true)
        }
      }
      object3D.position.copy(detail.position)
      object3D.quaternion.copy(detail.rotation)
      object3D.scale.setScalar(detail.scale)
      object3D.visible = true
    }
    scene.addEventListener('xrimagefound', show)
    scene.addEventListener('xrimageupdated', show)
    scene.addEventListener('xrimagelost', ({detail}) => {
      if (detail.name !== this.current) return
      this.current = null
      object3D.visible = false
      setStatus('Point at the sheet', 'warn')
    })
  },
})

// Loads the animated wall and drives its clip from the UI clock.
AFRAME.registerComponent('cmu-wall', {
  init() {
    const loader = new THREE.GLTFLoader()
    loader.setMeshoptDecoder(window.MeshoptDecoder)
    window.MeshoptDecoder.ready.then(() => loader.loadAsync('assets/cmu_wall.glb')).then((gltf) => {
      const root = gltf.scene
      root.traverse((o) => {
        if (!o.isMesh) return
        const name = o.material.name
        if (name === 'CMU') {
          // slight unit-to-unit shade variation, as in the Blender render
          o.material = o.material.clone()
          o.material.color.multiplyScalar(0.9 + Math.random() * 0.16)
        }
        if (name === 'CMU' || name === 'Mortar') state.ghost.push(o.material)
        if (name === 'Rebar' || name === 'Dowel') { o.material.metalness = 0.25; o.material.roughness = 0.6 }
        if (name === 'Galvanized wire' || name === 'Vibrator head') o.material.metalness = 0.3
      })
      this.el.setObject3D('mesh', root)
      // Blender's exporter writes one clip per object, each spanning the whole scene timeline
      state.mixer = new THREE.AnimationMixer(root)
      gltf.animations.forEach(clip => state.mixer.clipAction(clip).play())
      state.duration = Math.max(...gltf.animations.map(c => c.duration))
      setTime(0)
      setStatus(PREVIEW ? 'Desktop preview' : 'Point at the sheet', PREVIEW ? 'ok' : '')
      if (PREVIEW && QUERY.get('autoplay') !== '0') setPlaying(true)
      if (QUERY.has('t')) setTime(Number(QUERY.get('t')))
    }).catch((e) => {
      console.error(e)
      setStatus('Model failed to load', 'warn')
    })
  },
  tick(t, dt) {
    if (!state.mixer || !state.playing) return
    const next = state.time + Math.min(dt, 100) / 1000 * state.speed
    if (next >= state.duration) {
      setTime(state.duration)
      setPlaying(false)
    } else {
      setTime(next)
    }
  },
})

// Desktop preview: drag to orbit, wheel to zoom. Starts in front of the wall's face.
AFRAME.registerComponent('preview-orbit', {
  init() {
    const cam = () => document.getElementById('camera').object3D
    const [ox] = PLACEMENT.plan_origin_px
    const target = new THREE.Vector3((ox / PLACEMENT.poster_px[0] - 0.5) * 0.75, 0.09, 0)
    let yaw = -Math.PI / 2 - 0.5, pitch = 0.55, dist = 1.45, drag = null
    const update = () => {
      const c = cam()
      // back off on portrait screens; a page loaded in a hidden/zero-size window has no aspect yet
      const aspect = innerWidth / Math.max(innerHeight, 1)
      const d = dist * (aspect > 0 ? Math.max(1, 1.1 / aspect) : 1)
      c.position.set(target.x + Math.sin(yaw) * Math.cos(pitch) * d, target.y + Math.sin(pitch) * d,
        target.z + Math.cos(yaw) * Math.cos(pitch) * d)
      // entity lookAt would aim +Z at the target; cameras look down -Z
      const m = new THREE.Matrix4().lookAt(c.position, target, c.up)
      c.quaternion.setFromRotationMatrix(m)
    }
    this.el.addEventListener('loaded', update)
    window.addEventListener('resize', update)
    const canvas = () => this.el.canvas
    window.addEventListener('pointerdown', (e) => { if (e.target === canvas()) drag = {x: e.clientX, y: e.clientY} })
    window.addEventListener('pointermove', (e) => {
      if (!drag) return
      yaw -= (e.clientX - drag.x) * 0.006
      pitch = Math.min(1.5, Math.max(0.05, pitch + (e.clientY - drag.y) * 0.006))
      drag = {x: e.clientX, y: e.clientY}
      update()
    })
    window.addEventListener('pointerup', () => { drag = null })
    window.addEventListener('wheel', (e) => {
      if (e.target !== canvas()) return
      dist = Math.min(3, Math.max(0.25, dist * (1 + Math.sign(e.deltaY) * 0.1)))
      update()
    }, {passive: true})
  },
})

// ---------------------------------------------------------------- playback + overlays
const stepAt = t => STEPS.reduce((cur, s, i) => (t >= s.t0 - 1e-3 ? i : cur), 0)

function setTime(t) {
  state.time = Math.min(Math.max(t, 0), state.duration)
  if (state.mixer) state.mixer.setTime(state.time)
  const op = ghostOpacity(state.time)
  state.ghost.forEach((m) => {
    m.opacity = op
    const see = op < 0.999
    if (m.transparent !== see) { m.transparent = see; m.depthWrite = !see; m.needsUpdate = true }
  })
  const i = stepAt(state.time)
  const s = STEPS[i]
  if (i !== state.step) {
    state.step = i
    $('step-n').textContent = s.n
    $('step-title').textContent = s.title
    $('step-caption').textContent = s.caption
    document.querySelectorAll('.chip').forEach((c, j) => {
      c.classList.toggle('on', j === i)
      c.classList.toggle('done', j < i)
      c.setAttribute('aria-selected', String(j === i))
    })
    const chip = document.querySelectorAll('.chip')[i]
    if (chip) chip.scrollIntoView({block: 'nearest', inline: 'nearest'})
  }
  $('step-bar').style.width = `${Math.min(100, (state.time - s.t0) / (s.t1 - s.t0) * 100)}%`
  $('scrub').value = String(Math.round(state.time / state.duration * 1000))
  $('clock').textContent = fmt(state.time)
}

function setPlaying(on) {
  if (on && state.time >= state.duration - 0.05) setTime(0)    // replay from the start
  state.playing = on
  $('play').textContent = on ? '❚❚' : (state.time >= state.duration - 0.05 ? '↺' : '▶')
  $('play').setAttribute('aria-label', on ? 'Pause' : 'Play')
}

const buildUI = () => {
  $('step-count').textContent = STEPS.length
  $('chips').innerHTML = STEPS.map(s => `<button class="chip" role="tab" title="${s.caption}"><b>${s.n}</b>${s.title}</button>`).join('')
  document.querySelectorAll('.chip').forEach((c, i) => c.addEventListener('click', () => {
    setTime(STEPS[i].t0 + 0.01)
    setPlaying(true)
  }))
  $('play').addEventListener('click', () => setPlaying(!state.playing))
  $('scrub').addEventListener('input', (e) => {
    setTime(Number(e.target.value) / 1000 * state.duration)
  })
  $('speed').addEventListener('click', () => {
    state.speed = SPEEDS[(SPEEDS.indexOf(state.speed) + 1) % SPEEDS.length]
    $('speed').textContent = `${state.speed}×`
  })
  $('xray').addEventListener('click', () => {
    state.xray = XRAY_MODES[(XRAY_MODES.indexOf(state.xray) + 1) % XRAY_MODES.length]
    $('xray').textContent = `X-ray: ${state.xray}`
    $('xray').setAttribute('aria-pressed', String(state.xray === 'on'))
    setTime(state.time)
  })
  state.step = -1
  setTime(0)
}

// ---------------------------------------------------------------- boot
buildUI()
$('scene-root').innerHTML = sceneMarkup
