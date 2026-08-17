"""
Ingestor por CSV: descarga los snapshots públicos de bitnod.es.
===============================================================

Reemplaza el scraping del HTML por la descarga de los CSV que bitnod.es
publica explícitamente en su sección "Full Dataset Snapshots"
(https://bitnod.es/YYYY/MM/DD.csv). Ventajas frente al scraping:

  - Legal: se descarga un archivo publicado para descarga, no se
    "cosecha" la página. Es el uso previsto del dato.
  - Completo: el CSV trae TODOS los nodos alcanzables (~46k) con todos
    los campos, no sólo el top que mostraba la tabla HTML (~27k).
  - Estable: formato CSV fijo; no se rompe si cambian el diseño del HTML.
  - Histórico: se pueden bajar snapshots pasados (la lista llega hasta
    mayo 2026), llenando parte del hueco de la serie propia.

Nota de frecuencia: los CSV completos se publican ~1 vez por semana
(el HTML se actualizaba cada hora). Para curvas de adopción, que se
mueven en semanas, el CSV semanal es más que suficiente y de mejor
calidad. La columna export_date varía por fila (última vez que se vio
cada nodo); para el snapshot contamos todos los nodos del archivo como
el estado "a la fecha del CSV".

Estructura del CSV (verificada):
  export_date, ip_address, port, country, isp, services,
  protocol_version, user_agent, block_height

Uso:
    python3 csv_ingest.py ingest                 # baja el CSV de hoy
    python3 csv_ingest.py ingest --date 2026-08-16
    python3 csv_ingest.py backfill --since 2026-05-21   # histórico
    python3 csv_ingest.py ingest --file local.csv       # archivo local
    python3 csv_ingest.py report

Escribe a la MISMA telemetry.db, con source='bitnodes-csv' para
distinguir de los snapshots por scraping viejos. Reusa el parser de
user agents de bitnodes_ingest (incluye la detección de BIP-110).

Dependencias: biblioteca estándar.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from bitnodes_ingest import db_connect, parse_user_agent

CSV_URL = "https://bitnod.es/csv/bitcoin_nodes_{y}-{m:02d}-{d:02d}.csv"
UA_HEADER = {"User-Agent": "adoption-twin-ingest/0.3 (research; CSV dataset)"}
SOURCE = "bitnodes-csv"


def http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA_HEADER)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def parse_csv(text: str) -> tuple[dict, str]:
    """Devuelve (conteos_por_(impl,version), fecha_mayoritaria_del_csv)."""
    counts: dict[tuple[str, str], int] = {}
    dates: dict[str, int] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        ua = row.get("user_agent", "")
        key = parse_user_agent(ua)
        counts[key] = counts.get(key, 0) + 1
        d = row.get("export_date", "")
        if d:
            dates[d] = dates.get(d, 0) + 1
    # fecha representativa: la más frecuente en el archivo
    csv_date = max(dates, key=dates.get) if dates else ""
    return counts, csv_date


def store(con, ts: int, total: int, counts: dict, source: str) -> bool:
    """Guarda el snapshot manejando la columna source. Idempotente."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(snapshots)")]
    if "source" not in cols:
        con.execute("ALTER TABLE snapshots ADD COLUMN source TEXT "
                    "DEFAULT 'bitnodes'")
    if con.execute("SELECT 1 FROM snapshots WHERE ts=?", (ts,)).fetchone():
        return False
    con.execute("INSERT INTO snapshots (ts, total_nodes, source) "
                "VALUES (?,?,?)", (ts, total, source))
    con.executemany(
        "INSERT INTO version_counts VALUES (?,?,?,?)",
        [(ts, impl, ver, n) for (impl, ver), n in counts.items()])
    con.commit()
    return True


def ts_from_date(date_str: str) -> int:
    """'2026-08-16' -> unix ts (mediodía UTC, para no colisionar con
    snapshots por scraping del mismo día que usan la hora real)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=12, tzinfo=timezone.utc)
    return int(dt.timestamp())


def ingest_one(con, date_str: str, local_file: str | None) -> None:
    if local_file:
        text = open(local_file, encoding="utf-8", errors="replace").read()
        origen = local_file
    else:
        y, m, d = date_str.split("-")
        url = CSV_URL.format(y=y, m=int(m), d=int(d))
        try:
            text = http_bytes(url).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  {date_str}: no hay CSV publicado (404)")
                return
            raise
        origen = url

    counts, csv_date = parse_csv(text)
    if not counts:
        print(f"  {date_str}: CSV vacío o ilegible")
        return
    # usar la fecha del contenido si está; si no, la pedida
    eff_date = csv_date or date_str
    ts = ts_from_date(eff_date)
    total = sum(counts.values())
    nuevo = store(con, ts, total, counts, SOURCE)
    estado = "guardado" if nuevo else "ya existía"
    print(f"  {eff_date}: {total} nodos, {len(counts)} versiones "
          f"-> {estado}  [{origen.split('/')[-1]}]")


def cmd_ingest(args) -> None:
    con = db_connect(args.db)
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Ingesta CSV para {date_str}:")
    ingest_one(con, date_str, args.file)


def cmd_backfill(args) -> None:
    con = db_connect(args.db)
    start = datetime.strptime(args.since, "%Y-%m-%d").replace(
        tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    print(f"Backfill de {args.since} a hoy (los CSV son ~semanales; "
          f"las fechas sin archivo dan 404 y se saltean):")
    day = start
    got = 0
    while day <= end:
        ingest_one(con, day.strftime("%Y-%m-%d"), None)
        day += timedelta(days=args.step)
        got += 1
    print(f"Backfill terminado ({got} fechas intentadas).")


def cmd_report(args) -> None:
    con = db_connect(args.db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(snapshots)")]
    where = f"WHERE source='{SOURCE}'" if "source" in cols else ""
    row = con.execute(f"SELECT MAX(ts) FROM snapshots {where}").fetchone()
    if not row or row[0] is None:
        sys.exit(f"No hay snapshots CSV. Corré: csv_ingest.py ingest")
    ts = row[0]
    when = datetime.fromtimestamp(ts, tz=timezone.utc).date()
    rows = con.execute(
        "SELECT impl, version, count FROM version_counts WHERE ts=? "
        "ORDER BY count DESC", (ts,)).fetchall()
    total = sum(n for _, _, n in rows)
    print(f"Snapshot CSV {when} — {total} nodos\n")
    print(f"{'impl':<16}{'versión':<12}{'nodos':>8}{'%':>8}")
    for impl, ver, n in rows[:15]:
        print(f"{impl:<16}{ver:<12}{n:>8}{n / total:>8.1%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default="telemetry.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="bajar/leer un CSV y guardarlo")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: hoy)")
    p.add_argument("--file", default=None, help="leer un CSV local")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("backfill", help="bajar histórico de CSV")
    p.add_argument("--since", required=True, help="YYYY-MM-DD inicial")
    p.add_argument("--step", type=int, default=1,
                   help="paso en días (default 1; los 404 se saltean)")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("report", help="último snapshot CSV")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
