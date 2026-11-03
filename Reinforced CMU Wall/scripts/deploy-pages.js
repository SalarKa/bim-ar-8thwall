// Publish dist/ to /reinforced-cmu-wall/ on the gh-pages branch, next to the other demos.
// Like the hospital deploy, this replaces only its own folder and pushes a normal commit, so the
// original demo at the site root and /hospital/ stay exactly as they are.
const {execSync} = require('child_process')
const fs = require('fs')
const os = require('os')
const path = require('path')

const SUBDIR = 'reinforced-cmu-wall'
const dist = path.join(__dirname, '..', 'dist')
const remote = execSync('git remote get-url origin').toString().trim()
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gh-pages-'))
const run = cmd => execSync(cmd, {cwd: work, stdio: 'inherit'})

if (!fs.existsSync(path.join(dist, 'index.html'))) throw new Error('run npm run build first')
run(`git clone --quiet --depth 1 --branch gh-pages ${remote} .`)
fs.rmSync(path.join(work, SUBDIR), {recursive: true, force: true})
fs.cpSync(dist, path.join(work, SUBDIR), {recursive: true})
run(`git add -A ${SUBDIR}`)
run('git -c user.name="deploy" -c user.email="deploy@localhost" commit -q -m "Deploy reinforced CMU wall demo"')
run('git push origin gh-pages')
fs.rmSync(work, {recursive: true, force: true})
