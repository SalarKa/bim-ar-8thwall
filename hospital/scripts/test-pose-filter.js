// Simulated tracking: how much jitter the pose filter removes, and how much it lags real camera moves.
// Compares no filter, the previous One Euro filter and the new one. Noise levels are assumptions
// (they can't be measured off a phone here): near the sheet, tilt and depth are much noisier.
// Usage: node scripts/test-pose-filter.js
const {PoseFilter} = require('../src/pose-filter')

const DEG = Math.PI / 180
let seed = 7
const rand = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
const gauss = () => Math.sqrt(-2 * Math.log(rand())) * Math.cos(2 * Math.PI * rand())

class OneEuro {
  constructor(minCutoff, beta) { Object.assign(this, {minCutoff, beta, x: null, dx: 0}) }

  filter(v, dt) {
    if (this.x === null) { this.x = v; return v }
    const a = (c) => 1 / (1 + 1 / (2 * Math.PI * c) / dt)
    this.dx += a(1) * ((v - this.x) / dt - this.dx)
    this.x += a(this.minCutoff + this.beta * Math.abs(this.dx)) * (v - this.x)
    return this.x
  }
}

const quatX = a => [Math.sin(a / 2), 0, 0, Math.cos(a / 2)]
const angleOf = q => 2 * Math.atan2(q[0], q[3])

// camera at the origin looking down -z at a sheet `dist` away (scene units: sheet height = 1)
const noisy = (truth, dist, close) => {
  const tilt = (close ? 0.9 : 0.15) * DEG, lat = (close ? 0.08 : 0.03) * DEG, depth = close ? 0.012 : 0.003
  return {
    p: [truth.x + gauss() * lat * dist, gauss() * lat * dist, -dist * (1 + gauss() * depth)],
    q: quatX(truth.tilt + gauss() * tilt),
    s: 1,
  }
}

const run = (name, dist, close, motion) => {
  const dt = 1 / 30
  const pf = new PoseFilter()
  const euro = [new OneEuro(0.8, 1.5), new OneEuro(0.8, 1.5), new OneEuro(0.8, 1.5), new OneEuro(0.8, 0.6)]
  const stat = {raw: [0, 0], euro: [0, 0], filter: [0, 0]}
  let last = {}
  const frames = 600
  for (let i = 0; i < frames; i++) {
    const t = i * dt
    const truth = motion(t)
    const r = noisy(truth, dist, close)
    const f = pf.update(r, [0, 0, 0], dt)
    const e = {p: [euro[0].filter(r.p[0], dt), euro[1].filter(r.p[1], dt), euro[2].filter(r.p[2], dt)],
      tilt: euro[3].filter(angleOf(r.q), dt)}
    const rec = {raw: {x: r.p[0], z: r.p[2], tilt: angleOf(r.q)}, euro: {x: e.p[0], z: e.p[2], tilt: e.tilt},
      filter: {x: f.p[0], z: f.p[2], tilt: angleOf(f.q)}}
    if (i > 90) {
      Object.keys(rec).forEach((k) => {
        // a point on top of the building, 0.12 sheet heights above the paper: what the eye sees rock
        const top = o => o.x + 0.12 * Math.sin(o.tilt)
        if (last[k]) stat[k][0] += Math.abs(top(rec[k]) - top(last[k])) / dist / DEG
        stat[k][1] = Math.max(stat[k][1], Math.abs(rec[k].x - truth.x) / dist / DEG)
      })
    }
    last = rec
  }
  const row = k => `${(stat[k][0] / (frames - 91)).toFixed(4)} deg/frame, worst lag ${stat[k][1].toFixed(2)} deg`
  console.log(`${name}\n  no filter : ${row('raw')}\n  One Euro  : ${row('euro')}\n  new filter: ${row('filter')}`)
  return stat
}

const still = () => ({x: 0, tilt: 0})
const pan = t => ({x: 0.25 * Math.sin(t * 1.2), tilt: 0.2 * Math.sin(t * 0.9)})
const a = run('Still phone, far (2 sheet heights)', 2, false, still)
const b = run('Still phone, close (0.6 sheet heights)', 0.6, true, still)
run('Moving phone, far', 2, false, pan)
const c = run('Moving phone, close', 0.6, true, pan)
const ratio = (s, k) => s.raw[0] / s[k][0]
console.log(`\nJitter removed, still and close: ${ratio(b, 'filter').toFixed(1)}x (One Euro ${ratio(b, 'euro').toFixed(1)}x)`)
const ok = ratio(b, 'filter') > 2 * ratio(b, 'euro') && ratio(a, 'filter') > ratio(a, 'euro') && c.filter[1] < 3
console.log(ok ? 'PASS' : 'FAIL')
process.exit(ok ? 0 : 1)
