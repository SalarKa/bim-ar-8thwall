// Hospital AR demo build. Self-contained in hospital/ but reuses the parent repo's webpack
// toolchain (../node_modules) and its shared static files (../external: fonts, USM logo, 8frame).
// Output: hospital/dist, published under /hospital/ next to the original demo.
const fs = require('fs')
const path = require('path')
const HtmlWebpackPlugin = require('html-webpack-plugin')
const CopyWebpackPlugin = require('copy-webpack-plugin')

const rootPath = path.join(__dirname, '..')
const repoPath = path.join(rootPath, '..')
const distPath = path.join(rootPath, 'dist')
const srcPath = path.join(rootPath, 'src')
const shotsPath = path.join(rootPath, 'pipeline', 'out', 'shots')

module.exports = {
  entry: path.join(srcPath, 'app.js'),
  output: {
    filename: 'bundle.js',
    path: distPath,
    publicPath: 'auto',
    clean: true,
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: path.join(srcPath, 'index.html'),
      filename: 'index.html',
      inject: false,
    }),
    new CopyWebpackPlugin({
      patterns: [
        {from: path.join(repoPath, 'external'), to: path.join(distPath, 'external')},
        {from: path.join(rootPath, 'external'), to: path.join(distPath, 'external')},
        {from: path.join(srcPath, 'assets'), to: path.join(distPath, 'assets')},
        // the engine only needs the target JSON + luminance image; the full-size crops stay out of dist
        {from: path.join(rootPath, 'image-targets'), to: path.join(distPath, 'image-targets'), noErrorOnMissing: true,
          filter: p => !/_(original|cropped)\.png$/.test(p)},
      ],
    }),
  ],
  resolve: {extensions: ['.js'], modules: [path.join(repoPath, 'node_modules')]},
  resolveLoader: {modules: [path.join(repoPath, 'node_modules')]},
  module: {
    rules: [
      {
        test: /\.js$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: [[require.resolve('@babel/preset-env', {paths: [repoPath]}),
              {targets: 'defaults and supports es6-module'}]],
            sourceType: 'unambiguous',
            plugins: [require.resolve('@babel/plugin-transform-runtime', {paths: [repoPath]})],
          },
        },
      },
      {test: /\.css$/, use: ['style-loader', 'css-loader']},
    ],
  },
  mode: 'production',
  performance: {hints: false},
  context: srcPath,
  devServer: {
    open: false,
    compress: true,
    hot: false,
    liveReload: true,
    client: {overlay: {warnings: false, errors: true}},
    // Dev only: the poster script renders its comparison images from the live app.
    // POST /__shot?name=<file> stores a PNG data URL in pipeline/out/shots.
    setupMiddlewares: (middlewares, devServer) => {
      devServer.app.post('/__shot', (req, res) => {
        const name = String(req.query.name || 'shot').replace(/[^a-z0-9_-]/gi, '')
        let body = ''
        req.on('data', (c) => { body += c })
        req.on('end', () => {
          fs.mkdirSync(shotsPath, {recursive: true})
          const b64 = body.replace(/^data:image\/png;base64,/, '')
          fs.writeFileSync(path.join(shotsPath, `${name}.png`), Buffer.from(b64, 'base64'))
          res.end('ok')
        })
      })
      return middlewares
    },
  },
}
