/* ═══════════════════════════════════════════════════════════════════════════
   reports-lib.js — window.IBBAReports
   Monta os RELATÓRIOS EM HTML (para o blast de WhatsApp) dos dados mensais
   do IABr e da SECEX, no padrão visual do time S&M / P&P.

   PURO: não fala com o banco nem com a tela. Recebe os bancos SQLite já
   abertos (sql.js) + os textos do analista, devolve UM arquivo .html
   autossuficiente (fontes, gráficos e animações embutidos — abre offline,
   dentro do visualizador do WhatsApp, sem buscar nada na rede).

   Camadas:
     1. formatação e calendário (en-US: é o idioma dos relatórios)
     2. modelos de dados  — iabrModel(db, período) / secexModel(sm, pp, período)
     3. rascunhos         — o NÚMERO virando frase (o analista edita; não é IA)
     4. gráficos          — SVG + rótulos em HTML (nítidos em qualquer largura)
     5. documento         — o padrão S&M/P&P: cabeçalho com o emblema animado,
                            seções, rodapé com o time e os disclosures

   ⚠️ Regras que este arquivo segue (lições do CLAUDE.md):
     • número nunca é digitado à mão: tudo sai do banco a cada geração;
     • comparação sempre na MESMA base dos dois lados (penetração = razão das
       SOMAS, nunca média de razões; "Flat*" = planos + placas nas duas pontas);
     • sem JavaScript o relatório continua inteiro e legível (o JS só anima e
       dá interação) — o visualizador do WhatsApp pode bloquear script.
   ═══════════════════════════════════════════════════════════════════════════ */
(function (root) {
  'use strict';

  var R = {};

  // ═══════════════ 1. FORMATAÇÃO E CALENDÁRIO ═══════════════════════════════
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];
  var MINUS = '−';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function grp(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
  // número com casas fixas, milhar com vírgula, sinal de menos tipográfico
  function fmtNum(v, d) {
    if (!isNum(v)) return '—';
    d = d == null ? 0 : d;
    var s = Math.abs(v).toFixed(d), p = s.split('.');
    var out = grp(p[0]) + (p[1] ? '.' + p[1] : '');
    if (out.replace(/[0.,]/g, '') === '') return out;       // "-0" vira "0"
    return (v < 0 ? MINUS : '') + out;
  }
  function fmtInt(v) { return fmtNum(v, 0); }
  // variação: +3.1% / −0.7% (v é FRAÇÃO: 0.031)
  function fmtPct(v, d, sign) {
    if (!isNum(v)) return '—';
    d = d == null ? 1 : d;
    var x = v * 100, s = fmtNum(x, d);
    if (sign !== false && x > 0 && Number(Math.abs(x).toFixed(d)) !== 0) s = '+' + s;
    return s + '%';
  }
  // nível em %: 19.1% (sem sinal)
  function fmtShare(v, d) { return fmtPct(v, d == null ? 1 : d, false); }
  // pontos percentuais: +2.4 p.p.
  function fmtPP(v, d) {
    if (!isNum(v)) return '—';
    d = d == null ? 1 : d;
    var x = v * 100, s = fmtNum(x, d);
    if (x > 0 && Number(Math.abs(x).toFixed(d)) !== 0) s = '+' + s;
    return s + ' p.p.';
  }
  // "~6%" do texto corrido (arredonda para inteiro; abaixo de 1% vira 1 casa)
  function approxPct(v) {
    if (!isNum(v)) return '—';
    var a = Math.abs(v * 100);
    var s = a >= 1 ? String(Math.round(a)) : a.toFixed(1);
    return '~' + s + '%';
  }

  function pAdd(period, n) {               // '2026-09' + n meses
    var y = +period.slice(0, 4), m = +period.slice(5, 7) - 1 + n;
    y += Math.floor(m / 12); m = ((m % 12) + 12) % 12;
    return y + '-' + (m < 9 ? '0' : '') + (m + 1);
  }
  function pRange(a, b) { var o = []; for (var p = a; p <= b; p = pAdd(p, 1)) o.push(p); return o; }
  function monLabel(p) { return MON[+p.slice(5, 7) - 1] + '-' + p.slice(2, 4); }      // Sep-26
  function monLong(p) { return MONTH[+p.slice(5, 7) - 1] + ' ' + p.slice(0, 4); }    // September 2026
  function monName(p) { return MONTH[+p.slice(5, 7) - 1]; }                          // September
  function quarterOf(p) {
    var y = +p.slice(0, 4), m = +p.slice(5, 7), q = Math.ceil(m / 3);
    var first = y + '-' + ('0' + (q * 3 - 2)).slice(-2);
    return { y: y, q: q, label: q + 'Q' + String(y).slice(2), months: pRange(first, pAdd(first, 2)),
      end: m === q * 3 };
  }
  function prevQuarter(Q) {
    var y = Q.q === 1 ? Q.y - 1 : Q.y, q = Q.q === 1 ? 4 : Q.q - 1;
    return quarterOf(y + '-' + ('0' + (q * 3)).slice(-2));
  }
  function yearAgoQuarter(Q) { return quarterOf((Q.y - 1) + '-' + ('0' + (Q.q * 3)).slice(-2)); }
  function dateLong(ymd) {                 // '2026-10-16' → October 16, 2026
    if (!ymd) return '';
    var d = String(ymd).slice(0, 10).split('-');
    if (d.length < 3) return String(ymd);
    return MONTH[+d[1] - 1] + ' ' + (+d[2]) + ', ' + d[0];
  }

  R.esc = esc; R.fmtNum = fmtNum; R.fmtInt = fmtInt; R.fmtPct = fmtPct; R.fmtPP = fmtPP;
  R.fmtShare = fmtShare; R.approxPct = approxPct; R.pAdd = pAdd; R.pRange = pRange;
  R.monLabel = monLabel; R.monLong = monLong; R.quarterOf = quarterOf; R.dateLong = dateLong;

  // ═══════════════ 2a. ACESSO AO BANCO (sql.js) ═════════════════════════════
  function rows(db, sql, params) {
    var st = db.prepare(sql), out = [];
    try { if (params) st.bind(params); while (st.step()) out.push(st.getAsObject()); }
    finally { st.free(); }
    return out;
  }
  function hasTable(db, name) {
    return rows(db, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name]).length > 0;
  }
  R.rows = rows; R.hasTable = hasTable;

  // soma de uma série {período: valor} num conjunto de meses — null se FALTAR
  // qualquer mês (soma parcial fingindo trimestre cheio é pior que travessão)
  function sumOver(map, months) {
    var s = 0;
    for (var i = 0; i < months.length; i++) {
      var v = map[months[i]];
      if (!isNum(v)) return null;
      s += v;
    }
    return s;
  }
  function chg(a, b) { return (isNum(a) && isNum(b) && b !== 0) ? a / b - 1 : null; }
  function diff(a, b) { return (isNum(a) && isNum(b)) ? a - b : null; }

  // ═══════════════ 2b. MODELO DO IABr ═══════════════════════════════════════
  // Convenções = as do relatório que o time manda (e as da página de M&M):
  //   Flat* = planos + placas · Long** = longos + blocos/tarugos (mesma base do
  //   consumo aparente do IABr, que "inclui placas"). Produção total¹ = laminados
  //   + semiacabados p/ venda. Penetração = (consumo aparente − vendas internas)
  //   ÷ consumo aparente — é a importação QUE ENTRA no consumo do IABr (bate
  //   com o relatório: 19,1% em set/26; importação/CA daria 26%, outra coisa).
  //   Importação e exportação: como o IABr publica (planos/longos/semi).
  var IABR_METRICS = [
    { k: 'dom', label: 'Domestic sales', lvl: 0 },
    { k: 'dom_flat', label: 'Flat steel*', lvl: 1 },
    { k: 'dom_long', label: 'Long steel**', lvl: 1 },
    { k: 'imp', label: 'Imports', lvl: 0 },
    { k: 'imp_flat', label: 'Flat steel', lvl: 1 },
    { k: 'imp_long', label: 'Long steel', lvl: 1 },
    { k: 'imp_semi', label: 'Semi-finished', lvl: 1 },
    { k: 'exp', label: 'Exports', lvl: 0 },
    { k: 'exp_flat', label: 'Flat steel', lvl: 1 },
    { k: 'exp_long', label: 'Long steel', lvl: 1 },
    { k: 'exp_semi', label: 'Semi-finished', lvl: 1 },
    { k: 'prod', label: 'Total steel production¹', lvl: 0 },
    { k: 'prod_flat', label: 'Flat steel*', lvl: 1 },
    { k: 'prod_long', label: 'Long steel**', lvl: 1 },
    { k: 'rolled', label: 'Rolled steel production', lvl: 0 },
    { k: 'crude', label: 'Crude steel production', lvl: 0 },
    { k: 'ca', label: 'Apparent consumption²', lvl: 0 },
    { k: 'ca_flat', label: 'Flat steel*', lvl: 1 },
    { k: 'ca_long', label: 'Long steel**', lvl: 1 },
    { k: 'pen', label: 'Import penetration³', lvl: 0, ratio: ['ca', 'dom'] },
    { k: 'pen_flat', label: 'Flat steel*', lvl: 1, ratio: ['ca_flat', 'dom_flat'] },
    { k: 'pen_long', label: 'Long steel**', lvl: 1, ratio: ['ca_long', 'dom_long'] }
  ];
  R.IABR_METRICS = IABR_METRICS;

  function iabrSeries(db) {
    var S = {};
    IABR_METRICS.forEach(function (m) { if (!m.ratio) S[m.k] = {}; });
    function put(k, p, v) { if (isNum(v)) S[k][p] = v; }
    function add2(a, b) { return (isNum(a) || isNum(b)) ? (a || 0) + (b || 0) : null; }
    rows(db, 'SELECT period,total,flat,long_prod,slabs,billets FROM iabr_domestic_sales').forEach(function (r) {
      put('dom', r.period, r.total);
      put('dom_flat', r.period, add2(r.flat, r.slabs));
      put('dom_long', r.period, add2(r.long_prod, r.billets));
    });
    rows(db, 'SELECT period,flat_ktons,long_ktons,semi_ktons,total_ktons FROM iabr_imports').forEach(function (r) {
      put('imp', r.period, r.total_ktons); put('imp_flat', r.period, r.flat_ktons);
      put('imp_long', r.period, r.long_ktons); put('imp_semi', r.period, r.semi_ktons);
    });
    rows(db, 'SELECT period,flat_ktons,long_ktons,semi_ktons,total_ktons FROM iabr_exports').forEach(function (r) {
      put('exp', r.period, r.total_ktons); put('exp_flat', r.period, r.flat_ktons);
      put('exp_long', r.period, r.long_ktons); put('exp_semi', r.period, r.semi_ktons);
    });
    rows(db, 'SELECT period,crude_steel,flat,long_prod,semi,slabs,billets FROM iabr_production').forEach(function (r) {
      var tot = (isNum(r.flat) && isNum(r.long_prod) && isNum(r.semi)) ? r.flat + r.long_prod + r.semi : null;
      put('prod', r.period, tot);
      put('prod_flat', r.period, add2(r.flat, r.slabs));
      put('prod_long', r.period, add2(r.long_prod, r.billets));
      put('rolled', r.period, (isNum(r.flat) && isNum(r.long_prod)) ? r.flat + r.long_prod : null);
      put('crude', r.period, r.crude_steel);
    });
    rows(db, 'SELECT period,total,flat,long_prod FROM iabr_consumption').forEach(function (r) {
      put('ca', r.period, r.total); put('ca_flat', r.period, r.flat); put('ca_long', r.period, r.long_prod);
    });
    return S;
  }
  function penOf(S, ca, dom, months) {
    var c = sumOver(S[ca], months), d = sumOver(S[dom], months);
    return (isNum(c) && isNum(d) && c > 0) ? (c - d) / c : null;
  }

  // períodos com o MÊS COMPLETO publicado (consumo + vendas + importação)
  R.iabrPeriods = function (db) {
    return rows(db, 'SELECT c.period FROM iabr_consumption c JOIN iabr_domestic_sales d USING(period) ' +
      'JOIN iabr_imports i USING(period) WHERE c.total IS NOT NULL AND d.total IS NOT NULL ' +
      'AND i.total_ktons IS NOT NULL ORDER BY c.period DESC').map(function (r) { return r.period; });
  };

  R.iabrModel = function (db, period) {
    var S = iabrSeries(db);
    var Q = quarterOf(period), Qp = prevQuarter(Q), Qy = yearAgoQuarter(Q);
    var y = +period.slice(0, 4), m = +period.slice(5, 7);
    var ytd = pRange(y + '-01', period), ytd1 = pRange((y - 1) + '-01', pAdd(period, -12));
    // trimestre: no fim do trimestre é o trimestre cheio; fora dele, o trimestre
    // até agora (QTD) contra o MESMO pedaço do ano anterior — nunca um trimestre
    // parcial contra um cheio.
    var qtdMonths = Q.months.filter(function (p) { return p <= period; });
    var qtdY1 = qtdMonths.map(function (p) { return pAdd(p, -12); });
    var cols = {
      m: [period], m1: [pAdd(period, -1)], y1: [pAdd(period, -12)],
      ytd: ytd, ytd1: ytd1,
      q: Q.end ? Q.months : qtdMonths, qp: Q.end ? Qp.months : null, qy: Q.end ? Qy.months : qtdY1
    };
    var lines = IABR_METRICS.map(function (mt) {
      var v = {};
      Object.keys(cols).forEach(function (c) {
        if (!cols[c]) { v[c] = null; return; }
        v[c] = mt.ratio ? penOf(S, mt.ratio[0], mt.ratio[1], cols[c]) : sumOver(S[mt.k], cols[c]);
      });
      var d = mt.ratio ? diff : chg;
      return { k: mt.k, label: mt.label, lvl: mt.lvl, ratio: !!mt.ratio, v: v,
        d: { mom: d(v.m, v.m1), yoy: d(v.m, v.y1), ytd: d(v.ytd, v.ytd1), qoq: d(v.q, v.qp), qyoy: d(v.q, v.qy) } };
    });
    var byK = {}; lines.forEach(function (l) { byK[l.k] = l; });

    // séries mensais (7 anos = o mesmo recorte do relatório, set/19 → set/26)
    var hist = pRange(pAdd(period, -84), period);
    function ser(k) { return hist.map(function (p) { var x = S[k][p]; return isNum(x) ? x : null; }); }
    function penSer(ca, dom) {
      return hist.map(function (p) { return penOf(S, ca, dom, [p]); });
    }
    var series = { x: hist };
    ['dom', 'dom_flat', 'dom_long', 'imp', 'imp_flat', 'imp_long', 'imp_semi', 'exp', 'exp_flat', 'exp_long',
      'exp_semi', 'ca', 'ca_flat', 'ca_long', 'crude'].forEach(function (k) { series[k] = ser(k); });
    series.pen = penSer('ca', 'dom'); series.pen_flat = penSer('ca_flat', 'dom_flat');
    series.pen_long = penSer('ca_long', 'dom_long');

    // sazonalidade: 5 anos, jan→dez, um traço por ano
    var years = []; for (var yy = y - 4; yy <= y; yy++) years.push(yy);
    var seasonal = {};
    ['dom_flat', 'dom_long', 'imp_flat', 'imp_long', 'exp_flat', 'exp_long', 'ca_flat', 'ca_long'].forEach(function (k) {
      seasonal[k] = years.map(function (yr) {
        return { year: yr, v: MON.map(function (_, i) {
          var p = yr + '-' + ('0' + (i + 1)).slice(-2);
          if (p > period) return null;
          var x = S[k][p]; return isNum(x) ? x : null;
        }) };
      });
    });

    // KPIs (destaques do topo) com o mini-histórico de 13 meses
    var spark = pRange(pAdd(period, -12), period);
    function sp(k) { return spark.map(function (p) { var x = k.indexOf('pen') === 0 ? null : S[k][p]; return isNum(x) ? x : null; }); }
    function spPen(ca, dom) { return spark.map(function (p) { return penOf(S, ca, dom, [p]); }); }
    var kpis = [
      { k: 'ca', label: 'Apparent consumption', unit: 'kt', spark: sp('ca') },
      { k: 'dom', label: 'Domestic sales', unit: 'kt', spark: sp('dom') },
      { k: 'imp', label: 'Imports', unit: 'kt', spark: sp('imp') },
      { k: 'pen', label: 'Import penetration', unit: '%', spark: spPen('ca', 'dom') }
    ].map(function (x) {
      var l = byK[x.k];
      return { k: x.k, label: x.label, unit: x.unit, value: l.v.m, ratio: l.ratio, spark: x.spark, sparkX: spark,
        when: monLabel(period),
        d1: { lbl: 'MoM', v: l.d.mom }, d2: { lbl: 'YoY', v: l.d.yoy },
        dq: Q.end ? { lbl: 'QoQ', v: l.d.qoq, q: l.v.q, ql: Q.label } : null };
    });

    var missing = [];
    [['prod', 'production'], ['exp', 'exports'], ['crude', 'crude steel']].forEach(function (x) { if (!isNum(S[x[0]][period])) missing.push('IABr ' + x[1]); });
    return {
      kind: 'iabr', missing: missing, period: period, label: monLabel(period), long: monLong(period), monthName: monName(period),
      quarter: { label: Q.label, end: Q.end, prev: Qp.label, yago: Qy.label, months: Q.months,
        qtdLabel: Q.label + ' QTD' },
      cols: {
        m: monLabel(period), m1: monLabel(pAdd(period, -1)), y1: monLabel(pAdd(period, -12)),
        ytd: 'YTD’' + String(y).slice(2), ytd1: 'YTD’' + String(y - 1).slice(2),
        q: Q.end ? Q.label : Q.label + ' QTD', qp: Q.end ? Qp.label : null,
        qy: Q.end ? Qy.label : Qy.label + ' QTD'
      },
      lines: lines, byK: byK, series: series, seasonal: seasonal, years: years, kpis: kpis,
      ytdMonths: m
    };
  };

  // ═══════════════ 3a. RASCUNHO DO IABr (número → frase) ════════════════════
  // Não é IA: é a frase-modelo que o analista já escreve, com o número certo.
  // O "Our take" nasce vazio de propósito — opinião é do analista.
  function verbUpDown(v, up, down, flat) {
    if (!isNum(v)) return flat || 'was unchanged';
    if (Math.abs(v) < 0.005) return flat || 'remained broadly stable';
    return v > 0 ? up : down;
  }
  function signedApprox(v) {
    if (!isNum(v)) return '—';
    var a = Math.abs(v * 100), s = a >= 1 ? String(Math.round(a)) : a.toFixed(1);
    return (v > 0 ? '+' : v < 0 ? MINUS : '') + s + '%';
  }
  function ppApprox(v) {
    if (!isNum(v)) return '—';
    return Math.abs(v * 100).toFixed(1) + ' p.p.';
  }
  R.iabrDraft = function (M) {
    var B = M.byK, q = M.quarter.end, per = q ? M.quarter.label : M.monthName;
    var cmpLbl = q ? 'QoQ' : 'MoM';
    function dd(k) { return q ? B[k].d.qoq : B[k].d.mom; }
    function lvl(k) { return q ? B[k].v.q : B[k].v.m; }
    var items = [];
    // 1) What's new?
    var ca = dd('ca'), ds = dd('dom'), im = dd('imp');
    var whatsNew = 'The Brazilian Steel Institute (IABr) released industry data for ' + M.monthName +
      (q ? ' and ' + M.quarter.label : ' ' + M.period.slice(0, 4)) + '. ' +
      (q ? 'The quarter' : 'The month') + ' showed apparent consumption ' +
      verbUpDown(ca, 'up', 'down', 'stable') + ' ' + approxPct(ca) + ' ' + cmpLbl +
      ' and domestic sales ' + verbUpDown(ds, 'up', 'down', 'stable') + ' ' + approxPct(ds) + ' ' + cmpLbl +
      ', while steel imports ' + verbUpDown(im, 'rose', 'fell', 'were stable') + ' ' + approxPct(im) + ' ' + cmpLbl +
      ' to ~' + fmtInt(Math.round((lvl('imp') || 0) / 10) * 10) + ' ktons.';
    items.push({ lead: 'What’s new?', text: whatsNew, lvl: 0 });
    // 2/3) Long e Flat
    [['long', 'Long steel'], ['flat', 'Flat steel']].forEach(function (f) {
      var dk = 'dom_' + f[0], ik = 'imp_' + f[0], ck = 'ca_' + f[0], pk = 'pen_' + f[0];
      var dD = dd(dk), dI = dd(ik);
      var lead = (isNum(dD) && isNum(dI) && dD >= dI)
        ? 'Domestic sales outperformed imports in ' + per
        : 'Imports outpaced domestic sales in ' + per;
      var penNow = q ? B[pk].v.q : B[pk].v.m, penPrev = q ? B[pk].v.qp : B[pk].v.m1;
      var penDir = (isNum(penNow) && isNum(penPrev)) ? (penNow > penPrev ? 'increasing' : penNow < penPrev ? 'declining' : 'stable') : 'at';
      var t = '(' + signedApprox(dD) + ' vs. ' + signedApprox(dI) + ' ' + cmpLbl + '), with import penetration ' +
        penDir + ' to ' + approxPct(penNow).replace('~', '~') + ' of apparent consumption (vs. ' +
        approxPct(penPrev) + ' in ' + (q ? M.quarter.prev : M.cols.m1) + ').';
      if (q) {
        var mi = B[ik].d.mom, md = B[dk].d.mom, mc = B[ck].d.mom;
        t += ' In ' + M.monthName + ', imports ' + verbUpDown(mi, 'increased ' + approxPct(mi), 'declined ' + approxPct(mi), 'were stable') +
          ' MoM, while domestic sales ' + verbUpDown(md, 'increased ' + approxPct(md), 'decreased ' + approxPct(md), 'remained broadly stable') +
          (Math.abs(md || 0) < 0.005 ? '' : ' MoM') + ' and apparent consumption ' +
          verbUpDown(mc, 'increased ' + approxPct(mc), 'fell ' + approxPct(mc), 'was flat') + (Math.abs(mc || 0) < 0.005 ? '' : ' MoM') + '.';
      }
      items.push({ lead: f[1] + ': ' + lead, text: t, lvl: 1 });
    });
    items.push({ lead: 'Our take:', text: '', lvl: 0 });
    return items;
  };
  // manchete do topo (o "deck"): uma frase com os 3 números que mais pesam
  R.iabrDeck = function (M) {
    var B = M.byK, q = M.quarter.end;
    var im = q ? B.imp.d.qoq : B.imp.d.mom, pen = q ? B.pen.v.q : B.pen.v.m;
    var penD = q ? B.pen.d.qoq : B.pen.d.mom, ca = q ? B.ca.d.qoq : B.ca.d.mom;
    var lbl = q ? 'QoQ' : 'MoM', per = q ? M.quarter.label : M.monthName;
    return 'Steel imports ' + (im >= 0 ? 'rose ' : 'fell ') + approxPct(im).replace('~', '') + ' ' + lbl + ' in ' + per +
      ', taking import penetration ' + (penD >= 0 ? 'up ' : 'down ') + ppApprox(penD) + ' to ' + fmtShare(pen) +
      ', while apparent consumption ' + (ca >= 0 ? 'grew ' : 'declined ') + approxPct(ca).replace('~', '') + '.';
  };

  // ═══════════════ 4. GRÁFICOS ══════════════════════════════════════════════
  // Desenho = SVG esticado na área do gráfico (preserveAspectRatio="none", traço
  // que não escala) + rótulos em HTML por cima, posicionados em %. Assim o texto
  // fica sempre do tamanho certo, do celular de 360px ao monitor, sem distorcer.
  var VBW = 1000, VBH = 400;

  function niceStep(span, n) {
    var raw = span / Math.max(n, 1), mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    var f = raw / mag, nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return nf * mag;
  }
  function niceScale(lo, hi, n, zero) {
    if (!isNum(lo) || !isNum(hi)) { lo = 0; hi = 1; }
    if (zero) lo = Math.min(0, lo);
    if (hi === lo) hi = lo + 1;
    var st = niceStep(hi - lo, n || 4);
    var a = Math.floor(lo / st) * st, b = Math.ceil(hi / st) * st;
    var ticks = []; for (var t = a; t <= b + st / 2; t += st) ticks.push(+t.toFixed(10));
    return { min: a, max: b, ticks: ticks };
  }
  function extent(arrs) {
    var lo = Infinity, hi = -Infinity;
    arrs.forEach(function (a) { a.forEach(function (v) { if (isNum(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }); });
    return lo === Infinity ? [0, 1] : [lo, hi];
  }
  function fmtTick(v, f, dec) {
    if (f === 'pct') return fmtNum(v * 100, dec || 0) + '%';
    if (dec) return fmtNum(v, dec);
    if (f === 'usd') return fmtNum(v, 0);
    if (Math.abs(v) >= 10000) return fmtNum(v / 1000, 0) + 'k';
    return fmtNum(v, Math.abs(v) < 10 && v % 1 ? 1 : 0);
  }
  function xLabel(x, mode) {
    if (mode === 'q' || mode === 'raw') return x;
    if (mode === 'mon') return MON[+x.slice(5, 7) - 1];
    return monLabel(x);
  }
  // pontos (em coordenadas do viewBox) — null vira quebra de linha
  function linePath(vals, sc, n, x0) {
    var d = '', pen = false;
    for (var i = 0; i < vals.length; i++) {
      var v = vals[i];
      if (!isNum(v)) { pen = false; continue; }
      var X = xPos(i, n, x0), Y = yPos(v, sc);
      d += (pen ? 'L' : 'M') + X.toFixed(1) + ' ' + Y.toFixed(1);
      pen = true;
    }
    return d;
  }
  function areaPath(top, base, sc, n, x0) {    // base = array (empilhado) ou null (zero)
    var seg = [], cur = [];
    for (var i = 0; i < top.length; i++) {
      if (!isNum(top[i])) { if (cur.length) seg.push(cur); cur = []; continue; }
      cur.push(i);
    }
    if (cur.length) seg.push(cur);
    return seg.map(function (ix) {
      var up = ix.map(function (i) { return xPos(i, n, x0).toFixed(1) + ' ' + yPos(top[i], sc).toFixed(1); });
      var dn = ix.slice().reverse().map(function (i) {
        var b = base ? (isNum(base[i]) ? base[i] : 0) : Math.max(sc.min, 0);
        return xPos(i, n, x0).toFixed(1) + ' ' + yPos(b, sc).toFixed(1);
      });
      return 'M' + up.join('L') + 'L' + dn.join('L') + 'Z';
    }).join('');
  }
  function xPos(i, n, x0) { return x0 ? x0 + (i + 0.5) * (VBW / n) - x0 : (n <= 1 ? VBW / 2 : i * VBW / (n - 1)); }
  function yPos(v, sc) { return VBH - (v - sc.min) / (sc.max - sc.min) * VBH; }
  function xPct(i, n, band) { return band ? (i + 0.5) / n * 100 : (n <= 1 ? 50 : i / (n - 1) * 100); }

  // escolhe ~k rótulos de eixo X igualmente espaçados, sempre incluindo o último
  function xTicks(n, k) {
    if (n <= k) { var a = []; for (var i = 0; i < n; i++) a.push(i); return a; }
    var step = Math.ceil((n - 1) / (k - 1)), out = [];
    for (var j = n - 1; j >= 0; j -= step) out.unshift(j);
    return out;
  }

  var _chartSeq = 0;
  /* spec = {
       title, sub, unit, x:[...], xMode:'my'|'mon'|'q'|'raw', band:bool (barras),
       series:[{name, type:'line'|'area'|'bar', axis:'l'|'r', cls:'o'|'f'|'g1'|'g2'|'g3'|'y0'..,
                values:[...], fmt:'int'|'pct'|'usd', dash:bool, w:number}],
       stack:bool (barras/áreas empilhadas na ordem dada), yl:{fmt, zero}, yr:{fmt, zero},
       height, legend:true, hl:[índices destacados], note, ticks:k
     } */
  function chartHTML(spec) {
    var id = 'c' + (++_chartSeq), n = spec.x.length, band = !!spec.band;
    var L = spec.series.filter(function (s) { return s.axis !== 'r'; });
    var Rr = spec.series.filter(function (s) { return s.axis === 'r'; });
    var stackTops = null;
    if (spec.stack) {                         // empilha na ordem dada
      var acc = spec.x.map(function () { return 0; });
      stackTops = L.map(function (s) {
        var base = acc.slice();
        acc = acc.map(function (a, i) { return a + (isNum(s.values[i]) ? s.values[i] : 0); });
        return { base: base, top: acc.slice() };
      });
    }
    var yl = spec.yl || {}, yr = spec.yr || {};
    var eL = stackTops ? extent([stackTops[stackTops.length - 1].top]) : extent(L.map(function (s) { return s.values; }));
    var scL = niceScale(yl.zero === false ? eL[0] : Math.min(0, eL[0]), eL[1], yl.n || 4, yl.zero !== false);
    if (yl.zero === false) scL = niceScale(eL[0] - (eL[1] - eL[0]) * 0.08, eL[1] + (eL[1] - eL[0]) * 0.04, yl.n || 4, false);
    var scR = null;
    if (Rr.length) {
      var eR = extent(Rr.map(function (s) { return s.values; }));
      scR = yr.zero === false
        ? niceScale(eR[0] - (eR[1] - eR[0]) * 0.1, eR[1] + (eR[1] - eR[0]) * 0.1, yr.n || 4, false)
        : niceScale(0, eR[1] * 1.04, yr.n || 4, true);
    }
    var x0 = band ? 1 : 0;   // em barras, cada ponto ocupa uma "faixa"
    var g = '';
    // grade
    scL.ticks.forEach(function (t) {
      var Y = yPos(t, scL).toFixed(1);
      g += '<line class="gl' + (t === 0 ? ' z' : '') + '" x1="0" x2="' + VBW + '" y1="' + Y + '" y2="' + Y + '"/>';
    });
    var marks = '';
    // barras (agrupadas lado a lado ou empilhadas)
    var bars = L.filter(function (s) { return s.type === 'bar'; });
    if (bars.length) {
      var bw = VBW / n, inner = bw * (spec.barW || 0.62);
      bars.forEach(function (s, bi) {
        var si = L.indexOf(s);
        for (var i = 0; i < n; i++) {
          var v = s.values[i]; if (!isNum(v)) continue;
          var x, w, y0, y1;
          if (spec.stack) {
            w = inner; x = i * bw + (bw - w) / 2;
            y0 = stackTops[si].base[i]; y1 = stackTops[si].top[i];
          } else {
            w = inner / bars.length; x = i * bw + (bw - inner) / 2 + bi * w;
            y0 = 0; y1 = v;
          }
          var Ya = yPos(Math.max(y0, y1), scL), Yb = yPos(Math.min(y0, y1), scL);
          var isHl = spec.hl && spec.hl.indexOf(i) >= 0;
          marks += '<rect class="b ' + (isHl ? (spec.hlCls || 'o') : (s.cls || 'f')) + '" style="--i:' + i + '" x="' + x.toFixed(1) + '" y="' + Ya.toFixed(1) +
            '" width="' + w.toFixed(1) + '" height="' + Math.max(Yb - Ya, 0.6).toFixed(1) + '"/>';
        }
      });
    }
    // áreas
    L.forEach(function (s, si) {
      if (s.type !== 'area') return;
      var top = stackTops ? stackTops[si].top : s.values, base = stackTops ? stackTops[si].base : null;
      marks += '<path class="a ' + (s.cls || 'f') + '" d="' + areaPath(top, base, scL, n, x0) + '"/>';
      marks += '<path class="ln ' + (s.cls || 'f') + '" style="stroke-width:' + (s.w || 1.3) + 'px" d="' + linePath(top, scL, n, x0) + '"/>';
    });
    // linhas (eixo esquerdo e direito)
    spec.series.forEach(function (s) {
      if (s.type !== 'line') return;
      var sc = s.axis === 'r' ? scR : scL;
      marks += '<path class="ln ' + (s.cls || 'o') + (s.dash ? ' dash' : '') + '" style="stroke-width:' + (s.w || 2) + 'px" d="' +
        linePath(s.values, sc, n, x0) + '"/>';
    });
    // rótulos — casas decimais pelo PASSO do eixo (17,5% não vira "18%")
    function tickDec(ticks, f) {
      var m = f === 'pct' ? 100 : 1;
      return ticks.some(function (t) { return Math.abs(t * m - Math.round(t * m)) > 1e-9; }) ? 1 : 0;
    }
    var dL = tickDec(scL.ticks, yl.fmt), dR = scR ? tickDec(scR.ticks, yr.fmt) : 0;
    var yLab = scL.ticks.map(function (t) {
      return '<span style="top:' + ((1 - (t - scL.min) / (scL.max - scL.min)) * 100).toFixed(2) + '%">' + esc(fmtTick(t, yl.fmt, dL)) + '</span>';
    }).join('');
    var yLabR = scR ? scR.ticks.map(function (t) {
      return '<span style="top:' + ((1 - (t - scR.min) / (scR.max - scR.min)) * 100).toFixed(2) + '%">' + esc(fmtTick(t, yr.fmt, dR)) + '</span>';
    }).join('') : '';
    var xt = xTicks(n, spec.ticks || 7);
    var xLab = xt.map(function (i, j) {
      var ec = spec.mini ? (i === n - 1 ? ' class="e1"' : j === 0 ? ' class="e0"' : '') : '';
      return '<span' + ec + ' style="left:' + xPct(i, n, band).toFixed(2) + '%">' + esc(xLabel(spec.x[i], spec.xMode)) + '</span>';
    }).join('');
    // ponto no último valor (gráfico dos destaques)
    var dot = '';
    if (spec.dot) {
      var s0 = spec.series[0], sc0 = s0.axis === 'r' ? scR : scL;
      for (var di = s0.values.length - 1; di >= 0; di--) {
        if (isNum(s0.values[di])) {
          dot = '<i class="dot" style="left:' + xPct(di, n, band).toFixed(2) + '%;top:' +
            ((1 - (s0.values[di] - sc0.min) / (sc0.max - sc0.min)) * 100).toFixed(2) + '%"></i>';
          break;
        }
      }
    }
    // legenda
    var leg = (spec.legend === false || spec.mini) ? '' : '<div class="lg">' + spec.series.map(function (s) {
      return '<span><i class="k ' + (s.type === 'line' ? 'kl ' : 'kb ') + (s.cls || 'f') + (s.type === 'area' && (s.cls || 'f') === 'f' ? ' a-f' : '') + (s.dash ? ' dash' : '') + '"></i>' + esc(s.name) +
        (s.axis === 'r' ? ' <em>(rhs)</em>' : '') + '</span>';
    }).join('') + '</div>';
    // dados para a dica (tooltip) — números já arredondados
    var data = {
      x: spec.x.map(function (x) { return xLabel(x, spec.xMode === 'mon' ? 'mon' : spec.xMode); }),
      band: band,
      s: spec.series.map(function (s) {
        return { n: s.name, c: s.cls || (s.type === 'line' ? 'o' : 'f'), f: s.fmt || (s.axis === 'r' ? yr.fmt : yl.fmt) || 'int',
          v: s.values.map(function (v) { return isNum(v) ? +v.toFixed(s.fmt === 'pct' || (s.axis === 'r' && yr.fmt === 'pct') ? 5 : 2) : null; }) };
      }),
      tot: spec.stack && spec.total ? spec.total : null
    };
    var h = spec.height || 250;
    return '<figure class="ch' + (band ? ' band' : '') + (scR ? ' dual' : '') + (spec.mini ? ' mini' : '') + '" id="' + id + '">' +
      (spec.title && !spec.mini ? '<figcaption><h3>' + esc(spec.title) + '</h3>' + (spec.sub ? '<p>' + esc(spec.sub) + '</p>' : '') + '</figcaption>' : '') +
      leg +
      '<div class="plot" style="--h:' + h + 'px">' +
      '<div class="yl">' + yLab + '</div>' + (scR ? '<div class="yr">' + yLabR + '</div>' : '') +
      '<div class="pa"><svg viewBox="0 0 ' + VBW + ' ' + VBH + '" preserveAspectRatio="none" aria-hidden="true">' + g + marks + '</svg>' + dot +
      '<i class="cur" aria-hidden="true"></i><i class="rule" aria-hidden="true"></i><div class="tip" aria-hidden="true"></div></div>' +
      '<div class="xl">' + xLab + '</div>' +
      '</div>' +
      (spec.note ? '<p class="cnote">' + esc(spec.note) + '</p>' : '') +
      '<script type="application/json" class="chd">' + JSON.stringify(data).replace(/</g, '\\u003c') + '</script>' +
      '</figure>';
  }
  R.chartHTML = chartHTML;

  // mini-histórico do KPI (SVG fixo, sem eixo)
  function sparkSVG(vals) {
    var e = extent([vals]), lo = e[0], hi = e[1];
    if (hi === lo) { hi = lo + 1; }
    var n = vals.length, d = '', pen = false, last = null;
    vals.forEach(function (v, i) {
      if (!isNum(v)) { pen = false; return; }
      var X = n <= 1 ? 120 : i / (n - 1) * 240, Y = 34 - (v - lo) / (hi - lo) * 30;
      d += (pen ? 'L' : 'M') + X.toFixed(1) + ' ' + Y.toFixed(1); pen = true; last = [X, Y];
    });
    return '<svg class="spk" viewBox="-3 0 246 38" preserveAspectRatio="xMinYMid meet" aria-hidden="true"><path pathLength="1" d="' + d + '"/>' +
      (last ? '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="2.6"/>' : '') + '</svg>';
  }

  // ═══════════════ 5. FORMAS (os traços do Itaú) ════════════════════════════
  // Contorno externo do "itaú" (o mesmo quadrado-arredondado do logo oficial,
  // do pacote do portal) — centralizado na origem, raio ~16,33.
  var ITAU_OUTER = 'M31.5426 4.91115C31.2124 3.28577 30.2727 2.21911 28.4442 1.81277C25.4982 1.07627 21.1046 0.669922 16.3554 0.669922C11.6062 0.669922 7.21263 1.07627 4.26662 1.81277C2.43807 2.21911 1.4984 3.28577 1.14285 4.91115C0.482535 7.73017 0 12.2508 0 16.9999C0 21.7491 0.482535 26.2697 1.14285 29.0887C1.473 30.7141 2.41268 31.7807 4.24123 32.1871C7.18723 32.9236 11.5808 33.3299 16.33 33.3299C21.0792 33.3299 25.4728 32.9236 28.4188 32.1871C30.2219 31.7807 31.187 30.7141 31.5172 29.0887C32.1775 26.2697 32.66 21.7491 32.66 16.9999C32.66 12.2508 32.2029 7.73017 31.5426 4.91115Z';
  // logo completo (quadrado com as letras vazadas + o miolo do "a")
  var ITAU_LOGO = ITAU_OUTER.slice(0, -1) + 'ZM23.5426 15.6539L27.5299 14.9428V17.0253H23.5426V15.6539ZM6.01899 26.3205H3.63171V18.1174H6.01899V26.3205ZM4.82535 17.2793C3.98726 17.2793 3.40314 16.7206 3.40314 15.9079C3.40314 15.0952 3.98726 14.5365 4.82535 14.5365C5.66344 14.5365 6.24756 15.0952 6.24756 15.9079C6.24756 16.7206 5.66344 17.2793 4.82535 17.2793ZM10.5396 19.9967V23.3491C10.5396 24.2126 10.8951 24.5935 11.657 24.5935C12.0126 24.5935 12.3427 24.5427 12.5205 24.4919V26.3205C12.2412 26.3967 11.7078 26.4728 11.0475 26.4728C9.19356 26.4728 8.1523 25.4824 8.1523 23.5014V19.9967H6.95866V18.1174H8.1523V16.0856L10.5396 15.6539V18.1174H12.6221V19.9967H10.5396ZM20.5966 26.3205H18.3617L18.3363 25.457C17.8538 26.1173 17.1427 26.5236 16.203 26.5236C14.4761 26.5236 13.384 25.5586 13.384 24.0094C13.384 22.2316 14.984 21.1904 17.4982 21.1904C17.803 21.1904 18.1078 21.2158 18.3109 21.2158C18.3109 20.3269 17.7014 19.8189 16.584 19.8189C15.6951 19.8189 14.7554 20.0475 13.8157 20.4031V18.4729C14.7808 18.1174 15.7713 17.9142 16.9649 17.9142C19.3014 17.9142 20.5966 19.1586 20.5966 21.4951V26.3205ZM29.0791 26.3205H26.768L26.7426 25.4316C26.2093 26.1681 25.5236 26.5236 24.5331 26.5236C22.8569 26.5236 21.7649 25.33 21.7649 23.4507V18.1174H24.1521V23.0443C24.1521 24.0348 24.5839 24.5681 25.3712 24.5681C26.1839 24.5681 26.6664 23.9332 26.6664 22.9427V18.1174H29.0537V26.3205H29.0791Z';
  var ITAU_BOWL = 'M15.6951 23.8062C15.6951 24.4665 16.1522 24.7967 16.8887 24.7967C17.7268 24.7967 18.3109 24.1872 18.3109 23.3237V22.6888C18.2093 22.6634 17.9046 22.6634 17.7014 22.6634C16.4062 22.6634 15.6951 23.0443 15.6951 23.8062Z';
  // o contorno, escalado e centrado em (cx,cy) com "raio" r — números já
  // transformados (sem `transform` de escala → o traço fica com a mesma espessura)
  function itauShape(cx, cy, r, sx, sy) {
    var k = r / 16.33; sx = sx || 1; sy = sy || 1;
    var i = 0;
    return ITAU_OUTER.replace(/-?\d+(\.\d+)?/g, function (num) {
      var v = parseFloat(num), isX = (i++ % 2) === 0;
      return (isX ? cx + (v - 16.33) * k * sx : cy + (v - 17.0) * k * sy).toFixed(1);
    });
  }
  R.itauShape = itauShape;

  // ═══════════════ 6. TEXTO RICO DO ANALISTA (sanitizado) ═══════════════════
  // Aceita só b/strong/i/em/u/a/br vindos do editor; o resto vira texto.
  R.cleanRich = function (html) {
    var s = String(html == null ? '' : html);
    s = s.replace(/<(script|style)[\s\S]*?<\/\1>/gi, '');
    s = s.replace(/<br\s*\/?>/gi, '\u0001BR\u0001')
      .replace(/<\/(div|p)>\s*<(div|p)[^>]*>/gi, '\u0001BR\u0001')
      .replace(/<(div|p)[^>]*>/gi, '').replace(/<\/(div|p)>/gi, '');
    var keep = [];
    s = s.replace(/<(\/?)(b|strong|i|em|u)\b[^>]*>/gi, function (_, sl, t) {
      t = t.toLowerCase(); t = t === 'strong' ? 'b' : t === 'em' ? 'i' : t;
      keep.push('<' + sl + t + '>'); return '\u0002' + (keep.length - 1) + '\u0002';
    });
    s = s.replace(/<a\b[^>]*?href\s*=\s*("([^"]*)"|'([^']*)')[^>]*>/gi, function (_, __, h1, h2) {
      var h = (h1 || h2 || '').trim();
      if (!/^(https?:|mailto:)/i.test(h)) { keep.push('<a>'); return '\u0002' + (keep.length - 1) + '\u0002'; }
      keep.push('<a href="' + esc(h) + '" target="_blank" rel="noopener">'); return '\u0002' + (keep.length - 1) + '\u0002';
    }).replace(/<\/a>/gi, function () { keep.push('</a>'); return '\u0002' + (keep.length - 1) + '\u0002'; });
    s = s.replace(/<[^>]*>/g, '');                         // qualquer outra tag sai
    s = s.replace(/&nbsp;/g, ' ');
    // o texto restante já pode conter entidades do navegador (&amp;) — normaliza
    var tmp = s.replace(/&(amp|lt|gt|quot|#39);/g, function (m, e) { return { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[e]; });
    s = esc(tmp).replace(/\u0002(\d+)\u0002/g, function (_, i) { return keep[+i]; }).replace(/\u0001BR\u0001/g, '<br>');
    return s.replace(/(<br>\s*)+$/, '').trim();
  };
  // texto simples (com **negrito**) → html seguro — usado no rascunho
  R.plainToRich = function (t) {
    return esc(t || '').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>');
  };

  // ═══════════════ 7. TIME E DISCLOSURES (padrões) ══════════════════════════
  R.DEFAULT_TEAM = [
    { name: 'Daniel Sasson, CFA', phone: '+55-11-3073-3031', email: 'daniel.sasson@itaubba.com' },
    { name: 'Marcelo Furlan Palhares, CFA', phone: '+55-11-3073-3357', email: 'marcelo.palhares@itaubba.com' },
    { name: 'Rafael Giacometti de Araujo', phone: '+55-11-3073-3055', email: 'rafael.giacometti@itaubba.com' },
    { name: 'João Paulo Helito, CNPI', phone: '+55-11-3073-3005', email: 'joao.helito@itaubba.com' }
  ];
  R.DEFAULT_DISCLOSURE_ANALYSTS = ['Daniel Sasson', 'Marcelo Furlan', 'João Helito'];
  R.PLATFORM_URL = 'https://www.itau.com.br/itaubba-pt/portal/auth/mind';

  var DISCLAIMER = [
    'This report was prepared by Itaú Unibanco, a company regulated by the Securities and Exchange Commission (CVM), and distributed by Itaú Unibanco, Itaú Corretora de Valores S.A. and Itaú BBA. Itaú BBA is a brand used by Itaú Unibanco. This report was prepared by a securities analyst from Itaú Unibanco and is subject to Resolution 20 of the Securities and Exchange Commission (CVM).',
    'This report does not constitute an offer to buy or sell any financial instrument or to participate in any particular trading strategy under any jurisdiction. The information herein was deemed reasonable on the date of publication and was obtained from reliable public sources. Neither Itaú Unibanco nor any of its affiliated companies (“Grupo Itaú Unibanco”) ensure or guarantee, either expressly or implicitly, that the information contained herein is accurate or complete. Furthermore, this report is not a complete or summarized representation of investment strategies, markets or developments thereof. The opinions, estimates and/or forecasts provided in this report reflect the current opinion of the analyst responsible for the content herein on the date of publication and are, therefore, subject to change without notice. The prices and availability of the financial instruments are merely indicative and subject to change beyond the control of Itaú Unibanco. The prices used for the production of this report were obtained as of the close of the relevant market. Neither Itaú Unibanco nor any of its affiliated companies are obligated to update, amend or otherwise alter this report, or to inform readers of any changes in its content, except upon termination of coverage of the securities issuers discussed herein.',
    'The analyst responsible for preparing this document, highlighted in bold, hereby certifies that all opinions expressed in this report accurately, solely and exclusively reflect his/her personal views and opinions regarding all of the issuers and securities analyzed herein, provided in this document independently and autonomously, including from Itaú Unibanco, Itaú Corretora, Itaú BBA, and any of their affiliated companies. Whereas the personal opinions of the analyst may diverge, Itaú Unibanco, Itaú Corretora, Itaú BBA and/or any of their affiliated companies may have published or eventually publish other reports that are inconsistent and/or reach different conclusions than those provided herein.',
    'The analyst responsible for preparing this report is not registered and/or not qualified as a survey analyst at the NYSE or FINRA, nor is such analyst in any way associated with Itau BBA USA Securities, Inc. and is, therefore, not subject to the provisions of Rule 2242 on communications with researched companies, public appearances and transactions involving securities held in a research analyst account.',
    'Part of the analysts’ compensation is determined based on the total top line of Itaú Unibanco and some of its affiliated companies, including revenues arising from investment services and investment bank services. Nonetheless, the analyst responsible for the content in this report hereby certifies that no part of his/her compensation is or will be directly or indirectly conditioned to any specific recommendations or opinions herein.',
    'The financial instruments and strategies discussed in this document may not be suitable for all investors and certain investors may not be eligible to purchase or participate in some or all of them. This report does not take under consideration the specific investment objectives, financial situation or needs of any particular investor. Investors who intend to purchase or trade the securities covered in this report must seek out the applicable information and documents in order to decide whether to invest in such securities. Investors must independently seek out financial, accounting, legal, economic and market guidance, based on their personal characteristics, before making any investment decision regarding the securities of the issuers analyzed in this report. Each investor must make the final investment decision based on a range of related risks, fees and commissions. In the event that a financial instrument is expressed in currencies other than the one used by the investor, exchange rate variations may adversely affect the price, value or profitability. Financial instrument yields may vary, ultimately increasing or decreasing the price or value of financial instruments, either directly or indirectly. Past performance is not necessarily indicative of future results, and this report does not ensure or guarantee, either expressly or implicitly, any possible future performance or any other aspect thereof. Itaú Unibanco and its affiliated companies may not be held liable for eventual losses, either direct or indirect, arising from the use of this report or its content. Upon using the content herein, investors undertake to irrevocably and irreparably hold Itaú Unibanco and/or any of its affiliated companies harmless against any claims, complaints, and/or losses.',
    'This report may not be reproduced or redistributed to third parties, either in whole or in part, for any reason whatsoever, without prior written consent from Itaú Unibanco. This report and its contents are confidential.',
    'In accordance with the rules of the Brazilian Securities and Exchange Commission, the analysts responsible for this report have described scenarios of potential conflict of interest in the “Relevant Information” chart below.',
    'Itaú Unibanco is not party to an agreement with the issuer(s) relating to the production of this report.',
    'The description of the internal organizational and administrative arrangements and of the information barriers in place for the prevention and avoidance of conflicts of interest with respect to the production of research reports are available at www.itaucorretora.com.br.',
    'Material information regarding the proprietary models used by Itaú Unibanco are available at Itaú BBA Bloomberg page.',
    'Other important regulatory information is available at https://disclosure.cloud.itau.com.br'
  ];
  var DISCLAIMER_NOTE = 'Additional note for reports distributed in the (i) United Kingdom: This material has been prepared by Itau Unibanco S.A. for informational purposes only and it does not constitute or should be construed as a proposal for request to enter into any financial instrument or to participate in any specific business trading strategy. The financial instruments discussed in this material may not be suitable for all investors and are directed solely at Eligible Counterparties and Professional Clients, as defined by the Financial Conduct Authority. This material does not take into consideration the objectives, financial situation or specific needs of any particular client. Clients must independently obtain financial, legal, tax, accounting, economic, credit, and market advice on an individual basis, based on their personal characteristics and objectives, prior to making any decision based on the information contained herein. By accessing this material, you confirm that you are aware of the laws in your jurisdiction relating to the provision and sale of financial service products. You acknowledge that this material contains proprietary information and you are required to keep this information confidential. Itau BBA International plc (IBBAInt) exempts itself from any liability for any losses, whether direct or indirect, which may arise from the use of this material and is under no obligation to update the information contained in this material. Additionally, you confirm that you understand the risks related to the financial instruments discussed in this material. Due to international regulations, not all financial services/instruments may be available to all clients. You should be aware of and observe any such restrictions when considering a potential investment decisions. Past performance and forecast are not a reliable indicator of future results. The information contained herein has been obtained from internal and external sources, and is believed to be reliable as of the date in which this material was issued, however IBBAInt does not make any representation or warranty as to the completeness, reliability or accuracy of information obtained by third parties or public sources. Additional information relative to the financial products discussed in this material is available upon request. Itau BBA International plc has its registered offices at 100 Liverpool Street, Level 2, London, EC2M 2AT, United Kingdom, and is authorised by the Prudential Regulation Authority and regulated by the Financial Conduct Authority and the Prudential Regulation Authority (FRN 575225). For any queries please contact your relationship manager; (ii) European Economic Area: this report is distributed in the EEA by Itaú BBA Europe, S.A. (“IBBA Europe”), an affiliate of Itaú Unibanco with registered offices at Rua Tierno Galvan, Torre III, 11 piso, Lisbon, Portugal, authorised as a credit institution by the Bank of Portugal (registration number 195), and as a financial intermediary by the Portuguese Securities Market Commission – CMVM (registration number 396). This material is informational only and shall not be construed as a proposal to trade with financial instruments, nor any kind of personal recommendation of advice. This material is directed solely at Professional Clients and Eligible Counterparties (as defined by Article 4, 1, (10) and Article 30 of Directive 2014/65/EU - MiFID II), who possess the experience, knowledge and expertise to make its own investment decisions and properly assess the risks that they incur. Relevant information regarding conflicts of interest contained in this material apply to IBBA Europe as an affiliate of Itaú Unibanco. IBBA Europe does not exercise discretion as to the selection of the research reports to disseminate and exempts itself from any liability for any losses, whether direct or indirect, which may arise from the use of this material and/or its content and has no obligation to update the information contained in this material. The financial instruments/services discussed in this material may not be available to all clients and/or in their respective jurisdictions. Additional information about the financial instruments discussed in this material are available upon request (iii) USA: Itaú BBA USA Securities, Inc., a FINRA registered Broker Dealer and an SIPC member, is responsible for distributing this report and the content herein. Any U.S. investor who receives this report and intends to trade any of the securities addressed herein must do so through Itau BBA USA Securities, Inc. at 540 Madison Avenue, 24th Floor, New York, NY 10022; (iv) Brazil: Itaú Corretora de Valores S.A., a subsidiary of Itaú Unibanco S.A., is authorized by the Central Bank of Brazil and sanctioned by the CVM to distribute this report. Contact the Customer Service channel for more information: 4004-3131* (capital and metropolitan regions) or 0800-722-3131 (other locations) during business hours (BRT). If additional service is required after contacting the aforementioned channels, contact: 0800-570-0011 (business hours) or write to P.O. Box 67.600, São Paulo – SP, CEP (Postal Code) 03162-971.* (local costs apply); v) México: This research report is distributed in Mexico by Itaú Unibanco, S.A., and by third parties that may be authorized by Itaú Unibanco, S.A., from time to time. This research report shall not be construed to constitute any of the following activities under the jurisdiction of Mexico: (a) issue, market, advertise, commercialize, offer advice, recommend, solicitate and/or promote financial products and/or services of [Itaú Unibanco, S.A., its affiliates and/or subsidiaries], and/or of any third party, (b) offer to buy or sell any financial instrument or security, (c) participate in any particular trading strategy, and/or (d) intend to carry out any activity or service reserved to regulated entities by the Mexican financial laws. This research report has not been reviewed and/or approved by the Mexican National Banking and Securities Commission (Comisión Nacional Bancaria y de Valores). By reviewing this research report, you accept that you (a) are the sole responsible for taking any action or omitting to take any action in connection with the relevant securities, including but not limited to stocks, bonds, derivatives, warrants, options, and swaps, (b) must conduct your own independent appraisal regarding the relevant securities, and (c) release [Itaú Unibanco, S.A., its affiliates and/or subsidiaries] from any and all liability in connection with any action taken or omitted to be taken by you, in connection with the relevant securities.';
  var DISCLOSURE_ITEMS = [
    'The securities analyst(s) involved in preparing this report are associated with individuals who work for the issuers addressed herein.',
    'The securities analyst(s) spouse(s) or partner(s) hold, either directly or indirectly, on their own behalf or on behalf of third parties, stock and/or other securities discussed in this report.',
    'The securities analyst(s), spouse(s) or partner(s) are directly or indirectly involved in the purchase, sale or intermediation of the securities discussed in this report.',
    'The securities analyst(s), respective spouse(s) or partner(s) hold, either directly or indirectly, any financial interest related to the securities issuers analyzed in this report.'
  ];

  // ═══════════════ 8. O DOCUMENTO ═══════════════════════════════════════════
  // Monta o arquivo inteiro. `opt` = {
  //   kind:'iabr'|'secex', model, texts:{kicker,big,title,deck,date,stamp,takeLead,
  //   takeaways:[{lead,html,lvl}], link:{label,url}, team:[...], disclosure:[...]},
  //   fonts:{display:'base64…', body:'base64…'} (opcional — sem elas usa a do sistema)
  // }
  function shapesSVG(cls, vb, shapes) {
    return '<svg class="shapes ' + cls + '" viewBox="' + vb + '" preserveAspectRatio="xMaxYMin slice" aria-hidden="true">' +
      shapes.map(function (s) {
        return '<path class="' + s.c + '" pathLength="1" d="' + itauShape(s.x, s.y, s.r, s.sx, s.sy) + '" style="--rot:' + s.rot + 'deg"/>';
      }).join('') + '</svg>';
  }
  function emblemSVG() {
    // 3 traços (laranja · branco · laranja) em volta do logo — o desenho do
    // material da Extel, em movimento: desenham-se em sequência e depois
    // giram devagar, cada um num sentido e num ritmo.
    return '<svg class="emb" viewBox="0 0 120 120" aria-hidden="true">' +
      '<g class="tr">' +
      '<path class="t1" pathLength="1" d="' + itauShape(60, 60, 55) + '"/>' +
      '<path class="t2" pathLength="1" d="' + itauShape(60, 60, 45) + '"/>' +
      '<path class="t3" pathLength="1" d="' + itauShape(60, 60, 35.5) + '"/>' +
      '</g>' +
      // 2 grupos: o de FORA posiciona (atributo), o de DENTRO anima (CSS) — se o CSS
      // mexesse no mesmo <g>, a animação apagaria o translate/scale do atributo
      '<g transform="translate(37.14 36.2) scale(1.4)"><g class="lg0">' +
      '<path class="lgf" d="' + ITAU_LOGO + '"/><path class="lgf" d="' + ITAU_BOWL + '"/></g></g>' +
      '</svg>';
  }

  function kpiHTML(k) {
    var pctU = k.unit === '%', dec = pctU ? 1 : (k.dec || 0);
    var raw = pctU ? (isNum(k.value) ? k.value * 100 : null) : k.value;
    var val = fmtNum(raw, dec);
    function pill(d) {
      if (!d || !isNum(d.v)) return '';
      var up = d.v > 0;
      var txt = k.ratio ? fmtPP(d.v) : fmtPct(d.v);
      return '<span class="dl"><i class="tri ' + (up ? 'up' : 'dn') + '"></i>' + esc(txt) + ' <em>' + esc(d.lbl) + '</em></span>';
    }
    var qline = '';
    if (k.dq && isNum(k.dq.q)) {
      qline = '<span class="kqv"><span class="kqp">' + esc(k.dq.ql) + '</span><b>' + esc(pctU ? fmtShare(k.dq.q) : fmtNum(k.dq.q, dec) + ' ' + k.unit) + '</b></span>' + pill(k.dq);
    }
    // gráfico com eixos, do MESMO tamanho nos 4 quadros (13 meses)
    var sc = k.scale || 1;
    var vals = (k.spark || []).map(function (v) { return isNum(v) ? (pctU ? v : v / sc) : null; });
    var chart = (k.sparkX && k.sparkX.length) ? chartHTML({
      mini: true, dot: true, x: k.sparkX, xMode: 'my', ticks: 3, height: 104,
      series: [{ name: k.label, type: 'area', cls: 'ol', values: vals, w: 2, fmt: pctU ? 'pct' : (dec ? 'dec1' : 'int') }],
      yl: { fmt: pctU ? 'pct' : 'int', zero: false, n: 2 }
    }) : '';
    return '<div class="kpi">' +
      '<p class="kl">' + esc(k.label) + '</p>' +
      '<p class="kv"><span class="cnt" data-to="' + (isNum(raw) ? raw.toFixed(dec) : '') + '" data-d="' + dec + '">' + esc(val) + '</span>' +
      '<span class="ku">' + esc(k.unit || '') + '</span></p>' +
      '<p class="kd">' + pill(k.d1) + pill(k.d2) + '</p>' +
      '<p class="kq">' + qline + '</p>' +
      '<div class="kc">' + chart + '</div></div>';
  }

  // tabela com 3 vistas (Month · YTD · Quarter) — troca por CSS puro (rádio),
  // funciona mesmo com o script bloqueado
  function iabrTableHTML(M, defView) {
    var C = M.cols, q = M.quarter.end;
    var views = [
      { id: 'm', label: 'Month', cols: [['m', C.m], ['m1', C.m1], ['d:mom', 'MoM'], ['y1', C.y1], ['d:yoy', 'YoY']] },
      { id: 'y', label: 'Year to date', cols: [['ytd', C.ytd], ['ytd1', C.ytd1], ['d:ytd', 'YoY']] },
      { id: 'q', label: q ? 'Quarter' : 'Quarter to date', cols: q
        ? [['q', C.q], ['qp', C.qp], ['d:qoq', 'QoQ'], ['qy', C.qy], ['d:qyoy', 'YoY']]
        : [['q', C.q], ['qy', C.qy], ['d:qyoy', 'YoY']] }
    ];
    var name = 'tv' + (++_chartSeq);
    var radios = views.map(function (v) {
      return '<input type="radio" class="vr" name="' + name + '" id="' + name + v.id + '" data-v="' + v.id + '"' + (v.id === defView ? ' checked' : '') + '>';
    }).join('');
    var seg = '<div class="seg" role="group" aria-label="Table view">' + views.map(function (v) {
      return '<label for="' + name + v.id + '">' + esc(v.label) + '</label>';
    }).join('') + '</div>';
    var tables = views.map(function (v) {
      var head = '<tr><th class="rl">ktons</th>' + v.cols.map(function (c) {
        return '<th' + (c[0].indexOf('d:') === 0 ? ' class="dc"' : '') + '>' + esc(c[1]) + '</th>';
      }).join('') + '</tr>';
      var body = M.lines.map(function (l) {
        return '<tr class="l' + l.lvl + (l.ratio ? ' pr' : '') + '"><th class="rl" scope="row">' + esc(l.label) + '</th>' +
          v.cols.map(function (c) {
            if (c[0].indexOf('d:') === 0) {
              var dv = l.d[c[0].slice(2)];
              return '<td class="dc">' + esc(l.ratio ? fmtPP(dv) : fmtPct(dv)) + '</td>';
            }
            var x = l.v[c[0]];
            return '<td>' + esc(l.ratio ? fmtShare(x) : fmtInt(x)) + '</td>';
          }).join('') + '</tr>';
      }).join('');
      return '<div class="tw" data-v="' + v.id + '"><table class="dt"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>';
    }).join('');
    return '<div class="views">' + radios + seg + '<div class="vpanes">' + tables + '</div></div>';
  }

  // widget de abas (CSS puro): grupos de rádio + painéis por chave composta
  function tabsHTML(groups, panes, defKeys) {
    // groups = [{id, label, opts:[[val,label],...]}]; panes = {'a|b': html}
    var nm = 'tb' + (++_chartSeq);
    var radios = '', segs = '', css = '';
    groups.forEach(function (g, gi) {
      g.opts.forEach(function (o) {
        var rid = nm + 'g' + gi + o[0];
        radios += '<input type="radio" class="vr" name="' + nm + 'g' + gi + '" id="' + rid + '" data-g="' + gi + '" data-o="' + o[0] + '"' +
          (defKeys[gi] === o[0] ? ' checked' : '') + '>';
      });
      segs += '<div class="seg" role="group" aria-label="' + esc(g.label) + '">' + g.opts.map(function (o) {
        return '<label for="' + nm + 'g' + gi + o[0] + '">' + esc(o[1]) + '</label>';
      }).join('') + '</div>';
    });
    // regra que mostra o painel certo para cada combinação marcada
    Object.keys(panes).forEach(function (key) {
      var parts = key.split('|');
      css += parts.map(function (p, gi) { return '#' + nm + 'g' + gi + p + ':checked'; }).join(' ~ ') +
        ' ~ .tpanes > [data-k="' + key + '"]{display:block}';
      // marca o rótulo ativo
    });
    groups.forEach(function (g, gi) {
      g.opts.forEach(function (o) {
        css += '#' + nm + 'g' + gi + o[0] + ':checked ~ .segs label[for="' + nm + 'g' + gi + o[0] + '"]{background:var(--orange);color:#000}';
      });
    });
    var body = Object.keys(panes).map(function (key) {
      return '<div class="tp" data-k="' + key + '">' + panes[key] + '</div>';
    }).join('');
    return '<div class="tabs" id="' + nm + '"><style>' + css + '</style>' + radios + '<div class="segs">' + segs + '</div><div class="tpanes">' + body + '</div></div>';
  }

  function takeawaysHTML(T) {
    // item sem corpo não sai (o "Our take:" que nasce vazio, por exemplo)
    var items = (T.takeaways || []).filter(function (t) { return R.cleanRich(t.html).replace(/<[^>]+>/g, '').trim(); });
    if (!items.length) return '';
    return '<ul class="tk">' + items.map(function (t) {
      var body = R.cleanRich(t.html);
      var lead = t.lead ? '<b class="tl">' + esc(t.lead) + '</b> ' : '';
      return '<li class="tk' + (t.lvl ? ' sub' : '') + '">' + lead + body + '</li>';
    }).join('') + '</ul>';
  }

  // o documento — comum aos dois relatórios
  function documentHTML(o) {
    var T = o.texts || {}, F = o.fonts || {};
    // arquivo final: fontes EMBUTIDAS (base64) — abre offline no WhatsApp. Prévia da
    // tela de admin: por endereço (redesenha a cada tecla sem carregar 100 KB de texto).
    function ff(fam, w, b64, url) {
      var src = b64 ? 'url(data:font/woff2;base64,' + b64 + ')' : url ? 'url(' + url + ')' : '';
      return src ? "@font-face{font-family:'" + fam + "';font-style:normal;font-weight:" + w + ";font-display:block;src:" + src + " format('woff2')}" : '';
    }
    var fontCSS = ff('Unbounded', '200 900', F.display, F.displayUrl) + ff('Manrope', '200 800', F.body, F.bodyUrl);
    var team = (T.team && T.team.length ? T.team : R.DEFAULT_TEAM).filter(function (p) { return p && p.name; });
    var disc = (T.disclosure && T.disclosure.length ? T.disclosure : R.DEFAULT_DISCLOSURE_ANALYSTS);
    var sections = o.sections;               // [{id, num, title, count, html, dock}]
    var titleTxt = (T.kicker ? T.kicker + ' — ' : '') + (T.big || '') + ' ' + (T.title || '');

    var hero = '<header class="hero">' +
      shapesSVG('big', '0 0 960 640', [
        { c: 'o', x: 842, y: 250, r: 212, rot: 9 },
        { c: 'o', x: 872, y: 214, r: 148, rot: -7 },
        { c: 'w', x: 808, y: 286, r: 124, rot: 21 },
        { c: 'o', x: 930, y: 386, r: 90, rot: -18 }
      ]) +
      '<div class="topbar pad">' +
      '<div class="brand">' + emblemSVG() +
      '<div class="bt"><strong class="team">STEEL &amp; MINING <span>/</span> PULP &amp; PAPER</strong>' +
      '<span class="sub">Itaú BBA · Equity Research</span></div></div>' +
      '<div class="tools"><div class="theme-sw" role="group" aria-label="Colour theme">' +
      '<button type="button" data-theme-set="dark" aria-pressed="true">Dark</button>' +
      '<button type="button" data-theme-set="light" aria-pressed="false">Light</button></div>' +
      '<div class="stamp"><b>' + esc(dateLong(T.date)) + '</b><br>' + esc(T.stamp || 'Brazil | Sector Data') + '</div></div>' +
      '</div>' +
      '<div class="lockup pad">' +
      (T.kicker ? '<p class="kicker">' + esc(T.kicker) + '</p>' : '') +
      '<h1 aria-label="' + esc((T.big || '') + ' ' + (T.title || '')) + '"><span class="period">' + esc(T.big || '') + '</span>' +
      '<span class="word">' + esc(T.title || '') + '</span></h1>' +
      (T.deck ? '<p class="deck">' + R.cleanRich(T.deck) + '</p>' : '') +
      '</div></header>';

    var kpis = o.kpis && o.kpis.length ? '<section class="kpis pad" aria-label="Highlights">' +
      (o.kpiCap ? '<p class="kcap"><span><b>' + esc(o.kpiCap[0]) + '</b></span><span>' + esc(o.kpiCap[1]) + '</span></p>' : '') +
      o.kpis.map(kpiHTML).join('') + '</section>' : '';

    var index = '<div class="pad"><nav class="index" aria-label="Sections" style="--n:' + sections.length + '">' + sections.map(function (s) {
      return '<a href="#' + s.id + '"><span class="num">' + esc(s.num) + '</span>' + esc(s.short || s.title) +
        (s.count ? '<span class="n">' + esc(s.count) + '</span>' : '') + '</a>';
    }).join('') + '</nav>' + (o.about ? '<div class="about">' + o.about + '</div>' : '') +
      (o.note ? '<div class="note">' + o.note + '</div>' : '') + '</div>';

    var body = '<main>' + sections.map(function (s, i) {
      return '<section class="sec' + (s.cls ? ' ' + s.cls : '') + '" id="' + s.id + '" aria-labelledby="h-' + s.id + '">' +
        (s.shapes ? shapesSVG('side', '540 0 420 300', [
          { c: 'o', x: 905, y: 150, r: 120, rot: 14 }, { c: 'w', x: 940, y: 205, r: 78, rot: -12 }]) : '') +
        '<div class="sec-head pad"><h2 id="h-' + s.id + '"><span class="num">' + esc(s.num) + '</span>' + esc(s.title) + '</h2>' +
        (s.aside ? '<p class="count">' + esc(s.aside) + '</p>' : '') + '</div>' +
        '<div class="pad"><div class="rows">' + s.html + '</div></div></section>';
    }).join('') + '</main>';

    var link = (T.link && T.link.url) ? '<a class="platform" href="' + esc(T.link.url) + '" target="_blank" rel="noopener">' + esc(T.link.label || 'Read the full report') + '</a><br>' : '';
    var foot = '<footer class="foot">' +
      shapesSVG('corner', '0 0 420 330', [
        { c: 'o', x: 380, y: 300, r: 150, rot: 12 }, { c: 'w', x: 420, y: 330, r: 105, rot: -9 }, { c: 'o', x: 400, y: 360, r: 70, rot: 24 }]) +
      '<div class="inner pad"><h2>Steel &amp; Mining · Pulp &amp; Paper team</h2>' +
      '<div class="people">' + team.map(function (p) {
        return '<div class="person"><p class="p-name">' + esc(p.name) + '</p>' +
          (p.email ? '<p><a href="mailto:' + esc(p.email) + '">' + esc(p.email) + '</a></p>' : '') +
          (p.phone ? '<p>' + esc(p.phone) + '</p>' : '') + '</div>';
      }).join('') + '</div>' +
      link +
      '<a class="platform" href="' + R.PLATFORM_URL + '" target="_blank" rel="noopener">Access all reports on Itaú BBA Research Platform</a>' +
      '<p class="fine">' + esc(o.sourceNote || '') + ' Itaú BBA does and seeks to do business with companies covered in its research reports. As a result, investors should be aware that the firm may have a conflict of interest that could affect the objectivity of this report. Investors should not consider this report as the sole factor in making their investment decision. Full disclosures below.</p>' +
      '<details class="disc"><summary>Disclaimer and analyst disclosures</summary>' +
      '<ol>' + DISCLAIMER.map(function (p) { return '<li>' + esc(p) + '</li>'; }).join('') + '</ol>' +
      '<p>' + esc(DISCLAIMER_NOTE) + '</p>' +
      '<h4>Relevant Information – Analysts</h4>' +
      '<table class="dtab"><thead><tr><th>Analysts</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5</th></tr></thead><tbody>' +
      disc.map(function (a) { return '<tr><td>' + esc(a) + '</td><td></td><td></td><td></td><td></td><td>OK</td></tr>'; }).join('') +
      '</tbody></table><ol class="di">' + DISCLOSURE_ITEMS.map(function (p) { return '<li>' + esc(p) + '</li>'; }).join('') + '</ol>' +
      '</details>' +
      '<div class="sig"><strong>Itaú BBA</strong><span>Equity Research · Steel &amp; Mining / Pulp &amp; Paper</span></div>' +
      '</div></footer>';

    var dockS = sections.filter(function (s) { return s.dock; });
    var dock = '<nav class="dock" aria-label="Sections" style="--n:' + dockS.length + '">' + dockS.map(function (s) {
      return '<a href="#' + s.id + '">' + esc(s.dock) + '</a>';
    }).join('') + '</nav>' +
      '<div class="linkbox" id="linkbox" role="status" aria-live="polite" hidden><p id="lb-msg"></p><span class="lb-url" id="lb-url"></span><button type="button" id="lb-close">Close</button></div>';

    return '<!doctype html>\n<html lang="en"' + (o.calm ? ' class="calm"' : '') + '>\n<head>\n<meta charset="utf-8">\n' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="color-scheme" content="dark light">\n' +
      '<script>(function(){try{var q=new URLSearchParams(location.search).get("theme"),t=q||localStorage.getItem("ibba-rpt-theme");if(t==="light")document.documentElement.setAttribute("data-theme","light")}catch(e){}})();</script>\n' +
      '<title>' + esc(titleTxt.trim()) + ' | Steel &amp; Mining / Pulp &amp; Paper | Itaú BBA</title>\n' +
      '<style>' + fontCSS + CSS + '</style>\n</head>\n<body class="no-js">\n<div class="sheet">\n' +
      hero + kpis + index + body + foot + dock + '\n</div>\n<script>' + RUNTIME + '</script>\n</body>\n</html>\n';
  }
  R.documentHTML = documentHTML;

  // ═══════════════ 9. O RELATÓRIO DO IABr ═══════════════════════════════════
  R.iabrDefaults = function (M, today) {
    return {
      kicker: 'Brazilian Steel Institute (IABr)',
      big: M.quarter.end ? M.quarter.label : M.label.replace('-', ' '),
      title: 'Brazilian Steel Market',
      deck: R.plainToRich(R.iabrDeck(M)),
      date: today || '',
      stamp: 'Brazil | Steel Industry Data',
      takeLead: '',
      takeaways: R.iabrDraft(M).map(function (t) { return { lead: t.lead, html: R.plainToRich(t.text), lvl: t.lvl }; }),
      link: { label: 'Read the full report', url: '' },
      team: R.DEFAULT_TEAM.slice(),
      disclosure: R.DEFAULT_DISCLOSURE_ANALYSTS.slice()
    };
  };

  R.renderIabr = function (M, T, fonts, opts) {
    _chartSeq = 0;
    var S = M.series, q = M.quarter.end;
    // II — tabela
    var tbl = iabrTableHTML(M, q ? 'q' : 'm') +
      '<p class="fn">* Flat steel + slabs. ** Long steel + billets. ¹ Rolled products + semi-finished for sale. ' +
      '² Domestic sales + imports, as published by IABr. ³ (Apparent consumption − domestic sales) ÷ apparent consumption. ' +
      'Imports and exports as published by IABr (flat, long and semi-finished). Source: IABr, Itaú BBA.</p>';
    // III — penetração (abas Total/Flat/Long)
    function penChart(ca, pen, lbl) {
      return chartHTML({
        title: lbl + ' — apparent consumption vs. import penetration',
        sub: 'ktons (lhs) · % of apparent consumption (rhs) · monthly',
        x: S.x, xMode: 'my', ticks: 8, height: 270,
        series: [
          { name: 'Apparent consumption', type: 'area', cls: 'f', values: S[ca], fmt: 'int' },
          { name: 'Import penetration', type: 'line', axis: 'r', cls: 'o', values: S[pen], fmt: 'pct', w: 2.2 }
        ],
        yl: { fmt: 'int' }, yr: { fmt: 'pct' }
      });
    }
    var pen = tabsHTML([{ label: 'Product', opts: [['t', 'Total'], ['f', 'Flat*'], ['l', 'Long**']] }], {
      't': penChart('ca', 'pen', 'Total steel'),
      'f': penChart('ca_flat', 'pen_flat', 'Flat steel'),
      'l': penChart('ca_long', 'pen_long', 'Long steel')
    }, ['t']);
    // IV — balança: vendas internas × importação × exportação
    var bal = chartHTML({
      title: 'Domestic sales, exports and imports', sub: 'ktons · monthly', x: S.x, xMode: 'my', ticks: 8, height: 250,
      series: [
        { name: 'Domestic sales', type: 'line', cls: 'f', values: S.dom, w: 2 },
        { name: 'Exports', type: 'line', cls: 'g1', values: S.exp, w: 1.6 },
        { name: 'Imports', type: 'line', cls: 'o', values: S.imp, w: 2.2 }
      ], yl: { fmt: 'int' }
    });
    function stackChart(a, b, c, ttl) {
      var ser = [{ name: 'Flat steel' + (a === 'dom' ? '*' : ''), type: 'area', cls: 'o', values: S[a + '_flat'] },
        { name: 'Long steel' + (a === 'dom' ? '**' : ''), type: 'area', cls: 'f', values: S[a + '_long'] }];
      if (c) ser.push({ name: 'Semi-finished', type: 'area', cls: 'g2', values: S[a + '_semi'] });
      return chartHTML({ title: ttl, sub: 'ktons · monthly · stacked', x: S.x, xMode: 'my', ticks: 8, height: 230,
        series: ser, stack: true, yl: { fmt: 'int' } });
    }
    var mix = tabsHTML([{ label: 'Flow', opts: [['d', 'Domestic sales'], ['i', 'Imports'], ['e', 'Exports']] }], {
      'd': stackChart('dom', null, false, 'Domestic sales by product'),
      'i': stackChart('imp', null, true, 'Imports by product'),
      'e': stackChart('exp', null, true, 'Exports by product')
    }, ['d']);
    // V — sazonalidade (métrica × produto), um traço por ano
    var yrs = M.years;
    function seasonChart(k, ttl) {
      var cls = ['y0', 'y1', 'y2', 'y3', 'yc'];
      return chartHTML({ title: ttl, sub: 'ktons · by month, ' + yrs[0] + '–' + yrs[yrs.length - 1],
        x: MON.map(function (_, i) { return '2000-' + ('0' + (i + 1)).slice(-2); }), xMode: 'mon', ticks: 12, height: 240,
        series: M.seasonal[k].map(function (s, i) {
          return { name: String(s.year), type: 'line', cls: cls[i + (5 - yrs.length)] || 'y0', values: s.v, w: i === yrs.length - 1 ? 2.6 : 1.4 };
        }), yl: { fmt: 'int', zero: false } });
    }
    var met = [['ca', 'Apparent consumption'], ['dom', 'Domestic sales'], ['imp', 'Imports'], ['exp', 'Exports']];
    var panes = {};
    met.forEach(function (mm) {
      panes[mm[0] + '|f'] = seasonChart(mm[0] + '_flat', 'Flat steel — ' + mm[1].toLowerCase());
      panes[mm[0] + '|l'] = seasonChart(mm[0] + '_long', 'Long steel — ' + mm[1].toLowerCase());
    });
    var season = tabsHTML([
      { label: 'Metric', opts: met.map(function (x) { return [x[0], x[1]]; }) },
      { label: 'Product', opts: [['f', 'Flat'], ['l', 'Long']] }
    ], panes, ['ca', 'f']);

    var nTk = (T.takeaways || []).filter(function (t) { return R.cleanRich(t.html).replace(/<[^>]+>/g, '').trim(); }).length;
    var sections = [
      { id: 'take', num: 'I', title: 'Key takeaways', short: 'Key takeaways', count: nTk ? String(nTk) : '', dock: 'Takeaways', shapes: true,
        html: (T.takeLead ? '<p class="lead">' + R.cleanRich(T.takeLead) + '</p>' : '') + takeawaysHTML(T) },
      { id: 'data', num: 'II', title: 'IABr snapshot', short: 'Snapshot', dock: 'Data', aside: 'ktons, unless stated',
        html: tbl },
      { id: 'pen', num: 'III', title: 'Import penetration', short: 'Import penetration', dock: 'Imports',
        html: pen },
      { id: 'mkt', num: 'IV', title: 'Market balance', short: 'Market & seasonality', dock: 'Charts',
        html: bal + mix + '<h3 class="sh">Seasonality</h3>' + season }
    ];
    return documentHTML({
      kind: 'iabr', texts: T, fonts: fonts, sections: sections, calm: !!(opts && opts.calm),
      kpis: M.kpis, kpiCap: [M.long, 'Monthly data \u00b7 charts show the last 13 months'],
      sourceNote: 'Source: Brazilian Steel Institute (IABr), Itaú BBA. Data through ' + M.long + '.'
    });
  };

  // ═══════════════ 10. A SECEX (aço · minério · celulose) ═══════════════════
  // Fontes = as MESMAS tabelas das páginas de M&M e P&P:
  //   aço      → secex_imports / secex_exports (categorias do dicionário único de SH6)
  //              + secex_country (origem/destino) + import_prediction/pred_exports
  //   minério  → secex_iron_ore (finos/pelotas × país) + secex_iron_ore_port (porto; MESMO
  //              download do MDIC, então porto e país somam igual)
  //   celulose → secex_pulp_country (produto × país) + secex_pulp_port (porto, todas as fibras)
  //   dias úteis → calendar (pulp_paper.db) — UM calendário para os três
  var PT_EN = {
    'Vietnã': 'Vietnam', 'Coreia do Sul': 'South Korea', 'Japão': 'Japan', 'Egito': 'Egypt', 'Turquia': 'Turkey',
    'Índia': 'India', 'Alemanha': 'Germany', 'Estados Unidos': 'United States', 'Canadá': 'Canada',
    'Tailândia': 'Thailand', 'Indonésia': 'Indonesia', 'Taiwan (Formosa)': 'Taiwan', 'Malásia': 'Malaysia',
    'Países Baixos (Holanda)': 'Netherlands', 'Bélgica': 'Belgium', 'Espanha': 'Spain', 'França': 'France',
    'Itália': 'Italy', 'Reino Unido': 'United Kingdom', 'Rússia': 'Russia', 'Ucrânia': 'Ukraine', 'México': 'Mexico',
    'Argentina': 'Argentina', 'Chile': 'Chile', 'Peru': 'Peru', 'Uruguai': 'Uruguay', 'Paraguai': 'Paraguay',
    'Colômbia': 'Colombia', 'Bolívia': 'Bolivia', 'Equador': 'Ecuador', 'Venezuela': 'Venezuela',
    'Arábia Saudita': 'Saudi Arabia', 'Emirados Árabes Unidos': 'UAE', 'Omã': 'Oman', 'Barein': 'Bahrain',
    'Catar': 'Qatar', 'Coveite (Kuweit)': 'Kuwait', 'Irã': 'Iran', 'Iraque': 'Iraq', 'Israel': 'Israel',
    'Áustria': 'Austria', 'Finlândia': 'Finland', 'Suécia': 'Sweden', 'Noruega': 'Norway', 'Dinamarca': 'Denmark',
    'Polônia': 'Poland', 'Eslovênia': 'Slovenia', 'Eslováquia': 'Slovakia', 'Tcheca, República': 'Czech Republic',
    'Hungria': 'Hungary', 'Romênia': 'Romania', 'Bulgária': 'Bulgaria', 'Croácia': 'Croatia', 'Sérvia': 'Serbia',
    'Grécia': 'Greece', 'Portugal': 'Portugal', 'Suíça': 'Switzerland', 'Irlanda': 'Ireland', 'Luxemburgo': 'Luxembourg',
    'Filipinas': 'Philippines', 'Singapura': 'Singapore', 'Hong Kong': 'Hong Kong', 'Brunei': 'Brunei',
    'Paquistão': 'Pakistan', 'Bangladesh': 'Bangladesh', 'Sri Lanka': 'Sri Lanka', 'Cazaquistão': 'Kazakhstan',
    'Belarus': 'Belarus', 'Geórgia': 'Georgia', 'Austrália': 'Australia', 'Nova Zelândia': 'New Zealand',
    'África do Sul': 'South Africa', 'Argélia': 'Algeria', 'Líbia': 'Libya', 'Marrocos': 'Morocco', 'Tunísia': 'Tunisia',
    'Nigéria': 'Nigeria', 'Trinidad e Tobago': 'Trinidad and Tobago', 'China': 'China', 'Japan': 'Japan',
    'Não Definido': 'Not declared', 'Outros': 'Others'
  };
  function en(c) { return PT_EN[c] || c; }
  R.countryEN = en;

  function seriesBy(db, sql, params) {        // [{period, k, v, r}] → {k: {vol:{p:v}, rev:{p:v}}}
    var out = {};
    rows(db, sql, params).forEach(function (x) {
      var o = out[x.k] = out[x.k] || { vol: {}, rev: {} };
      if (isNum(x.v)) o.vol[x.period] = (o.vol[x.period] || 0) + x.v;
      if (isNum(x.r)) o.rev[x.period] = (o.rev[x.period] || 0) + x.r;
    });
    return out;
  }
  function addMaps(a, b) {
    var o = {}; [a, b].forEach(function (m) { Object.keys(m || {}).forEach(function (p) { o[p] = (o[p] || 0) + m[p]; }); });
    return o;
  }

  R.secexPeriods = function (db) {
    return rows(db, "SELECT period FROM secex_imports WHERE category='total' AND volume_ktons IS NOT NULL " +
      "INTERSECT SELECT period FROM secex_exports WHERE category='total' AND volume_ktons IS NOT NULL ORDER BY period DESC")
      .map(function (r) { return r.period; });
  };

  // colunas das vistas (mesma lógica do IABr): Month · YTD · Quarter (QTD fora do fim)
  function colsFor(period) {
    var Q = quarterOf(period), Qp = prevQuarter(Q), Qy = yearAgoQuarter(Q);
    var y = +period.slice(0, 4);
    var qtd = Q.months.filter(function (p) { return p <= period; });
    return {
      Q: Q, Qp: Qp, Qy: Qy,
      months: {
        m: [period], m1: [pAdd(period, -1)], y1: [pAdd(period, -12)],
        ytd: pRange(y + '-01', period), ytd1: pRange((y - 1) + '-01', pAdd(period, -12)),
        q: Q.end ? Q.months : qtd, qp: Q.end ? Qp.months : null,
        qy: Q.end ? Qy.months : qtd.map(function (p) { return pAdd(p, -12); })
      },
      labels: {
        m: monLabel(period), m1: monLabel(pAdd(period, -1)), y1: monLabel(pAdd(period, -12)),
        ytd: 'YTD’' + String(y).slice(2), ytd1: 'YTD’' + String(y - 1).slice(2),
        q: Q.end ? Q.label : Q.label + ' QTD', qp: Q.end ? Qp.label : null, qy: Q.end ? Qy.label : Qy.label + ' QTD'
      }
    };
  }
  // linhas genéricas: def = {label, lvl, fmt:'int'|'dec1'|'pct', d:'pct'|'pp'|'abs', fn(sumOf)} ou {group}
  function buildLines(defs, C) {
    return defs.map(function (df) {
      if (df.group) return df;
      var v = {};
      Object.keys(C.months).forEach(function (c) {
        var ms = C.months[c];
        v[c] = ms ? df.fn(function (map) { return sumOver(map, ms); }) : null;
      });
      var dk = df.d || 'pct', f = dk === 'pct' ? chg : diff;
      return { label: df.label, lvl: df.lvl || 0, fmt: df.fmt || 'int', dk: dk, v: v, strong: df.strong,
        d: { mom: f(v.m, v.m1), yoy: f(v.m, v.y1), ytd: f(v.ytd, v.ytd1), qoq: f(v.q, v.qp), qyoy: f(v.q, v.qy) } };
    });
  }
  function fmtVal(x, f) {
    if (f === 'pct') return fmtShare(x);
    if (f === 'dec1') return fmtNum(x, 1);
    return fmtInt(x);
  }
  function fmtD(x, dk) {
    if (dk === 'pp') return fmtPP(x);
    if (dk === 'abs') return isNum(x) ? (x > 0 ? '+' : '') + fmtNum(x, 0) : '—';
    return fmtPct(x, 0);
  }
  // tabela com vistas (rádio, CSS puro) — versão genérica
  function viewTable(lines, C, defView, unitLbl, views) {
    var L = C.labels, q = C.Q.end;
    var all = {
      m: { id: 'm', label: 'Month', cols: [['m', L.m], ['m1', L.m1], ['d:mom', 'MoM'], ['y1', L.y1], ['d:yoy', 'YoY']] },
      y: { id: 'y', label: 'Year to date', cols: [['ytd', L.ytd], ['ytd1', L.ytd1], ['d:ytd', 'YoY']] },
      q: { id: 'q', label: q ? 'Quarter' : 'Quarter to date', cols: q
        ? [['q', L.q], ['qp', L.qp], ['d:qoq', 'QoQ'], ['qy', L.qy], ['d:qyoy', 'YoY']]
        : [['q', L.q], ['qy', L.qy], ['d:qyoy', 'YoY']] }
    };
    var vs = (views || ['m', 'y', 'q']).map(function (k) { return all[k]; });
    var name = 'tv' + (++_chartSeq);
    var radios = vs.map(function (v) {
      return '<input type="radio" class="vr" name="' + name + '" id="' + name + v.id + '" data-v="' + v.id + '"' + (v.id === defView ? ' checked' : '') + '>';
    }).join('');
    var seg = '<div class="seg" role="group" aria-label="Table view">' + vs.map(function (v) {
      return '<label for="' + name + v.id + '">' + esc(v.label) + '</label>';
    }).join('') + '</div>';
    var tables = vs.map(function (v) {
      var head = '<tr><th class="rl">' + esc(unitLbl || '') + '</th>' + v.cols.map(function (c) {
        return '<th' + (c[0].indexOf('d:') === 0 ? ' class="dc"' : '') + '>' + esc(c[1]) + '</th>';
      }).join('') + '</tr>';
      var body = lines.map(function (l) {
        if (l.group) return '<tr class="gr"><th class="rl" colspan="' + (v.cols.length + 1) + '">' + esc(l.group) + '</th></tr>';
        return '<tr class="l' + l.lvl + (l.strong ? ' st' : '') + '"><th class="rl" scope="row">' + esc(l.label) + '</th>' +
          v.cols.map(function (c) {
            if (c[0].indexOf('d:') === 0) return '<td class="dc">' + esc(fmtD(l.d[c[0].slice(2)], l.dk)) + '</td>';
            return '<td>' + esc(fmtVal(l.v[c[0]], l.fmt)) + '</td>';
          }).join('') + '</tr>';
      }).join('');
      return '<div class="tw" data-v="' + v.id + '"><table class="dt gx"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>';
    }).join('');
    return '<div class="views">' + radios + seg + '<div class="vpanes">' + tables + '</div></div>';
  }

  // barras horizontais (origens/destinos) — HTML puro, crescem ao aparecer
  function hbarsHTML(title, sub, rows2, note) {
    var mx = 0; rows2.forEach(function (r) { if (r.v > mx) mx = r.v; });
    return '<figure class="ch hb">' +
      '<figcaption><h3>' + esc(title) + '</h3>' + (sub ? '<p>' + esc(sub) + '</p>' : '') + '</figcaption>' +
      '<div class="hbl" role="list">' + rows2.map(function (r, i) {
        return '<div class="hbr' + (r.hl ? ' hl' : '') + '" role="listitem" style="--i:' + i + '"><span class="hbn">' + esc(r.name) + '</span>' +
          '<span class="hbt"><i style="width:' + (mx ? (r.v / mx * 100).toFixed(2) : 0) + '%"></i></span>' +
          '<span class="hbv">' + esc(r.vtxt) + '</span><span class="hbs">' + esc(r.stxt || '') + '</span></div>';
      }).join('') + '</div>' + (note ? '<p class="cnote">' + esc(note) + '</p>' : '') + '</figure>';
  }

  // acumulado móvel de 12 meses por país (mesma conta da página de M&M: soma das 12 linhas)
  function roll12ByCountry(list) {
    var byC = {};
    list.forEach(function (r) { (byC[r.country] = byC[r.country] || []).push(r); });
    var out = {};
    Object.keys(byC).forEach(function (c) {
      var arr = byC[c].sort(function (a, b) { return a.period < b.period ? -1 : 1; });
      arr.forEach(function (r, i) {
        if (i < 11) return;
        var s = 0; for (var j = i - 11; j <= i; j++) s += arr[j].volume_kg || 0;
        out[r.period] = (out[r.period] || 0) + s;
      });
    });
    return out;
  }

  R.secexModel = function (sm, pp, period) {
    var C = colsFor(period), Q = C.Q;
    // ── aço
    var imp = seriesBy(sm, 'SELECT period, category AS k, volume_ktons AS v, revenue_usd_mn AS r FROM secex_imports');
    var exp = seriesBy(sm, 'SELECT period, category AS k, volume_ktons AS v, revenue_usd_mn AS r FROM secex_exports');
    // ── minério
    var iron = seriesBy(sm, "SELECT period, product AS k, SUM(volume_ktons) AS v, SUM(revenue_usd_mn) AS r FROM secex_iron_ore WHERE direction='exp' GROUP BY period, product");
    var ironTot = { vol: addMaps((iron.Fines || {}).vol, (iron.Pellets || {}).vol), rev: addMaps((iron.Fines || {}).rev, (iron.Pellets || {}).rev) };
    // ── minério por PORTO — as 4 linhas do PDF do analista + Others (Corumbá e resíduos).
    //    Nomes = os da base (_shared/ports.py); o Porto do Açu é a URF "Campos Dos Goytacazes".
    var IRON_PORTS = [['São Luís', 'São Luís'], ['Vitória', 'Vitória'], ['Itaguaí', 'Itaguaí'],
      ['Campos Dos Goytacazes', 'Goytacazes (Açu)']];
    var ironPortRows = hasTable(sm, 'secex_iron_ore_port')
      ? rows(sm, 'SELECT period, port, SUM(volume_ktons) AS v FROM secex_iron_ore_port GROUP BY period, port') : [];
    var ironPortS = {}; IRON_PORTS.forEach(function (p) { ironPortS[p[0]] = {}; }); ironPortS.Others = {};
    ironPortRows.forEach(function (r) {
      var k = ironPortS[r.port] ? r.port : 'Others';
      if (isNum(r.v)) ironPortS[k][r.period] = (ironPortS[k][r.period] || 0) + r.v;
    });
    // mês publicado em que um porto não embarcou = 0, não "falta" (senão o total do trimestre vira travessão)
    var ironPortPer = {}; ironPortRows.forEach(function (r) { ironPortPer[r.period] = 1; });
    Object.keys(ironPortS).forEach(function (k) { Object.keys(ironPortPer).forEach(function (p2) { if (!isNum(ironPortS[k][p2])) ironPortS[k][p2] = 0; }); });
    // ── celulose
    var pulp = seriesBy(pp, 'SELECT period, product AS k, SUM(volume_ktons) AS v, SUM(revenue_usd_mn) AS r FROM secex_pulp_country GROUP BY period, product');
    var pulpPer = {}; Object.keys(pulp).forEach(function (k) { Object.keys(pulp[k].vol).forEach(function (pp2) { pulpPer[pp2] = 1; }); });
    ['hardwood', 'softwood', 'ukp', 'dissolving'].forEach(function (k) {
      var o = pulp[k] = pulp[k] || { vol: {}, rev: {} };
      Object.keys(pulpPer).forEach(function (pp2) { if (!isNum(o.vol[pp2])) o.vol[pp2] = 0; if (!isNum(o.rev[pp2])) o.rev[pp2] = 0; });
    });
    function pm(k) { return pulp[k] || { vol: {}, rev: {} }; }
    var bkp = { vol: addMaps(pm('hardwood').vol, pm('softwood').vol), rev: addMaps(pm('hardwood').rev, pm('softwood').rev) };
    var mkt = { vol: addMaps(bkp.vol, pm('ukp').vol), rev: addMaps(bkp.rev, pm('ukp').rev) };
    var wd = {}; rows(pp, 'SELECT period, working_days FROM calendar').forEach(function (r) { if (isNum(r.working_days)) wd[r.period] = r.working_days; });

    function V(o) { return function (s) { return s(o.vol); }; }
    function Rv(o) { return function (s) { return s(o.rev); }; }
    function price(o) { return function (s) { var v = s(o.vol), r = s(o.rev); return (isNum(v) && isNum(r) && v > 0) ? r / v * 1000 : null; }; }
    function perDay(o) { return function (s) { var v = s(o.vol), d = s(wd); return (isNum(v) && isNum(d) && d > 0) ? v / d : null; }; }
    function W() { return function (s) { return s(wd); }; }
    function cat(src, k) { return src[k] || { vol: {}, rev: {} }; }

    // tabela-resumo (as duas do relatório)
    var snapExp = buildLines([
      { group: 'Iron ore' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(ironTot) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(ironTot) },
      { group: 'Market pulp' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(mkt) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(mkt) },
      { group: 'Total steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(exp, 'total')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(exp, 'total')) },
      { group: 'Flat steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(exp, 'flat')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(exp, 'flat')) },
      { group: 'Long steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(exp, 'long')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(exp, 'long')) },
      { group: 'Semi-finished steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(exp, 'semi')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(exp, 'semi')) }
    ], C);
    var snapImp = buildLines([
      { group: 'Total steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(imp, 'total')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(imp, 'total')) },
      { group: 'Flat steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(imp, 'flat')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(imp, 'flat')) },
      { group: 'Long steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(imp, 'long')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(imp, 'long')) },
      { group: 'Semi-finished steel' },
      { label: 'Volume (ktons)', lvl: 1, fn: V(cat(imp, 'semi')) }, { label: 'Revenue (US$ mn)', lvl: 1, fn: Rv(cat(imp, 'semi')) }
    ], C);
    function steelTable(src, word) {
      return buildLines([
        { label: 'Flat steel volumes (ktons)', fn: V(cat(src, 'flat')) },
        { label: 'Long steel volumes (ktons)', fn: V(cat(src, 'long')) },
        { label: 'Semi-finished volumes (ktons)', fn: V(cat(src, 'semi')) },
        { label: '(a) Total ' + word + ' (ktons)', fn: V(cat(src, 'total')), strong: true },
        { label: 'Flat steel revenues (US$ mn)', fn: Rv(cat(src, 'flat')) },
        { label: 'Long steel revenues (US$ mn)', fn: Rv(cat(src, 'long')) },
        { label: 'Semi-finished revenues (US$ mn)', fn: Rv(cat(src, 'semi')) },
        { label: '(b) Total revenues (US$ mn)', fn: Rv(cat(src, 'total')), strong: true },
        { label: '(c) Working days', fn: W(), d: 'abs' },
        { label: '(a)/(c) Avg. volume per working day', fn: perDay(cat(src, 'total')) },
        { label: '(b)/(a) Average price (US$/t)', fn: price(cat(src, 'total')) }
      ], C);
    }
    var ironTable = buildLines([
      { label: '(a) Volumes (ktons)', fn: V(ironTot), strong: true },
      { label: 'Fines', lvl: 1, fn: V(cat(iron, 'Fines')) },
      { label: 'Pellets', lvl: 1, fn: V(cat(iron, 'Pellets')) },
      { label: '(b) Revenues (US$ mn)', fn: Rv(ironTot), strong: true },
      { label: '(c) Working days', fn: W(), d: 'abs' },
      { label: '(a)/(c) Avg. volume per working day', fn: perDay(ironTot) },
      { label: '(b)/(a) Average price (US$/t)', fn: price(ironTot) },
      { label: 'Fines', lvl: 1, fn: price(cat(iron, 'Fines')) },
      { label: 'Pellets', lvl: 1, fn: price(cat(iron, 'Pellets')) }
    ], C);
    // a tabela "Iron Ore Volumes (ktons)" do PDF: um porto por linha + Others + Total
    var ironPortTable = buildLines(IRON_PORTS.map(function (p) {
      return { label: p[1], fn: (function (m) { return function (s) { return s(m); }; })(ironPortS[p[0]]) };
    }).concat([{ label: 'Others', fn: function (s) { return s(ironPortS.Others); } },
      { label: 'Total', strong: true, fn: function (s) {
        var t = 0, ok = true;
        Object.keys(ironPortS).forEach(function (k) { var x = s(ironPortS[k]); if (isNum(x)) t += x; else ok = false; });
        return ok ? t : null; } }]), C);
    var pulpTable = buildLines([
      { label: 'Market pulp (ktons)', fn: V(mkt), strong: true },
      { label: 'BKP', lvl: 1, fn: V(bkp) },
      { label: 'Hardwood', lvl: 2, fn: V(pm('hardwood')) },
      { label: 'Softwood', lvl: 2, fn: V(pm('softwood')) },
      { label: 'UKP / BCTMP', lvl: 1, fn: V(pm('ukp')) },
      { label: 'Dissolving pulp¹ (ktons)', fn: V(pm('dissolving')), strong: true },
      { label: 'BKP revenues (US$ mn)', fn: Rv(bkp), strong: true },
      { label: 'Hardwood', lvl: 1, fn: Rv(pm('hardwood')) },
      { label: 'Softwood', lvl: 1, fn: Rv(pm('softwood')) },
      { label: '(c) Working days', fn: W(), d: 'abs' },
      { label: 'BKP avg. volume per working day', fn: perDay(bkp) },
      { label: 'BKP average price (US$/t)', fn: price(bkp), strong: true },
      { label: 'Hardwood', lvl: 1, fn: price(pm('hardwood')) },
      { label: 'Softwood', lvl: 1, fn: price(pm('softwood')) }
    ], C);

    // ── portos da celulose (todas as fibras; o porto não vem quebrado por produto)
    var PORTS = [['Santos', 'Santos (Suzano, Eldorado, Bracell)'], ['Vitória', 'Vitória (Suzano)'],
      ['Rio Grande', 'Rio Grande (CMPC)'], ['São Luís', 'São Luís (Suzano)'], ['Paranaguá', 'Paranaguá (Klabin)']];
    var portRows = rows(pp, 'SELECT period, port, volume_ktons AS v FROM secex_pulp_port');
    var portS = {}; PORTS.forEach(function (p) { portS[p[0]] = {}; }); portS.Others = {};
    portRows.forEach(function (r) {
      var k = portS[r.port] ? r.port : 'Others';
      if (isNum(r.v)) portS[k][r.period] = (portS[k][r.period] || 0) + r.v;
    });
    var portPer = {}; portRows.forEach(function (r) { portPer[r.period] = 1; });
    Object.keys(portS).forEach(function (k) { Object.keys(portPer).forEach(function (pp2) { if (!isNum(portS[k][pp2])) portS[k][pp2] = 0; }); });
    var portTable = buildLines(PORTS.map(function (p) {
      return { label: p[1], fn: (function (m) { return function (s) { return s(m); }; })(portS[p[0]]) };
    }).concat([{ label: 'Others', fn: function (s) { return s(portS.Others); } },
      { label: 'Total pulp (all grades)', fn: function (s) {
        var t = 0, ok = true;
        Object.keys(portS).forEach(function (k) { var x = s(portS[k]); if (isNum(x)) t += x; });
        return ok ? t : null; }, strong: true }]), C);

    // ── origens / destinos (país)
    function topCountries(sql, params, months, n) {
      var tot = {}, all = 0;
      rows(sm === null ? pp : sm, sql, params).forEach(function (r) {
        if (months.indexOf(r.period) < 0 || !isNum(r.v)) return;
        tot[r.country] = (tot[r.country] || 0) + r.v; all += r.v;
      });
      var list = Object.keys(tot).map(function (c) { return { c: c, v: tot[c] }; }).sort(function (a, b) { return b.v - a.v; });
      return { list: list.slice(0, n || 8), all: all };
    }
    var monthsQ = C.months.q;
    var originsM = topCountries("SELECT period, country, SUM(volume_ktons) AS v FROM secex_country WHERE direction='imp' AND category IN ('flat','long','semi') AND period IN (" +
      [period].concat(monthsQ).map(function () { return '?'; }).join(',') + ") GROUP BY period, country", [period].concat(monthsQ), [period], 8);
    var originsQ = topCountries("SELECT period, country, SUM(volume_ktons) AS v FROM secex_country WHERE direction='imp' AND category IN ('flat','long','semi') AND period IN (" +
      monthsQ.map(function () { return '?'; }).join(',') + ") GROUP BY period, country", monthsQ, monthsQ, 30);
    var prevM = topCountries("SELECT period, country, SUM(volume_ktons) AS v FROM secex_country WHERE direction='imp' AND category IN ('flat','long','semi') AND period=? GROUP BY period, country",
      [pAdd(period, -1)], [pAdd(period, -1)], 30);
    var ironDest = topCountries("SELECT period, country, SUM(volume_ktons) AS v FROM secex_iron_ore WHERE direction='exp' AND period IN (" +
      monthsQ.map(function () { return '?'; }).join(',') + ") GROUP BY period, country", monthsQ, monthsQ, 6);
    var pulpDest = (function () {
      var tot = {}, all = 0;
      rows(pp, 'SELECT period, country, SUM(volume_ktons) AS v FROM secex_pulp_country WHERE period IN (' + monthsQ.map(function () { return '?'; }).join(',') + ') GROUP BY period, country', monthsQ)
        .forEach(function (r) { if (isNum(r.v)) { tot[r.country] = (tot[r.country] || 0) + r.v; all += r.v; } });
      var list = Object.keys(tot).map(function (c) { return { c: c, v: tot[c] }; }).sort(function (a, b) { return b.v - a.v; });
      return { list: list.slice(0, 6), all: all };
    })();

    // ── séries dos gráficos
    var m25 = pRange(pAdd(period, -24), period);
    function serM(map, ms) { return ms.map(function (p) { var x = map[p]; return isNum(x) ? x : null; }); }
    function priceSer(o, ms) { return ms.map(function (p) { var v = o.vol[p], r = o.rev[p]; return (isNum(v) && isNum(r) && v > 0) ? r / v * 1000 : null; }); }
    // trimestres: 21 (o recorte do relatório), o atual pode ser QTD
    var qs = [], qq = Q;
    for (var i = 0; i < 21; i++) { qs.unshift(qq); qq = prevQuarter(qq); }
    function qSum(map, Qx) { var ms = Qx.months.filter(function (p) { return p <= period; }); return sumOver(map, ms); }
    function serQ(map, list) { return list.map(function (Qx) { return qSum(map, Qx); }); }
    var qLabels = qs.map(function (Qx) { return Qx.label + (Qx.label === Q.label && !Q.end ? '*' : ''); });
    var q12 = qs.slice(-12), q12L = qLabels.slice(-12);
    var m60 = pRange(pAdd(period, -59), period);

    // modelo preditivo: acumulado de 12 meses, exportação China+Coreia deslocada +3 meses
    var pred = {};
    ['HRC', 'CRC', 'Coated'].forEach(function (prod) {
      var impR = rows(sm, 'SELECT period, country, volume_kg FROM import_prediction WHERE product=? AND country IN (\'China\',\'Korea\') ORDER BY period', [prod]);
      var expR = rows(sm, 'SELECT period, country, volume_kg FROM pred_exports WHERE product=? AND country IN (\'China\',\'Korea\') ORDER BY period', [prod]);
      if (!impR.length || !expR.length) return;
      var imp12 = roll12ByCountry(impR), exp12 = roll12ByCountry(expR);
      var expShift = {}; Object.keys(exp12).forEach(function (p) { expShift[pAdd(p, 3)] = exp12[p]; });
      var last = Object.keys(expShift).sort().pop();
      var xs = pRange('2022-12', last > period ? last : period).filter(function (p) { return p <= pAdd(period, 3); });
      pred[prod] = { x: xs,
        imp: xs.map(function (p) { return p <= period && isNum(imp12[p]) ? imp12[p] / 1e6 : null; }),
        exp: xs.map(function (p) { return isNum(expShift[p]) ? expShift[p] / 1e6 : null; }) };
    });

    var missing = [];
    if (!isNum(ironTot.vol[period])) missing.push('minério de ferro');
    if (!ironPortRows.some(function (r) { return r.period === period; })) missing.push('minério por porto');
    if (!isNum(pm('hardwood').vol[period]) || !pm('hardwood').vol[period]) missing.push('celulose');
    if (!portRows.some(function (r) { return r.period === period; })) missing.push('celulose por porto');
    if (!isNum(wd[period])) missing.push('dias úteis (calendário)');
    var model = {
      kind: 'secex', missing: missing, period: period, label: monLabel(period), long: monLong(period), monthName: monName(period),
      quarter: { label: Q.label, end: Q.end, prev: C.Qp.label, yago: C.Qy.label, months: Q.months },
      C: C, cols: C.labels,
      snapExp: snapExp, snapImp: snapImp,
      impTable: steelTable(imp, 'imports'), expTable: steelTable(exp, 'exports'),
      ironTable: ironTable, ironPortTable: ironPortTable, pulpTable: pulpTable, portTable: portTable,
      charts: {
        m25: m25, qLabels: qLabels, q12L: q12L, m60: m60,
        impVol: serM(cat(imp, 'total').vol, m25), impPrice: priceSer(cat(imp, 'total'), m25),
        expVol: serM(cat(exp, 'total').vol, m25), expPrice: priceSer(cat(exp, 'total'), m25),
        impQ: { flat: serQ(cat(imp, 'flat').vol, qs), semi: serQ(cat(imp, 'semi').vol, qs), long: serQ(cat(imp, 'long').vol, qs) },
        expQ: { flat: serQ(cat(exp, 'flat').vol, qs), semi: serQ(cat(exp, 'semi').vol, qs), long: serQ(cat(exp, 'long').vol, qs) },
        ironQ: serQ(ironTot.vol, q12),
        ironM: { fines: serM(cat(iron, 'Fines').vol, m60), pellets: serM(cat(iron, 'Pellets').vol, m60), price: priceSer(ironTot, m60) },
        ironPorts: IRON_PORTS.map(function (p) { return { name: p[1], v: serM(ironPortS[p[0]], m60) }; }).concat([{ name: 'Others', v: serM(ironPortS.Others, m60) }]),
        pulpQ: { market: serQ(mkt.vol, qs), diss: serQ(pm('dissolving').vol, qs) },
        ports: PORTS.map(function (p) { return { name: p[0], v: serM(portS[p[0]], m60) }; }).concat([{ name: 'Others', v: serM(portS.Others, m60) }]),
        pred: pred
      },
      origins: { month: originsM, quarter: originsQ, prev: prevM }, ironDest: ironDest, pulpDest: pulpDest,
      raw: { imp: imp, exp: exp, ironTot: ironTot, ironPortS: ironPortS, mkt: mkt, portS: portS, wd: wd }
    };
    // KPIs do topo
    function kp(label, o, unit, scale, dec) {
      var Cm = C.months, s = function (ms) { return sumOver(o.vol, ms); };
      var m = s(Cm.m), m1 = s(Cm.m1), y1 = s(Cm.y1), q = s(Cm.q), qp = Cm.qp ? s(Cm.qp) : null;
      var spark = pRange(pAdd(period, -12), period).map(function (p) { return isNum(o.vol[p]) ? o.vol[p] : null; });
      return { label: label, unit: unit, scale: scale || 1, dec: dec || 0, value: isNum(m) ? m / (scale || 1) : null, spark: spark,
        sparkX: pRange(pAdd(period, -12), period), when: monLabel(period),
        d1: { lbl: 'MoM', v: chg(m, m1) }, d2: { lbl: 'YoY', v: chg(m, y1) },
        dq: Q.end ? { lbl: 'QoQ', v: chg(q, qp), q: isNum(q) ? q / (scale || 1) : null, ql: Q.label } : null };
    }
    model.kpis = [
      kp('Steel imports', cat(imp, 'total'), 'kt'), kp('Steel exports', cat(exp, 'total'), 'kt'),
      kp('Iron ore exports', ironTot, 'Mt', 1000, 1), kp('Market pulp exports', mkt, 'kt')
    ];
    // números que o rascunho cita
    function g(lines, label) { for (var i = 0; i < lines.length; i++) if (lines[i].label === label) return lines[i]; return null; }
    model.n = {
      impTot: g(model.impTable, '(a) Total imports (ktons)'), impFlat: g(model.impTable, 'Flat steel volumes (ktons)'),
      impLong: g(model.impTable, 'Long steel volumes (ktons)'), impSemi: g(model.impTable, 'Semi-finished volumes (ktons)'),
      expTot: g(model.expTable, '(a) Total exports (ktons)'), ironVol: g(model.ironTable, '(a) Volumes (ktons)'),
      ironPx: g(model.ironTable, '(b)/(a) Average price (US$/t)'), pulpMkt: g(model.pulpTable, 'Market pulp (ktons)'),
      bkpPx: g(model.pulpTable, 'BKP average price (US$/t)'),
      vit: g(model.portTable, 'Vitória (Suzano)'), rg: g(model.portTable, 'Rio Grande (CMPC)')
    };
    return model;
  };

  // ── rascunho da SECEX
  R.secexDraft = function (M) {
    var q = M.quarter.end, N = M.n, per = q ? M.quarter.label : M.monthName;
    function lv(l) { return l ? (q ? l.v.q : l.v.m) : null; }
    function dP(l) { return l ? (q ? l.d.qoq : l.d.mom) : null; }
    function dY(l) { return l ? (q ? l.d.qyoy : l.d.yoy) : null; }
    var cmp = q ? 'QoQ' : 'MoM';
    function pair(l) { return '(' + signedApprox(dP(l)) + ' ' + cmp + ' and ' + signedApprox(dY(l)) + ' YoY)'; }
    function kt(v) { return isNum(v) ? '~' + fmtInt(Math.round(v)) + ' ktons' : '—'; }
    function mt(v) { return !isNum(v) ? '—' : Math.abs(v) >= 1000 ? '~' + fmtNum(v / 1000, 1) + ' mtons' : '~' + fmtInt(Math.round(v)) + ' ktons'; }
    var items = [];
    var it = dP(N.impTot);
    items.push({ lead: 'Steel imports ' + (it > 0.03 ? 'rebounded' : it < -0.03 ? 'declined' : 'were broadly stable') + ' in ' + per + '.',
      text: 'Total steel imports reached ' + kt(lv(N.impTot)) + ' in ' + per + ' ' + pair(N.impTot) + '. Flat steel imports totaled ' + kt(lv(N.impFlat)) + ' ' + pair(N.impFlat) +
        ', long steel ' + kt(lv(N.impLong)) + ' ' + pair(N.impLong) + ' and semi-finished ' + kt(lv(N.impSemi)) + ' ' + pair(N.impSemi) + '.', lvl: 0 });
    var o = M.origins.month, top = o.list[0];
    if (top && o.all > 0) {
      var prevShare = null; (M.origins.prev.list || []).forEach(function (r) { if (r.c === top.c && M.origins.prev.all > 0) prevShare = r.v / M.origins.prev.all; });
      items.push({ lead: en(top.c) + ' was the main steel supplier to Brazil in ' + M.monthName + '.',
        text: en(top.c) + ' accounted for ~' + Math.round(top.v / o.all * 100) + '% of Brazil’s total steel imports in ' + M.monthName +
          (isNum(prevShare) ? ' (vs. ~' + Math.round(prevShare * 100) + '% in ' + monName(pAdd(M.period, -1)) + ')' : '') + '.', lvl: 1 });
    }
    if (q) {
      var ms = M.quarter.months, impM = M.raw.imp.total ? M.raw.imp.total.vol : {};
      items.push({ lead: M.monthName + ' imports ' + ((impM[ms[2]] || 0) > (impM[ms[1]] || 0) ? 'accelerated' : 'slowed') + '.',
        text: 'After ' + fmtInt(impM[ms[0]]) + ' ktons in ' + monName(ms[0]) + ' and ' + fmtInt(impM[ms[1]]) + ' ktons in ' + monName(ms[1]) +
          ', steel imports reached ' + fmtInt(impM[ms[2]]) + ' ktons in ' + monName(ms[2]) + '.', lvl: 1 });
    }
    var et = dP(N.expTot);
    items.push({ lead: 'Steel exports ' + (Math.abs(et || 0) <= 0.03 ? 'remained broadly stable' : et > 0 ? 'increased' : 'decreased') + '.',
      text: 'Total steel exports reached ' + kt(lv(N.expTot)) + ' in ' + per + ' ' + pair(N.expTot) + '.', lvl: 1 });
    var ir = dP(N.ironVol);
    items.push({ lead: 'Iron ore exports ' + (ir > 0.01 ? 'increased' : ir < -0.01 ? 'decreased' : 'were stable') + '.',
      text: 'Iron ore exports reached ' + mt(lv(N.ironVol)) + ' in ' + per + ' ' + pair(N.ironVol) + ', at an average export price of US$' +
        fmtInt(lv(N.ironPx)) + '/t (' + signedApprox(dP(N.ironPx)) + ' ' + cmp + ').', lvl: 0 });
    var pu = dP(N.pulpMkt);
    items.push({ lead: 'Pulp exports ' + (pu > 0.03 ? 'recovered' : pu < -0.03 ? 'declined' : 'were broadly stable') + '.',
      text: 'Market pulp exports totaled ' + mt(lv(N.pulpMkt)) + ' in ' + per + ' ' + pair(N.pulpMkt) + '. Vitória port volumes (Suzano) reached ' + mt(lv(N.vit)) +
        ' ' + pair(N.vit) + ', while Rio Grande (CMPC) posted ' + mt(lv(N.rg)) + ' ' + pair(N.rg) + '. BKP average export price at US$' + fmtInt(lv(N.bkpPx)) + '/t (' +
        signedApprox(dP(N.bkpPx)) + ' ' + cmp + ').', lvl: 0 });
    items.push({ lead: 'Our take:', text: '', lvl: 0 });
    return items;
  };
  R.secexDeck = function (M) {
    var q = M.quarter.end, N = M.n, per = q ? M.quarter.label : M.monthName, cmp = q ? 'QoQ' : 'MoM';
    function dP(l) { return l ? (q ? l.d.qoq : l.d.mom) : null; }
    function lv(l) { return l ? (q ? l.v.q : l.v.m) : null; }
    var a = dP(N.impTot);
    return 'Steel imports ' + (a >= 0 ? 'rose ' : 'fell ') + approxPct(a).replace('~', '') + ' ' + cmp + ' in ' + per + ' to ~' + fmtInt(Math.round(lv(N.impTot))) +
      ' ktons; iron ore exports ' + (dP(N.ironVol) >= 0 ? 'grew ' : 'fell ') + approxPct(dP(N.ironVol)).replace('~', '') + ' and market pulp exports ' +
      (dP(N.pulpMkt) >= 0 ? 'grew ' : 'fell ') + approxPct(dP(N.pulpMkt)).replace('~', '') + '.';
  };
  R.secexDefaults = function (M, today) {
    return {
      kicker: 'SECEX · Brazilian trade data',
      big: M.quarter.end ? M.quarter.label : M.label.replace('-', ' '),
      title: 'Steel, Iron Ore & Pulp',
      deck: R.plainToRich(R.secexDeck(M)),
      date: today || '', stamp: 'Brazil | Trade Data',
      takeLead: '',
      takeaways: R.secexDraft(M).map(function (t) { return { lead: t.lead, html: R.plainToRich(t.text), lvl: t.lvl }; }),
      link: { label: 'Read the full report', url: '' },
      team: R.DEFAULT_TEAM.slice(), disclosure: R.DEFAULT_DISCLOSURE_ANALYSTS.slice()
    };
  };
  R.secexAuto = function (M) {
    return [
      ['Fonte', 'SECEX/MDIC pelas tabelas da dashboard (aço: <code>secex_imports/exports</code> + <code>secex_country</code>; minério: <code>secex_iron_ore</code> + <code>secex_iron_ore_port</code>; celulose: <code>secex_pulp_country</code> + <code>secex_pulp_port</code>) — dado até <b>' + esc(M.long) + '</b>, já com as revisões do MDIC.'],
      ['Trimestre', M.quarter.end ? esc(M.quarter.label) + ' cheio contra ' + esc(M.quarter.prev) + ' e ' + esc(M.quarter.yago) + '.' : 'Fora do fim de trimestre: a vista “Quarter” mostra <b>' + esc(M.quarter.label) + ' QTD</b> contra o mesmo pedaço do ano anterior; os gráficos trimestrais marcam o trimestre parcial com *.'],
      ['Dias úteis', 'Um calendário só (<code>calendar</code> do P&amp;P) para aço, minério e celulose.'],
      ['Celulose', 'Market pulp = BKP (fibra curta + longa) + UKP/BCTMP; dissolving inclui a mecânica. <b>Porto = todas as fibras</b> (a base não quebra porto por produto).'],
      ['Minério', 'Por <b>produto</b> (finos/pelotas), <b>porto</b> e <b>destino</b>, do mesmo download do MDIC (porto e país somam igual). O Porto do Açu vem na base como a URF “Campos Dos Goytacazes” e sai como <b>Goytacazes (Açu)</b>; Others é quase todo Corumbá.'],
      ['Preditivo', 'Acumulado de 12 meses: exportação China + Coreia deslocada 3 meses × importação do Brasil, nos mesmos SH6 (a conta da página de M&amp;M).']
    ];
  };

  R.renderSecex = function (M, T, fonts, opts) {
    _chartSeq = 0;
    var K = M.charts, q = M.quarter.end, defV = q ? 'q' : 'm';
    var curQ = M.quarter.months.filter(function (p) { return p <= M.period; });
    var hlIdx = K.m25.map(function (p, i) { return curQ.indexOf(p) >= 0 ? i : -1; }).filter(function (i) { return i >= 0; });
    function monthlyBars(vol, px, ttl, pxName) {
      return chartHTML({ title: ttl, sub: 'ktons (lhs) · US$/t (rhs) · monthly · current quarter in white', x: K.m25, xMode: 'my', band: true, ticks: 7, height: 250, hl: hlIdx, hlCls: 'f',
        series: [{ name: 'Volume', type: 'bar', cls: 'g2', values: vol, fmt: 'int' },
          { name: pxName, type: 'line', axis: 'r', cls: 'o', values: px, fmt: 'usd', w: 2.2 }],
        yl: { fmt: 'int' }, yr: { fmt: 'usd', zero: false } });
    }
    function qStack(o, ttl) {
      return chartHTML({ title: ttl, sub: 'ktons · quarterly' + (q ? '' : ' · * quarter to date'), x: K.qLabels, xMode: 'raw', band: true, ticks: 7, height: 240, stack: true,
        series: [{ name: 'Flat', type: 'bar', cls: 'f', values: o.flat }, { name: 'Semi-finished', type: 'bar', cls: 'o', values: o.semi },
          { name: 'Long', type: 'bar', cls: 'g1', values: o.long }], yl: { fmt: 'int' } });
    }
    var fnS = '<p class="fn">Source: SECEX/MDIC, Itaú BBA. Working days per Brazilian calendar. Average price = FOB revenue ÷ volume.</p>';
    // origens
    var oM = M.origins.month, oQ = M.origins.quarter;
    var qShare = {}; oQ.list.forEach(function (r) { qShare[r.c] = oQ.all ? r.v / oQ.all : null; });
    var origins = oM.list.length ? hbarsHTML('Who is shipping steel to Brazil', 'Top origins in ' + M.label + ' · ktons and share of total steel imports' + (isNum(qShare[oM.list[0].c]) ? ' · share in ' + (q ? M.quarter.label : M.quarter.label + ' QTD') : ''),
      oM.list.map(function (r, i) {
        return { name: en(r.c), v: r.v, hl: i === 0, vtxt: fmtInt(r.v) + ' kt · ' + fmtShare(oM.all ? r.v / oM.all : null, 0),
          stxt: isNum(qShare[r.c]) ? fmtShare(qShare[r.c], 0) : '' };
      }), 'Flat + long + semi-finished. Right column: share in the quarter.') : '';
    // preditivo
    var predPanes = {}, predOpts = [];
    ['HRC', 'CRC', 'Coated'].forEach(function (p) {
      var d = K.pred[p]; if (!d) return;
      predOpts.push([p.toLowerCase(), p]);
      predPanes[p.toLowerCase()] = chartHTML({ title: '12-month accumulated — China + Korea exports vs. Brazil imports (' + p + ')',
        sub: 'ktons · exports shifted +3 months (leading indicator)', x: d.x, xMode: 'my', ticks: 8, height: 250,
        series: [{ name: 'China + Korea exports (+3M)', type: 'line', cls: 'f', values: d.exp, w: 1.8 },
          { name: 'Brazil imports', type: 'line', cls: 'o', values: d.imp, w: 2.4 }], yl: { fmt: 'int' },
        note: 'Brazil imports lag China and South Korea exports by ~3 months (e.g., January exports are a proxy for April imports). Same SH6 codes on both sides.' });
    });
    var predHTML = predOpts.length ? tabsHTML([{ label: 'Product', opts: predOpts }], predPanes, [predOpts[0][0]]) : '<p class="fn">No prediction data.</p>';
    // minério
    var ironQ = chartHTML({ title: 'Quarterly iron ore exports', sub: 'ktons' + (q ? '' : ' · * quarter to date'), x: K.q12L, xMode: 'raw', band: true, ticks: 12, height: 230, hl: [K.q12L.length - 1], hlCls: 'o',
      series: [{ name: 'Iron ore exports', type: 'bar', cls: 'g1', values: K.ironQ }], yl: { fmt: 'int' }, legend: false });
    var ironM = chartHTML({ title: 'Monthly iron ore exports by product', sub: 'ktons (lhs) · average price US$/t (rhs) · 5 years', x: K.m60, xMode: 'my', ticks: 7, height: 240, stack: true,
      series: [{ name: 'Fines', type: 'area', cls: 'o', values: K.ironM.fines }, { name: 'Pellets', type: 'area', cls: 'g1', values: K.ironM.pellets },
        { name: 'Average price', type: 'line', axis: 'r', cls: 'f', values: K.ironM.price, fmt: 'usd', w: 1.4, dash: true }],
      yl: { fmt: 'int' }, yr: { fmt: 'usd', zero: false } });
    // por porto: só aparece se a base tiver o porto (banco antigo sem a tabela → bloco some, sem travessões)
    var ironPortCls = ['o', 'f', 'g1', 'g2', 'g3'];
    var ironPortSer = K.ironPorts || [];
    var ironPortHTML = ironPortSer.some(function (p) { return p.v.some(isNum); })
      ? '<h3 class="sh sm">Iron ore exports by port</h3>' + viewTable(M.ironPortTable, M.C, defV, 'ktons') +
        chartHTML({ title: 'Monthly iron ore exports by port', sub: 'ktons · 5 years', x: K.m60, xMode: 'my', ticks: 7, height: 250, stack: true,
          series: ironPortSer.map(function (p, i) { return { name: p.name, type: 'area', cls: ironPortCls[i], values: p.v }; }), yl: { fmt: 'int' } })
      : '';
    var iD = M.ironDest;
    var ironDest = iD.list.length ? hbarsHTML('Iron ore destinations', 'Share of exports in ' + (q ? M.quarter.label : M.quarter.label + ' QTD'),
      iD.list.map(function (r, i) { return { name: en(r.c), v: r.v, hl: i === 0, vtxt: fmtNum(r.v / 1000, 1) + ' Mt · ' + fmtShare(iD.all ? r.v / iD.all : null, 0) }; })) : '';
    // celulose
    var portCls = ['o', 'f', 'g1', 'g2', 'g3', 'y0'];
    var pulpPorts = chartHTML({ title: 'Monthly pulp exports by port', sub: 'ktons · all grades · 5 years', x: K.m60, xMode: 'my', ticks: 7, height: 250, stack: true,
      series: K.ports.map(function (p, i) { return { name: p.name, type: 'area', cls: portCls[i], values: p.v }; }), yl: { fmt: 'int' } });
    var pulpQ = chartHTML({ title: 'Quarterly pulp exports', sub: 'ktons' + (q ? '' : ' · * quarter to date'), x: K.qLabels, xMode: 'raw', band: true, ticks: 7, height: 230, stack: true,
      series: [{ name: 'Market pulp', type: 'bar', cls: 'o', values: K.pulpQ.market }, { name: 'Dissolving / mechanical', type: 'bar', cls: 'f', values: K.pulpQ.diss }], yl: { fmt: 'int' } });
    var pD = M.pulpDest;
    var pulpDest = pD.list.length ? hbarsHTML('Pulp destinations', 'Share of exports in ' + (q ? M.quarter.label : M.quarter.label + ' QTD') + ' · all grades',
      pD.list.map(function (r, i) { return { name: en(r.c), v: r.v, hl: i === 0, vtxt: fmtInt(r.v) + ' kt · ' + fmtShare(pD.all ? r.v / pD.all : null, 0) }; })) : '';

    var nTk = (T.takeaways || []).filter(function (t) { return R.cleanRich(t.html).replace(/<[^>]+>/g, '').trim(); }).length;
    var sections = [
      { id: 'take', num: 'I', title: 'Key takeaways', short: 'Key takeaways', count: nTk ? String(nTk) : '', dock: 'Takeaways', shapes: true,
        html: (T.takeLead ? '<p class="lead">' + R.cleanRich(T.takeLead) + '</p>' : '') + takeawaysHTML(T) },
      { id: 'data', num: 'II', title: 'Summary', short: 'Summary', dock: 'Data', aside: 'Exports and imports',
        html: '<h3 class="sh first">Exports</h3>' + viewTable(M.snapExp, M.C, defV, '') + '<h3 class="sh">Imports</h3>' + viewTable(M.snapImp, M.C, defV, '') + fnS },
      { id: 'steel', num: 'III', title: 'Steel', short: 'Steel', dock: 'Steel',
        html: '<h3 class="sh first">Imports</h3>' + viewTable(M.impTable, M.C, defV, '') + monthlyBars(K.impVol, K.impPrice, 'Monthly steel imports and revenue per ton', 'Revenue per ton') +
          qStack(K.impQ, 'Quarterly steel imports by type') + origins +
          '<h3 class="sh">Prediction analysis</h3>' + predHTML +
          '<h3 class="sh">Exports</h3>' + viewTable(M.expTable, M.C, defV, '') + monthlyBars(K.expVol, K.expPrice, 'Monthly steel exports and revenue per ton', 'Revenue per ton') +
          qStack(K.expQ, 'Quarterly steel exports by type') + fnS },
      { id: 'iron', num: 'IV', title: 'Iron ore & pulp', short: 'Iron ore & pulp', dock: 'Ore & pulp',
        html: '<h3 class="sh first">Iron ore exports</h3>' + viewTable(M.ironTable, M.C, defV, '') + ironQ + ironM + ironPortHTML + ironDest +
          '<h3 class="sh">Pulp exports</h3>' + viewTable(M.pulpTable, M.C, defV, '') +
          '<p class="fn">¹ Dissolving pulp includes mechanical pulp. Market pulp = BKP (hardwood + softwood) + UKP/BCTMP.</p>' +
          '<h3 class="sh sm">Pulp exports by port (all grades)</h3>' + viewTable(M.portTable, M.C, defV, 'ktons') + pulpPorts + pulpQ + pulpDest + fnS }
    ];
    return documentHTML({
      kind: 'secex', texts: T, fonts: fonts, sections: sections, calm: !!(opts && opts.calm),
      kpis: M.kpis, kpiCap: [M.long, 'Monthly export and import volumes \u00b7 charts show the last 13 months'],
      sourceNote: 'Source: SECEX/MDIC, Itaú BBA. Data through ' + M.long + ', including MDIC revisions.'
    });
  };


  // ═══════════════ 11. O CLIPPING DE NOTÍCIAS (o HTML do WhatsApp) ════════════
  // O modelo chega PRONTO do robô do clipping (news-hunter/clipping/whatsapp.py), montado
  // dos MESMOS itens que geram o Word (corpo raspado, tradução, take, setor, publicações,
  // analistas) e no mesmo run. Aqui só se desenha — o conteúdo nunca diverge do Word.
  //   M = { date, intro_html, highlights:[str], items:[{i, sector, take, title, tr_title,
  //         source, url, blocks:[…], tr_blocks:[…]}], publications:[{sector,name,link,date,
  //         companies}], earnings:{label, items:[…]}|null, analysts:[{name,role,phone,email}] }
  //   bloco = {t:'p'|'h3'|'h4'|'q'|'li'|'img'|'table', x, b, src, w, h, rows}
  var NEWS_SECTOR = { NR: 'Natural Resources', SM: 'Steel & Mining', PP: 'Pulp & Paper' };
  var NEWS_SHORT = { NR: 'NR', SM: 'S&M', PP: 'P&P' };
  var NEWS_ORDER = ['NR', 'SM', 'PP'];          // a ordem do Word: NR → S&M → P&P

  function takeBadge(t) {
    var k = t === '+' ? 'pos' : t === '-' ? 'neg' : 'neu';
    var lbl = t === '+' ? 'positive' : t === '-' ? 'negative' : 'neutral';
    return '<span class="tkb ' + k + '" title="Our take: ' + lbl + '" role="img" aria-label="Our take: ' + lbl + '">' +
      (t === '+' ? '+' : t === '-' ? MINUS : '=') + '</span>';
  }
  function shortDate(ymd) {                      // '2026-11-09' → 'Nov 9'
    var d = String(ymd || '').slice(0, 10).split('-');
    return d.length === 3 ? MON[+d[1] - 1] + ' ' + (+d[2]) : '';
  }
  // um bloco do corpo → HTML (só texto escapado; imagem só se vier embutida como data:image)
  function newsBlocksHTML(blocks) {
    var out = '', inList = false;
    (blocks || []).forEach(function (b) {
      if (b.t !== 'li' && inList) { out += '</ul>'; inList = false; }
      if (b.t === 'p') out += '<p>' + (b.b ? '<b>' + esc(b.x) + '</b>' : esc(b.x)) + '</p>';
      else if (b.t === 'h3') out += '<h4 class="bh">' + esc(b.x) + '</h4>';
      else if (b.t === 'h4') out += '<h5 class="bh2">' + esc(b.x) + '</h5>';
      else if (b.t === 'q') out += '<blockquote>' + esc(b.x) + '</blockquote>';
      else if (b.t === 'li') { if (!inList) { out += '<ul>'; inList = true; } out += '<li>' + esc(b.x) + '</li>'; }
      else if (b.t === 'img' && /^data:image\/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+\/=]+$/.test(b.src || '')) {
        out += '<figure class="st-img"><img src="' + b.src + '"' + (b.w ? ' width="' + (+b.w) + '"' : '') + (b.h ? ' height="' + (+b.h) + '"' : '') +
          ' alt="" loading="lazy" decoding="async"></figure>';
      } else if (b.t === 'table' && b.rows && b.rows.length) {
        out += '<div class="tw st-tw"><table class="dt st-t"><tbody>' + b.rows.map(function (r) {
          return '<tr>' + (r || []).map(function (c) {
            var tag = c.header ? 'th' : 'td', sp = +c.colspan > 1 ? ' colspan="' + (+c.colspan) + '"' : '';
            var al = c.align === 'right' || c.align === 'center' ? ' style="text-align:' + c.align + '"' : '';
            return '<' + tag + sp + al + '>' + esc(c.text || '') + '</' + tag + '>';
          }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>';
      }
    });
    if (inList) out += '</ul>';
    return out;
  }
  // corpo da matéria: o começo à vista, o resto atrás de "Read the full story" (<details>
  // nativo — abre e fecha sem JavaScript; na impressão o script abre todos)
  function storyBodyHTML(blocks) {
    blocks = blocks || [];
    if (!blocks.length) return '<p class="st-na">Full text not available — see the source.</p>';
    var nP = 0, cut = blocks.length;
    for (var i = 0; i < blocks.length; i++) {
      if (blocks[i].t === 'p') nP++;
      if (nP >= 2 || (blocks[i].t === 'img' && i > 0)) { cut = i + 1; break; }
    }
    if (blocks.length - cut <= 1) cut = blocks.length;      // não esconde um parágrafo só
    var lead = newsBlocksHTML(blocks.slice(0, cut)), rest = blocks.slice(cut);
    var nRest = rest.filter(function (b) { return b.t === 'p' || b.t === 'li'; }).length;
    return lead + (rest.length ? '<details class="rm"><summary><span class="o">Read the full story' +
      (nRest ? ' <em>(' + nRest + ' more paragraph' + (nRest > 1 ? 's' : '') + ')</em>' : '') + '</span><span class="c">Show less</span></summary>' +
      newsBlocksHTML(rest) + '</details>' : '');
  }

  R.newsDefaults = function (M) {
    var items = M.items || [], n = items.length;
    var by = {}; items.forEach(function (it) { by[it.sector] = (by[it.sector] || 0) + 1; });
    var parts = NEWS_ORDER.filter(function (s) { return by[s]; }).map(function (s) { return by[s] + ' in ' + NEWS_SECTOR[s]; });
    var d = String(M.date || '').slice(0, 10).split('-');
    return {
      kicker: 'LatAm S&M and P&P',
      big: d.length === 3 ? MON[+d[1] - 1] + ' ' + (+d[2]) : '',
      title: 'Daily News',
      deck: esc(n + ' headline' + (n === 1 ? '' : 's') + ' this morning' + (parts.length > 1 ? ' — ' + parts.join(' and ') : parts.length ? ' in ' + NEWS_SECTOR[NEWS_ORDER.filter(function (s) { return by[s]; })[0]] : '') + ' — with our take on each.'),
      date: M.date || '', stamp: 'Brazil & LatAm | Daily News',
      team: (M.analysts && M.analysts.length ? M.analysts : R.DEFAULT_TEAM).map(function (a) {
        return { name: a.name || '', email: a.email || '', phone: a.phone || '' };
      }),
      disclosure: R.DEFAULT_DISCLOSURE_ANALYSTS.slice()
    };
  };

  R.renderNews = function (M, T, fonts, opts) {
    _chartSeq = 0;
    T = T || R.newsDefaults(M);
    var items = (M.items || []).slice();
    var secs = NEWS_ORDER.filter(function (s) { return items.some(function (it) { return it.sector === s; }); });
    // ordem = a do Word: setor (NR → S&M → P&P) e, dentro dele, a ordem que o analista montou
    var ordered = [];
    secs.forEach(function (s) { items.forEach(function (it) { if (it.sector === s) ordered.push(it); }); });
    var cnt = { '+': 0, '=': 0, '-': 0 };
    ordered.forEach(function (it) { cnt[it.take === '+' || it.take === '-' ? it.take : '=']++; });
    function headOf(it) { return it.tr_title || it.title; }

    // ── manchetes: o placar dos takes + filtro por setor + a lista (cada uma leva à matéria)
    var glance = '<div class="glance"><span><b>' + ordered.length + '</b> headline' + (ordered.length === 1 ? '' : 's') + '</span>' +
      '<span class="gb" aria-hidden="true">' + ['+', '=', '-'].map(function (t) {
        return cnt[t] ? '<i class="' + (t === '+' ? 'pos' : t === '-' ? 'neg' : 'neu') + '" style="flex:' + cnt[t] + '"></i>' : '';
      }).join('') + '</span><span class="gl">' +
      [['+', 'positive', 'pos'], ['=', 'neutral', 'neu'], ['-', 'negative', 'neg']].map(function (x) {
        return '<span class="gk"><i class="' + x[2] + '"></i>' + cnt[x[0]] + ' ' + x[1] + '</span>';
      }).join('') + '</span></div>';
    var filter = secs.length > 1 ? '<div class="filter" role="group" aria-label="Filter by sector"><button type="button" data-f="all" aria-pressed="true">All<span>' + ordered.length + '</span></button>' +
      secs.map(function (s) {
        return '<button type="button" data-f="' + s + '" aria-pressed="false">' + esc(NEWS_SECTOR[s]) + '<span>' +
          ordered.filter(function (it) { return it.sector === s; }).length + '</span></button>';
      }).join('') + '</div>' : '';
    var heads = secs.map(function (s) {
      return '<h3 class="hl-sec" data-sector="' + s + '">' + esc(NEWS_SECTOR[s]) + '</h3>' + ordered.filter(function (it) { return it.sector === s; }).map(function (it) {
        return '<a class="hl-row" data-sector="' + s + '" href="#st-' + (+it.i) + '">' + takeBadge(it.take) +
          '<span class="hl-t">' + esc(headOf(it)) + (it.tr_title ? '<small>' + esc(it.title) + '</small>' : '') + '</span>' +
          '<span class="hl-s">' + esc(it.source || '') + '</span></a>';
      }).join('');
    }).join('');

    // ── publicações do time (as mesmas do Word: Recent Publications + Earnings Review)
    function pubList(list) {
      return '<div class="pubs">' + (list || []).filter(function (p) { return p && (p.name || '').trim(); }).map(function (p) {
        var meta = [shortDate(p.date)].concat(p.companies || []).filter(Boolean).join(' · ');
        var inner = '<span class="pub-s">' + esc(NEWS_SHORT[p.sector] || 'S&M') + '</span><span class="pub-n">' + esc(p.name) + '</span>' +
          '<span class="pub-m">' + esc(meta) + '</span>';
        return /^https?:\/\//i.test(p.link || '') ? '<a class="pub" href="' + esc(p.link) + '" target="_blank" rel="noopener">' + inner + '</a>'
          : '<div class="pub">' + inner + '</div>';
      }).join('') + '</div>';
    }
    var pubs = (M.publications || []).filter(function (p) { return p && (p.name || '').trim(); });
    var er = M.earnings && (M.earnings.items || []).some(function (p) { return p && (p.name || '').trim(); }) ? M.earnings : null;
    var research = (pubs.length ? pubList(pubs) : '<p class="fn">No new publications.</p>') +
      (er ? '<h3 class="sh">' + esc(er.label || 'Earnings Review') + '</h3>' + pubList(er.items) : '');
    var nRes = pubs.length + (er ? er.items.filter(function (p) { return p && (p.name || '').trim(); }).length : 0);

    // ── as matérias completas, por setor
    var stories = secs.map(function (s, si) {
      var list = ordered.filter(function (it) { return it.sector === s; });
      return '<h3 class="sh' + (si === 0 ? ' first' : '') + ' st-sec" data-sector="' + s + '">' + esc(NEWS_SECTOR[s]) +
        ' <span>' + list.length + ' stor' + (list.length === 1 ? 'y' : 'ies') + '</span></h3>' + list.map(function (it) {
          var src = /^https?:\/\//i.test(it.url || '') ? '<a class="st-l" href="' + esc(it.url) + '" target="_blank" rel="noopener">Open at ' + esc(it.source || 'source') + '</a>' : '';
          var body;
          if (it.tr_title && (it.tr_blocks || []).length) {
            body = tabsHTML([{ label: 'Language', opts: [['en', 'English (free translation)'], ['or', 'Original']] }], {
              en: '<div class="st-b">' + storyBodyHTML(it.tr_blocks) + '</div>',
              or: '<div class="st-b"><p class="st-o2">' + esc(it.title) + '</p>' + storyBodyHTML(it.blocks) + '</div>'
            }, ['en']);
          } else {
            body = '<div class="st-b">' + storyBodyHTML(it.blocks) + '</div>';
          }
          return '<article class="story" id="st-' + (+it.i) + '" data-sector="' + s + '">' +
            '<p class="st-meta">' + takeBadge(it.take) + '<span class="sec">' + esc(NEWS_SECTOR[s]) + '</span><span>' + esc(it.source || '') + '</span></p>' +
            '<h3 class="st-h">' + esc(headOf(it)) + '</h3>' + (it.tr_title ? '<p class="st-o">Original: ' + esc(it.title) + '</p>' : '') +
            body + src + '</article>';
        }).join('');
    }).join('');

    var hl = (M.highlights || []).filter(function (x) { return String(x || '').trim(); });
    var sections = [];
    if (hl.length) sections.push({ id: 'hl', num: 'I', title: 'Highlights', short: 'Highlights', count: String(hl.length), dock: 'Highlights', shapes: true,
      html: '<ul class="tk">' + hl.map(function (x) { return '<li class="tk">' + esc(x) + '</li>'; }).join('') + '</ul>' });
    var R0 = hl.length ? 1 : 0, NUM = ['I', 'II', 'III', 'IV'];
    sections.push({ id: 'heads', num: NUM[R0], title: 'Sector headlines', short: 'Headlines', count: String(ordered.length), dock: 'Headlines', shapes: !hl.length,
      html: glance + filter + '<div class="hl-list">' + heads + '</div>' });
    sections.push({ id: 'research', num: NUM[R0 + 1], title: 'Our research', short: 'Our research', count: nRes ? String(nRes) : '', dock: 'Research',
      aside: 'Itaú BBA reports on the sector', html: research });
    sections.push({ id: 'stories', num: NUM[R0 + 2], title: 'Full stories', short: 'Full stories', count: String(ordered.length), dock: 'Stories',
      html: stories });

    var sources = [];
    ordered.forEach(function (it) { if (it.source && sources.indexOf(it.source) < 0) sources.push(it.source); });
    return documentHTML({
      kind: 'news', texts: T, fonts: fonts, sections: sections, calm: !!(opts && opts.calm),
      note: M.intro_html ? R.cleanRich(M.intro_html) : '',
      sourceNote: 'News: ' + (sources.join(', ') || 'third-party sources') + '. Summaries and free translations of third-party content for our clients; the original articles prevail. Our take (+ positive, = neutral, − negative) is Itaú BBA’s view.'
    });
  };

  // ═══════════════ CSS do documento ═════════════════════════════════════════
  var CSS = [
    ':root{--pos:#2fbf71;--neg:#ff5d5d;--orange:#FF5500;--bg:#000;--fg:#fff;--ink:#E3E3E3;--soft:#BDBDBD;--mute:#939393;--faint:#7E7E7E;--line:#4d4d4d;--grid:#262626;--hover:#141414;--otext:#FF5500;--hair:rgba(255,85,0,.40);--tip-bg:#fff;--tip-fg:#000;--tip-k:#5c5c5c;--area:rgba(255,255,255,.14);--g1:#9a9a9a;--g2:#5e5e5e;--g3:#3a3a3a;--y0:#3d3d3d;--y1:#5a5a5a;--y2:#8a8a8a;--y3:#d6d6d6;--display:"Unbounded","Manrope",system-ui,sans-serif;--body:"Manrope",system-ui,-apple-system,"Segoe UI",Arial,sans-serif;--pad:52px}',
    'html[data-theme="light"]{--pos:#138a4b;--neg:#d92d2d;--bg:#fff;--fg:#000;--ink:#1C1C1C;--soft:#4A4A4A;--mute:#666;--faint:#707070;--line:#BDBDBD;--grid:#E6E6E6;--hover:#F3F3F3;--otext:#C74200;--hair:rgba(255,85,0,.50);--tip-bg:#000;--tip-fg:#fff;--tip-k:#B5B5B5;--area:rgba(0,0,0,.10);--g1:#7a7a7a;--g2:#b0b0b0;--g3:#d4d4d4;--y0:#d2d2d2;--y1:#b4b4b4;--y2:#8a8a8a;--y3:#2e2e2e}',
    '*{box-sizing:border-box;margin:0;padding:0}html{background:var(--bg);-webkit-text-size-adjust:100%;scroll-behavior:smooth}',
    '*{font-feature-settings:"calt" 0!important}body{background:var(--bg);color:var(--ink);font:400 15px/1.58 var(--body);-webkit-font-smoothing:antialiased;-webkit-print-color-adjust:exact;print-color-adjust:exact}',
    'a{color:inherit}.sheet{position:relative;max-width:960px;margin:0 auto;background:var(--bg);overflow:hidden}.pad{padding-left:var(--pad);padding-right:var(--pad)}',
    /* formas */
    '.shapes{position:absolute;pointer-events:none}.shapes path{fill:none;stroke-width:1.15;stroke-dasharray:1.02;transform-box:fill-box;transform-origin:center;transform:rotate(var(--rot,0deg))}.shapes .o{stroke:var(--orange)}.shapes .w{stroke:var(--fg)}',
    /* topo */
    '.hero{position:relative;padding-bottom:70px}.hero .shapes{inset:0;width:100%;height:100%}',
    '.topbar{position:relative;display:flex;justify-content:space-between;align-items:center;gap:18px;padding-top:26px;min-height:96px}',
    '.brand{display:flex;align-items:center;gap:16px;min-width:0}',
    '.emb{flex:none;width:86px;height:86px;overflow:visible}.emb path{fill:none;stroke-width:1.35;stroke-linejoin:round;stroke-dasharray:1.02;transform-box:view-box;transform-origin:60px 60px}',
    '.emb .t1,.emb .t3{stroke:var(--orange)}.emb .t2{stroke:var(--fg)}.emb .t1{transform:rotate(-17deg)}.emb .t2{transform:rotate(24deg)}.emb .t3{transform:rotate(-5deg)}',
    '.emb .lgf{fill:var(--fg);stroke:none}.emb .lg0{transform-box:fill-box;transform-origin:center}',
    '.bt{display:flex;flex-direction:column;gap:5px;min-width:0}.team{font:600 15px/1.25 var(--display);letter-spacing:.02em;color:var(--fg)}.team span{color:var(--orange);font-weight:300;margin:0 .15em}',
    '.bt .sub{font-size:12.5px;color:var(--mute)}',
    '.tools{display:flex;align-items:center;gap:18px}.stamp{font-size:13px;color:var(--mute);text-align:right;background:var(--bg);padding:6px 0 6px 14px;border-radius:10px;white-space:nowrap}.stamp b{font-weight:650;color:var(--fg)}',
    '.theme-sw{display:flex;gap:2px;padding:3px;border:1px solid var(--hair);border-radius:999px;background:var(--bg)}.theme-sw button{all:unset;cursor:pointer;font:650 11.5px/1 var(--body);letter-spacing:.02em;padding:6px 11px;border-radius:999px;color:var(--mute)}.theme-sw button[aria-pressed="true"]{background:var(--orange);color:#000}.theme-sw button:focus-visible{outline:2px solid var(--fg);outline-offset:2px}.no-js .theme-sw,.shot .theme-sw{display:none}',
    '.lockup{position:relative;padding-top:64px}.kicker{font:550 15px/1.3 var(--body);color:var(--otext);margin-bottom:10px}',
    'h1{font-family:var(--display);color:var(--fg);letter-spacing:-.025em}h1 .period{display:block;margin-left:-6px;font-size:clamp(78px,17.5vw,172px);line-height:.98;font-weight:300;letter-spacing:-.035em;color:var(--bg);-webkit-text-stroke:2.3px var(--orange);paint-order:stroke fill;white-space:nowrap}',
    'h1 .word{display:block;margin-top:8px;font-size:clamp(26px,5.4vw,50px);line-height:1.06;font-weight:500}.deck{max-width:31em;margin-top:24px;font-size:17px;line-height:1.5;color:var(--ink)}.deck b{color:var(--fg)}',
    /* KPIs */
    '.kpis{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));grid-auto-rows:auto;gap:0;margin-bottom:34px}',
    '.kcap{grid-column:1/-1;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:4px 16px;padding:0 0 12px;font-size:12.5px;color:var(--mute)}.kcap b{font-weight:650;color:var(--fg)}',
    '.kpi{position:relative;display:grid;grid-row:span 5;grid-template-rows:subgrid;row-gap:0;align-content:start;padding:18px 18px 10px;border-top:1px solid var(--orange);border-bottom:1px solid var(--hair);min-width:0}.kpi+.kpi{border-left:1px solid var(--hair)}',
    '@supports not (grid-template-rows:subgrid){.kpi{display:flex;flex-direction:column}.kq{min-height:30px}}',
    '.kl{align-self:end;font-size:12.5px;line-height:1.3;font-weight:650;color:var(--soft);letter-spacing:.01em}.kv{margin-top:8px;font:300 34px/1 var(--display);letter-spacing:-.03em;color:var(--fg);font-variant-numeric:tabular-nums;white-space:nowrap}.ku{margin-left:5px;font:500 13px/1 var(--body);letter-spacing:0;color:var(--mute)}',
    '.kd{display:flex;flex-direction:column;align-items:flex-start;gap:4px;margin-top:10px;font-size:12px;color:var(--ink);font-variant-numeric:tabular-nums}.dl{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}.dl em{font-style:normal;color:var(--mute)}',
    '.kq{display:flex;flex-direction:column;align-items:flex-start;gap:4px;margin-top:10px;padding-top:9px;border-top:1px solid var(--grid);font-size:12px;color:var(--mute);font-variant-numeric:tabular-nums}.kq:empty{margin:0;padding:0;border:0}.kq b{color:var(--fg);font-weight:650}.kq .kqv{display:inline-flex;align-items:baseline;gap:8px;white-space:nowrap}.kq .kqp{font-weight:650;color:var(--otext)}',
    '.kc{margin-top:12px;min-width:0}',
    'figure.ch.mini{margin:0;padding:0;border:0;border-radius:0;background:transparent}figure.ch.mini+figure.ch.mini{margin-top:0}.ch.mini .plot{height:var(--h,104px);margin-top:0;padding:6px 3px 18px 36px}',
    '.ch.mini .yl{top:6px;bottom:18px;width:32px;font-size:10px}.ch.mini .yl span{right:6px}.ch.mini .xl{left:36px;right:3px;height:14px;font-size:10px}.ch.mini .xl span.e0{transform:translateX(-25%)}.ch.mini .xl span.e1{transform:translateX(-100%)}',
    '.ch.mini .tip{min-width:96px;padding:6px 9px;font-size:11px}.ch.mini .tip .r span{display:none}',
    '.dot{position:absolute;width:8px;height:8px;margin:-4px 0 0 -4px;border-radius:50%;background:var(--orange);pointer-events:none}',
    '.a.ol{fill:var(--orange);fill-opacity:.14}.ln.ol{stroke:var(--orange)}',
    '.tri{display:inline-block;width:0;height:0;border-left:4.5px solid transparent;border-right:4.5px solid transparent}.tri.up{border-bottom:7px solid var(--orange)}.tri.dn{border-top:7px solid var(--mute)}',
    '.spk{display:block;width:100%;height:34px;margin-top:10px;overflow:visible}.spk path{fill:none;stroke:var(--orange);stroke-width:1.6;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}.spk circle{fill:var(--orange)}',
    /* índice */
    '.index{position:relative;display:grid;grid-template-columns:repeat(var(--n,4),minmax(0,1fr));border-top:1px solid var(--orange);border-bottom:1px solid var(--hair)}',
    '.index a{display:flex;align-items:baseline;gap:12px;padding:18px 0 19px;text-decoration:none;color:var(--fg);font-size:15px;font-weight:650;min-width:0}.index a+a{padding-left:22px;border-left:1px solid var(--hair)}.index .num{font:300 15px/1 var(--display);color:var(--otext)}',
    '.index .n{margin-left:auto;padding-right:22px;font-weight:400;font-size:13px;color:var(--mute)}.index a:last-child .n{padding-right:0}.index a:hover,.index a:focus-visible{color:var(--otext);outline:0}',
    '.about{columns:2;column-gap:38px;padding-top:26px;font-size:13.5px;line-height:1.6;color:var(--mute)}.about p{break-inside:avoid;margin-bottom:12px}',
    /* seções */
    '.sec{position:relative;scroll-margin-top:12px}.sec .shapes.side{right:0;top:0;width:420px;height:300px}',
    '.sec-head{position:relative;display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding-top:58px;padding-bottom:16px}',
    '.sec-head h2{font:300 34px/1.1 var(--display);letter-spacing:-.025em;color:var(--fg)}.sec-head h2 .num{display:block;margin-bottom:12px;font-size:15px;letter-spacing:0;color:var(--otext)}.sec-head .count{font-size:13px;color:var(--mute);white-space:nowrap}',
    '.rows{position:relative;border-top:1px solid var(--orange);background:var(--bg)}.lead{padding:26px 0 4px;max-width:30em;font-size:21px;line-height:1.4;font-weight:500;color:var(--fg);letter-spacing:-.01em}',
    '.sh{margin:40px 0 0;padding-top:22px;border-top:1px solid var(--hair);font:300 22px/1.2 var(--display);letter-spacing:-.02em;color:var(--fg)}',
    /* takeaways */
    'ul.tk{list-style:none;padding:12px 0 6px}li.tk{position:relative;padding:16px 0 16px 26px;border-bottom:1px solid var(--hair);font-size:16px;line-height:1.62;color:var(--ink)}li.tk:last-child{border-bottom:0}',
    'li.tk::before{content:"";position:absolute;left:0;top:27px;width:12px;height:2px;background:var(--orange)}li.tk.sub{margin-left:26px;padding-left:22px;font-size:15px}li.tk.sub::before{width:7px;height:7px;top:24px;border-radius:50%;background:none;border:1.5px solid var(--orange)}',
    'li.tk .tl{color:var(--fg);font-weight:750}li.tk b{color:var(--fg);font-weight:700}li.tk a{color:var(--otext);text-underline-offset:3px}',
    /* tabela com vistas */
    '.views,.tabs{position:relative;padding-top:20px}.vr{position:absolute;opacity:0;pointer-events:none;width:1px;height:1px}',
    '.seg{display:inline-flex;flex-wrap:wrap;gap:2px;padding:3px;border:1px solid var(--hair);border-radius:999px;background:var(--bg)}.seg label{cursor:pointer;font:650 12px/1 var(--body);padding:8px 13px;border-radius:999px;color:var(--mute);white-space:nowrap;user-select:none;-webkit-user-select:none}.seg label:hover{color:var(--fg)}',
    '.segs{display:flex;flex-wrap:wrap;gap:8px 10px;margin-bottom:6px}',
    '.vr:focus-visible~.seg,.vr:focus-visible~.segs .seg{outline:2px solid var(--fg);outline-offset:2px}',
    '.vpanes .tw{display:none}.tpanes .tp{display:none}',
    '.vr[data-v="m"]:checked~.vpanes .tw[data-v="m"],.vr[data-v="y"]:checked~.vpanes .tw[data-v="y"],.vr[data-v="q"]:checked~.vpanes .tw[data-v="q"]{display:block}',
    '.vr[data-v="m"]:checked~.seg label[for$="m"],.vr[data-v="y"]:checked~.seg label[for$="y"],.vr[data-v="q"]:checked~.seg label[for$="q"]{background:var(--orange);color:#000}',
    '.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:16px}',
    'table.dt{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}table.dt th,table.dt td{padding:9px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--grid)}',
    'table.dt thead th{font:650 12px/1.2 var(--body);color:var(--mute);border-bottom:1px solid var(--orange);padding-top:4px}table.dt th.rl{text-align:left;padding-left:0;font-weight:650;color:var(--fg)}table.dt thead th.rl{color:var(--mute);font-weight:500}',
    'table.dt tr.l0 th.rl,table.dt tr.l0 td{color:var(--fg);font-weight:700}table.dt tr.l0 td{border-top:1px solid var(--line)}table.dt tr.l1 th.rl{padding-left:16px;font-weight:450;color:var(--soft)}table.dt tr.l1 td{color:var(--ink)}',
    'table.dt td.dc{color:var(--otext)}table.dt tr.l0 td.dc{color:var(--otext)}table.dt tbody tr:hover td,table.dt tbody tr:hover th{background:var(--hover)}',
    '.fn{margin-top:14px;font-size:12px;line-height:1.55;color:var(--faint);max-width:60em}',
    'table.dt.gx tr.l0 th.rl,table.dt.gx tr.l0 td{font-weight:450;color:var(--ink)}table.dt.gx tr.l0 td{border-top:0}table.dt.gx tr.l0 th.rl{color:var(--soft)}',
    'table.dt.gx tr.st th.rl,table.dt.gx tr.st td{font-weight:700;color:var(--fg)}table.dt.gx tr.st td,table.dt.gx tr.st th{border-top:1px solid var(--line)}table.dt.gx tr.st td.dc{color:var(--otext)}',
    'table.dt tr.l2 th.rl{padding-left:32px;font-weight:450;color:var(--soft)}table.dt tr.l2 td{color:var(--ink)}',
    'table.dt tr.gr th{padding:18px 0 6px;text-align:left;font:650 11.5px/1.2 var(--body);letter-spacing:.07em;text-transform:uppercase;color:var(--otext);border-bottom:1px solid var(--grid)}table.dt tbody tr.gr:hover th{background:none}',
    '.sh.first{margin-top:24px;padding-top:0;border-top:0}.sh.sm{font-size:18px;margin-top:30px}',
    'figure.hb .hbl{margin-top:14px}.hbr{display:grid;grid-template-columns:132px minmax(0,1fr) 132px 46px;align-items:center;gap:12px;height:30px;font-size:13px;font-variant-numeric:tabular-nums;border-radius:7px}.hbr:hover{background:var(--hover)}',
    '.hbn{padding-left:6px;font-weight:650;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.hbt{position:relative;height:12px}.hbt i{position:absolute;left:0;top:0;bottom:0;background:var(--g2);border-radius:0 4px 4px 0;transform-origin:left center}',
    '.hbr.hl .hbt i{background:var(--orange)}.hbr.hl .hbn{color:var(--fg)}.hbv{text-align:right;color:var(--fg);font-weight:550;white-space:nowrap}.hbs{padding-right:6px;text-align:right;color:var(--mute);font-size:12px}',
    '.js figure.hb.arm:not(.go) .hbt i{transform:scaleX(0)}.js figure.hb.arm.go .hbt i{animation:growx .9s cubic-bezier(.2,.7,.2,1) both;animation-delay:calc(var(--i,0)*55ms)}@keyframes growx{from{transform:scaleX(0)}to{transform:scaleX(1)}}',
    /* gráficos */
    'figure.ch{position:relative;margin-top:22px;padding:22px 24px 16px;border:1px solid var(--hair);border-radius:28px;background:var(--bg)}figure.ch+figure.ch{margin-top:18px}',
    'figure.ch figcaption h3{font:650 16px/1.3 var(--body);color:var(--fg);letter-spacing:-.005em}figure.ch figcaption p{margin-top:3px;font-size:12.5px;color:var(--mute)}',
    '.lg{display:flex;flex-wrap:wrap;gap:6px 18px;margin:12px 0 4px;font-size:12.5px;color:var(--soft)}.lg span{display:inline-flex;align-items:center;gap:7px}.lg em{font-style:normal;color:var(--mute)}',
    '.k{display:inline-block;width:16px}.k.kl{height:0;border-top:2.5px solid currentColor}.k.kl.dash{border-top-style:dashed}.k.kb{height:10px;border-radius:2px;background:currentColor}',
    '.plot{position:relative;height:var(--h,250px);margin-top:10px;padding:8px 6px 26px 48px}.ch.dual .plot{padding-right:48px}',
    '.pa{position:relative;width:100%;height:100%}.pa svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}',
    '.yl,.yr{position:absolute;top:8px;bottom:26px;width:44px;font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}.yl{left:0;text-align:right}.yr{right:0;text-align:left}.yl span,.yr span{position:absolute;transform:translateY(-50%);white-space:nowrap}.yl span{right:8px}.yr span{left:8px}',
    '.xl{position:absolute;left:48px;right:6px;bottom:0;height:20px;font-size:11px;color:var(--faint)}.ch.dual .xl{right:48px}.xl span{position:absolute;transform:translateX(-50%);white-space:nowrap}',
    '.gl{stroke:var(--grid);stroke-width:1;vector-effect:non-scaling-stroke}.gl.z{stroke:var(--line)}',
    '.ln{fill:none;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}.ln.dash{stroke-dasharray:5 4}',
    '.ln.o{stroke:var(--orange)}.ln.f{stroke:var(--fg)}.ln.g1{stroke:var(--g1)}.ln.g2{stroke:var(--g2)}.ln.g3{stroke:var(--g3)}.ln.y0{stroke:var(--y0)}.ln.y1{stroke:var(--y1)}.ln.y2{stroke:var(--y2)}.ln.y3{stroke:var(--y3)}.ln.yc{stroke:var(--orange)}',
    '.a{stroke:none}.a.f{fill:var(--area)}.a.o{fill:var(--orange);fill-opacity:.78}.a.g1{fill:var(--g1);opacity:.55}.a.g2{fill:var(--g2);opacity:.75}.a.g3{fill:var(--g3);opacity:.9}.a.y0{fill:var(--y2);opacity:.35}',
    '.b{transform-box:fill-box;transform-origin:50% 100%}.b.f{fill:var(--fg)}.b.o{fill:var(--orange)}.b.g1{fill:var(--g1)}.b.g2{fill:var(--g2)}.b.g3{fill:var(--g3)}.b.hl{fill:var(--orange)}',
    '.k.o{color:var(--orange)}.k.f{color:var(--fg)}.k.g1{color:var(--g1)}.k.g2{color:var(--g2)}.k.g3{color:var(--g3)}.k.y0{color:var(--y0)}.k.y1{color:var(--y1)}.k.y2{color:var(--y2)}.k.y3{color:var(--y3)}.k.yc{color:var(--orange)}.k.kb.f{color:var(--soft)}.k.kb.a-f{background:var(--area);border-top:2px solid var(--fg)}',
    '.cur{position:absolute;top:-2px;bottom:-2px;right:-2px;left:0;background:var(--bg);transform-origin:100% 50%;transform:scaleX(0);pointer-events:none;display:none}',
    '.rule{position:absolute;top:0;bottom:0;width:1px;background:var(--fg);opacity:0;pointer-events:none;transition:opacity .15s}',
    '.tip{position:absolute;top:0;z-index:5;min-width:120px;padding:8px 11px;border-radius:10px;background:var(--tip-bg);color:var(--tip-fg);font:500 12px/1.45 var(--body);white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .15s;font-variant-numeric:tabular-nums}',
    '.tip b{display:block;font-weight:800;margin-bottom:2px}.tip .r{display:flex;align-items:center;gap:8px}.tip .r i{display:inline-block;width:9px;height:9px;border-radius:2px;background:currentColor}.tip .r span{color:var(--tip-k)}.tip .r strong{margin-left:auto;padding-left:10px;font-weight:700}',
    '.pa.on .rule,.pa.on .tip{opacity:1}.cnote{margin-top:8px;font-size:11.5px;color:var(--faint)}',
    '.tabs .tp>figure.ch:first-child{margin-top:14px}',
    /* notícias (o HTML do clipping) */
    '.note{margin-top:22px;padding:14px 18px;border-left:2px solid var(--orange);border-radius:0 14px 14px 0;background:var(--hover);font-size:14px;line-height:1.6;color:var(--ink)}.note b{color:var(--fg)}.note a{color:var(--otext);text-underline-offset:3px}',
    '.tkb{display:inline-grid;place-items:center;flex:none;width:22px;height:22px;border-radius:7px;font:800 14px/1 var(--body)}.tkb.pos{background:var(--pos);color:#000}.tkb.neg{background:var(--neg);color:#000}.tkb.neu{border:1.5px solid var(--line);color:var(--soft)}',
    '.glance{display:flex;flex-wrap:wrap;align-items:center;gap:10px 18px;padding:20px 0 4px;font-size:13px;color:var(--mute)}.glance b{font:300 22px/1 var(--display);color:var(--fg);margin-right:4px}',
    '.gb{display:flex;flex:1 1 140px;max-width:300px;height:8px;gap:2px;border-radius:99px;overflow:hidden}.gb i{display:block}.gb .pos,.gk .pos{background:var(--pos)}.gb .neu,.gk .neu{background:var(--line)}.gb .neg,.gk .neg{background:var(--neg)}',
    '.gl{display:flex;flex-wrap:wrap;gap:4px 14px}.gk{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}.gk i{display:inline-block;width:8px;height:8px;border-radius:2px}',
    '.filter{display:inline-flex;flex-wrap:wrap;gap:2px;margin:16px 0 2px;padding:3px;border:1px solid var(--hair);border-radius:999px;background:var(--bg)}.filter button{all:unset;cursor:pointer;font:650 12px/1 var(--body);padding:8px 12px;border-radius:999px;color:var(--mute);white-space:nowrap}.filter button span{font-weight:400;margin-left:5px;opacity:.75}.filter button[aria-pressed="true"]{background:var(--orange);color:#000}.filter button:focus-visible{outline:2px solid var(--fg);outline-offset:2px}.no-js .filter,.shot .filter{display:none}',
    '.hl-sec{margin:22px 0 2px;font:650 11.5px/1.2 var(--body);letter-spacing:.07em;text-transform:uppercase;color:var(--otext)}[data-sector][hidden]{display:none!important}',
    '.hl-row{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:14px;align-items:start;padding:14px 6px;border-bottom:1px solid var(--grid);border-radius:8px;text-decoration:none;color:var(--ink)}.hl-row:hover,.hl-row:focus-visible{background:var(--hover);outline:0}',
    '.hl-t{font-size:15.5px;line-height:1.4;font-weight:650;color:var(--fg)}.hl-t small{display:block;margin-top:3px;font-size:12.5px;font-weight:450;color:var(--mute)}.hl-s{padding-top:2px;font-size:12px;color:var(--mute);white-space:nowrap}',
    '.pubs{padding-top:8px}.pub{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:baseline;padding:14px 6px;border-bottom:1px solid var(--grid);border-radius:8px;text-decoration:none;color:var(--ink)}a.pub:hover,a.pub:focus-visible{background:var(--hover);outline:0}a.pub:hover .pub-n{color:var(--otext)}',
    '.pub-s{font:650 11px/1 var(--body);letter-spacing:.05em;color:var(--otext);border:1px solid var(--hair);border-radius:999px;padding:4px 8px;white-space:nowrap}.pub-n{font-size:15.5px;font-weight:650;line-height:1.4;color:var(--fg)}.pub-m{font-size:12px;color:var(--mute);white-space:nowrap}',
    '.st-sec span{font:500 13px/1 var(--body);letter-spacing:0;color:var(--mute);margin-left:8px}',
    '.story{position:relative;padding:26px 0 28px;border-bottom:1px solid var(--hair);scroll-margin-top:12px}.story:last-child{border-bottom:0}',
    '.st-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;font-size:12.5px;color:var(--mute)}.st-meta .sec{font-weight:650;color:var(--otext);letter-spacing:.02em}',
    '.st-h{margin-top:12px;font:700 20px/1.3 var(--body);letter-spacing:-.01em;color:var(--fg)}.st-o{margin-top:6px;font-size:13px;font-style:italic;color:var(--mute)}.st-o2{font-weight:650;color:var(--fg)}',
    '.st-b{margin-top:14px;max-width:44em;font-size:15.5px;line-height:1.65;color:var(--ink)}.st-b p{margin:0 0 12px}.st-b .bh{margin:18px 0 8px;font:700 16px/1.35 var(--body);color:var(--fg)}.st-b .bh2{margin:14px 0 6px;font:700 15px/1.35 var(--body);color:var(--fg)}',
    '.st-b blockquote{margin:12px 0;padding-left:14px;border-left:2px solid var(--line);font-style:italic;color:var(--soft)}.st-b ul{margin:0 0 12px 20px}.st-b li{margin-bottom:6px}.st-na{font-style:italic;color:var(--mute)}',
    '.st-img{margin:16px 0}.st-img img{display:block;max-width:100%;height:auto;border-radius:12px;background:#fff}.st-tw{margin:12px 0}.st-t{font-size:13px}.st-t th,.st-t td{padding:7px 8px;text-align:left;white-space:normal}.st-t th{color:var(--fg)}',
    'details.rm summary{cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:8px;margin:2px 0 4px;font-size:13px;font-weight:650;color:var(--otext)}details.rm summary::-webkit-details-marker{display:none}details.rm summary em{font-style:normal;font-weight:450;color:var(--mute)}',
    'details.rm summary::after{content:"";width:7px;height:7px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:translateY(-2px) rotate(45deg);transition:transform .2s}details.rm[open] summary::after{transform:translateY(2px) rotate(-135deg)}details.rm .c{display:none}details.rm[open] .o{display:none}details.rm[open] .c{display:inline}details.rm[open] summary{margin:0 0 14px}',
    '.st-l{display:inline-block;margin-top:8px;font-size:12.5px;color:var(--mute);text-underline-offset:3px;text-decoration-thickness:1px}.st-l:hover,.st-l:focus-visible{color:var(--otext)}',
    '.story .tabs{padding-top:14px}.story .tabs .tp>.st-b{margin-top:10px}',
    /* rodapé */
    '.foot{position:relative;margin-top:64px;border-top:1px solid var(--orange);padding-top:50px;padding-bottom:46px}.foot .shapes{right:0;bottom:0;width:420px;height:330px}.foot .inner{position:relative}',
    '.foot h2{font:300 26px/1.15 var(--display);letter-spacing:-.025em;color:var(--fg)}.people{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 30px;margin-top:26px}',
    '.person{padding-top:14px;border-top:1px solid var(--hair);font-size:13.5px;line-height:1.62;color:var(--soft)}.person .p-name{font-size:15px;font-weight:700;color:var(--fg)}.person a{text-decoration:none;overflow-wrap:anywhere}.person a:hover,.person a:focus-visible{color:var(--otext)}',
    '.platform{display:inline-block;margin-top:26px;font-size:13.5px;color:var(--fg);text-underline-offset:4px;text-decoration-color:var(--orange)}.platform:hover,.platform:focus-visible{color:var(--otext)}',
    '.fine{max-width:56em;margin-top:22px;font-size:11.5px;line-height:1.55;color:var(--faint)}',
    '.disc{max-width:60em;margin-top:16px;font-size:11px;line-height:1.55;color:var(--faint)}.disc summary{cursor:pointer;font-size:12px;font-weight:650;color:var(--mute);padding:6px 0}.disc summary:hover{color:var(--otext)}',
    '.disc ol{padding-left:18px;margin:10px 0}.disc li{margin-bottom:6px}.disc p{margin:10px 0}.disc h4{margin-top:14px;font-size:12px;color:var(--soft)}.dtab{border-collapse:collapse;margin:8px 0;font-size:11px}.dtab th,.dtab td{border-bottom:1px solid var(--grid);padding:4px 10px;text-align:center}.dtab td:first-child,.dtab th:first-child{text-align:left}',
    '.sig{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-top:22px;font-size:13px;color:var(--mute)}.sig strong{font-weight:750;font-size:15px;color:var(--fg)}',
    /* dock + caixa de link */
    '.dock{display:none}.linkbox{position:fixed;left:50%;bottom:22px;z-index:30;transform:translateX(-50%);width:min(560px,calc(100% - 28px));padding:14px 16px 16px;border:1px solid var(--orange);border-radius:18px;background:var(--bg);color:var(--ink);font-size:13px;line-height:1.45}.linkbox[hidden]{display:none}',
    '.linkbox p{margin-right:54px;color:var(--fg);font-weight:650}.linkbox .lb-url{display:block;margin-top:8px;padding:9px 10px;border:1px solid var(--line);border-radius:10px;font-size:12px;line-height:1.4;color:var(--ink);overflow-wrap:anywhere;user-select:all;-webkit-user-select:all}.linkbox button{all:unset;cursor:pointer;position:absolute;right:14px;top:12px;font-size:12.5px;font-weight:650;color:var(--mute)}',
    '.shot .dock,.shot .linkbox{display:none}',
    /* movimento — o estado de repouso é sempre o desenho final */
    '@keyframes draw{from{stroke-dashoffset:1.02}}@keyframes rise{from{opacity:0;transform:translateY(16px)}}@keyframes fade{from{opacity:0}}',
    '@keyframes drift1{to{transform:rotate(var(--rot,0deg)) translate(7px,-9px) rotate(2.2deg)}}@keyframes drift2{to{transform:rotate(var(--rot,0deg)) translate(-6px,7px) rotate(-2.6deg)}}@keyframes drift3{to{transform:rotate(var(--rot,0deg)) translate(9px,6px) rotate(1.8deg)}}@keyframes drift4{to{transform:rotate(var(--rot,0deg)) translate(-8px,-6px) rotate(-1.6deg)}}',
    '@keyframes o1{from{transform:rotate(-17deg) scale(1)}to{transform:rotate(-8deg) scale(1.02)}}@keyframes o2{from{transform:rotate(24deg)}to{transform:rotate(34deg) scale(.985)}}@keyframes o3{from{transform:rotate(-5deg)}to{transform:rotate(-16deg) scale(1.025)}}',
    '@keyframes pop{0%{opacity:0;transform:scale(.6)}60%{opacity:1;transform:scale(1.06)}100%{transform:scale(1)}}',
    '.emb .t1{animation:draw 1.6s cubic-bezier(.3,.6,.2,1) .15s backwards,o1 9s ease-in-out 1.8s infinite alternate}',
    '.emb .t2{animation:draw 1.6s cubic-bezier(.3,.6,.2,1) .4s backwards,o2 12s ease-in-out 1.8s infinite alternate}',
    '.emb .t3{animation:draw 1.6s cubic-bezier(.3,.6,.2,1) .65s backwards,o3 7.5s ease-in-out 1.8s infinite alternate}',
    '.emb .lg0{animation:pop .9s cubic-bezier(.2,.7,.2,1) .05s backwards}',
    '.hero .shapes path:nth-child(1){animation:draw 2s cubic-bezier(.3,.6,.2,1) .2s backwards,drift1 17s ease-in-out 1.2s infinite alternate}',
    '.hero .shapes path:nth-child(2){animation:draw 2s cubic-bezier(.3,.6,.2,1) .45s backwards,drift2 21s ease-in-out 1.2s infinite alternate}',
    '.hero .shapes path:nth-child(3){animation:draw 2s cubic-bezier(.3,.6,.2,1) .75s backwards,drift3 25s ease-in-out 1.2s infinite alternate}',
    '.hero .shapes path:nth-child(4){animation:draw 2s cubic-bezier(.3,.6,.2,1) .05s backwards,drift4 29s ease-in-out 1.2s infinite alternate}',
    '.sec .shapes path:nth-child(1){animation:drift2 23s ease-in-out infinite alternate}.sec .shapes path:nth-child(2){animation:drift1 19s ease-in-out infinite alternate}',
    '.foot .shapes path:nth-child(1){animation:drift4 24s ease-in-out infinite alternate}.foot .shapes path:nth-child(2){animation:drift1 20s ease-in-out infinite alternate}.foot .shapes path:nth-child(3){animation:drift2 28s ease-in-out infinite alternate}',
    '.shapes.idle path,.emb.idle path{animation-play-state:paused}.shapes.wait path{stroke-dashoffset:1.02}.shapes.wait.seen path{stroke-dashoffset:0;transition:stroke-dashoffset 1.9s cubic-bezier(.3,.6,.2,1)}.shapes.wait.seen path:nth-child(2){transition-delay:.25s}.shapes.wait.seen path:nth-child(3){transition-delay:.5s}',
    '.kicker{animation:fade .8s ease-out backwards}h1 .period{animation:rise .9s cubic-bezier(.2,.7,.2,1) .1s backwards}h1 .word{animation:rise .9s cubic-bezier(.2,.7,.2,1) .28s backwards}.deck{animation:rise .9s cubic-bezier(.2,.7,.2,1) .42s backwards}',
    '.rv{opacity:0;transform:translateY(18px)}.rv.in{opacity:1;transform:none;transition:opacity .7s ease-out,transform .7s cubic-bezier(.2,.7,.2,1)}.kpi.rv.in:nth-child(2),.person.rv.in:nth-child(2){transition-delay:.08s}.kpi.rv.in:nth-child(3),.person.rv.in:nth-child(3){transition-delay:.16s}.kpi.rv.in:nth-child(4),.person.rv.in:nth-child(4){transition-delay:.24s}',
    '.js .ch.arm .cur{display:block}.js .ch.arm.go .cur{transform:scaleX(0);transition:none}.js .ch.arm:not(.go) .cur{transform:scaleX(1)}.js .ch.arm.go .cur{animation:wipe 1.5s cubic-bezier(.45,.05,.2,1) both}',
    '@keyframes wipe{from{transform:scaleX(1)}to{transform:scaleX(0)}}',
    '.js .ch.arm:not(.go) .b{transform:scaleY(0)}.js .ch.arm.go .b{animation:grow .8s cubic-bezier(.2,.7,.2,1) both;animation-delay:calc(var(--i,0)*22ms)}@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}',
    '.js .ch.arm:not(.go) .spk path,.js .kpi:not(.in) .spk path{stroke-dasharray:1.02;stroke-dashoffset:1.02}.js .kpi.in .spk path{stroke-dasharray:1.02;animation:draw 1.6s cubic-bezier(.3,.6,.2,1) .3s backwards}',
    '@media (prefers-reduced-motion:reduce){.js figure.hb.arm .hbt i{transform:none;animation:none}html{scroll-behavior:auto}.shapes path,.emb path,.emb .lg0,.kicker,h1 .period,h1 .word,.deck{animation:none!important}.shapes.wait path{stroke-dashoffset:0}.rv{opacity:1;transform:none}.js .ch.arm .cur{display:none}.js .ch.arm .b{transform:none;animation:none}.js .kpi .spk path{animation:none;stroke-dashoffset:0}}',
    '.shot .shapes path,.shot .emb path,.shot .emb .lg0,.shot .kicker,.shot h1 .period,.shot h1 .word,.shot .deck,.calm .shapes path,.calm .emb .lg0,.calm .kicker,.calm h1 .period,.calm h1 .word,.calm .deck{animation:none!important}.calm .emb .t1{animation:o1 9s ease-in-out infinite alternate}.calm .emb .t2{animation:o2 12s ease-in-out infinite alternate}.calm .emb .t3{animation:o3 7.5s ease-in-out infinite alternate}',
    /* celular */
    '@media (max-width:760px){:root{--pad:20px}body{font-size:15px}.sheet{padding-bottom:58px}',
    '.topbar{display:grid;grid-template-columns:1fr auto;grid-template-areas:"brand sw" "st st";align-items:center;row-gap:16px;padding-top:18px}.brand{grid-area:brand;gap:12px}.tools{display:contents}.theme-sw{grid-area:sw;align-self:start;margin-top:8px}.stamp{grid-area:st;padding:0;background:none;text-align:left;font-size:12.5px}.stamp br{display:none}.stamp b{margin-right:8px}',
    '.emb{width:62px;height:62px}.team{font-size:12.5px;letter-spacing:.01em}.team span{display:block;width:0;height:0;overflow:hidden;margin:0}.team{display:flex;flex-direction:column}.bt .sub{font-size:11.5px}',
    '.hero{padding-bottom:36px}.lockup{padding-top:30px}.hero .shapes{left:34%;top:90px;opacity:.55}.sec .shapes{opacity:.45}.foot .shapes{width:240px;height:190px;opacity:.6}',
    '.kpis{grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-bottom:22px}.kpi{padding:16px 12px 8px}.kpi:nth-of-type(3){border-left:0}.kv{font-size:27px}.kd{gap:3px;font-size:11.5px}.kq{font-size:11.5px;gap:3px}.kcap{font-size:12px}.ch.mini .plot{padding-left:32px}.ch.mini .yl{width:28px}.ch.mini .xl{left:32px}',
    '.index{grid-template-columns:1fr 1fr}.index a{padding:15px 0 16px;font-size:14px;gap:9px;border-bottom:1px solid var(--hair)}.index a+a{padding-left:0;border-left:0}.index a:nth-child(even){padding-left:14px;border-left:1px solid var(--hair)}.index a:nth-child(n+3){border-bottom:0}.index .n{padding-right:12px}.index a:nth-child(even) .n{padding-right:0}.about{columns:1}',
    '.sec-head{flex-direction:column;align-items:flex-start;gap:10px;padding-top:44px;padding-bottom:14px}.sec-head h2{font-size:26px}.sec-head h2 .num{margin-bottom:9px}.lead{font-size:18px;padding-top:20px}',
    'li.tk{font-size:15.5px;padding-left:22px}li.tk.sub{margin-left:12px;font-size:15px}',
    'table.dt{font-size:12px}table.dt th,table.dt td{padding:8px 4px}table.dt thead th{font-size:11px}table.dt th.rl{white-space:normal;line-height:1.25}table.dt tr.l1 th.rl{padding-left:8px}',
    '.hbr{grid-template-columns:92px minmax(0,1fr) 96px 34px;gap:8px;font-size:12px}.hbs{font-size:11px}',
    'figure.ch{padding:16px 12px 12px;border-radius:22px}.plot{padding-left:40px}.ch.dual .plot{padding-right:38px}.yl,.yr{width:38px;font-size:10.5px}.xl{left:40px;font-size:10.5px}.ch.dual .xl{right:38px}.seg label{padding:9px 12px}',
    '.people{grid-template-columns:1fr;gap:16px}.foot{padding-top:40px}',
    '.hl-row{grid-template-columns:22px minmax(0,1fr);gap:12px;padding:13px 2px}.hl-s{grid-column:2;padding-top:0;margin-top:-6px}.hl-t{font-size:15px}',
    '.pub{grid-template-columns:minmax(0,1fr);gap:6px;padding:14px 2px}.pub-s{justify-self:start}.pub-n{font-size:15px}',
    '.st-h{font-size:18px}.st-b{font-size:15px}.note{font-size:13.5px}.filter button{padding:9px 12px}',
    '.dock{display:grid;grid-template-columns:repeat(var(--n,4),1fr);position:fixed;left:0;right:0;bottom:0;z-index:20;background:var(--bg);border-top:1px solid var(--orange);padding-bottom:env(safe-area-inset-bottom)}.dock a{padding:14px 2px 13px;text-align:center;text-decoration:none;font-size:12.5px;font-weight:650;color:var(--mute)}.dock a[aria-current="true"]{color:var(--fg);box-shadow:inset 0 2px 0 var(--orange)}.linkbox{bottom:70px}}',
    '@media (max-width:380px){.kv{font-size:24px}.team{font-size:11.5px}.emb{width:54px;height:54px}}',
    '@media print{.theme-sw,.dock,.linkbox,.seg,.segs{display:none}.sheet{max-width:none}.shapes path,.emb path,.emb .lg0,.kicker,h1 .period,h1 .word,.deck{animation:none!important}.shapes.wait path{stroke-dashoffset:0!important}.rv{opacity:1!important;transform:none!important}.vpanes .tw,.tpanes .tp{display:block!important}.cur{display:none!important}.b,.hbt i{transform:none!important;animation:none!important}.disc{display:block}.disc>*{display:block}}'
  ].join('\n');

  // ═══════════════ JS embutido no documento (progressivo) ═══════════════════
  var RUNTIME = [
    '(function(){',
    'var D=document,B=D.body;B.classList.remove("no-js");D.documentElement.classList.add("js");',
    'var qs=new URLSearchParams(location.search);if(qs.get("shot")==="1")D.documentElement.classList.add("shot");',
    'var calm=qs.get("shot")==="1"||D.documentElement.classList.contains("calm")||(window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches);',
    'var canWatch=!calm&&"IntersectionObserver" in window;',
    // tema
    'var tb=D.querySelectorAll(".theme-sw button");function syncT(){var c=D.documentElement.getAttribute("data-theme")==="light"?"light":"dark";tb.forEach(function(b){b.setAttribute("aria-pressed",String(b.dataset.themeSet===c))})}',
    'tb.forEach(function(b){b.addEventListener("click",function(){if(b.dataset.themeSet==="light")D.documentElement.setAttribute("data-theme","light");else D.documentElement.removeAttribute("data-theme");try{localStorage.setItem("ibba-rpt-theme",b.dataset.themeSet)}catch(e){}syncT()})});syncT();',
    // contagem dos KPIs
    'function fmt(v,d){var s=Math.abs(v).toFixed(d).split(".");s[0]=s[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,",");return(v<0?"\\u2212":"")+s.join(".")}',
    'function count(el){var to=parseFloat(el.dataset.to),d=+el.dataset.d||0;if(!isFinite(to)||calm)return;var t0=null,dur=1100;function st(t){if(!t0)t0=t;var k=Math.min(1,(t-t0)/dur),e=1-Math.pow(1-k,3);el.textContent=fmt(to*e,d);if(k<1)requestAnimationFrame(st);else el.textContent=fmt(to,d)}requestAnimationFrame(st)}',
    // revelar blocos ao rolar
    'var REV=".kpi,.index,.about,.note,.sec-head,.lead,li.tk,.views,.tabs,figure.ch,.fn,.sh,.glance,.hl-row,.pub,.story,.foot h2,.person,.platform,.fine,.sig,.disc";var io=null;',
    'function armRev(){if(!canWatch)return;io=io||new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add("in");io.unobserve(e.target);if(e.target.classList.contains("kpi")){var c=e.target.querySelector(".cnt");if(c)count(c)}}})},{threshold:.04});',
    'var vh=innerHeight||D.documentElement.clientHeight;D.querySelectorAll(REV).forEach(function(n){if(n.classList.contains("rv"))return;if(n.closest(".tp")&&!n.closest(".tp").offsetParent)return;var top=n.getBoundingClientRect().top;if(top<vh*.94&&!n.classList.contains("kpi"))return;n.classList.add("rv");io.observe(n)})}armRev();',
    'if(!canWatch)D.querySelectorAll(".kpi").forEach(function(k){k.classList.add("in")});',
    // gráficos: cortina + barras quando entram na tela
    'var cio=canWatch?new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add("go");cio.unobserve(e.target)}})},{threshold:.25}):null;',
    'function armCharts(root){if(!cio)return;(root||D).querySelectorAll("figure.ch").forEach(function(f){if(f.classList.contains("arm"))return;f.classList.add("arm");cio.observe(f)})}armCharts();',
    // trocar de aba → o gráfico novo se desenha
    'D.querySelectorAll(".tabs .vr,.views .vr").forEach(function(r){r.addEventListener("change",function(){var t=r.closest(".tabs,.views");setTimeout(function(){t.querySelectorAll(".tp,.tw").forEach(function(p){if(p.offsetParent){p.querySelectorAll("figure.ch").forEach(function(f){f.classList.remove("go");void f.offsetWidth;f.classList.add("arm","go")});p.querySelectorAll(".rv").forEach(function(n){n.classList.add("in")})}})},0)})});',
    // dica (tooltip) — mouse e dedo
    'function fv(v,f){if(v==null)return"\\u2014";if(f==="pct")return fmt(v*100,1)+"%";if(f==="usd")return fmt(v,0);if(f==="dec1")return fmt(v,1);return fmt(v,Math.abs(v)<10&&v%1?1:0)}',
    'D.querySelectorAll("figure.ch").forEach(function(f){var dj=f.querySelector(".chd");if(!dj)return;var d;try{d=JSON.parse(dj.textContent)}catch(e){return}var pa=f.querySelector(".pa"),rule=pa.querySelector(".rule"),tip=pa.querySelector(".tip"),n=d.x.length;',
    'function show(cx){var r=pa.getBoundingClientRect();if(!r.width)return;var x=Math.max(0,Math.min(r.width,cx-r.left)),i=d.band?Math.floor(x/r.width*n):Math.round(x/r.width*(n-1));i=Math.max(0,Math.min(n-1,i));var px=d.band?(i+.5)/n*r.width:(n>1?i/(n-1)*r.width:r.width/2);',
    'rule.style.left=px+"px";var h="<b>"+d.x[i]+"</b>";d.s.forEach(function(s){h+="<div class=\\"r "+s.c+"\\"><i class=\\"k "+s.c+"\\" style=\\"border:0\\"></i><span>"+s.n+"</span><strong>"+fv(s.v[i],s.f)+"</strong></div>"});tip.innerHTML=h;',
    'var tw=tip.offsetWidth;tip.style.left=(px+12+tw>r.width?Math.max(0,px-12-tw):px+12)+"px";pa.classList.add("on")}',
    'function hide(){pa.classList.remove("on")}pa.addEventListener("pointermove",function(e){show(e.clientX)});pa.addEventListener("pointerdown",function(e){show(e.clientX)});pa.addEventListener("pointerleave",hide);',
    'pa.addEventListener("touchmove",function(e){if(e.touches[0])show(e.touches[0].clientX)},{passive:true})});',
    'D.addEventListener("pointerdown",function(e){if(!e.target.closest||!e.target.closest(".pa"))D.querySelectorAll(".pa.on").forEach(function(p){p.classList.remove("on")})});',
    // formas: pausa fora da tela, desenho ao aparecer
    'if(canWatch){var low=D.querySelectorAll(".sec .shapes,.foot .shapes");low.forEach(function(s){s.classList.add("wait")});var sio=new IntersectionObserver(function(es){es.forEach(function(e){e.target.classList.toggle("idle",!e.isIntersecting);if(e.isIntersecting)e.target.classList.add("seen")})},{threshold:.05});D.querySelectorAll(".shapes,.emb").forEach(function(s){sio.observe(s)});',
    'window.addEventListener("beforeprint",function(){D.querySelectorAll(".rv").forEach(function(n){n.classList.add("in")});D.querySelectorAll("figure.ch").forEach(function(f){f.classList.add("go")});low.forEach(function(s){s.classList.add("seen")})});',
    'window.addEventListener("scroll",function(){if(innerHeight+scrollY>=D.documentElement.scrollHeight-4)D.querySelectorAll(".rv:not(.in)").forEach(function(n){if(n.getBoundingClientRect().top<innerHeight)n.classList.add("in")})},{passive:true})}',
    // links: alguns visualizadores bloqueiam navegação → copia
    'var box=D.getElementById("linkbox");function showBox(m,u){D.getElementById("lb-msg").textContent=m;D.getElementById("lb-url").textContent=u;box.hidden=false}D.getElementById("lb-close").addEventListener("click",function(){box.hidden=true});',
    'function copyText(t){return new Promise(function(res){try{if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(function(){res(true)},function(){res(fb())});return}}catch(e){}res(fb());function fb(){try{var ta=D.createElement("textarea");ta.value=t;ta.setAttribute("readonly","");ta.style.cssText="position:fixed;left:-999px;top:0;opacity:0";D.body.appendChild(ta);ta.select();ta.setSelectionRange(0,t.length);var ok=D.execCommand("copy");ta.remove();return ok}catch(e){return false}}})}',
    'function settle(){setTimeout(function(){D.querySelectorAll(".rv:not(.in)").forEach(function(n){var r=n.getBoundingClientRect();if(r.top<innerHeight&&r.bottom>0)n.classList.add("in")})},650)}',
    'D.addEventListener("click",function(e){var a=e.target.closest?e.target.closest("a"):null;if(!a||e.defaultPrevented||e.metaKey||e.ctrlKey||e.shiftKey)return;var h=a.getAttribute("href")||"";',
    'if(h.charAt(0)==="#"){var t=D.getElementById(h.slice(1));if(!t)return;e.preventDefault();t.scrollIntoView({behavior:calm?"auto":"smooth",block:"start"});settle();return}',
    'if(/^https?:/i.test(h)){e.preventDefault();var w=null;try{w=window.open(h,"_blank")}catch(er){}if(w){try{w.opener=null}catch(er){}return}copyText(h).then(function(ok){showBox(ok?"This viewer did not open the link. It was copied: paste it in your browser.":"This viewer did not open the link. Copy it from here:",h)})}});',
    // filtro por setor (manchetes e matérias juntas); sem script o filtro some e tudo aparece
    'D.querySelectorAll(".filter").forEach(function(g){var bs=g.querySelectorAll("button");bs.forEach(function(b){b.addEventListener("click",function(){var f=b.dataset.f;bs.forEach(function(x){x.setAttribute("aria-pressed",String(x===b))});D.querySelectorAll("[data-sector]").forEach(function(r){r.hidden=f!=="all"&&r.dataset.sector!==f;if(!r.hidden)r.classList.add("rv","in")})})})});',
    // impressão: "Read the full story" abre tudo
    'window.addEventListener("beforeprint",function(){D.querySelectorAll("details").forEach(function(d){d.open=true})});',
    // dock
    'var dl=D.querySelectorAll(".dock a");if("IntersectionObserver" in window&&dl.length){var dio=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting)dl.forEach(function(l){l.setAttribute("aria-current",String(l.getAttribute("href")==="#"+e.target.id))})})},{rootMargin:"-45% 0px -50% 0px"});dl.forEach(function(l){var s=D.getElementById(l.getAttribute("href").slice(1));if(s)dio.observe(s)})}',
    '})();'
  ].join('\n');

  root.IBBAReports = R;
})(typeof window !== 'undefined' ? window : this);
