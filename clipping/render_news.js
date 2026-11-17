// Desenha o HTML do clipping para o WhatsApp com o MESMO motor da aba Reports da dashboard
// (reports-lib.js, window.IBBAReports.renderNews). Chamado por clipping/whatsapp.py.
//
//   node render_news.js <reports-lib.js> <model.json> <fonts.json> <saida.html>
//
// Sem dependências: o reports-lib.js é puro (só monta texto), então roda numa caixa de
// areia do `vm` com um `window` falso onde ele se pendura.
'use strict';
const fs = require('fs');
const vm = require('vm');

const [libPath, modelPath, fontsPath, outPath] = process.argv.slice(2);
if (!outPath) {
  console.error('uso: node render_news.js <reports-lib.js> <model.json> <fonts.json> <saida.html>');
  process.exit(64);
}
const sandbox = { console };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(libPath, 'utf8'), sandbox, { filename: 'reports-lib.js' });
const R = sandbox.IBBAReports;
if (!R || typeof R.renderNews !== 'function' || typeof R.newsDefaults !== 'function') {
  console.error('reports-lib.js sem renderNews/newsDefaults (versão antiga?)');
  process.exit(2);
}
const model = JSON.parse(fs.readFileSync(modelPath, 'utf8'));
const fonts = JSON.parse(fs.readFileSync(fontsPath, 'utf8'));
const html = R.renderNews(model, R.newsDefaults(model), fonts);
fs.writeFileSync(outPath, html, 'utf8');
