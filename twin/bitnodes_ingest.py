"""
Ingestor de telemetría de Bitnodes para el gemelo digital.
==========================================================

Componente "Telemetría de mainnet" del módulo de adopción. Hace tres cosas:

1. ingest : baja el último snapshot de Bitnodes (todos los nodos
            alcanzables con su user agent), lo reduce a conteos por
            (implementación, versión) y lo guarda en SQLite.
2. curve  : con los snapshots acumulados, construye la curva de adopción
            de un release (fracción de la red con versión >= objetivo,
            día por día desde la fecha del release) y la exporta a CSV
            en el formato que consume adoption_model.fit().
3. report : muestra la distribución de versiones del último snapshot.

La fecha del release se obtiene automáticamente de la API de GitHub
(tag del repo bitcoin/bitcoin), así que no hay que hardcodear nada.

Rate limits (importante)
------------------------
La API pública de Bitnodes sin API key admite muy pocos requests por día
(del orden de decenas). Por eso este script:
  - pide UN solo snapshot por corrida de `ingest`;
  - guarda todo localmente y nunca re-pide datos que ya tiene.
Un cron diario (o cada 12 h) es suficiente: las curvas de adopción se
mueven en escala de semanas. Los snapshots viven 60 días en el servidor;
para historia más profunda existe el Bitnodes Archive.

Uso
---
    python3 bitnodes_ingest.py ingest                 # snapshot -> SQLite
    python3 bitnodes_ingest.py ingest --mock          # prueba sin red
    python3 bitnodes_ingest.py report                 # distribución actual
    python3 bitnodes_ingest.py curve --version 29.0 \
            --out curve_29.csv                        # curva para el modelo

Dependencias: solo la biblioteca estándar (urllib, sqlite3, csv).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

BITNODES_LATEST = "https://bitnodes.io/api/v1/snapshots/latest/"
GITHUB_RELEASE = ("https://api.github.com/repos/bitcoin/bitcoin/"
                  "releases/tags/v{v}")
DB_PATH = "telemetry.db"
UA_HEADER = {"User-Agent": "adoption-twin-ingest/0.1"}

# '/Satoshi:29.0.0/'  -> Core 29.0.0
# '/Satoshi:27.1.0/Knots:20240801/' -> Knots 27.1.0
UA_RE = re.compile(r"/Satoshi:(?P<ver>[\d.]+)[^/]*/(?:(?P<knots>Knots)[^/]*/)?")


# ---------------------------------------------------------------------------
# Parsing de user agents
# ---------------------------------------------------------------------------

def parse_user_agent(ua: str) -> tuple[str, str]:
    """Devuelve (implementación, versión). Lo no reconocible va a 'other'."""
    m = UA_RE.match(ua or "")
    if not m:
        return ("other", "unknown")
    impl = "knots" if m.group("knots") else "core"
    ver = m.group("ver").rstrip(".")
    return (impl, ver)


def version_key(ver: str) -> tuple[int, ...]:
    """'29.0.0' -> (29, 0, 0) para comparar versiones numéricamente."""
    try:
        return tuple(int(x) for x in ver.split("."))
    except ValueError:
        return (-1,)


# ---------------------------------------------------------------------------
# Almacenamiento
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts INTEGER PRIMARY KEY,          -- unix time del snapshot
    total_nodes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS version_counts (
    ts INTEGER NOT NULL REFERENCES snapshots(ts),
    impl TEXT NOT NULL,              -- core | knots | other
    version TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (ts, impl, version)
);
"""


def db_connect(path: str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def store_snapshot(con: sqlite3.Connection, ts: int, total: int,
                   counts: dict[tuple[str, str], int]) -> bool:
    """Guarda un snapshot. Devuelve False si ya existía (idempotente)."""
    cur = con.execute("SELECT 1 FROM snapshots WHERE ts=?", (ts,))
    if cur.fetchone():
        return False
    con.execute("INSERT INTO snapshots VALUES (?,?)", (ts, total))
    con.executemany(
        "INSERT INTO version_counts VALUES (?,?,?,?)",
        [(ts, impl, ver, n) for (impl, ver), n in counts.items()],
    )
    con.commit()
    return True


# ---------------------------------------------------------------------------
# Fuentes de datos
# ---------------------------------------------------------------------------

def http_json(url: str) -> dict:
    headers = dict(UA_HEADER)
    # GITHUB_TOKEN (opcional) sube el rate limit de GitHub de 60 a 5000/h.
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def fetch_latest_snapshot() -> tuple[int, int, dict[tuple[str, str], int]]:
    """Baja el último snapshot y lo reduce a conteos por versión."""
    data = http_json(BITNODES_LATEST)
    ts = int(data["timestamp"])
    nodes = data.get("nodes", {})
    counts: dict[tuple[str, str], int] = {}
    for _, fields in nodes.items():
        # fields = [protocol, user_agent, connected_since, ...]
        ua = fields[1] if len(fields) > 1 else ""
        key = parse_user_agent(ua)
        counts[key] = counts.get(key, 0) + 1
    return ts, len(nodes), counts


def mock_snapshot(ts: int | None = None):
    """Snapshot sintético para probar el pipeline sin tocar la red."""
    ts = ts or int(time.time())
    counts = {
        ("core", "29.0.0"): 4200, ("core", "28.1.0"): 7100,
        ("core", "28.0.0"): 3900, ("core", "27.2.0"): 2800,
        ("core", "26.0.0"): 1500, ("knots", "28.1.0"): 1600,
        ("other", "unknown"): 900,
    }
    return ts, sum(counts.values()), counts


def github_release_date(version: str) -> datetime:
    """Fecha de publicación (UTC) del release v<version> en bitcoin/bitcoin."""
    try:
        rel = http_json(GITHUB_RELEASE.format(v=version))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            sys.exit("GitHub devolvió 403 (rate limit sin token). Opciones: "
                     "exportá GITHUB_TOKEN, o pasá --release-date YYYY-MM-DD.")
        if e.code == 404:
            sys.exit(f"No existe el release v{version} en bitcoin/bitcoin. "
                     "Verificá el número o pasá --release-date.")
        raise
    date_str = rel["published_at"]
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Construcción de la curva de adopción
# ---------------------------------------------------------------------------

def adoption_curve(con: sqlite3.Connection, impl: str, version: str,
                   release_dt: datetime) -> list[tuple[float, float]]:
    """
    Para cada snapshot almacenado posterior al release, calcula la fracción
    de nodos de `impl` con versión >= `version`, relativa al total de nodos
    de esa implementación. Devuelve [(días_desde_release, fracción), ...].

    Nota metodológica: usar >= (y no ==) evita que la curva "baje" cuando
    sale el siguiente release y la gente salta directo a él.
    """
    target = version_key(version)
    rows = con.execute(
        "SELECT ts, version, count FROM version_counts WHERE impl=?",
        (impl,),
    ).fetchall()
    by_ts: dict[int, list[tuple[str, int]]] = {}
    for ts, ver, n in rows:
        by_ts.setdefault(ts, []).append((ver, n))

    release_ts = release_dt.timestamp()
    curve = []
    for ts in sorted(by_ts):
        if ts < release_ts:
            continue
        total = sum(n for _, n in by_ts[ts])
        newer = sum(n for v, n in by_ts[ts] if version_key(v) >= target)
        if total:
            days = (ts - release_ts) / 86400.0
            curve.append((round(days, 2), newer / total))
    return curve


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_ingest(args) -> None:
    con = db_connect(args.db)
    if args.mock:
        ts, total, counts = mock_snapshot()
        origen = "mock"
    else:
        ts, total, counts = fetch_latest_snapshot()
        origen = "bitnodes"
    nuevo = store_snapshot(con, ts, total, counts)
    when = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    estado = "guardado" if nuevo else "ya existía, ignorado"
    print(f"Snapshot {when} ({origen}): {total} nodos, "
          f"{len(counts)} versiones distintas -> {estado}")


def cmd_report(args) -> None:
    con = db_connect(args.db)
    row = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()
    if not row or row[0] is None:
        sys.exit("No hay snapshots. Corré primero: ingest")
    ts = row[0]
    when = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    rows = con.execute(
        "SELECT impl, version, count FROM version_counts WHERE ts=? "
        "ORDER BY count DESC", (ts,)
    ).fetchall()
    total = sum(n for _, _, n in rows)
    print(f"Snapshot {when} — {total} nodos alcanzables\n")
    print(f"{'impl':<8}{'versión':<12}{'nodos':>8}{'%':>8}")
    for impl, ver, n in rows[:15]:
        print(f"{impl:<8}{ver:<12}{n:>8}{n / total:>8.1%}")


def cmd_curve(args) -> None:
    con = db_connect(args.db)
    if args.release_date:
        release_dt = datetime.fromisoformat(args.release_date)
        release_dt = release_dt.replace(tzinfo=timezone.utc)
        print(f"Fecha de release (manual): {release_dt.date()}")
    else:
        release_dt = github_release_date(args.version)
        print(f"Fecha de release v{args.version} según GitHub: "
              f"{release_dt.date()}")

    curve = adoption_curve(con, args.impl, args.version, release_dt)
    if not curve:
        sys.exit("Sin datos posteriores al release. Acumulá más snapshots.")

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["days_since_release", "adopted_fraction"])
        w.writerows(curve)
    print(f"{len(curve)} puntos exportados a {args.out}")
    print("Cargalos en adoption_model.fit() con:")
    print(f"  data = np.loadtxt('{args.out}', delimiter=',', skiprows=1)")
    print("  fitted, rmse = fit(data[:, 0], data[:, 1])")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default=DB_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="bajar y guardar el último snapshot")
    p.add_argument("--mock", action="store_true",
                   help="usar datos sintéticos (sin red)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("report", help="distribución del último snapshot")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("curve", help="exportar curva de adopción a CSV")
    p.add_argument("--impl", default="core", choices=["core", "knots"])
    p.add_argument("--version", required=True, help="ej: 29.0")
    p.add_argument("--release-date", default=None,
                   help="ISO date; si falta, se consulta GitHub")
    p.add_argument("--out", default="adoption_curve.csv")
    p.set_defaults(func=cmd_curve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
