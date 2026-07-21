"""
Modelo de hazard para adopción de versiones de Bitcoin Core.
================================================================

Pieza central del "gemelo digital": modela, agente por agente, la decisión
de actualizar a un nuevo release como un proceso de supervivencia en tiempo
discreto (1 tick = 1 día).

Idea central
------------
Para un agente que todavía NO actualizó, la probabilidad de actualizar
en el día t es:

    p(t) = p_activo * (1 - exp(-h(t)))

donde el hazard h(t) combina cuatro fuerzas:

    h(t) = beta_base                      # propensión propia del perfil
         * awareness(t; k, theta)         # enterarse del release (rampa gamma)
         * (1 + beta_sec * es_security)   # urgencia si corrige un CVE
         * (1 + beta_imit * F(t))         # imitación: F(t) = fracción de la
                                          # red que ya adoptó (efecto rebaño)

y p_activo = 1 - p_never captura a los nodos "zombies" que nunca actualizan
(en mainnet siempre queda una cola larga de versiones viejas).

El acoplamiento vía F(t) hace que los perfiles no sean independientes:
los early adopters "arrastran" a los conservadores. Eso reproduce la forma
en S de las curvas reales de adopción.

Dos modos de uso
----------------
1. simulate_population(): simulación estocástica agente por agente.
   Es lo que después conecta con el orquestador de Warnet (cada agente
   que "muere" = un nodo cuya imagen hay que reemplazar).
2. expected_curve(): versión determinista (valor esperado) del mismo
   modelo. Rápida y suave -> se usa para CALIBRAR contra datos reales.

Calibración
-----------
fit() ajusta los parámetros libres minimizando el error cuadrático entre
expected_curve() y una curva observada (snapshots de Bitnodes).
Acá se incluye una curva objetivo DE EJEMPLO con la forma típica de un
release de Core (arranque lento, rampa, meseta ~75% a los 6 meses).
Reemplazala por tus propios snapshots: una serie (día_desde_release,
fracción_de_nodos_en_la_versión_nueva).

Uso
---
    python3 adoption_model.py          # calibra, simula y genera un PNG

Dependencias: numpy, scipy, matplotlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from scipy import optimize
from scipy.stats import gamma as gamma_dist


# ---------------------------------------------------------------------------
# 1. Perfiles de adopción
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdoptionProfile:
    """Parámetros de comportamiento de una clase de operador de nodo."""

    name: str
    weight: float        # fracción de la población con este perfil
    beta_base: float     # hazard base diario (propensión a actualizar)
    beta_sec: float      # multiplicador extra si el release corrige un CVE
    beta_imit: float     # sensibilidad a la fracción de la red ya adoptada
    aware_k: float       # forma de la rampa de "enterarse" (gamma)
    aware_theta: float   # escala (días) de esa rampa
    p_never: float       # probabilidad de ser un nodo que nunca actualiza

    def awareness(self, t: np.ndarray) -> np.ndarray:
        """CDF gamma: qué fracción del perfil ya 'se enteró' al día t."""
        return gamma_dist.cdf(t, a=self.aware_k, scale=self.aware_theta)

    def hazard(self, t: np.ndarray, adopted_frac: float,
               is_security: bool) -> np.ndarray:
        sec = 1.0 + (self.beta_sec if is_security else 0.0)
        imit = 1.0 + self.beta_imit * adopted_frac
        return self.beta_base * self.awareness(t) * sec * imit


def default_profiles() -> list[AdoptionProfile]:
    """Punto de partida razonable; fit() ajusta los parámetros libres."""
    return [
        AdoptionProfile("early_adopter", weight=0.10, beta_base=0.120,
                        beta_sec=1.5, beta_imit=0.2, aware_k=1.5,
                        aware_theta=2.0, p_never=0.00),
        AdoptionProfile("mainstream",    weight=0.45, beta_base=0.025,
                        beta_sec=2.0, beta_imit=2.0, aware_k=2.0,
                        aware_theta=10.0, p_never=0.02),
        AdoptionProfile("conservative",  weight=0.25, beta_base=0.008,
                        beta_sec=3.0, beta_imit=3.5, aware_k=2.5,
                        aware_theta=25.0, p_never=0.05),
        AdoptionProfile("corporate",     weight=0.12, beta_base=0.010,
                        beta_sec=4.0, beta_imit=1.0, aware_k=4.0,
                        aware_theta=15.0, p_never=0.03),
        AdoptionProfile("laggard",       weight=0.08, beta_base=0.002,
                        beta_sec=0.5, beta_imit=1.0, aware_k=3.0,
                        aware_theta=40.0, p_never=0.60),
    ]


# ---------------------------------------------------------------------------
# 2. Modelo determinista (valor esperado) — para calibrar
# ---------------------------------------------------------------------------

def expected_curve(profiles: list[AdoptionProfile], horizon: int,
                   is_security: bool = False) -> np.ndarray:
    """
    Fracción esperada de la red en la versión nueva, día por día.

    Itera en tiempo discreto porque el término de imitación acopla a todos
    los perfiles a través de F(t): no hay forma cerrada, pero la iteración
    es O(horizon * perfiles) y tarda microsegundos.
    """
    n = len(profiles)
    weights = np.array([p.weight for p in profiles])
    weights = weights / weights.sum()
    active = np.array([1.0 - p.p_never for p in profiles])

    surv = np.ones(n)               # P(no adoptó aún | es activo), por perfil
    frac = np.zeros(horizon)        # F(t): fracción total adoptada
    adopted = 0.0
    for t in range(horizon):
        tt = np.array([float(t)])
        for i, prof in enumerate(profiles):
            h = prof.hazard(tt, adopted, is_security)[0]
            surv[i] *= np.exp(-h)
        adopted = float(np.sum(weights * active * (1.0 - surv)))
        frac[t] = adopted
    return frac


# ---------------------------------------------------------------------------
# 3. Simulación estocástica agente por agente — para conectar con Warnet
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    agent_id: int
    profile: AdoptionProfile
    never_updates: bool
    upgraded_on: int | None = None   # día en que actualizó (None = todavía no)


def make_population(profiles: list[AdoptionProfile], n_agents: int,
                    rng: np.random.Generator) -> list[Agent]:
    weights = np.array([p.weight for p in profiles])
    weights = weights / weights.sum()
    idx = rng.choice(len(profiles), size=n_agents, p=weights)
    return [
        Agent(agent_id=i, profile=profiles[j],
              never_updates=bool(rng.random() < profiles[j].p_never))
        for i, j in enumerate(idx)
    ]


def simulate_population(profiles: list[AdoptionProfile], n_agents: int,
                        horizon: int, is_security: bool = False,
                        seed: int = 42):
    """
    Devuelve (curva_de_fracción_adoptada, agentes).

    En el gemelo digital, el loop interno se ejecuta un tick por vez y cada
    `upgraded_on == t` se traduce en una llamada al orquestador para
    reemplazar la imagen del nodo correspondiente en Warnet.
    """
    rng = np.random.default_rng(seed)
    agents = make_population(profiles, n_agents, rng)
    frac = np.zeros(horizon)
    adopted = 0

    for t in range(horizon):
        f_now = adopted / n_agents
        tt = np.array([float(t)])
        for ag in agents:
            if ag.upgraded_on is not None or ag.never_updates:
                continue
            h = ag.profile.hazard(tt, f_now, is_security)[0]
            if rng.random() < 1.0 - np.exp(-h):
                ag.upgraded_on = t
                adopted += 1
        frac[t] = adopted / n_agents
    return frac, agents


# ---------------------------------------------------------------------------
# 4. Calibración contra una curva observada
# ---------------------------------------------------------------------------

# Parámetros que fit() puede tocar, como (índice_de_perfil, nombre_de_campo).
# Empezamos con pocos grados de libertad: es fácil sobreajustar una sola
# curva. Con varios releases históricos se pueden liberar más.
FREE_PARAMS: list[tuple[int, str]] = [
    (0, "beta_base"),
    (1, "beta_base"), (1, "beta_imit"), (1, "aware_theta"),
    (2, "beta_base"), (2, "beta_imit"), (2, "aware_theta"),
    (4, "p_never"),
]

BOUNDS = {
    "beta_base": (1e-4, 0.5),
    "beta_imit": (0.0, 8.0),
    "aware_theta": (1.0, 60.0),
    "p_never": (0.0, 0.95),
}


def _apply(profiles: list[AdoptionProfile],
           x: np.ndarray) -> list[AdoptionProfile]:
    out = list(profiles)
    for val, (i, name) in zip(x, FREE_PARAMS):
        out[i] = replace(out[i], **{name: float(val)})
    return out


def fit(observed_days: np.ndarray, observed_frac: np.ndarray,
        profiles: list[AdoptionProfile] | None = None,
        is_security: bool = False) -> tuple[list[AdoptionProfile], float]:
    """
    Ajusta FREE_PARAMS para que expected_curve() pase por los puntos
    observados. Devuelve (perfiles_calibrados, rmse).
    """
    profiles = profiles or default_profiles()
    horizon = int(observed_days.max()) + 1
    x0 = np.array([getattr(profiles[i], name) for i, name in FREE_PARAMS])
    bounds = [BOUNDS[name] for _, name in FREE_PARAMS]

    def loss(x: np.ndarray) -> float:
        curve = expected_curve(_apply(profiles, x), horizon, is_security)
        return float(np.mean((curve[observed_days.astype(int)]
                              - observed_frac) ** 2))

    res = optimize.minimize(loss, x0, bounds=bounds, method="L-BFGS-B")
    fitted = _apply(profiles, res.x)
    rmse = float(np.sqrt(res.fun))
    return fitted, rmse


# ---------------------------------------------------------------------------
# 5. Datos de ejemplo + demo
# ---------------------------------------------------------------------------

def example_observed_curve() -> tuple[np.ndarray, np.ndarray]:
    """
    Curva objetivo DE EJEMPLO con la forma típica de la adopción de un
    release mayor de Bitcoin Core (según curvas públicas de Bitnodes):
    despegue lento la primera semana, rampa fuerte entre el mes 1 y 3,
    meseta en torno al 75% a los ~6 meses (la cola nunca llega a 100%).

    REEMPLAZAR por snapshots reales: (días_desde_release, fracción).
    """
    days = np.array([0, 3, 7, 14, 21, 30, 45, 60, 90, 120, 150, 180])
    frac = np.array([0.000, 0.008, 0.03, 0.09, 0.16, 0.26,
                     0.40, 0.50, 0.63, 0.70, 0.735, 0.75])
    return days, frac


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days, obs = example_observed_curve()
    horizon = int(days.max()) + 1

    print("Calibrando contra la curva observada...")
    fitted, rmse = fit(days, obs)
    print(f"  RMSE del ajuste: {rmse:.4f}\n")

    print(f"{'perfil':<14}{'peso':>6}{'beta_base':>11}"
          f"{'beta_imit':>11}{'theta':>8}{'p_never':>9}")
    for p in fitted:
        print(f"{p.name:<14}{p.weight:>6.2f}{p.beta_base:>11.4f}"
              f"{p.beta_imit:>11.2f}{p.aware_theta:>8.1f}{p.p_never:>9.2f}")

    det = expected_curve(fitted, horizon)
    runs = [simulate_population(fitted, n_agents=500, horizon=horizon,
                                seed=s)[0] for s in range(5)]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for i, r in enumerate(runs):
        ax.plot(r, color="tab:orange", alpha=0.35, lw=1,
                label="simulación estocástica (500 agentes)" if i == 0
                else None)
    ax.plot(det, color="tab:blue", lw=2, label="modelo calibrado (esperado)")
    ax.plot(days, obs, "ko", ms=6, label="curva observada (objetivo)")
    ax.set_xlabel("días desde el release")
    ax.set_ylabel("fracción de la red en la versión nueva")
    ax.set_title("Adopción de un release: modelo de hazard calibrado")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig("adoption_fit.png", dpi=150)
    print("\nGráfico guardado en adoption_fit.png")

    # Experimento extra: mismo ecosistema, release de seguridad.
    sec = expected_curve(fitted, horizon, is_security=True)
    d30, s30 = det[30], sec[30]
    print(f"\nContrafáctico: si el release corrigiera un CVE, al día 30 "
          f"la adopción sería {s30:.0%} en vez de {d30:.0%}.")


if __name__ == "__main__":
    main()
