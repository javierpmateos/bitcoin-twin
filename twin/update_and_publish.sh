#!/usr/bin/env bash
# update_and_publish.sh — ingesta + regenera el dashboard + publica.
#
# Pensado para correr desde el Programador de tareas de Windows (o cron):
#   1. ingesta un snapshot fresco de mainnet
#   2. regenera docs/index.html
#   3. si algo cambió, commitea y pushea (GitHub Pages sirve la nueva versión)
#
# Robusto: no se corta si no hay cambios que commitear, y reporta cada
# paso a un log. Correr desde la carpeta twin/.
#
# Uso manual:   ./update_and_publish.sh
# En Windows:   wsl -d Ubuntu-24.04 -- bash -c "cd ~/projects/bitcoin-twin/twin && ./update_and_publish.sh"

set -uo pipefail   # NO usamos -e: queremos manejar los fallos a mano

cd "$(dirname "$0")" || exit 1
LOG="$(cd "$(dirname "$0")/../data" && pwd)/publish.log"
stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(stamp)] $*" | tee -a "$LOG"; }

# Activar venv si existe y no está activo
if [[ -z "${VIRTUAL_ENV:-}" && -f ../.venv/bin/activate ]]; then
    source ../.venv/bin/activate
fi

log "=== update_and_publish ==="

# 1. Ingesta (si falla, seguimos: quizás la fuente está caída pero el
#    dashboard igual se puede regenerar con lo que haya en la base).
if python3 bitnodes_ingest.py ingest >>"$LOG" 2>&1; then
    log "ingesta OK"
else
    log "ingesta FALLÓ (sigo con datos existentes)"
fi

# 2. Regenerar el dashboard.
if python3 build_dashboard.py --out ../docs/index.html >>"$LOG" 2>&1; then
    log "dashboard regenerado"
else
    log "build_dashboard FALLÓ — abortando publicación"
    exit 1
fi

# 3. Publicar sólo si hay cambios.
cd ..
if [[ -n "$(git status --porcelain docs/)" ]]; then
    git add docs/
    git commit -m "dashboard: snapshot $(date -u +%Y-%m-%d\ %H:%M)" >>"$LOG" 2>&1
    if git push >>"$LOG" 2>&1; then
        log "publicado en GitHub Pages"
    else
        log "git push FALLÓ (¿credenciales o red?) — commit local hecho, "
        log "se publicará en el próximo push exitoso"
    fi
else
    log "sin cambios en docs/ — nada que publicar"
fi

log "=== fin ==="
