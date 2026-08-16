#!/usr/bin/env python3
"""
build_dashboard.py — genera un dashboard HTML autocontenido del gemelo.

Lee telemetry.db (telemetría en vivo, multi-fuente) y wayback.db
(historia 2016-2019 rescatada) y produce un único archivo HTML con todos
los datos y gráficos embebidos (sin dependencias de red salvo la librería
de charts por CDN). Abrilo en el navegador, subilo a GitHub Pages, o
adjuntalo al grant.

Sin servidor: es una FOTO de los datos al momento de generarlo. Para
refrescarlo, volvé a correrlo:  python3 build_dashboard.py

Salida: ../docs/dashboard.html

Uso:
    python3 build_dashboard.py
    python3 build_dashboard.py --out ../docs/index.html
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone


def con_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Extracción de datos
# ---------------------------------------------------------------------------

def latest_snapshot(con, source="bitnodes"):
    r = con.execute("SELECT MAX(ts) FROM snapshots WHERE source=?",
                    (source,)).fetchone()
    if not r or r[0] is None:
        r = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()
    ts = r[0]
    rows = con.execute(
        "SELECT impl, version, count FROM version_counts WHERE ts=?",
        (ts,)).fetchall()
    return ts, rows


def normalize_impl(impl):
    """Colapsa sub-implementaciones para agrupar, preservando la marca."""
    base = impl.replace("-bip110", "")
    is_bip110 = impl.endswith("-bip110")
    return base, is_bip110


def current_distribution(rows, top=12):
    """Distribución por versión desplegable (major.minor), top N."""
    agg = {}
    total = 0
    for impl, ver, n in rows:
        base, _ = normalize_impl(impl)
        mm = ".".join(ver.split(".")[:2])
        label = f"{base} {mm}"
        agg[label] = agg.get(label, 0) + n
        total += n
    ranked = sorted(agg.items(), key=lambda kv: -kv[1])[:top]
    return ranked, total


def impl_split(rows):
    """Core vs Knots vs otros, en el último snapshot."""
    d = {"core": 0, "knots": 0, "other": 0}
    for impl, _, n in rows:
        base, _ = normalize_impl(impl)
        d[base if base in d else "other"] = d.get(base, 0) + n
    return d


def bip110_count(rows):
    knots = core = 0
    for impl, _, n in rows:
        if impl.endswith("-bip110"):
            if impl.startswith("knots"):
                knots += n
            else:
                core += n
    return knots, core


def timeseries(con, source="bitnodes"):
    """Serie temporal: un punto por DÍA (el último snapshot de cada fecha),
    con total y desglose core/knots/bip110. Agrupar por día evita el eje X
    con fechas repetidas cuando hay varios snapshots el mismo día."""
    tss = [r[0] for r in con.execute(
        "SELECT DISTINCT ts FROM snapshots WHERE source=? ORDER BY ts",
        (source,)).fetchall()]
    # Quedarse con el ts más alto (más tardío) de cada fecha.
    by_day = {}
    for ts in tss:
        by_day[iso(ts)] = ts   # como tss está ordenado asc, gana el último
    out = []
    for day in sorted(by_day):
        ts = by_day[day]
        rows = con.execute(
            "SELECT impl, count FROM version_counts WHERE ts=?",
            (ts,)).fetchall()
        core = knots = bip = total = 0
        for impl, n in rows:
            total += n
            if impl.endswith("-bip110"):
                bip += n
            base = impl.replace("-bip110", "")
            if base == "core":
                core += n
            elif base == "knots":
                knots += n
        out.append({"date": day, "total": total, "core": core,
                    "knots": knots, "bip110": bip})
    return out


def source_compare(con):
    """Último snapshot de cada fuente: total y share por versión Core."""
    res = {}
    for src in ("bitnodes", "dsn"):
        r = con.execute("SELECT MAX(ts) FROM snapshots WHERE source=?",
                        (src,)).fetchone()
        if not r or r[0] is None:
            continue
        ts = r[0]
        rows = con.execute(
            "SELECT impl, version, count FROM version_counts WHERE ts=?",
            (ts,)).fetchall()
        total = sum(n for _, _, n in rows)
        by_mm = {}
        for impl, ver, n in rows:
            base, _ = normalize_impl(impl)
            if base != "core":
                continue
            mm = ".".join(ver.split(".")[:2])
            by_mm[mm] = by_mm.get(mm, 0) + n
        res[src] = {"ts": iso(ts), "total": total,
                    "share": {k: v / total for k, v in by_mm.items()}}
    return res


def wayback_series(path):
    """Serie histórica: tamaño de red estimado por snapshot (2016-2019)."""
    try:
        con = con_ro(path)
    except sqlite3.OperationalError:
        return []
    rows = con.execute(
        "SELECT ts, total FROM snapshots ORDER BY ts").fetchall()
    return [{"date": iso(ts), "total": total} for ts, total in rows]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def build_html(data: dict) -> str:
    payload = json.dumps(data)
    # El HTML/CSS/JS va en un solo archivo. Chart.js por CDN.
    return TEMPLATE.replace("__PAYLOAD__", payload)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin Network Twin — Panel</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --line:#21262d; --ink:#e6edf3;
    --muted:#8b949e; --core:#4493f8; --knots:#bc8cff; --signal:#f0a020;
    --signal-dim:#5a4213; --grid:#1c2128;
  }
  *{box-sizing:border-box}
  body{
    margin:0; background:var(--bg); color:var(--ink);
    font-family:ui-monospace,"SF Mono","Cascadia Code",Menlo,monospace;
    line-height:1.5; -webkit-font-smoothing:antialiased;
  }
  .wrap{max-width:1080px; margin:0 auto; padding:32px 20px 80px}
  header{border-bottom:1px solid var(--line); padding-bottom:20px; margin-bottom:8px}
  h1{font-size:20px; font-weight:600; letter-spacing:-.01em; margin:0 0 4px}
  .sub{color:var(--muted); font-size:13px}
  .eyebrow{color:var(--signal); font-size:11px; letter-spacing:.14em;
    text-transform:uppercase; margin-bottom:14px}
  .stamp{color:var(--muted); font-size:11px; margin-top:6px}
  section{padding:34px 0; border-bottom:1px solid var(--line)}
  h2{font-size:12px; letter-spacing:.12em; text-transform:uppercase;
    color:var(--muted); font-weight:600; margin:0 0 18px}
  .stat-row{display:flex; flex-wrap:wrap; gap:28px; margin-bottom:8px}
  .stat .n{font-size:30px; font-weight:600; letter-spacing:-.02em}
  .stat .l{font-size:12px; color:var(--muted); margin-top:2px}
  .chartbox{position:relative; height:300px; margin-top:8px}
  .chartbox.tall{height:340px}
  .note{color:var(--muted); font-size:12px; margin-top:14px; max-width:70ch}
  /* Apartado BIP-110 destacado */
  .signal-panel{
    background:linear-gradient(180deg, #1a1408 0%, var(--panel) 100%);
    border:1px solid var(--signal-dim); border-radius:10px; padding:26px 24px;
    margin-top:4px;
  }
  .signal-panel .live{
    display:inline-flex; align-items:center; gap:7px; font-size:11px;
    letter-spacing:.1em; text-transform:uppercase; color:var(--signal);
  }
  .dot{width:8px; height:8px; border-radius:50%; background:var(--signal);
    box-shadow:0 0 0 0 rgba(240,160,32,.6); animation:pulse 2s infinite}
  @keyframes pulse{
    0%{box-shadow:0 0 0 0 rgba(240,160,32,.5)}
    70%{box-shadow:0 0 0 9px rgba(240,160,32,0)}
    100%{box-shadow:0 0 0 0 rgba(240,160,32,0)}
  }
  @media (prefers-reduced-motion:reduce){.dot{animation:none}}
  .signal-big{font-size:52px; font-weight:700; letter-spacing:-.03em;
    color:var(--signal); line-height:1; margin:14px 0 4px}
  .signal-grid{display:flex; flex-wrap:wrap; gap:26px; margin-top:18px}
  .signal-grid .n{font-size:20px; font-weight:600}
  .signal-grid .l{font-size:11px; color:var(--muted)}
  table{width:100%; border-collapse:collapse; font-size:13px; margin-top:6px}
  th,td{text-align:left; padding:7px 10px; border-bottom:1px solid var(--grid)}
  th{color:var(--muted); font-weight:500; font-size:11px;
    letter-spacing:.06em; text-transform:uppercase}
  td.num{text-align:right; font-variant-numeric:tabular-nums}
  .foot{color:var(--muted); font-size:11px; margin-top:40px; max-width:80ch}
  a{color:var(--core); text-decoration:none}
  .topbar{display:flex; justify-content:space-between; align-items:center}
  .langtoggle{display:flex; gap:2px; border:1px solid var(--line);
    border-radius:6px; overflow:hidden}
  .langtoggle button{background:transparent; color:var(--muted); border:0;
    padding:4px 10px; font-family:inherit; font-size:11px; cursor:pointer;
    letter-spacing:.05em}
  .langtoggle button.active{background:var(--line); color:var(--ink)}
  .langtoggle button:hover{color:var(--ink)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="topbar">
      <div class="eyebrow">Bitcoin Network Digital Twin</div>
      <div class="langtoggle" id="langToggle">
        <button data-lang="en" class="active">EN</button>
        <button data-lang="es">ES</button>
      </div>
    </div>
    <h1 data-i18n="title">Telemetry &amp; adoption panel</h1>
    <div class="sub" data-i18n="subtitle">Synthetic replica calibrated against the live network · open data</div>
    <div class="stamp" id="stamp"></div>
  </header>

  <!-- BIP-110: la firma de la página -->
  <section>
    <h2 data-i18n="bipHead">BIP-110 signaling · UASF</h2>
    <div class="signal-panel">
      <span class="live"><span class="dot"></span> <span data-i18n="bipWindow">Flag-day window · August 2026</span></span>
      <div class="signal-big" id="bipPct">—</div>
      <div class="l" style="color:var(--muted);font-size:12px" data-i18n="bipCaption">
        of the network signals BIP-110 (nodes with a UASF-BIP110 user agent)</div>
      <div class="signal-grid">
        <div><div class="n" id="bipKnots">—</div><div class="l" data-i18n="viaKnots">via Bitcoin Knots</div></div>
        <div><div class="n" id="bipCore">—</div><div class="l" data-i18n="viaCore">via patched Core</div></div>
        <div><div class="n" id="bipTotal">—</div><div class="l" data-i18n="totalMeasured">total nodes measured</div></div>
      </div>
      <p class="note" data-i18n="bipNote">
        Own measurement over reachable user agents. Counts only active
        signaling (UASF-BIP110 / bip110-v*); excludes BIP110-Theory
        mentions and the historical UASF-SegWit-BIP148 from 2017. The time
        series begins the day the parser started preserving the marker.
      </p>
    </div>
    <div class="chartbox" style="margin-top:24px"><canvas id="bipTs"></canvas></div>
  </section>

  <!-- Estado actual -->
  <section>
    <h2 data-i18n="todayHead">The network today</h2>
    <div class="stat-row" id="statRow"></div>
    <div class="chartbox tall"><canvas id="dist"></canvas></div>
    <p class="note" id="distNote"></p>
  </section>

  <!-- Evolución -->
  <section>
    <h2 data-i18n="evoHead">Network evolution</h2>
    <div class="chartbox"><canvas id="evo"></canvas></div>
    <p class="note" data-i18n="evoNote">Reachable nodes by implementation, one point per day.
      Short window for now: the series grows daily.</p>
  </section>

  <!-- Fuentes -->
  <section>
    <h2 data-i18n="srcHead">Discrepancy between sources</h2>
    <div id="srcWrap"></div>
    <p class="note" data-i18n="srcNote">Different crawlers see the network differently
      (coverage and methodology). Cross-checking sources, rather than trusting a
      single one, is part of the project's goal — single-source dependency is
      what left the ecosystem without data when bitnodes.io expired in May 2026.</p>
  </section>

  <!-- Historia -->
  <section>
    <h2 data-i18n="histHead">Recovered history · 2016–2019</h2>
    <div class="chartbox"><canvas id="hist"></canvas></div>
    <p class="note" data-i18n="histNote">Reachable-network size reconstructed from Internet
      Archive captures (bitnodes.21.co and earn.com eras), a dataset that had
      been lost. Basis for the model's historical calibrations (e.g. adoption
      of the CVE-2018-17144 fix).</p>
  </section>

  <div class="foot" id="foot"></div>
</div>

<script>
const I18N = {
  en: {
    title: "Telemetry & adoption panel",
    subtitle: "Synthetic replica calibrated against the live network · open data",
    bipHead: "BIP-110 signaling · UASF",
    bipWindow: "Flag-day window · August 2026",
    bipCaption: "of the network signals BIP-110 (nodes with a UASF-BIP110 user agent)",
    viaKnots: "via Bitcoin Knots", viaCore: "via patched Core",
    totalMeasured: "total nodes measured",
    bipNote: "Own measurement over reachable user agents. Counts only active signaling (UASF-BIP110 / bip110-v*); excludes BIP110-Theory mentions and the historical UASF-SegWit-BIP148 from 2017. The time series begins the day the parser started preserving the marker.",
    todayHead: "The network today",
    statNodes: "reachable nodes", statDominant: "dominant version",
    statKnots: "Knots", statVersions: "distinct versions",
    distNote: "Top {n} deployable versions (major.minor). Core in blue, Knots in purple.",
    evoHead: "Network evolution",
    evoNote: "Reachable nodes by implementation, one point per day. Short window for now: the series grows daily.",
    srcHead: "Discrepancy between sources",
    srcNote: "Different crawlers see the network differently (coverage and methodology). Cross-checking sources, rather than trusting a single one, is part of the project's goal — single-source dependency is what left the ecosystem without data when bitnodes.io expired in May 2026.",
    srcCols: ["source", "date", "nodes seen"],
    srcOne: "Only one source has data so far. Run dsn_ingest.py ingest to populate the comparison.",
    histHead: "Recovered history · 2016–2019",
    histNote: "Reachable-network size reconstructed from Internet Archive captures (bitnodes.21.co and earn.com eras), a dataset that had been lost. Basis for the model's historical calibrations (e.g. adoption of the CVE-2018-17144 fix).",
    generated: "Generated", source: "source",
    noChart: "(chart unavailable without the charts library)",
    foot: "Data: bitnod.es and DSN/KIT (via bitcoin-stats-archive, CC BY 4.0), and the Internet Archive. This panel is a static snapshot generated on {d}; regenerate with build_dashboard.py. Open-source project (MIT).",
    axisSignaling: "signaling nodes"
  },
  es: {
    title: "Panel de telemetría y adopción",
    subtitle: "Réplica sintética calibrada contra la red real · datos abiertos",
    bipHead: "Señalización BIP-110 · UASF",
    bipWindow: "Ventana de flag day · agosto 2026",
    bipCaption: "de la red señaliza BIP-110 (nodos con user agent UASF-BIP110)",
    viaKnots: "vía Bitcoin Knots", viaCore: "vía Core parcheado",
    totalMeasured: "nodos totales medidos",
    bipNote: "Medición propia sobre user agents alcanzables. Cuenta sólo señalización activa (UASF-BIP110 / bip110-v*); excluye menciones BIP110-Theory y el histórico UASF-SegWit-BIP148 de 2017. La serie temporal arranca el día en que el parser comenzó a preservar la marca.",
    todayHead: "La red hoy",
    statNodes: "nodos alcanzables", statDominant: "versión dominante",
    statKnots: "Knots", statVersions: "versiones distintas",
    distNote: "Top {n} versiones desplegables (major.minor). Core en azul, Knots en violeta.",
    evoHead: "Evolución de la red",
    evoNote: "Nodos alcanzables por implementación, un punto por día. Ventana corta por ahora: la serie crece a diario.",
    srcHead: "Discrepancia entre fuentes",
    srcNote: "Distintos crawlers ven la red de forma distinta (cobertura y metodología). Cruzar fuentes, en vez de confiar en una sola, es parte del objetivo del proyecto — la fuente única fue lo que dejó al ecosistema sin datos cuando bitnodes.io expiró en mayo 2026.",
    srcCols: ["fuente", "fecha", "nodos vistos"],
    srcOne: "Sólo una fuente con datos por ahora. Corré dsn_ingest.py ingest para poblar la comparación.",
    histHead: "Historia rescatada · 2016–2019",
    histNote: "Tamaño de la red alcanzable reconstruido desde capturas del Internet Archive (eras bitnodes.21.co y earn.com), un dataset que se había perdido. Base de las calibraciones históricas del modelo (p. ej. la adopción del fix del CVE-2018-17144).",
    generated: "Generado", source: "fuente",
    noChart: "(gráfico no disponible sin la librería de charts)",
    foot: "Datos: bitnod.es y DSN/KIT (vía bitcoin-stats-archive, CC BY 4.0), e Internet Archive. Este panel es una foto estática generada el {d}; regenerar con build_dashboard.py. Proyecto open-source (MIT).",
    axisSignaling: "nodos señalizando"
  }
};
let LANG = "en";
const D = __PAYLOAD__;
const C = getComputedStyle(document.documentElement);
const col = n => C.getPropertyValue(n).trim();
const fmt = n => n.toLocaleString('en-US');
const hasCharts = (typeof Chart !== 'undefined');
if (hasCharts) {
  Chart.defaults.color = col('--muted');
  Chart.defaults.font.family = "ui-monospace, monospace";
  Chart.defaults.font.size = 11;
}
const gridc = col('--grid');
// Helper: crear chart sólo si la librería cargó; si no, mostrar aviso.
function chart(id, cfg){
  const el = document.getElementById(id);
  if(!el) return;
  if(!hasCharts){
    const p=document.createElement('div'); p.className='note';
    p.textContent='(gráfico no disponible sin conexión a la librería de charts)';
    el.replaceWith(p); return;
  }
  new Chart(el, cfg);
}

// --- valores numéricos (no dependen del idioma) ---
document.getElementById('bipPct').textContent = D.bip.pct.toFixed(2) + '%';
document.getElementById('bipKnots').textContent = fmt(D.bip.knots);
document.getElementById('bipCore').textContent = fmt(D.bip.core);
document.getElementById('bipTotal').textContent = fmt(D.bip.networkTotal);

let charts = {};   // referencias para poder re-etiquetar al cambiar idioma

function buildCharts(t){
  Object.values(charts).forEach(c => { if(c && c.destroy) c.destroy(); });
  charts = {};
  const bipSeries = D.timeseries.filter(p => p.bip110 > 0);
  charts.bip = mkchart('bipTs', {
    type:'line',
    data:{ labels:(bipSeries.length?bipSeries:D.timeseries).map(p=>p.date),
      datasets:[{ data:(bipSeries.length?bipSeries:D.timeseries).map(p=>p.bip110),
        borderColor:col('--signal'), backgroundColor:'rgba(240,160,32,.12)',
        fill:true, tension:.25, pointRadius:3 }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{x:{grid:{color:gridc}},y:{grid:{color:gridc},beginAtZero:true,
        title:{display:true,text:t.axisSignaling}}}}
  });
  charts.dist = mkchart('dist', {
    type:'bar',
    data:{ labels:D.distribution.map(d=>d[0]),
      datasets:[{ data:D.distribution.map(d=>d[1]),
        backgroundColor:D.distribution.map(d=>
          d[0].startsWith('knots')?col('--knots'):
          d[0].startsWith('core')?col('--core'):col('--muted')),
        borderRadius:3 }]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{x:{grid:{color:gridc}},y:{grid:{display:false}}}}
  });
  charts.evo = mkchart('evo', {
    type:'line',
    data:{ labels:D.timeseries.map(p=>p.date),
      datasets:[
        {label:'Core',data:D.timeseries.map(p=>p.core),
         borderColor:col('--core'),tension:.25,pointRadius:2},
        {label:'Knots',data:D.timeseries.map(p=>p.knots),
         borderColor:col('--knots'),tension:.25,pointRadius:2}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{boxWidth:12}}},
      scales:{x:{grid:{color:gridc}},y:{grid:{color:gridc},beginAtZero:true}}}
  });
  if(D.wayback.length){
    charts.hist = mkchart('hist', {
      type:'line',
      data:{ labels:D.wayback.map(p=>p.date),
        datasets:[{ data:D.wayback.map(p=>p.total), borderColor:col('--ink'),
          backgroundColor:'rgba(230,237,243,.06)', fill:true,
          tension:.2, pointRadius:2 }]},
      options:{responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false}},
        scales:{x:{grid:{color:gridc}},y:{grid:{color:gridc}}}}
    });
  }
}
// mkchart: como chart(), pero devuelve la instancia (o null) para destruir luego
function mkchart(id, cfg){
  const el = document.getElementById(id);
  if(!el) return null;
  if(!hasCharts){
    if(!el.dataset.noted){
      const p=document.createElement('div'); p.className='note';
      p.textContent=I18N[LANG].noChart; el.replaceWith(p);
    }
    return null;
  }
  return new Chart(el, cfg);
}

function applyLang(lang){
  LANG = lang;
  const t = I18N[lang];
  document.documentElement.lang = lang;
  // Textos con data-i18n
  document.querySelectorAll('[data-i18n]').forEach(el=>{
    const k = el.getAttribute('data-i18n');
    if(t[k]) el.textContent = t[k];
  });
  // Sello
  document.getElementById('stamp').textContent =
    t.generated + ' ' + D.generated + ' · ' + D.snapshotDate +
    ' · ' + t.source + ' ' + D.primarySource;
  // Stats
  const sr = document.getElementById('statRow'); sr.innerHTML='';
  [[t.statNodes, fmt(D.networkTotal)],
   [t.statDominant, D.dominant],
   [t.statKnots, D.knotsPct.toFixed(1)+'%'],
   [t.statVersions, D.distinctVersions]
  ].forEach(([l,n])=>{
    const d=document.createElement('div'); d.className='stat';
    d.innerHTML=`<div class="n">${n}</div><div class="l">${l}</div>`;
    sr.appendChild(d);
  });
  document.getElementById('distNote').textContent =
    t.distNote.replace('{n}', D.distribution.length);
  // Fuentes
  const sw = document.getElementById('srcWrap');
  if(Object.keys(D.sources).length>1){
    const [c0,c1,c2]=t.srcCols;
    let html=`<table><thead><tr><th>${c0}</th><th>${c1}</th>`+
      `<th class="num">${c2}</th></tr></thead><tbody>`;
    Object.entries(D.sources).forEach(([k,v])=>{ html+=`<tr><td>${k}</td>`+
      `<td>${v.ts}</td><td class="num">${fmt(v.total)}</td></tr>`; });
    sw.innerHTML=html+'</tbody></table>';
  }else{
    sw.innerHTML=`<p class="note">${t.srcOne}</p>`;
  }
  // Pie
  document.getElementById('foot').innerHTML =
    t.foot.replace('{d}', D.generated);
  // Botones activos
  document.querySelectorAll('#langToggle button').forEach(b=>{
    b.classList.toggle('active', b.dataset.lang===lang);
  });
  // Charts (recrear para actualizar etiquetas de ejes traducidas)
  if(hasCharts) buildCharts(t);
}

document.querySelectorAll('#langToggle button').forEach(b=>{
  b.addEventListener('click', ()=>applyLang(b.dataset.lang));
});

// Idioma inicial: inglés por defecto; respeta ?lang=es o el navegador.
const urlLang = new URLSearchParams(location.search).get('lang');
const navLang = (navigator.language||'en').slice(0,2);
applyLang(urlLang && I18N[urlLang] ? urlLang : (I18N[navLang] ? navLang : 'en'));
if(!hasCharts) buildCharts(I18N[LANG]);  // dispara los avisos de "no chart"
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--telemetry", default="telemetry.db")
    ap.add_argument("--wayback", default="wayback.db")
    ap.add_argument("--out", default="../docs/dashboard.html")
    args = ap.parse_args()

    con = con_ro(args.telemetry)
    ts, rows = latest_snapshot(con, "bitnodes")
    dist, total = current_distribution(rows)
    split = impl_split(rows)
    knots_bip, core_bip = bip110_count(rows)
    ranked_all, _ = current_distribution(rows, top=1)

    # versión dominante (con etiqueta base+mm)
    dominant = dist[0][0] if dist else "—"
    distinct = len({(normalize_impl(i)[0], v) for i, v, _ in rows})

    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "snapshotDate": iso(ts),
        "primarySource": "bitnod.es",
        "networkTotal": total,
        "dominant": dominant,
        "knotsPct": 100.0 * split.get("knots", 0) / total if total else 0,
        "distinctVersions": distinct,
        "distribution": dist,
        "bip": {
            "knots": knots_bip, "core": core_bip,
            "networkTotal": total,
            "pct": 100.0 * (knots_bip + core_bip) / total if total else 0,
        },
        "timeseries": timeseries(con, "bitnodes"),
        "sources": source_compare(con),
        "wayback": wayback_series(args.wayback),
    }

    html = build_html(data)
    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Dashboard generado: {args.out}")
    print(f"  snapshot {data['snapshotDate']} · {total:,} nodos · "
          f"BIP-110 {data['bip']['pct']:.2f}%")
    print(f"  serie: {len(data['timeseries'])} puntos live, "
          f"{len(data['wayback'])} históricos")
    print("Abrilo en el navegador, o subilo a GitHub Pages (carpeta docs/).")


if __name__ == "__main__":
    main()
