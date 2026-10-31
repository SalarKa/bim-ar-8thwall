// Publish hospital/dist to /hospital/ on the gh-pages branch, next to the original demo.
// Unlike the root deploy (which force-pushes the whole site), this checks out the current
// gh-pages branch, replaces only its hospital/ folder, and pushes a normal commit, so the
// original demo at the site root is left exactly as it is.
const {execSync} = require('child_process')
const fs = require('fs')
const os = require('os')
const path = require('path')

const dist = path.join(__dirname, '..', 'dist')
const remote = execSync('git remote get-url origin').toString().trim()
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gh-pages-'))
const run = cmd => execSync(cmd, {cwd: work, stdio: 'inherit'})

if (!fs.existsSync(path.join(dist, 'index.html'))) throw new Error('run npm run build first')
run(`git clone --quiet --depth 1 --branch gh-pages ${remote} .`)
fs.rmSync(path.join(work, 'hospital'), {recursive: true, force: true})
fs.cpSync(dist, path.join(work, 'hospital'), {recursive: true})
run('git add -A hospital')
run('git -c user.name="deploy" -c user.email="deploy@localhost" commit -q -m "Deploy hospital demo"')
run('git push origin gh-pages')
fs.rmSync(work, {recursive: true, force: true})
