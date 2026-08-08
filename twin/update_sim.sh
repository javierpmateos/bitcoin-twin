#!/usr/bin/env bash
# update_sim.sh — corre el pipeline del gemelo, actualizado a hoy.
#
# Encadena, en orden:
#   1. ingesta fresca de mainnet (bitnod.es) -> telemetry.db
#   2. regenera la red sintética con la distribución de versiones de HOY
#   3. simula la adopción de un release contra esa red (dry-run)
#
# Uso:
#   ./update_sim.sh                 # release y nodos por defecto
#   ./update_sim.sh 31.1 30         # versión nueva = 31.1, 30 nodos
#
# Corré esto desde la carpeta twin/. No toca el cluster (dry-run);
# para hablar con Warnet, cambiá --backend abajo.

set -euo pipefail

# Activar el venv si existe y no está activo
if [[ -z "${VIRTUAL_ENV:-}" && -f ../.venv/bin/activate ]]; then
    source ../.venv/bin/activate
fi

VERSION="${1:-31.1}"     # release a simular (arg 1, default 31.1)
NODES="${2:-20}"         # cantidad de nodos    (arg 2, default 20)
NET="../networks/network.yaml"

echo "=============================================="
echo " Gemelo Bitcoin — actualización $(date +%Y-%m-%d\ %H:%M)"
echo " release simulado: v$VERSION | nodos: $NODES"
echo "=============================================="

echo
echo "[1/3] Ingesta fresca de mainnet ..."
python3 bitnodes_ingest.py ingest

echo
echo "[2/3] Regenerando red sintética con la distribución de hoy ..."
python3 orchestrator.py gen-network --nodes "$NODES" --out "$NET"

echo
echo "[3/3] Simulando adopción de v$VERSION ..."
python3 orchestrator.py run --nodes "$NODES" --new-version "$VERSION" \
    --backend dry-run --out adoption_sim.csv | tail -6

echo
echo "Listo. Curva emergente -> adoption_sim.csv"
echo "Red sintética         -> $NET"
echo "Para comparar contra la curva real (si hay snapshots suficientes):"
echo "  python3 validator.py compare --sim adoption_sim.csv --version $VERSION"
