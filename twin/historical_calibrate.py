"""
Calibración del modelo contra la historia REAL rescatada del Archive.
=====================================================================

Primer resultado empírico del gemelo: ajustar el modelo de adopción
contra la curva real de un release histórico, usando los snapshots
recuperados de la Wayback Machine (wayback_raw/).

Caso de estudio por default: **Bitcoin Core 0.16.3**, publicado el
2018-09-18 con el fix de CVE-2018-17144 (bug de inflación). Es el mejor
caso disponible para el parámetro `beta_sec` del modelo, porque fue el
release de emergencia más urgente de la historia de Bitcoin: el 17/09 se
reportó como simple DoS, el 18/09 salió el parche, y el 20/09 se hizo
pública la vulnerabilidad de inflación. Dos shocks de urgencia seguidos.

Por qué lee los CRUDOS y no wayback.db
--------------------------------------
La tabla de user agents traía conteo Y porcentaje ("3235 (32.51%)").
wayback.db guarda solo el conteo, que representa la porción visible de
la red (top ~40 user agents). Pero con conteo/porcentaje se recupera el
TAMAÑO TOTAL de la red en cada snapshot, que es el denominador correcto
para una curva de adopción. Por eso este script re-parsea los crudos.

Sesgos conocidos (declarados, no escondidos)
--------------------------------------------
1. NUMERADOR = cota inferior. Solo suma versiones >= objetivo que sean
   lo bastante grandes como para aparecer en la tabla; versiones nuevas
   muy chicas quedan fuera. Subestima levemente la adopción tardía.
2. DENOMINADOR = tamaño total estimado por mediana de conteo/pct sobre
   las filas con pct >= 0.5% (las filas chiquitas tienen redondeo
   grosero y distorsionan la estimación).
3. La serie es RALA e irregular: el Archive capturó lo que capturó.
   Para 0.16.3 hay ~1 punto en el primer mes, así que estos datos NO
   constrainen la rampa inicial; sí constrainen la cola larga (cuánto
   persisten las versiones viejas, p_never). El script lo dice explícito.

Uso:
    python3 historical_calibrate.py                    # caso 0.16.3
    python3 historical_calibrate.py --version 0.17.0 --release-date 2018-10-03
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np

from adoption_model import expected_curve, fit
from wayback_recover import parse_user_agent

RAW_DIR = "wayback_raw"

# Igual que TOP_UA_RE de wayback_recover pero capturando también el
# porcentaje, que es lo que permite recuperar el tamaño de la red.
ROW_RE = re.compile(
    r'\?q=(?P<ua>/?Satoshi:[^"&\']+)["\'][^>]*>.*?</a>\s*</td>\s*'
    r'<td[^>]*>\s*(?P<count>[\d,]+)\s*\(\s*(?P<pct>[\d.]+)\s*%\s*\)',
    re.S | re.I)


def version_key(ver: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in ver.split("."))
    except ValueError:
        return (-1,)


# ---------------------------------------------------------------------------
# Lectura de crudos -> puntos de la curva
# ---------------------------------------------------------------------------

def parse_raw(path: str) -> list[tuple[str, str, int, float]]:
    """Devuelve [(impl, version, count, pct)] de un crudo."""
    html = open(path, "rb").read().decode("utf-8", errors="replace")
    out = []
    for m in ROW_RE.finditer(html):
        impl, ver = parse_user_agent(m.group("ua"))
        out.append((impl, ver, int(m.group("count").replace(",", "")),
                    float(m.group("pct"))))
    return out


def ts_from_filename(path: str) -> datetime:
    stamp = os.path.basename(path).split("_")[0]
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc)


def build_curve(raw_dir: str, target: str, release_dt: datetime,
                min_pct: float = 0.5):
    """
    Construye [(días_desde_release, fracción_adoptada, cobertura,
    red_total)] para todos los crudos posteriores al release.
    """
    target_key = version_key(target)
    points = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.raw"))):
        dt = ts_from_filename(path)
        if dt < release_dt:
            continue
        rows = parse_raw(path)
        if not rows:
            continue
        implied = [c / (p / 100.0) for _, _, c, p in rows if p >= min_pct]
        if not implied:
            continue
        net_total = float(np.median(implied))
        visible = sum(c for _, _, c, _ in rows)
        adopted = sum(c for _, v, c, _ in rows
                      if version_key(v) >= target_key)
        days = (dt - release_dt).total_seconds() / 86400.0
        points.append((round(days, 2), adopted / net_total,
                       visible / net_total, net_total))
    return points


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--raw-dir", default=RAW_DIR)
    ap.add_argument("--version", default="0.16.3")
    ap.add_argument("--release-date", default="2018-09-18")
    ap.add_argument("--no-security", action="store_true",
                    help="tratar el release como NO crítico")
    ap.add_argument("--out", default="profiles_historical.json")
    ap.add_argument("--plot", default="historical_fit.png")
    args = ap.parse_args()

    if not os.path.isdir(args.raw_dir):
        sys.exit(f"No existe {args.raw_dir}/. Corré wayback_recover.py fetch")

    release_dt = datetime.fromisoformat(args.release_date).replace(
        tzinfo=timezone.utc)
    is_security = not args.no_security

    pts = build_curve(args.raw_dir, args.version, release_dt)
    if len(pts) < 3:
        sys.exit(f"Solo {len(pts)} snapshots posteriores a "
                 f"{release_dt.date()}: insuficiente para calibrar.")

    print(f"Release v{args.version} — {release_dt.date()}"
          f"{'  [de seguridad]' if is_security else ''}")
    print(f"\n{'días':>6}{'fecha':>13}{'adopción':>11}"
          f"{'red total':>11}{'cobertura':>11}")
    for d, f, cov, tot in pts:
        fecha = (release_dt.timestamp() + d * 86400)
        fecha = datetime.fromtimestamp(fecha, tz=timezone.utc).date()
        print(f"{d:>6.0f}{str(fecha):>13}{f:>10.1%}{tot:>11,.0f}"
              f"{cov:>10.0%}")

    days = np.array([p[0] for p in pts])
    frac = np.array([p[1] for p in pts])

    early = int(np.sum(days <= 30))
    print(f"\n{len(pts)} puntos | {early} en los primeros 30 días | "
          f"rango {days.min():.0f}-{days.max():.0f} días")
    if early < 3:
        print("ADVERTENCIA: con <3 puntos en el primer mes, la RAMPA "
              "INICIAL no queda determinada por los datos. Lo que estos "
              "datos sí constrainen es la cola larga (persistencia de "
              "versiones viejas, p_never).")

    fitted, rmse = fit(days, frac, is_security=is_security)
    print(f"\nCalibrado. RMSE contra la curva real: {rmse:.4f}\n")
    print(f"{'perfil':<14}{'beta_base':>11}{'beta_imit':>11}"
          f"{'theta':>8}{'p_never':>9}")
    for p in fitted:
        print(f"{p.name:<14}{p.beta_base:>11.4f}{p.beta_imit:>11.2f}"
              f"{p.aware_theta:>8.1f}{p.p_never:>9.2f}")

    meta = {"calibrated_at": datetime.now(timezone.utc).isoformat(),
            "source": "wayback_raw (Internet Archive)",
            "version": args.version, "release_date": args.release_date,
            "is_security": is_security, "n_points": len(pts),
            "points_first_30d": early, "rmse": rmse}
    with open(args.out, "w") as fh:
        json.dump({"meta": meta,
                   "profiles": [asdict(p) for p in fitted]}, fh, indent=2)
    print(f"\nPerfiles -> {args.out}")

    cf_label = ("sin urgencia de CVE" if is_security
                else "si hubiera sido crítico")
    # Contrafáctico: el mismo ecosistema sin la urgencia del CVE.
    horizon = int(days.max()) + 1
    curve_real = expected_curve(fitted, horizon, is_security)
    curve_cf = expected_curve(fitted, horizon, not is_security)
    for d in (30, 90, 180):
        if d < horizon:
            print(f"  día {d:>3}: {curve_real[d]:.0%} "
                  f"({cf_label}: {curve_cf[d]:.0%})")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        ax.plot(curve_real, lw=2, color="tab:blue",
                label="modelo calibrado")
        ax.plot(curve_cf, lw=1.5, ls="--", color="tab:gray",
                label=f"contrafáctico: {cf_label}")
        ax.plot(days, frac, "ko", ms=6,
                label="datos reales (Wayback Machine)")
        ax.set_xlabel("días desde el release")
        ax.set_ylabel("fracción de la red con versión >= objetivo")
        ax.set_title(f"Adopción real de Bitcoin Core {args.version} "
                     f"({args.release_date})")
        ax.legend(); ax.grid(alpha=0.3); fig.tight_layout()
        fig.savefig(args.plot, dpi=150)
        print(f"Gráfico -> {args.plot}")


if __name__ == "__main__":
    main()
