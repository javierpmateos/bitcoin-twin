"""
Orquestador de agentes para el gemelo digital.
==============================================

Tercera pieza del módulo de adopción: conecta el modelo de hazard
(adoption_model.py) y la telemetría real (bitnodes_ingest.py) con una red
de nodos Warnet.

Dos comandos
------------
gen-network : lee el último snapshot de telemetry.db y genera un
              network.yaml para Warnet cuya distribución de versiones
              REPLICA la de mainnet (muestreo proporcional). Este es el
              momento "el gemelo copia la foto de la red real".

run         : ejecuta el loop de simulación. En cada tick (= 1 día
              simulado) cada agente que aún no actualizó tira los dados
              según su hazard; los que "deciden" actualizar disparan un
              cambio de versión en su nodo. El loop registra la curva de
              adopción emergente en un CSV (comparable luego contra la
              curva real: ese es el trabajo del validador).

Backends
--------
--backend dry-run : no toca ningún cluster; imprime y registra cada
                    acción. Para desarrollar, depurar y demos.
--backend warnet  : ejecuta los comandos reales (kubectl) contra el
                    cluster donde corre Warnet. Todos los comandos están
                    aislados en WarnetBackend para ajustarlos fácil.

Sobre las imágenes: Warnet usa imágenes bitcoindevproject/bitcoin:<ver>
para las versiones publicadas de Core. El cambio de versión en caliente
se hace parcheando la imagen del contenedor del pod (el campo image es
mutable en pods de Kubernetes). Si tu deployment usa otro nombre de
contenedor, ajustalo en WarnetBackend.CONTAINER.

Uso
---
    python3 orchestrator.py gen-network --nodes 20 --out network.yaml
    python3 orchestrator.py run --nodes 20 --new-version 29.0 \
            --days 180 --backend dry-run --out adoption_sim.csv

Dependencias: numpy (vía adoption_model). PyYAML es opcional: si no
está, el YAML se escribe a mano (es simple).
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import subprocess
import sys
import time

import numpy as np

from adoption_model import (AdoptionProfile, Agent, default_profiles,
                            make_population)
from bitnodes_ingest import db_connect, version_key

WARNET_IMAGE = "bitcoindevproject/bitcoin:{version}"


# ---------------------------------------------------------------------------
# 1. Generación de la red inicial desde telemetría real
# ---------------------------------------------------------------------------

def mainnet_version_distribution(con: sqlite3.Connection,
                                 impl: str = "core",
                                 top_k: int = 5) -> list[tuple[str, float]]:
    """
    Distribución de versiones de `impl` en el último snapshot almacenado,
    limitada a las top_k versiones (el resto se agrega a la más vieja de
    las top_k, para no pedir imágenes exóticas).
    """
    row = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()
    if not row or row[0] is None:
        sys.exit("telemetry.db vacío. Corré antes: bitnodes_ingest.py ingest")
    ts = row[0]
    rows = con.execute(
        "SELECT version, count FROM version_counts WHERE ts=? AND impl=? "
        "ORDER BY count DESC", (ts, impl),
    ).fetchall()
    if not rows:
        sys.exit(f"El snapshot {ts} no tiene nodos de impl={impl}.")
    head, tail = rows[:top_k], rows[top_k:]
    extra = sum(n for _, n in tail)
    if extra and head:
        oldest = min(range(len(head)), key=lambda i: version_key(head[i][0]))
        head[oldest] = (head[oldest][0], head[oldest][1] + extra)
    total = sum(n for _, n in head)
    return [(v, n / total) for v, n in head]


def sample_versions(dist: list[tuple[str, float]], n: int,
                    rng: np.random.Generator) -> list[str]:
    versions = [v for v, _ in dist]
    probs = np.array([p for _, p in dist])
    probs = probs / probs.sum()
    idx = rng.choice(len(versions), size=n, p=probs)
    return [versions[i] for i in idx]


def to_warnet_version(ver: str) -> str:
    """Bitnodes reporta '28.1.0'; las imágenes de Warnet usan '28.1'."""
    parts = ver.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else ver


def write_network_yaml(node_versions: list[str], out: str,
                       connections: int = 4, seed: int = 7) -> None:
    """
    network.yaml estilo Warnet: cada nodo con su versión y conexiones
    aleatorias (grafo Erdős–Rényi simple). Verificá el formato exacto
    contra el scaffold de tu versión de Warnet (`warnet new`), que puede
    evolucionar.
    """
    rng = np.random.default_rng(seed)
    n = len(node_versions)
    lines = ["nodes:"]
    for i, ver in enumerate(node_versions):
        peers = rng.choice([j for j in range(n) if j != i],
                           size=min(connections, n - 1), replace=False)
        lines.append(f"  - name: tank-{i:04d}")
        lines.append(f"    version: \"{to_warnet_version(ver)}\"")
        lines.append("    addnode:")
        for p in sorted(peers):
            lines.append(f"      - tank-{int(p):04d}")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")


def cmd_gen_network(args) -> None:
    con = db_connect(args.db)
    dist = mainnet_version_distribution(con, top_k=args.top_k)
    print("Distribución objetivo (mainnet, último snapshot):")
    for v, p in dist:
        print(f"  core {v:<10} {p:6.1%}")
    rng = np.random.default_rng(args.seed)
    node_versions = sample_versions(dist, args.nodes, rng)
    write_network_yaml(node_versions, args.out)
    print(f"\n{args.nodes} nodos escritos en {args.out}")
    print(f"Deploy: warnet deploy {args.out}  (desde tu proyecto Warnet)")


# ---------------------------------------------------------------------------
# 2. Backends: dónde ejecutan las acciones los agentes
# ---------------------------------------------------------------------------

class DryRunBackend:
    """Simula las acciones. Ideal para desarrollar sin cluster."""

    def set_version(self, node: str, version: str) -> None:
        print(f"    [dry-run] {node} -> Core {version}")

    def describe(self) -> str:
        return "dry-run (sin cluster)"


class WarnetBackend:
    """
    Ejecuta acciones reales contra el cluster de Warnet vía kubectl.
    El campo `image` de un contenedor es mutable en pods de Kubernetes,
    así que el upgrade en caliente es un patch de imagen. Ajustá
    CONTAINER si tu pod usa otro nombre (mirá `kubectl describe pod`).
    """

    CONTAINER = "bitcoincore"
    NAMESPACE = "warnet"

    def set_version(self, node: str, version: str) -> None:
        image = WARNET_IMAGE.format(version=version)
        cmd = ["kubectl", "-n", self.NAMESPACE, "set", "image",
               f"pod/{node}", f"{self.CONTAINER}={image}"]
        print(f"    [warnet] {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"    [warnet] ERROR: {res.stderr.strip()}")

    def describe(self) -> str:
        return f"warnet (kubectl, ns={self.NAMESPACE})"


BACKENDS = {"dry-run": DryRunBackend, "warnet": WarnetBackend}


# ---------------------------------------------------------------------------
# 3. Loop de simulación
# ---------------------------------------------------------------------------

def run_simulation(agents: list[Agent], backend, new_version: str,
                   days: int, is_security: bool, tick_seconds: float,
                   log_path: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = len(agents)
    adopted = 0
    log: list[tuple[int, float]] = []

    print(f"\nRelease v{new_version} publicado. {n} agentes, "
          f"{days} días simulados, backend: {backend.describe()}")
    if is_security:
        print("(release de seguridad: hazard amplificado por beta_sec)")

    for t in range(days):
        f_now = adopted / n
        tt = np.array([float(t)])
        upgraders: list[Agent] = []
        for ag in agents:
            if ag.upgraded_on is not None or ag.never_updates:
                continue
            h = ag.profile.hazard(tt, f_now, is_security)[0]
            if rng.random() < 1.0 - np.exp(-h):
                ag.upgraded_on = t
                upgraders.append(ag)
        if upgraders:
            print(f"  día {t:3d}: {len(upgraders)} upgrade(s) "
                  f"[adopción {(adopted + len(upgraders)) / n:5.1%}]")
        for ag in upgraders:
            backend.set_version(f"tank-{ag.agent_id:04d}", new_version)
            adopted += 1
        log.append((t, adopted / n))
        if tick_seconds > 0:
            time.sleep(tick_seconds)

    with open(log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "adopted_fraction"])
        w.writerows(log)

    never = sum(1 for ag in agents if ag.never_updates)
    print(f"\nFin. Adopción final: {adopted / n:.1%} "
          f"({never} nodos 'zombies' que nunca actualizan)")
    print(f"Curva emergente guardada en {log_path}")
    print("Compararla contra la curva real del ingestor = tarea del "
          "validador (siguiente pieza).")


def cmd_run(args) -> None:
    profiles = default_profiles()   # TODO: cargar perfiles calibrados (JSON)
    rng = np.random.default_rng(args.seed)
    agents = make_population(profiles, args.nodes, rng)
    backend = BACKENDS[args.backend]()
    run_simulation(agents, backend, args.new_version, args.days,
                   args.security, args.tick_seconds, args.out, args.seed)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gen-network",
                       help="network.yaml con la distribución de mainnet")
    p.add_argument("--db", default="telemetry.db")
    p.add_argument("--nodes", type=int, default=20)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--out", default="network.yaml")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_gen_network)

    p = sub.add_parser("run", help="loop de adopción de un release nuevo")
    p.add_argument("--nodes", type=int, default=20)
    p.add_argument("--new-version", required=True, help="ej: 29.0")
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--security", action="store_true",
                   help="el release corrige un CVE")
    p.add_argument("--backend", default="dry-run", choices=BACKENDS)
    p.add_argument("--tick-seconds", type=float, default=0.0,
                   help="pausa real entre días simulados (0 = a fondo)")
    p.add_argument("--out", default="adoption_sim.csv")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
