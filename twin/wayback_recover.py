"""
Recuperación de la historia perdida de Bitnodes desde la Wayback Machine.
=========================================================================

bitnodes.io murió el 2026-05-03 y sus 13 años de telemetría se perdieron.
Pero el Internet Archive capturó sus páginas durante años. Esta
herramienta reconstruye la serie histórica de distribución de versiones
a partir de esas capturas.

Tres comandos (pensados para correrse en ese orden):

list  : consulta la API CDX del Archive y lista qué capturas existen
        para las URLs de interés (página /nodes/ con el top de user
        agents, y endpoints JSON de la API si fueron archivados).
        No baja nada; es el censo de lo recuperable.

fetch : baja las capturas (1 por día como máximo, con pausa entre
        requests para ser respetuosos con el Archive) y guarda los
        archivos CRUDOS en disco. Separar bajada de parseo es clave:
        los crudos se bajan una sola vez y se pueden re-parsear infinitas
        veces mientras mejora el parser.

parse : extrae (implementación, versión) -> conteo de cada crudo y lo
        guarda en una base SQLite SEPARADA (wayback.db por default).
        Separada porque la metodología difiere de la telemetría en vivo:
        la página /nodes/ solo mostraba el top 10 de user agents, así
        que las fracciones son sobre ese subconjunto (se guarda también
        el total del subconjunto para poder razonar sobre cobertura).
        Capturas de la API JSON (si existen) sí son snapshots completos
        y se marcan como tales.

URLs históricas de Bitnodes (el sitio cambió de casa dos veces):
    bitnodes.io            (2018-2026)
    bitnodes.earn.com      (2017-2018)
    bitnodes.21.co         (2015-2017)

Uso:
    python3 wayback_recover.py list
    python3 wayback_recover.py fetch --limit 50      # empezar de a poco
    python3 wayback_recover.py parse
    python3 wayback_recover.py report

Dependencias: solo biblioteca estándar.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CDX = "https://web.archive.org/cdx/search/cdx"
# id_ => contenido original crudo, sin la barra de herramientas de Wayback
CAPTURE = "https://web.archive.org/web/{ts}id_/{original}"

TARGETS = [
    "bitnodes.io/nodes/",
    "bitnodes.io/api/v1/snapshots/latest/",
    "bitnodes.earn.com/nodes/",
    "bitnodes.21.co/nodes/",
]

RAW_DIR = "wayback_raw"
DB_PATH = "wayback.db"
UA_HEADER = {"User-Agent": "bitcoin-twin-wayback-recovery/0.1 "
                           "(recovering lost public Bitnodes history)"}

UA_RE = re.compile(r"/Satoshi:(?P<ver>[\d.]+)[^/]*/(?:(?P<knots>Knots)[^/]*/)?")


def parse_user_agent(ua: str) -> tuple[str, str]:
    # La era earn.com mostraba user agents sin barras ("Satoshi:0.16.3");
    # normalizamos a la forma canónica "/.../" antes de parsear.
    ua = "/" + (ua or "").strip("/") + "/"
    m = UA_RE.match(ua)
    if not m:
        return ("other", "unknown")
    return ("knots" if m.group("knots") else "core", m.group("ver").rstrip("."))


# ---------------------------------------------------------------------------
# Almacenamiento
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts INTEGER PRIMARY KEY,          -- unix time de la captura
    source TEXT NOT NULL,            -- URL original archivada
    kind TEXT NOT NULL,              -- 'top10' (parcial) | 'api' (completo)
    total INTEGER NOT NULL           -- nodos representados en la captura
);
CREATE TABLE IF NOT EXISTS version_counts (
    ts INTEGER NOT NULL REFERENCES snapshots(ts),
    impl TEXT NOT NULL,
    version TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (ts, impl, version)
);
"""


def db_connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------------------
# CDX: censo de capturas disponibles
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA_HEADER)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cdx_captures(target: str) -> list[tuple[str, str]]:
    """
    Lista (timestamp, url_original) de capturas HTTP 200 del target,
    colapsadas a una por día (collapse=timestamp:8 usa YYYYMMDD).
    """
    q = urllib.parse.urlencode({
        "url": target, "output": "json", "filter": "statuscode:200",
        "collapse": "timestamp:8", "fl": "timestamp,original",
    })
    data = json.loads(http_get(f"{CDX}?{q}").decode())
    return [(row[0], row[1]) for row in data[1:]]  # fila 0 = encabezados


def cmd_list(args) -> None:
    total = 0
    for target in TARGETS:
        try:
            caps = cdx_captures(target)
        except Exception as e:
            print(f"{target}: error consultando CDX ({e})")
            continue
        total += len(caps)
        if caps:
            first = caps[0][0][:8]
            last = caps[-1][0][:8]
            print(f"{target}: {len(caps)} capturas diarias "
                  f"({first} a {last})")
        else:
            print(f"{target}: sin capturas")
        time.sleep(args.delay)
    print(f"\nTotal recuperable: {total} capturas (1/día máx). "
          f"Siguiente paso: fetch")


# ---------------------------------------------------------------------------
# Fetch: bajar crudos con throttling
# ---------------------------------------------------------------------------

def raw_path(ts: str, original: str) -> str:
    host = urllib.parse.urlparse("//" + original.split("://")[-1]).hostname
    kind = "api" if "/api/" in original else "nodes"
    return os.path.join(RAW_DIR, f"{ts}_{host}_{kind}.raw")


def cmd_fetch(args) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    fetched = skipped = failed = 0
    for target in TARGETS:
        try:
            caps = cdx_captures(target)
        except Exception as e:
            print(f"{target}: CDX falló ({e}); sigo con el próximo")
            continue
        for ts, original in caps:
            if args.limit and fetched >= args.limit:
                print(f"\nLímite de {args.limit} alcanzado. "
                      f"{fetched} bajadas, {skipped} ya existentes, "
                      f"{failed} fallidas. Re-corré para continuar.")
                return
            path = raw_path(ts, original)
            if os.path.exists(path):
                skipped += 1
                continue
            url = CAPTURE.format(ts=ts, original=original)
            try:
                data = http_get(url)
                with open(path, "wb") as f:
                    f.write(data)
                fetched += 1
                print(f"  {ts} <- {original} ({len(data)} bytes)")
            except Exception as e:
                failed += 1
                print(f"  {ts} FALLÓ: {e}")
            time.sleep(args.delay)   # respeto al Archive: sin martillar
    print(f"\nListo: {fetched} bajadas, {skipped} ya existentes, "
          f"{failed} fallidas. Crudos en {RAW_DIR}/")


# ---------------------------------------------------------------------------
# Parse: crudos -> base de datos
# ---------------------------------------------------------------------------

def parse_api_json(text: str) -> dict[tuple[str, str], int] | None:
    """Captura de /api/v1/snapshots/latest/: snapshot completo."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    nodes = data.get("nodes")
    if not isinstance(nodes, dict):
        return None
    counts: dict[tuple[str, str], int] = {}
    for fields in nodes.values():
        ua = fields[1] if isinstance(fields, list) and len(fields) > 1 else ""
        key = parse_user_agent(ua)
        counts[key] = counts.get(key, 0) + 1
    return counts


# Firma de la tabla "top user agents" en ambas eras del sitio:
# <a href="?q=/Satoshi:0.12.1/">...</a></td><td>1242 (19.55%)</td>
# El ancla es el conteo seguido de su porcentaje entre paréntesis: las
# filas de nodos individuales (con alturas de bloque y otros enteros)
# no tienen esa forma y quedan excluidas por construcción.
TOP_UA_RE = re.compile(
    r'\?q=(?P<ua>/?Satoshi:[^"&\']+)["\'][^>]*>.*?</a>\s*</td>\s*'
    r'<td[^>]*>\s*(?P<count>[\d,]+)\s*\(\s*[\d.]+\s*%\s*\)',
    re.S | re.I)


def parse_nodes_html(html: str) -> dict[tuple[str, str], int]:
    """
    Página /nodes/ (tabla de top user agents). Parser ESTRICTO: solo
    acepta el patrón link-con-?q= seguido de "conteo (porcentaje%)".
    Preferimos que un formato desconocido devuelva vacío (y quede
    flaggeado para inspección manual) antes que sumar números que no son
    conteos — lección aprendida: la versión laxa de este parser absorbía
    alturas de bloque de las filas de nodos individuales.
    """
    counts: dict[tuple[str, str], int] = {}
    for m in TOP_UA_RE.finditer(html):
        key = parse_user_agent(m.group("ua"))
        counts[key] = counts.get(key, 0) + int(
            m.group("count").replace(",", ""))
    return counts


def cmd_parse(args) -> None:
    con = db_connect(args.db)
    if not os.path.isdir(RAW_DIR):
        sys.exit(f"No existe {RAW_DIR}/. Corré fetch primero.")
    ok = empty = dup = 0
    for name in sorted(os.listdir(RAW_DIR)):
        path = os.path.join(RAW_DIR, name)
        ts_str = name.split("_")[0]
        ts = int(datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                 .replace(tzinfo=timezone.utc).timestamp())
        if con.execute("SELECT 1 FROM snapshots WHERE ts=?",
                       (ts,)).fetchone():
            dup += 1
            continue
        text = open(path, "rb").read().decode("utf-8", errors="replace")
        counts = parse_api_json(text)
        kind = "api"
        if counts is None:
            counts = parse_nodes_html(text)
            kind = "top10"
        if not counts:
            empty += 1
            continue
        total = sum(counts.values())
        con.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                    (ts, name, kind, total))
        con.executemany(
            "INSERT INTO version_counts VALUES (?,?,?,?)",
            [(ts, i, v, n) for (i, v), n in counts.items()])
        ok += 1
    con.commit()
    print(f"Parseados: {ok} | sin datos extraíbles: {empty} | "
          f"ya en base: {dup}")
    if empty:
        print("Los crudos no parseados quedan en disco: inspeccioná "
              "alguno a mano y ajustamos parse_nodes_html() para esa era "
              "del sitio.")


def cmd_report(args) -> None:
    con = db_connect(args.db)
    rows = con.execute(
        "SELECT ts, kind, total FROM snapshots ORDER BY ts").fetchall()
    if not rows:
        sys.exit("Base vacía. Corré fetch y parse primero.")
    first = datetime.fromtimestamp(rows[0][0], tz=timezone.utc).date()
    last = datetime.fromtimestamp(rows[-1][0], tz=timezone.utc).date()
    n_api = sum(1 for _, k, _ in rows if k == "api")
    print(f"Serie recuperada: {len(rows)} snapshots, {first} a {last}")
    print(f"  completos (API): {n_api} | parciales (top 10): "
          f"{len(rows) - n_api}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default=DB_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="censo de capturas en el Archive")
    p.add_argument("--delay", type=float, default=2.0)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("fetch", help="bajar crudos (educadamente)")
    p.add_argument("--limit", type=int, default=0,
                   help="máx. capturas nuevas por corrida (0 = sin tope)")
    p.add_argument("--delay", type=float, default=3.0,
                   help="segundos entre requests al Archive")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("parse", help="crudos -> wayback.db")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("report", help="resumen de la serie recuperada")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
