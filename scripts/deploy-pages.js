// Publish dist/ to the gh-pages branch (GitHub Pages source), without needing Actions.
const {execSync} = require('child_process')
const fs = require('fs')
const path = require('path')

const dist = path.join(__dirname, '..', 'dist')
const run = (cmd, cwd = dist) => execSync(cmd, {cwd, stdio: 'inherit'})
const remote = execSync('git remote get-url origin').toString().trim()

fs.writeFileSync(path.join(dist, '.nojekyll'), '')
fs.rmSync(path.join(dist, '.git'), {recursive: true, force: true})
run('git init -q -b gh-pages')
run('git add -A')
run('git -c user.name="deploy" -c user.email="deploy@localhost" commit -q -m "Deploy"')
run(`git push -f ${remote} gh-pages`)
fs.rmSync(path.join(dist, '.git'), {recursive: true, force: true})
