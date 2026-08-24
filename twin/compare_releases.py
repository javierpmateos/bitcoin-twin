#!/usr/bin/env python3
"""
compare_releases.py — gráfico de dos releases, sólo datos observados.

Grafica las curvas de adopción OBSERVADAS de dos releases de Bitcoin Core
sobre los mismos ejes, a partir de las capturas recuperadas del Internet
Archive. Sin modelo, sin contrafáctico: sólo los puntos medidos y la línea
que los une.

Es el gráfico que respalda la afirmación "los parches de emergencia se
propagan ~5x más rápido que los de rutina", porque muestra ambas curvas
reales en vez de una curva real contra una simulada.

Uso (desde twin/):
    python3 compare_releases.py
    python3 compare_releases.py --a 0.16.3 --a-date 2018-09-18 \
                               --b 0.18.0 --b-date 2019-05-02
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from historical_calibrate import build_curve, RAW_DIR


def series(raw_dir, version, date_str):
    dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    pts = build_curve(raw_dir, version, dt)
    return [p[0] for p in pts], [100 * p[1] for p in pts]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--raw-dir", default=RAW_DIR)
    ap.add_argument("--a", default="0.16.3")
    ap.add_argument("--a-date", default="2018-09-18")
    ap.add_argument("--a-label", default="0.16.3 — emergency CVE fix")
    ap.add_argument("--b", default="0.18.0")
    ap.add_argument("--b-date", default="2019-05-02")
    ap.add_argument("--b-label", default="0.18.0 — routine release")
    ap.add_argument("--days", type=int, default=200,
                    help="ventana en días a mostrar")
    ap.add_argument("--out", default="release_comparison.png")
    args = ap.parse_args()

    xa, ya = series(args.raw_dir, args.a, args.a_date)
    xb, yb = series(args.raw_dir, args.b, args.b_date)

    fig, ax = plt.subplots(figsize=(10, 5.6))
    ax.plot(xa, ya, "o-", color="#d1495b", lw=2, ms=7,
            label=args.a_label)
    ax.plot(xb, yb, "s--", color="#3d7ea6", lw=2, ms=6,
            label=args.b_label)

    # Anotar el punto de comparación: primer dato de cada release
    if xa and xb:
        ax.annotate(f"{ya[0]:.0f}% at day {xa[0]:.0f}",
                    xy=(xa[0], ya[0]), xytext=(xa[0] + 14, ya[0] - 9),
                    color="#d1495b", fontsize=10,
                    arrowprops=dict(arrowstyle="->", color="#d1495b", lw=1))
        ax.annotate(f"{yb[0]:.0f}% at day {xb[0]:.0f}",
                    xy=(xb[0], yb[0]), xytext=(xb[0] + 14, yb[0] - 9),
                    color="#3d7ea6", fontsize=10,
                    arrowprops=dict(arrowstyle="->", color="#3d7ea6", lw=1))

    ax.set_xlim(0, args.days)
    ax.set_xlabel("days since release")
    ax.set_ylabel("share of observed nodes on this version or newer (%)")
    ax.set_title("Bitcoin Core adoption: emergency fix vs routine release\n"
                 "observed data recovered from Internet Archive captures",
                 fontsize=12)
    ax.legend(loc="lower right", framealpha=.95)
    ax.grid(alpha=.25)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)
    print(f"Gráfico -> {args.out}")
    print(f"  {args.a}: {len(xa)} puntos observados; primero "
          f"día {xa[0]:.0f} = {ya[0]:.1f}%" if xa else "  (sin datos A)")
    print(f"  {args.b}: {len(xb)} puntos observados; primero "
          f"día {xb[0]:.0f} = {yb[0]:.1f}%" if xb else "  (sin datos B)")


if __name__ == "__main__":
    main()
