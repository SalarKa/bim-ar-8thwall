// Reinforced CMU wall AR demo build. Self-contained in "Reinforced CMU Wall/" but reuses the parent
// repo's webpack toolchain (../node_modules) and its shared static files (../external: fonts, USM
// logo, 8frame). Output: dist/, published under /reinforced-cmu-wall/ next to the other demos.
const path = require('path')
const HtmlWebpackPlugin = require('html-webpack-plugin')
const CopyWebpackPlugin = require('copy-webpack-plugin')

const rootPath = path.join(__dirname, '..')
const repoPath = path.join(rootPath, '..')
const distPath = path.join(rootPath, 'dist')
const srcPath = path.join(rootPath, 'src')

module.exports = {
  entry: path.join(srcPath, 'app.js'),
  output: {filename: 'bundle.js', path: distPath, publicPath: 'auto', clean: true},
  plugins: [
    new HtmlWebpackPlugin({template: path.join(srcPath, 'index.html'), filename: 'index.html', inject: false}),
    new CopyWebpackPlugin({
      patterns: [
        {from: path.join(repoPath, 'external'), to: path.join(distPath, 'external')},
        {from: path.join(repoPath, 'hospital', 'external', 'scripts', 'meshopt_decoder.js'),
          to: path.join(distPath, 'external', 'scripts', 'meshopt_decoder.js')},
        {from: path.join(srcPath, 'assets'), to: path.join(distPath, 'assets')},
        // the engine only needs the target JSON + luminance image (the preview shows the original)
        {from: path.join(rootPath, 'image-targets'), to: path.join(distPath, 'image-targets'), noErrorOnMissing: true,
          filter: p => !/_cropped\.png$/.test(p)},
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
  devServer: {open: false, compress: true, hot: false, liveReload: true, client: {overlay: {warnings: false, errors: true}}},
}
