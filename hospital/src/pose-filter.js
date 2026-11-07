// Steadies the printed sheet's pose in image-only tracking (no world tracking, so no motion-sensor
// permission). Close to the paper the camera sees few features: the sheet's position stays well
// measured, but its tilt and depth become noisy, and a model standing on the sheet rocks with them.
//
// So tilt, lateral position and depth are held separately inside a small dead band, measured as the
// camera sees them and wider when the camera is close. Inside the band the pose barely moves; beyond
// it the filter catches up quickly, so real camera moves stay responsive. A single wild frame is
// ignored: the pose only jumps if the next frame agrees.

const DEG = Math.PI / 180
const lerp = (a, b, t) => a + (b - a) * t
const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0)
const len = a => Math.sqrt(dot(a, a))

const FAR = {rot: 0.3 * DEG, lat: 0.1 * DEG, depth: 0.005}    // camera a few sheet heights away
const NEAR = {rot: 1.1 * DEG, lat: 0.22 * DEG, depth: 0.018}  // camera about half a sheet height away
const GAIN_MIN = 0.012       // per 30 Hz frame, inside the dead band
const GAIN_MAX = 0.8         // per 30 Hz frame, well outside it
const OUTLIER = 14           // dead bands away from the held pose: suspicious for one frame

const gain = (error, band, dt) => {
  const g = lerp(GAIN_MIN, GAIN_MAX, smoothstep(1, 4.5, error / band))
  return 1 - (1 - g) ** (dt * 30)
}

class PoseFilter {
  constructor() { this.reset() }

  reset() {
    this.pose = null
    this.suspect = false
  }

  // raw: {p: [x, y, z], q: [x, y, z, w], s}; camera: [x, y, z]; dt in seconds
  update(raw, camera, dt) {
    if (!this.pose) {
      this.pose = {p: [...raw.p], q: [...raw.q], s: raw.s}
      return this.pose
    }
    const {pose} = this
    const toSheet = sub(raw.p, camera)
    const dist = Math.max(len(toSheet), 1e-6)
    const ray = toSheet.map(v => v / dist)
    const close = 1 - smoothstep(0.6, 1.8, dist / raw.s)      // distance in sheet heights
    const band = {rot: lerp(FAR.rot, NEAR.rot, close), lat: lerp(FAR.lat, NEAR.lat, close),
      depth: lerp(FAR.depth, NEAR.depth, close)}

    const e = sub(raw.p, pose.p)
    const along = dot(e, ray)
    const lateral = sub(e, ray.map(v => v * along))
    const latError = len(lateral) / dist
    const depthError = Math.abs(along) / dist
    const q = dot(pose.q, raw.q) < 0 ? raw.q.map(v => -v) : raw.q   // same quaternion hemisphere
    const rotError = 2 * Math.acos(Math.min(1, Math.abs(dot(pose.q, q))))

    if (Math.max(rotError / band.rot, latError / band.lat, depthError / band.depth) > OUTLIER && !this.suspect) {
      this.suspect = true
      return pose
    }
    this.suspect = false

    const gLat = gain(latError, band.lat, dt)
    const gDepth = gain(depthError, band.depth, dt)
    pose.p = pose.p.map((v, i) => v + ray[i] * along * gDepth + lateral[i] * gLat)
    const gRot = gain(rotError, band.rot, dt)
    const blended = pose.q.map((v, i) => lerp(v, q[i], gRot))
    const norm = len(blended)
    pose.q = blended.map(v => v / norm)
    pose.s = lerp(pose.s, raw.s, gDepth)
    return pose
  }
}

module.exports = {PoseFilter}
