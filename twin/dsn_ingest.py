"""
Segunda fuente de telemetría: DSN Bitcoin Monitoring (KIT).
===========================================================

Fuente independiente de bitnod.es para robustez multi-fuente. Los datos
provienen del monitoreo de la red P2P de Bitcoin del instituto KIT
(Alemania), archivados a diario por bitcoin-data/bitcoin-stats-archive
(rama dsn-bitcoin-monitoring), bajo licencia CC BY 4.0.

Por qué una segunda fuente
--------------------------
La telemetría de Bitcoin post-Bitnodes está fragmentada y las fuentes
discrepan enormemente (de <2.500 a >90.000 nodos según quién mida). Una
sola fuente es un punto único de falla — el mismo error que dejó al
ecosistema sin datos cuando murió bitnodes.io. Con dos fuentes
independientes el gemelo gana robustez y además puede MEDIR y documentar
esas discrepancias, que es una contribución en sí misma.

Formato de origen (gnuplot)
---------------------------
El archivo I/versionstr_all.gp es un script gnuplot con datos embebidos:
  - una línea `plot ... title '/Satoshi:X/', ... title '/Satoshi:Y/'`
    que declara las series EN ORDEN;
  - luego un bloque por serie, encabezado por `# /Satoshi:X/`, con
    líneas `<unix_ts>\t<conteo>` (una muestra cada ~1 hora).
Los títulos y los bloques se corresponden 1:1 y en el mismo orden.
Para la "foto actual" tomamos, de cada serie, el conteo del último
timestamp disponible.

Las carpetas I y N son las dos vistas del monitor (incoming/listening y
la otra dirección). Usamos I por defecto (nodos alcanzables), que es lo
comparable con bitnod.es.

Uso:
    python3 dsn_ingest.py ingest              # baja, parsea y guarda
    python3 dsn_ingest.py ingest --view N
    python3 dsn_ingest.py report              # último snapshot DSN
    python3 dsn_ingest.py compare             # DSN vs bitnod.es lado a lado

Escribe a la MISMA telemetry.db que bitnodes_ingest.py, con source='dsn'
en la tabla de snapshots, para que las dos fuentes convivan sin pisarse.

Dependencias: biblioteca estándar. Reusa el esquema y helpers de
bitnodes_ingest.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

from bitnodes_ingest import (db_connect, parse_user_agent, store_snapshot,
                            version_key)

RAW_BASE = ("https://raw.githubusercontent.com/bitcoin-data/"
            "bitcoin-stats-archive/dsn-bitcoin-monitoring/{view}/"
            "versionstr_all.gp")
UA_HEADER = {"User-Agent": "adoption-twin-ingest/0.2 (research)"}

TITLE_RE = re.compile(r"title\s+'([^']*)'")


def http_text(url: str) -> str:
    req = urllib.request.Request(url, headers=UA_HEADER)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_gnuplot(text: str) -> tuple[int, dict[tuple[str, str], int]]:
    """
    Parsea el .gp y devuelve (timestamp_de_la_foto, conteos_por_version).
    Toma el ÚLTIMO valor de cada serie (el estado más reciente).
    """
    lines = text.splitlines()

    # 1) Orden de las series desde la línea `plot ...`.
    plot_line = next((ln for ln in lines if ln.lstrip().startswith("plot")),
                     None)
    if not plot_line:
        raise RuntimeError("No se encontró la línea 'plot' en el .gp")
    titles = TITLE_RE.findall(plot_line)
    if not titles:
        raise RuntimeError("No se extrajeron títulos de series del .gp")

    # 2) Recorrer los bloques `# <titulo>` y quedarse con el último punto.
    counts: dict[tuple[str, str], int] = {}
    latest_ts = 0
    current: str | None = None
    last_val_for_current: tuple[int, int] | None = None

    def flush():
        nonlocal last_val_for_current, latest_ts
        if current is not None and last_val_for_current is not None:
            ts, val = last_val_for_current
            key = parse_user_agent(current)
            counts[key] = counts.get(key, 0) + val
            latest_ts = max(latest_ts, ts)

    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            flush()
            current = s[1:].strip()   # el título de este bloque
            last_val_for_current = None
            continue
        if current is None:
            continue
        parts = s.split()
        if len(parts) == 2 and parts[0].isdigit():
            last_val_for_current = (int(parts[0]), int(float(parts[1])))
    flush()

    if not counts:
        raise RuntimeError("El .gp no produjo conteos; ¿cambió el formato?")
    return latest_ts, counts


def fetch_dsn_snapshot(view: str) -> tuple[int, int, dict]:
    text = http_text(RAW_BASE.format(view=view))
    ts, counts = parse_gnuplot(text)
    if not ts:
        ts = int(time.time())
    return ts, sum(counts.values()), counts


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_ingest(args) -> None:
    ts, total, counts = fetch_dsn_snapshot(args.view)
    con = db_connect(args.db)
    # store_snapshot de bitnodes_ingest no distingue source; para no
    # colisionar con snapshots de bitnod.es del mismo segundo, desplazamos
    # y marcamos el origen agregando una fila especial no es posible ahí,
    # así que usamos la variante con source de abajo.
    nuevo = _store_with_source(con, ts, total, counts, "dsn")
    when = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    estado = "guardado" if nuevo else "ya existía, ignorado"
    print(f"Snapshot DSN/KIT vista {args.view} {when}: {total} nodos, "
          f"{len(counts)} versiones -> {estado}")


def _store_with_source(con, ts, total, counts, source):
    """
    Como la tabla snapshots de bitnodes_ingest no tiene columna source,
    la agregamos si falta (migración idempotente) y guardamos con origen.
    """
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


def cmd_report(args) -> None:
    con = db_connect(args.db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(snapshots)")]
    where = "WHERE source='dsn'" if "source" in cols else ""
    row = con.execute(
        f"SELECT MAX(ts) FROM snapshots {where}").fetchone()
    if not row or row[0] is None:
        sys.exit("No hay snapshots DSN. Corré: dsn_ingest.py ingest")
    ts = row[0]
    when = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    rows = con.execute(
        "SELECT impl, version, count FROM version_counts WHERE ts=? "
        "ORDER BY count DESC", (ts,)).fetchall()
    total = sum(n for _, _, n in rows)
    print(f"Snapshot DSN/KIT {when} — {total} nodos\n")
    print(f"{'impl':<8}{'versión':<12}{'nodos':>8}{'%':>8}")
    for impl, ver, n in rows[:15]:
        print(f"{impl:<8}{ver:<12}{n:>8}{n / total:>8.1%}")


def cmd_compare(args) -> None:
    """Compara el último snapshot de cada fuente lado a lado."""
    con = db_connect(args.db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(snapshots)")]
    if "source" not in cols:
        sys.exit("La base no tiene datos multi-fuente todavía. Corré "
                 "ambos ingests primero.")

    def latest(source):
        r = con.execute("SELECT MAX(ts) FROM snapshots WHERE source=?",
                        (source,)).fetchone()
        if not r or r[0] is None:
            return None, {}
        ts = r[0]
        rows = con.execute("SELECT impl, version, count FROM version_counts "
                           "WHERE ts=?", (ts,)).fetchall()
        return ts, {(i, v): n for i, v, n in rows}

    ts_b, cb = latest("bitnodes")
    ts_d, cd = latest("dsn")
    if not cb or not cd:
        sys.exit("Faltan snapshots de alguna fuente. Corré ambos ingests.")

    tot_b, tot_d = sum(cb.values()), sum(cd.values())
    print(f"bitnod.es: {tot_b:,} nodos | DSN/KIT: {tot_d:,} nodos "
          f"| ratio {tot_d / tot_b:.1f}x\n")

    # Top versiones por Core major.minor, comparando cuota entre fuentes.
    def share_by_major(counts):
        agg: dict[str, int] = {}
        tot = sum(counts.values())
        for (impl, ver), n in counts.items():
            if impl != "core":
                continue
            mm = ".".join(ver.split(".")[:2])
            agg[mm] = agg.get(mm, 0) + n
        return {k: v / tot for k, v in agg.items()}, tot

    sb, _ = share_by_major(cb)
    sd, _ = share_by_major(cd)
    versions = sorted(set(sb) | set(sd),
                      key=lambda v: -(sb.get(v, 0) + sd.get(v, 0)))
    print(f"{'versión':<10}{'bitnod.es':>12}{'DSN/KIT':>12}{'Δ':>8}")
    for v in versions[:10]:
        b, d = sb.get(v, 0), sd.get(v, 0)
        print(f"core {v:<6}{b:>11.1%}{d:>11.1%}{(d - b) * 100:>+7.1f}")
    print("\nLas discrepancias entre fuentes son esperables (metodologías "
          "distintas) y documentarlas es parte del valor del proyecto.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default="telemetry.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="bajar y guardar snapshot DSN/KIT")
    p.add_argument("--view", default="I", choices=["I", "N"],
                   help="I=incoming/listening (default), N=la otra vista")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("report", help="último snapshot DSN")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("compare", help="DSN vs bitnod.es lado a lado")
    p.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
