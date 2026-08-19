#!/usr/bin/env python3
"""
build_dashboard.py — dashboard HTML interactivo del gemelo Bitcoin.

Lee telemetry.db (telemetría multi-fuente) y wayback.db (historia
2016-2019 rescatada) y genera UN archivo HTML autocontenido con todos
los datos embebidos y filtros interactivos:

  - rango de fechas (7d / 30d / 90d / todo)
  - filtro por implementación (Core / Knots / BIP-110 / otros)
  - comparación de versiones específicas en el tiempo
  - zoom y pan sobre los gráficos

Todo el filtrado ocurre en el navegador: el HTML lleva la serie completa
por snapshot y por versión en formato compacto, y los gráficos y las
métricas se recalculan al vuelo. No hay servidor ni llamadas de red
(salvo las librerías por CDN).

Salida: ../docs/index.html

Uso:
    python3 build_dashboard.py
    python3 build_dashboard.py --out ../docs/index.html
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone


def con_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Extracción: serie completa por snapshot y versión, en formato compacto
# ---------------------------------------------------------------------------

def pick_source(con) -> tuple[str, str]:
    """Elige la fuente primaria: prefiere el dataset CSV (más completo)."""
    def has(src):
        r = con.execute("SELECT COUNT(*) FROM snapshots WHERE source=?",
                        (src,)).fetchone()
        return r and r[0] > 0
    if has("bitnodes-csv"):
        return "bitnodes-csv", "bitnod.es (CSV dataset)"
    return "bitnodes", "bitnod.es"


def full_series(con, source: str) -> dict:
    """
    Serie completa, un punto por día (último snapshot de cada fecha).

    Formato compacto para no inflar el HTML:
      vk    : ["core|31.0.0", "knots|29.3.0", ...]  (catálogo de versiones)
      snaps : [{"d": "2026-05-20", "c": [[idx, n], ...]}, ...]
    El navegador reconstruye todo desde acá.
    """
    tss = [r[0] for r in con.execute(
        "SELECT DISTINCT ts FROM snapshots WHERE source=? ORDER BY ts",
        (source,)).fetchall()]
    by_day = {}
    for ts in tss:
        by_day[iso(ts)] = ts          # gana el último ts de cada día

    vk_index: dict[str, int] = {}
    snaps = []
    for day in sorted(by_day):
        ts = by_day[day]
        rows = con.execute(
            "SELECT impl, version, count FROM version_counts WHERE ts=?",
            (ts,)).fetchall()
        counts = []
        for impl, ver, n in rows:
            key = f"{impl}|{ver}"
            idx = vk_index.get(key)
            if idx is None:
                idx = len(vk_index)
                vk_index[key] = idx
            counts.append([idx, n])
        snaps.append({"d": day, "c": counts})

    vk = [None] * len(vk_index)
    for key, idx in vk_index.items():
        vk[idx] = key
    return {"vk": vk, "snaps": snaps}


def source_totals(con) -> dict:
    """Último total por fuente, para la tabla de discrepancias."""
    out = {}
    for src in ("bitnodes-csv", "bitnodes", "dsn"):
        r = con.execute("SELECT MAX(ts) FROM snapshots WHERE source=?",
                        (src,)).fetchone()
        if not r or r[0] is None:
            continue
        ts = r[0]
        tot = con.execute(
            "SELECT SUM(count) FROM version_counts WHERE ts=?",
            (ts,)).fetchone()[0] or 0
        out[src] = {"ts": iso(ts), "total": tot}
    return out


def wayback_series(path: str) -> list:
    try:
        con = con_ro(path)
        rows = con.execute(
            "SELECT ts, total FROM snapshots ORDER BY ts").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"date": iso(ts), "total": total} for ts, total in rows]


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin Network Twin — Panel</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/hammer.js/2.0.8/hammer.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-zoom/2.0.1/chartjs-plugin-zoom.min.js"></script>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --line:#21262d; --ink:#e6edf3;
    --muted:#8b949e; --core:#4493f8; --knots:#bc8cff; --signal:#f0a020;
    --other:#6e7681; --grid:#1c2128;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:ui-monospace,"SF Mono","Cascadia Code",Menlo,monospace;
    line-height:1.5;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
  header{border-bottom:1px solid var(--line);padding-bottom:20px}
  h1{font-size:20px;font-weight:600;letter-spacing:-.01em;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px}
  .eyebrow{color:var(--signal);font-size:11px;letter-spacing:.14em;
    text-transform:uppercase;margin-bottom:14px}
  .stamp{color:var(--muted);font-size:11px;margin-top:6px}
  .topbar{display:flex;justify-content:space-between;align-items:center}
  .langtoggle{display:flex;border:1px solid var(--line);border-radius:6px;
    overflow:hidden}
  .langtoggle button{background:transparent;color:var(--muted);border:0;
    padding:4px 10px;font-family:inherit;font-size:11px;cursor:pointer}
  .langtoggle button.active{background:var(--line);color:var(--ink)}
  section{padding:34px 0;border-bottom:1px solid var(--line)}
  h2{font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--muted);font-weight:600;margin:0 0 18px}
  .stat-row{display:flex;flex-wrap:wrap;gap:28px;margin-bottom:8px}
  .stat .n{font-size:30px;font-weight:600;letter-spacing:-.02em}
  .stat .l{font-size:12px;color:var(--muted);margin-top:2px}
  .chartbox{position:relative;height:300px;margin-top:8px}
  .chartbox.tall{height:340px}
  .note{color:var(--muted);font-size:12px;margin-top:14px;max-width:70ch}
  /* Barra de controles */
  .controls{position:sticky;top:0;z-index:20;background:rgba(13,17,23,.94);
    backdrop-filter:blur(6px);border:1px solid var(--line);border-radius:10px;
    padding:12px 14px;margin:18px 0 4px;display:flex;flex-wrap:wrap;
    gap:18px;align-items:center}
  .cgroup{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .clabel{font-size:10px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--muted)}
  .chip{background:transparent;border:1px solid var(--line);color:var(--muted);
    border-radius:999px;padding:3px 11px;font-family:inherit;font-size:11px;
    cursor:pointer;transition:all .12s}
  .chip:hover{color:var(--ink);border-color:var(--muted)}
  .chip.on{background:var(--line);color:var(--ink);border-color:var(--muted)}
  .chip.core.on{border-color:var(--core);color:var(--core)}
  .chip.knots.on{border-color:var(--knots);color:var(--knots)}
  .chip.bip110.on{border-color:var(--signal);color:var(--signal)}
  .reset{margin-left:auto;font-size:10px;color:var(--muted);cursor:pointer;
    background:none;border:0;font-family:inherit;text-decoration:underline}
  select{background:var(--panel);color:var(--ink);border:1px solid var(--line);
    border-radius:6px;padding:4px 8px;font-family:inherit;font-size:11px;
    max-width:260px}
  /* Panel BIP-110 */
  .signal-panel{background:linear-gradient(180deg,#1a1408 0%,var(--panel) 100%);
    border:1px solid #5a4213;border-radius:10px;padding:26px 24px}
  .signal-panel .live{display:inline-flex;align-items:center;gap:7px;
    font-size:11px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--signal)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--signal);
    animation:pulse 2s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(240,160,32,.5)}
    70%{box-shadow:0 0 0 9px rgba(240,160,32,0)}
    100%{box-shadow:0 0 0 0 rgba(240,160,32,0)}}
  @media (prefers-reduced-motion:reduce){.dot{animation:none}}
  .signal-big{font-size:52px;font-weight:700;letter-spacing:-.03em;
    color:var(--signal);line-height:1;margin:14px 0 4px}
  .signal-grid{display:flex;flex-wrap:wrap;gap:26px;margin-top:18px}
  .signal-grid .n{font-size:20px;font-weight:600}
  .signal-grid .l{font-size:11px;color:var(--muted)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--grid)}
  th{color:var(--muted);font-weight:500;font-size:11px;letter-spacing:.06em;
    text-transform:uppercase}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .foot{color:var(--muted);font-size:11px;margin-top:40px;max-width:80ch}
  .hint{font-size:10px;color:var(--muted);margin-top:6px;font-style:italic}
  .localctrl{display:flex;align-items:flex-start;gap:10px;margin:-4px 0 16px;
    flex-wrap:wrap}
  .localctrl .clabel{padding-top:6px;white-space:nowrap}
  .verchips{display:flex;flex-wrap:wrap;gap:6px;flex:1;min-width:240px}
  .verchips .chip{font-size:10.5px;padding:3px 9px}
  .verchips .chip.on{background:var(--line);color:var(--ink)}
  .chip.ghost{opacity:.6}
  .empty{color:var(--muted);font-size:12px;padding:34px 0 10px;
    text-align:center;border:1px dashed var(--line);border-radius:8px;
    margin-top:4px}
  .zoomhint{font-size:10px;color:var(--muted);opacity:.7;margin-top:8px;
    display:flex;align-items:center;gap:6px}
  .statusgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
    gap:22px}
  .stlabel{font-size:10px;letter-spacing:.1em;text-transform:uppercase;
    margin-bottom:8px;font-weight:600}
  .stlabel.done{color:#3fb950} .stlabel.wip{color:var(--signal)}
  .stlabel.todo{color:var(--muted)}
  .stlist{list-style:none;padding:0;margin:0;font-size:12px;color:var(--muted)}
  .stlist li{padding:3px 0 3px 16px;position:relative;line-height:1.45}
  .stlist li::before{position:absolute;left:0;top:3px}
  .stDone li::before{content:"✓";color:#3fb950}
  .stWip li::before{content:"◐";color:var(--signal)}
  .stTodo li::before{content:"○";color:var(--muted)}
  .globallabel{font-size:10px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--signal);opacity:.75;margin-right:4px}
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
    <div class="sub" data-i18n="subtitle">Telemetry layer of an open Bitcoin network twin · observed data, not a census</div>
    <div class="stamp" id="stamp"></div>
  </header>

  <!-- Controles -->
  <div class="controls" id="controls">
    <span class="globallabel" data-i18n="ctrlGlobal">Global</span>
    <div class="cgroup">
      <span class="clabel" data-i18n="ctrlRange">Range</span>
      <button class="chip range" data-days="7">7d</button>
      <button class="chip range" data-days="30">30d</button>
      <button class="chip range" data-days="90">90d</button>
      <button class="chip range on" data-days="0" data-i18n="ctrlAll">All</button>
    </div>
    <div class="cgroup">
      <span class="clabel" data-i18n="ctrlImpl">Implementation</span>
      <button class="chip impl core on" data-impl="core">Core</button>
      <button class="chip impl knots on" data-impl="knots">Knots</button>
      <button class="chip impl bip110 on" data-impl="bip110">BIP-110</button>
      <button class="chip impl other on" data-impl="other" data-i18n="ctrlOther">Other</button>
    </div>
    <button class="reset" id="resetBtn" data-i18n="ctrlReset">reset</button>
  </div>
  <div class="hint" data-i18n="hintZoom">🖱 Mouse wheel over any chart = zoom in/out · drag = pan sideways · double-click = reset</div>

  <!-- BIP-110 -->
  <section>
    <h2 data-i18n="bipHead">BIP-110 signaling · UASF</h2>
    <div class="signal-panel">
      <span class="live"><span class="dot"></span> <span data-i18n="bipWindow">Flag-day window · August 2026</span></span>
      <div class="signal-big" id="bipPct">—</div>
      <div style="color:var(--muted);font-size:12px" data-i18n="bipCaption">
        of observed nodes run BIP-110 signaling software</div>
      <div class="signal-grid">
        <div><div class="n" id="bipKnots">—</div><div class="l" data-i18n="viaKnots">via Bitcoin Knots</div></div>
        <div><div class="n" id="bipCore">—</div><div class="l" data-i18n="viaCore">via patched Core</div></div>
        <div><div class="n" id="bipTotal">—</div><div class="l" data-i18n="totalMeasured">nodes in snapshot</div></div>
      </div>
      <p class="note" data-i18n="bipNote">Own measurement over observed user agents. Counts only active signaling (UASF-BIP110 / bip110-v*); excludes BIP110-Theory mentions and the historical UASF-SegWit-BIP148 from 2017. Node software signaling — not miner block signaling, which is a separate metric.</p>
    </div>
    <div class="chartbox" style="margin-top:24px"><canvas id="bipTs"></canvas></div>
  </section>

  <!-- Estado actual -->
  <section>
    <h2 data-i18n="todayHead">Snapshot</h2>
    <div class="stat-row" id="statRow"></div>
    <div class="chartbox tall"><canvas id="dist"></canvas></div>
    <p class="note" id="distNote"></p>
  </section>

  <!-- Comparación de versiones -->
  <section id="verSection">
    <h2 data-i18n="verHead">Version comparison over time</h2>
    <div class="localctrl">
      <span class="clabel" data-i18n="ctrlVersions">Pick versions</span>
      <div class="verchips" id="verChips"></div>
      <button class="chip ghost" id="verClear" data-i18n="ctrlClear">clear</button>
    </div>
    <div class="chartbox" id="verBox"><canvas id="verChart"></canvas></div>
    <div class="empty" id="verEmpty" data-i18n="verEmpty">Pick one or more versions above to plot their share over time.</div>
    <p class="note" data-i18n="verNote">Select versions above to plot their share over time. Useful for watching a new release propagate while older ones decay. The global date range applies here too.</p>
  </section>

  <!-- Evolución -->
  <section>
    <h2 data-i18n="evoHead">Network evolution</h2>
    <div class="chartbox"><canvas id="evo"></canvas></div>
    <p class="note" data-i18n="evoNote">Observed nodes by implementation, one point per day.</p>
  </section>

  <!-- Core vs Knots -->
  <section>
    <h2 data-i18n="ckHead">Implementation share over time</h2>
    <div class="chartbox"><canvas id="ck"></canvas></div>
    <p class="note" data-i18n="ckNote">Percentage of observed nodes running each implementation.</p>
  </section>

  <!-- Cola larga -->
  <section>
    <h2 data-i18n="staleHead">The long tail · outdated nodes</h2>
    <div class="chartbox"><canvas id="stale"></canvas></div>
    <p class="note" data-i18n="staleNote">Core nodes running releases at least three major versions behind the newest (or 0.x).</p>
  </section>

  <!-- Fuentes -->
  <section>
    <h2 data-i18n="srcHead">Discrepancy between sources</h2>
    <div id="srcWrap"></div>
    <p class="note" data-i18n="srcNote">Different crawlers see the network differently (coverage and methodology). Every node count is an observation, not a census.</p>
  </section>

  <!-- Historia -->
  <section>
    <h2 data-i18n="histHead">Recovered history · 2016–2019</h2>
    <div class="chartbox"><canvas id="hist"></canvas></div>
    <p class="note" data-i18n="histNote">Observed network size reconstructed from Internet Archive captures (bitnodes.21.co and earn.com eras), a dataset that had become inaccessible.</p>
  </section>

  <!-- Estado del proyecto -->
  <section>
    <h2 data-i18n="statusHead">Project status</h2>
    <div class="statusgrid">
      <div>
        <div class="stlabel done" data-i18n="stDone">Implemented</div>
        <ul class="stlist" id="stDoneList"></ul>
      </div>
      <div>
        <div class="stlabel wip" data-i18n="stWip">Experimental</div>
        <ul class="stlist" id="stWipList"></ul>
      </div>
      <div>
        <div class="stlabel todo" data-i18n="stTodo">Planned</div>
        <ul class="stlist" id="stTodoList"></ul>
      </div>
    </div>
    <p class="note" data-i18n="statusNote">This page is the telemetry layer. The synthetic-network side of the project is under active development — listed honestly above rather than implied.</p>
  </section>

  <div class="foot" id="foot"></div>
</div>

<script>
const I18N = {
  en: {
    title:"Telemetry & adoption panel",
    subtitle:"Telemetry layer of an open Bitcoin network twin · observed data, not a census",
    ctrlRange:"Range", ctrlAll:"All", ctrlImpl:"Implementation",
    ctrlOther:"Other", ctrlVersions:"Pick versions", ctrlReset:"reset",
    ctrlGlobal:"Global filters", ctrlClear:"clear",
    hintZoom:"🖱 Mouse wheel over any chart = zoom in/out · drag = pan sideways · double-click = reset",
    bipHead:"BIP-110 signaling · UASF",
    bipWindow:"Flag-day window · August 2026",
    bipCaption:"of observed nodes run BIP-110 signaling software",
    viaKnots:"via Bitcoin Knots", viaCore:"via patched Core",
    totalMeasured:"nodes in snapshot",
    bipNote:"Own measurement over observed user agents. Counts only active signaling (UASF-BIP110 / bip110-v*); excludes BIP110-Theory mentions and the historical UASF-SegWit-BIP148 from 2017. Node software signaling — not miner block signaling, which is a separate metric.",
    todayHead:"Snapshot",
    statNodes:"observed nodes", statDominant:"dominant version",
    statKnots:"Knots", statVersions:"distinct versions",
    distNote:"Top {n} deployable versions (major.minor) in the selected snapshot.",
    verHead:"Version comparison over time",
    ctrlPick:"Pick versions",
    verEmpty:"Pick one or more versions above to plot their share over time.",
    verNote:"Select versions above to plot their share over time. Useful for watching a new release propagate while older ones decay. The global date range applies here too.",
    verEmpty:"Pick one or more versions above to plot their share over time.",
    evoHead:"Network evolution",
    evoNote:"Observed nodes by implementation, one point per day.",
    ckHead:"Implementation share over time",
    ckNote:"Percentage of observed nodes running each implementation.",
    staleHead:"The long tail · outdated nodes",
    staleNote:"Core nodes running releases at least three major versions behind the newest (or 0.x).",
    srcHead:"Discrepancy between sources",
    srcNote:"Different crawlers see the network differently (coverage and methodology). Every node count is an observation, not a census.",
    srcCols:["source","date","nodes seen"],
    histHead:"Recovered history · 2016–2019",
    histNote:"Observed network size reconstructed from Internet Archive captures (bitnodes.21.co and earn.com eras), a dataset that had become inaccessible.",
    statusHead:"Project status",
    stDone:"Implemented", stWip:"Experimental", stTodo:"Planned",
    statusNote:"This page is the telemetry layer. The synthetic-network side of the project is under active development — listed honestly above rather than implied.",
    listDone:["Automated telemetry ingest (public CSV datasets)","Continuous own series since May 2026","Historical recovery 2016–2019 (Internet Archive)","Synthetic-network generation from real distribution","Multi-source auditing","This dashboard, self-updating in CI"],
    listWip:["Adoption model (hazard, 5 profiles, imitation)","Calibration against historical release curves","Agent simulation (dry-run backend)","Validator: sim-vs-real distance metrics"],
    listTodo:["Live Warnet deployment at scale","Mixed Core + Knots synthetic networks","Self-hosted crawler (independent telemetry)","Modules: mempool/fees, propagation, hashrate"],
    generated:"Generated", source:"source",
    axisSignaling:"signaling nodes", axisStale:"outdated nodes",
    axisShare:"share of observed nodes",
    noChart:"(chart unavailable without the charts library)",
    foot:"Data: bitnod.es public CSV datasets, DSN/KIT (via bitcoin-stats-archive, CC BY 4.0), and the Internet Archive. Static snapshot generated {d}; regenerate with build_dashboard.py. Open-source (MIT)."
  },
  es: {
    title:"Panel de telemetría y adopción",
    subtitle:"Capa de telemetría de un gemelo abierto de la red Bitcoin · datos observados, no un censo",
    ctrlRange:"Rango", ctrlAll:"Todo", ctrlImpl:"Implementación",
    ctrlOther:"Otros", ctrlVersions:"Elegir versiones", ctrlReset:"reiniciar",
    ctrlGlobal:"Filtros globales", ctrlClear:"limpiar",
    hintZoom:"🖱 Rueda del mouse sobre cualquier gráfico = acercar/alejar · arrastrar = desplazar · doble clic = reiniciar",
    bipHead:"Señalización BIP-110 · UASF",
    bipWindow:"Ventana de flag day · agosto 2026",
    bipCaption:"de los nodos observados corre software que señaliza BIP-110",
    viaKnots:"vía Bitcoin Knots", viaCore:"vía Core parcheado",
    totalMeasured:"nodos en el snapshot",
    bipNote:"Medición propia sobre user agents observados. Cuenta sólo señalización activa (UASF-BIP110 / bip110-v*); excluye menciones BIP110-Theory y el histórico UASF-SegWit-BIP148 de 2017. Señalización por software de nodos — no señalización de bloques por mineros, que es una métrica distinta.",
    todayHead:"Snapshot",
    statNodes:"nodos observados", statDominant:"versión dominante",
    statKnots:"Knots", statVersions:"versiones distintas",
    distNote:"Top {n} versiones desplegables (major.minor) en el snapshot seleccionado.",
    verHead:"Comparación de versiones en el tiempo",
    ctrlPick:"Elegir versiones",
    verEmpty:"Elegí una o más versiones arriba para graficar su cuota en el tiempo.",
    verNote:"Elegí versiones acá arriba para graficar su cuota en el tiempo. Útil para ver propagarse un release nuevo mientras los viejos decaen. El rango global también aplica.",
    verEmpty:"Ninguna versión seleccionada — elegí algunas en la barra de arriba.",
    evoHead:"Evolución de la red",
    evoNote:"Nodos observados por implementación, un punto por día.",
    ckHead:"Cuota por implementación en el tiempo",
    ckNote:"Porcentaje de nodos observados que corre cada implementación.",
    staleHead:"La cola larga · nodos desactualizados",
    staleNote:"Nodos Core en releases al menos tres versiones mayores por detrás de la más nueva (o 0.x).",
    srcHead:"Discrepancia entre fuentes",
    srcNote:"Distintos crawlers ven la red de forma distinta (cobertura y metodología). Todo conteo de nodos es una observación, no un censo.",
    srcCols:["fuente","fecha","nodos vistos"],
    histHead:"Historia rescatada · 2016–2019",
    histNote:"Tamaño observado de la red reconstruido desde capturas del Internet Archive (eras bitnodes.21.co y earn.com), un dataset que se había vuelto inaccesible.",
    statusHead:"Estado del proyecto",
    stDone:"Implementado", stWip:"Experimental", stTodo:"Planeado",
    statusNote:"Esta página es la capa de telemetría. La parte de red sintética está en desarrollo activo — listada honestamente arriba en vez de dada por hecha.",
    listDone:["Ingesta automática de telemetría (datasets CSV públicos)","Serie propia continua desde mayo 2026","Recuperación histórica 2016–2019 (Internet Archive)","Generación de red sintética desde la distribución real","Auditoría multi-fuente","Este panel, autoactualizado en CI"],
    listWip:["Modelo de adopción (hazard, 5 perfiles, imitación)","Calibración contra curvas históricas de releases","Simulación de agentes (backend dry-run)","Validador: métricas de distancia sim-vs-real"],
    listTodo:["Deploy de Warnet a escala","Redes sintéticas mixtas Core + Knots","Crawler propio (telemetría independiente)","Módulos: mempool/fees, propagación, hashrate"],
    generated:"Generado", source:"fuente",
    axisSignaling:"nodos señalizando", axisStale:"nodos desactualizados",
    axisShare:"cuota de nodos observados",
    noChart:"(gráfico no disponible sin la librería de charts)",
    foot:"Datos: datasets CSV públicos de bitnod.es, DSN/KIT (vía bitcoin-stats-archive, CC BY 4.0), e Internet Archive. Foto estática generada el {d}; regenerar con build_dashboard.py. Open-source (MIT)."
  }
};

const D = __PAYLOAD__;
let LANG = "en";
const state = { days: 0, impls: new Set(["core","knots","bip110","other"]),
                versions: new Set() };

const C = getComputedStyle(document.documentElement);
const col = n => C.getPropertyValue(n).trim();
const fmt = n => n.toLocaleString('en-US');
const hasCharts = (typeof Chart !== 'undefined');
if (hasCharts && typeof ChartZoom !== 'undefined') {
  try { Chart.register(ChartZoom); } catch(e){}
}
if (hasCharts) {
  Chart.defaults.color = col('--muted');
  Chart.defaults.font.family = "ui-monospace, monospace";
  Chart.defaults.font.size = 11;
}
const gridc = col('--grid');
let charts = {};

// ---- helpers de datos -----------------------------------------------------
// vk: "impl|version"  ->  {impl, base, ver, mm}
const VK = D.series.vk.map(k => {
  const [impl, ver] = k.split("|");
  const base = impl.endsWith("-bip110") ? "bip110"
             : (impl === "core" || impl === "knots") ? impl : "other";
  const p = ver.split(".");
  const mm = (p.length > 1) ? p[0] + "." + p[1] : ver;
  const implBase = impl.replace("-bip110","");
  return { impl, implBase, base, ver, mm, label: implBase + " " + mm };
});

function snapsInRange(){
  const s = D.series.snaps;
  if (!state.days) return s;
  return s.slice(Math.max(0, s.length - state.days));
}
function implOK(v){ return state.impls.has(v.base); }

// Agrega un snapshot: devuelve {total, byBase, byLabel, bip:{knots,core}}
function agg(sn){
  let total = 0; const byBase = {core:0,knots:0,bip110:0,other:0};
  const byLabel = {}; let bipK = 0, bipC = 0; const mmSet = new Set();
  const verCount = {};
  for (const [idx, n] of sn.c){
    const v = VK[idx];
    if (!implOK(v)) continue;
    total += n;
    byBase[v.base] += n;
    byLabel[v.label] = (byLabel[v.label]||0) + n;
    verCount[v.implBase + " " + v.ver] = (verCount[v.implBase+" "+v.ver]||0)+n;
    mmSet.add(v.implBase + " " + v.ver);
    if (v.base === "bip110"){ if (v.implBase === "knots") bipK += n; else bipC += n; }
  }
  return { total, byBase, byLabel, bip:{knots:bipK, core:bipC},
           distinct: mmSet.size };
}

function mmKey(mm){ const p = mm.split("."); return [parseInt(p[0])||0, parseInt(p[1])||0]; }

// Cola larga: core con major <= newestMajor-3, o 0.x
//
// "newest" NO es el máximo: unos pocos nodos reportan user agents con
// versiones inexistentes (p. ej. 77.0.0, vistos en 2 nodos de ~47k) y el
// máximo crudo los tomaría como la versión actual, marcando toda la red
// como obsoleta. Se usa la versión mayor más nueva con presencia
// significativa (>=1% de los nodos Core), que es robusta a datos basura
// y a los builds de desarrollo (.99).
function staleOf(sn){
  let coreTot = 0;
  const byMajor = {};
  for (const [idx,n] of sn.c){
    const v = VK[idx];
    if (v.implBase !== "core") continue;
    coreTot += n;
    const [M, mi] = mmKey(v.mm);
    if (mi === 99) continue;                  // builds de desarrollo
    byMajor[M] = (byMajor[M]||0) + n;
  }
  if (!coreTot) return { stale:0, pct:0, newest:0 };
  const thresh = coreTot * 0.01;              // 1% de los nodos Core
  let newest = 0;
  for (const M in byMajor){
    if (byMajor[M] >= thresh) newest = Math.max(newest, parseInt(M));
  }
  if (!newest){                                // fallback defensivo
    for (const M in byMajor) newest = Math.max(newest, parseInt(M));
  }
  let stale = 0;
  for (const [idx,n] of sn.c){
    const v = VK[idx];
    if (v.implBase !== "core") continue;
    const M = mmKey(v.mm)[0];
    if (M === 0 || M < newest - 2) stale += n;
  }
  return { stale, pct: coreTot ? 100*stale/coreTot : 0, newest };
}

function shareOfVersion(sn, label){
  let hit = 0, tot = 0;
  for (const [idx,n] of sn.c){
    const v = VK[idx];
    if (!implOK(v)) continue;
    tot += n;
    if (v.label === label) hit += n;
  }
  return tot ? 100*hit/tot : 0;
}

// ---- construcción de gráficos --------------------------------------------
function mk(id, cfg){
  const el = document.getElementById(id);
  if (!el) return null;
  if (!hasCharts){
    const p = document.createElement('div'); p.className='note';
    p.textContent = I18N[LANG].noChart; el.replaceWith(p); return null;
  }
  return new Chart(el, cfg);
}
const ZOOM = {
  zoom:{ wheel:{enabled:true}, pinch:{enabled:true}, mode:'x' },
  pan:{ enabled:true, mode:'x' }
};
function baseOpts(t, extra){
  const o = { responsive:true, maintainAspectRatio:false,
    plugins:{ legend:{display:false}, zoom: ZOOM },
    scales:{ x:{grid:{color:gridc}}, y:{grid:{color:gridc},beginAtZero:true} } };
  return Object.assign(o, extra||{});
}

function rebuild(){
  const t = I18N[LANG];
  Object.values(charts).forEach(c => { if (c && c.destroy) c.destroy(); });
  charts = {};

  const snaps = snapsInRange();
  const labels = snaps.map(s => s.d);
  const aggs = snaps.map(agg);
  const last = aggs.length ? aggs[aggs.length-1] : {total:0,byBase:{},byLabel:{},bip:{knots:0,core:0},distinct:0};
  const lastSnap = snaps.length ? snaps[snaps.length-1] : {c:[],d:"—"};

  // --- stats + BIP-110 (del último snapshot del rango) ---
  const bipTot = last.bip.knots + last.bip.core;
  document.getElementById('bipPct').textContent =
    (last.total ? (100*bipTot/last.total) : 0).toFixed(2) + '%';
  document.getElementById('bipKnots').textContent = fmt(last.bip.knots);
  document.getElementById('bipCore').textContent = fmt(last.bip.core);
  document.getElementById('bipTotal').textContent = fmt(last.total);

  const distArr = Object.entries(last.byLabel).sort((a,b)=>b[1]-a[1]).slice(0,12);
  const dominant = distArr.length ? distArr[0][0] : "—";
  const knotsPct = last.total ? 100*last.byBase.knots/last.total : 0;
  const sr = document.getElementById('statRow'); sr.innerHTML='';
  [[t.statNodes, fmt(last.total)],
   [t.statDominant, dominant],
   [t.statKnots, knotsPct.toFixed(1)+'%'],
   [t.statVersions, last.distinct]
  ].forEach(([l,n])=>{
    const d=document.createElement('div'); d.className='stat';
    d.innerHTML='<div class="n">'+n+'</div><div class="l">'+l+'</div>';
    sr.appendChild(d);
  });
  document.getElementById('distNote').textContent =
    t.distNote.replace('{n}', distArr.length) + '  ·  ' + lastSnap.d;

  // --- BIP-110 en el tiempo ---
  charts.bip = mk('bipTs', { type:'line',
    data:{ labels, datasets:[{ data: aggs.map(a=>a.bip.knots+a.bip.core),
      borderColor:col('--signal'), backgroundColor:'rgba(240,160,32,.12)',
      fill:true, tension:.25, pointRadius:2 }]},
    options: baseOpts(t, { scales:{ x:{grid:{color:gridc}},
      y:{grid:{color:gridc},beginAtZero:true,
         title:{display:true,text:t.axisSignaling}} } }) });

  // --- distribución del snapshot ---
  charts.dist = mk('dist', { type:'bar',
    data:{ labels: distArr.map(d=>d[0]),
      datasets:[{ data: distArr.map(d=>d[1]),
        backgroundColor: distArr.map(d=> d[0].startsWith('knots')?col('--knots')
          : d[0].startsWith('core')?col('--core'):col('--other')),
        borderRadius:3 }]},
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{x:{grid:{color:gridc}},y:{grid:{display:false}}} } });

  // --- comparación de versiones ---
  const palette = [col('--core'),col('--knots'),col('--signal'),'#3fb950','#db6d28','#a371f7'];
  const sel = [...state.versions];
  const vBox = document.getElementById('verBox');
  const vEmpty = document.getElementById('verEmpty');
  if (sel.length){
    if (vBox) vBox.style.display = '';
    if (vEmpty) vEmpty.style.display = 'none';
    charts.ver = mk('verChart', { type:'line',
      data:{ labels, datasets: sel.map((lab,i)=>({
        label: lab, borderColor: palette[i%palette.length],
        data: snaps.map(s=>shareOfVersion(s,lab)),
        tension:.25, pointRadius:2, borderWidth:2 })) },
      options: baseOpts(t, { plugins:{legend:{labels:{boxWidth:12}},zoom:ZOOM},
        scales:{ x:{grid:{color:gridc}},
          y:{grid:{color:gridc},beginAtZero:true,
             ticks:{callback:v=>v+'%'},
             title:{display:true,text:t.axisShare}} } }) });
  } else {
    // sin selección: ocultar el canvas (evita un hueco vacío) y mostrar aviso
    if (vBox) vBox.style.display = 'none';
    if (vEmpty) vEmpty.style.display = '';
  }

  // --- evolución por implementación ---
  const dsEvo = [];
  if (state.impls.has('core')) dsEvo.push({label:'Core',
    data:aggs.map(a=>a.byBase.core), borderColor:col('--core'),tension:.25,pointRadius:2});
  if (state.impls.has('knots')) dsEvo.push({label:'Knots',
    data:aggs.map(a=>a.byBase.knots), borderColor:col('--knots'),tension:.25,pointRadius:2});
  if (state.impls.has('bip110')) dsEvo.push({label:'BIP-110',
    data:aggs.map(a=>a.byBase.bip110), borderColor:col('--signal'),tension:.25,pointRadius:2});
  charts.evo = mk('evo', { type:'line', data:{labels, datasets:dsEvo},
    options: baseOpts(t, {plugins:{legend:{labels:{boxWidth:12}},zoom:ZOOM}}) });

  // --- cuota por implementación ---
  const dsCk = [];
  const pct = (k) => aggs.map(a => a.total ? 100*a.byBase[k]/a.total : 0);
  if (state.impls.has('core')) dsCk.push({label:'Core %',data:pct('core'),
    borderColor:col('--core'),backgroundColor:'rgba(68,147,248,.08)',fill:true,tension:.25,pointRadius:2});
  if (state.impls.has('knots')) dsCk.push({label:'Knots %',data:pct('knots'),
    borderColor:col('--knots'),backgroundColor:'rgba(188,140,255,.10)',fill:true,tension:.25,pointRadius:2});
  if (state.impls.has('bip110')) dsCk.push({label:'BIP-110 %',data:pct('bip110'),
    borderColor:col('--signal'),backgroundColor:'rgba(240,160,32,.10)',fill:true,tension:.25,pointRadius:2});
  charts.ck = mk('ck', { type:'line', data:{labels,datasets:dsCk},
    options: baseOpts(t, {plugins:{legend:{labels:{boxWidth:12}},zoom:ZOOM},
      scales:{x:{grid:{color:gridc}},
        y:{grid:{color:gridc},beginAtZero:true,ticks:{callback:v=>v+'%'}}}}) });

  // --- cola larga ---
  const st = snaps.map(staleOf);
  charts.stale = mk('stale', { type:'line',
    data:{labels, datasets:[{ data: st.map(s=>s.stale),
      borderColor:col('--signal'), backgroundColor:'rgba(240,160,32,.10)',
      fill:true, tension:.25, pointRadius:2 }]},
    options: baseOpts(t, { plugins:{legend:{display:false},zoom:ZOOM,
      tooltip:{callbacks:{afterLabel:(c)=> '· '+st[c.dataIndex].pct.toFixed(1)+'% of Core'}}},
      scales:{x:{grid:{color:gridc}},
        y:{grid:{color:gridc},beginAtZero:true,title:{display:true,text:t.axisStale}}} }) });

  // --- historia wayback (no depende de los filtros) ---
  if (D.wayback.length){
    charts.hist = mk('hist', { type:'line',
      data:{ labels: D.wayback.map(p=>p.date),
        datasets:[{ data: D.wayback.map(p=>p.total), borderColor:col('--ink'),
          backgroundColor:'rgba(230,237,243,.06)', fill:true, tension:.2, pointRadius:2 }]},
      options: baseOpts(t, {plugins:{legend:{display:false},zoom:ZOOM}}) });
  }
}

// ---- i18n + render estático ----------------------------------------------
function applyLang(lang){
  LANG = lang; const t = I18N[lang];
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(el=>{
    const k = el.getAttribute('data-i18n'); if (t[k]) el.textContent = t[k];
  });
  document.getElementById('stamp').textContent =
    t.generated + ' ' + D.generated + ' · ' + t.source + ' ' + D.primarySource;
  const sw = document.getElementById('srcWrap');
  const [c0,c1,c2] = t.srcCols;
  let html = '<table><thead><tr><th>'+c0+'</th><th>'+c1+'</th><th class="num">'+c2+'</th></tr></thead><tbody>';
  Object.entries(D.sources).forEach(([k,v])=>{
    html += '<tr><td>'+k+'</td><td>'+v.ts+'</td><td class="num">'+fmt(v.total)+'</td></tr>';
  });
  sw.innerHTML = html + '</tbody></table>';
  // listas de estado del proyecto
  [['stDoneList', t.listDone, 'stDone'],
   ['stWipList',  t.listWip,  'stWip'],
   ['stTodoList', t.listTodo, 'stTodo']].forEach(([id, items, cls])=>{
    const ul = document.getElementById(id);
    if (!ul || !items) return;
    ul.className = 'stlist ' + cls;
    ul.innerHTML = items.map(x => '<li>' + x + '</li>').join('');
  });
  document.getElementById('foot').textContent = t.foot.replace('{d}', D.generated);
  document.querySelectorAll('#langToggle button').forEach(b=>{
    b.classList.toggle('active', b.dataset.lang===lang); });
  rebuild();
}

// ---- controles ------------------------------------------------------------
function initControls(){
  // Chips de versión: las 14 más presentes en el último snapshot.
  const lastSnap = D.series.snaps[D.series.snaps.length-1];
  const tally = {};
  if (lastSnap) for (const [idx,n] of lastSnap.c){
    const v = VK[idx]; tally[v.label] = (tally[v.label]||0)+n;
  }
  const opts = Object.entries(tally).sort((a,b)=>b[1]-a[1]).slice(0,14);
  const box = document.getElementById('verChips');
  opts.forEach(([lab])=>{
    const b = document.createElement('button');
    b.className = 'chip ver'; b.textContent = lab; b.dataset.ver = lab;
    b.addEventListener('click', ()=>{
      if (state.versions.has(lab)){ state.versions.delete(lab); b.classList.remove('on'); }
      else { state.versions.add(lab); b.classList.add('on'); }
      rebuild();
    });
    box.appendChild(b);
  });
  const clr = document.getElementById('verClear');
  if (clr) clr.addEventListener('click', ()=>{
    state.versions = new Set();
    document.querySelectorAll('.chip.ver').forEach(x=>x.classList.remove('on'));
    rebuild();
  });
  document.querySelectorAll('.chip.range').forEach(b=>{
    b.addEventListener('click', ()=>{
      document.querySelectorAll('.chip.range').forEach(x=>x.classList.remove('on'));
      b.classList.add('on');
      state.days = parseInt(b.dataset.days)||0;
      rebuild();
    });
  });
  document.querySelectorAll('.chip.impl').forEach(b=>{
    b.addEventListener('click', ()=>{
      const k = b.dataset.impl;
      if (state.impls.has(k) && state.impls.size > 1){ state.impls.delete(k); b.classList.remove('on'); }
      else { state.impls.add(k); b.classList.add('on'); }
      rebuild();
    });
  });
  document.getElementById('resetBtn').addEventListener('click', ()=>{
    state.days = 0; state.impls = new Set(["core","knots","bip110","other"]);
    state.versions = new Set();
    document.querySelectorAll('.chip.range').forEach(x=>x.classList.toggle('on', x.dataset.days==="0"));
    document.querySelectorAll('.chip.impl').forEach(x=>x.classList.add('on'));
    document.querySelectorAll('.chip.ver').forEach(x=>x.classList.remove('on'));
    Object.values(charts).forEach(c=>{ if(c&&c.resetZoom) try{c.resetZoom();}catch(e){} });
    rebuild();
  });
  document.querySelectorAll('#langToggle button').forEach(b=>{
    b.addEventListener('click', ()=>applyLang(b.dataset.lang)); });
  // doble clic en un gráfico -> reset zoom
  document.querySelectorAll('canvas').forEach(cv=>{
    cv.addEventListener('dblclick', ()=>{
      Object.values(charts).forEach(c=>{ if(c&&c.resetZoom) try{c.resetZoom();}catch(e){} });
    });
  });
}

initControls();
const urlLang = new URLSearchParams(location.search).get('lang');
applyLang(urlLang && I18N[urlLang] ? urlLang : 'en');
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--telemetry", default="telemetry.db")
    ap.add_argument("--wayback", default="wayback.db")
    ap.add_argument("--out", default="../docs/index.html")
    args = ap.parse_args()

    con = con_ro(args.telemetry)
    source, label = pick_source(con)
    series = full_series(con, source)

    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "primarySource": label,
        "series": series,
        "sources": source_totals(con),
        "wayback": wayback_series(args.wayback),
    }

    html = TEMPLATE.replace("__PAYLOAD__", json.dumps(data, separators=(",", ":")))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(html)

    n_snaps = len(series["snaps"])
    n_vers = len(series["vk"])
    size_kb = os.path.getsize(args.out) / 1024
    print(f"Dashboard generado: {args.out}")
    print(f"  fuente: {label} · {n_snaps} snapshots · {n_vers} versiones")
    print(f"  histórico wayback: {len(data['wayback'])} puntos")
    print(f"  tamaño: {size_kb:.0f} KB (datos embebidos, filtrado en el navegador)")


if __name__ == "__main__":
    main()
