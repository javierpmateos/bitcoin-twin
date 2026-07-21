"""
Validador del gemelo digital: cierra el loop de calibración.
============================================================

Última pieza del módulo de adopción. Dos comandos:

calibrate : construye la curva de adopción REAL de un release desde
            telemetry.db (snapshots acumulados por el ingestor), ajusta
            los perfiles del modelo contra ella con adoption_model.fit()
            y guarda los perfiles calibrados en un JSON que el
            orquestador puede cargar con --profiles. Este comando ES la
            flecha "↻ recalibra el modelo" del diagrama.

compare   : compara la curva EMERGENTE de una simulación (el CSV que
            escribe orchestrator.py run) contra la curva real del mismo
            release, y reporta métricas de distancia: RMSE, error máximo
            absoluto (estilo Kolmogorov-Smirnov) y error en el día final.

Flujo completo del gemelo:
    ingest (diario) -> calibrate -> run --profiles profiles.json
                                 -> compare -> (si diverge) calibrate ...

Nota de honestidad estadística: con pocos snapshots la curva real tiene
pocos puntos y la calibración es débil; el comando lo advierte. La curva
se va volviendo útil a medida que el cron acumula semanas de datos.

Uso:
    python3 validator.py calibrate --version 31.1 --out profiles.json
    python3 validator.py compare --sim adoption_sim.csv --version 31.1

Dependencias: numpy, scipy, matplotlib (vía adoption_model).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np

from adoption_model import AdoptionProfile, default_profiles, expected_curve, fit
from bitnodes_ingest import adoption_curve, db_connect, github_release_date


# ---------------------------------------------------------------------------
# Persistencia de perfiles calibrados
# ---------------------------------------------------------------------------

def save_profiles(profiles: list[AdoptionProfile], path: str,
                  meta: dict) -> None:
    payload = {"meta": meta, "profiles": [asdict(p) for p in profiles]}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_profiles(path: str) -> list[AdoptionProfile]:
    with open(path) as f:
        payload = json.load(f)
    return [AdoptionProfile(**p) for p in payload["profiles"]]


# ---------------------------------------------------------------------------
# Curva real desde la telemetría
# ---------------------------------------------------------------------------

def real_curve(db: str, impl: str, version: str,
               release_date: str | None) -> tuple[np.ndarray, np.ndarray]:
    con = db_connect(db)
    if release_date:
        dt = datetime.fromisoformat(release_date).replace(tzinfo=timezone.utc)
    else:
        dt = github_release_date(version)
        print(f"Fecha de release v{version} según GitHub: {dt.date()}")
    curve = adoption_curve(con, impl, version, dt)
    if not curve:
        sys.exit("Sin snapshots posteriores al release en telemetry.db. "
                 "Dejá correr el cron de ingesta y reintentá.")
    days = np.array([c[0] for c in curve])
    frac = np.array([c[1] for c in curve])
    return days, frac


# ---------------------------------------------------------------------------
# Métricas de distancia entre curvas
# ---------------------------------------------------------------------------

def curve_metrics(real_days: np.ndarray, real_frac: np.ndarray,
                  sim_days: np.ndarray, sim_frac: np.ndarray) -> dict:
    """Interpola la simulación en los días observados y mide distancias."""
    sim_at_obs = np.interp(real_days, sim_days, sim_frac)
    err = sim_at_obs - real_frac
    return {
        "n_points": int(len(real_days)),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "max_abs_error": float(np.max(np.abs(err))),
        "final_day_error": float(err[-1]),
        "span_days": float(real_days[-1] - real_days[0]),
    }


def verdict(m: dict) -> str:
    if m["n_points"] < 5 or m["span_days"] < 14:
        return ("DATOS INSUFICIENTES: la curva real tiene pocos puntos o "
                "poco rango temporal; las métricas no son concluyentes aún.")
    if m["rmse"] < 0.03 and m["max_abs_error"] < 0.06:
        return "OK: el gemelo sigue la dinámica real dentro del margen."
    if m["rmse"] < 0.08:
        return ("DERIVA MODERADA: conviene recalibrar "
                "(validator.py calibrate) y re-simular.")
    return ("DIVERGENCIA: el modelo no está capturando la dinámica real. "
            "Recalibrar y revisar supuestos (¿release de seguridad? "
            "¿cambio de comportamiento en la red?).")


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_calibrate(args) -> None:
    days, frac = real_curve(args.db, args.impl, args.version,
                            args.release_date)
    print(f"Curva real: {len(days)} puntos, días {days.min():.0f} a "
          f"{days.max():.0f}, adopción actual {frac[-1]:.1%}")
    if len(days) < 5:
        print("ADVERTENCIA: menos de 5 puntos; la calibración va a ser "
              "débil. Sirve como ensayo del pipeline, no como resultado.")

    fitted, rmse = fit(days, frac, is_security=args.security)
    print(f"Calibración terminada. RMSE contra la curva real: {rmse:.4f}\n")
    print(f"{'perfil':<14}{'beta_base':>11}{'beta_imit':>11}{'theta':>8}"
          f"{'p_never':>9}")
    for p in fitted:
        print(f"{p.name:<14}{p.beta_base:>11.4f}{p.beta_imit:>11.2f}"
              f"{p.aware_theta:>8.1f}{p.p_never:>9.2f}")

    meta = {
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "impl": args.impl, "version": args.version,
        "n_points": int(len(days)), "rmse": rmse,
        "is_security": bool(args.security),
    }
    save_profiles(fitted, args.out, meta)
    print(f"\nPerfiles calibrados guardados en {args.out}")
    print(f"Usalos en el orquestador: orchestrator.py run "
          f"--profiles {args.out} ...")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        horizon = int(days.max()) + 1
        det = expected_curve(fitted, horizon, args.security)
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(det, lw=2, label="modelo calibrado")
        ax.plot(days, frac, "ko", ms=5, label="curva real (telemetry.db)")
        ax.set_xlabel("días desde el release")
        ax.set_ylabel("fracción adoptada")
        ax.set_title(f"Calibración contra v{args.version} real")
        ax.legend(); ax.grid(alpha=0.3); fig.tight_layout()
        fig.savefig(args.plot, dpi=150)
        print(f"Gráfico: {args.plot}")


def cmd_compare(args) -> None:
    with open(args.sim, newline="") as f:
        rows = list(csv.reader(f))[1:]
    sim_days = np.array([float(r[0]) for r in rows])
    sim_frac = np.array([float(r[1]) for r in rows])

    days, frac = real_curve(args.db, args.impl, args.version,
                            args.release_date)
    m = curve_metrics(days, frac, sim_days, sim_frac)
    print(f"\nComparación simulación ({args.sim}) vs realidad "
          f"(v{args.version}):")
    for k, v in m.items():
        print(f"  {k:<16} {v:.4f}" if isinstance(v, float) else
              f"  {k:<16} {v}")
    print(f"\nVeredicto: {verdict(m)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default="telemetry.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("calibrate",
                       help="ajustar perfiles contra la curva real")
    p.add_argument("--impl", default="core", choices=["core", "knots"])
    p.add_argument("--version", required=True, help="ej: 31.1")
    p.add_argument("--release-date", default=None,
                   help="ISO date; si falta se consulta GitHub")
    p.add_argument("--security", action="store_true")
    p.add_argument("--out", default="profiles.json")
    p.add_argument("--plot", default=None,
                   help="ruta PNG opcional para el gráfico del ajuste")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("compare",
                       help="curva simulada vs curva real")
    p.add_argument("--sim", required=True, help="CSV de orchestrator run")
    p.add_argument("--impl", default="core", choices=["core", "knots"])
    p.add_argument("--version", required=True)
    p.add_argument("--release-date", default=None)
    p.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
