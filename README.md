# bitcoin-twin

Gemelo digital adaptativo del ecosistema Bitcoin (prototipo).

La idea: en lugar de una testnet con condiciones inventadas, una red
sintética de nodos reales (Bitcoin Core sobre [Warnet]) poblada por
agentes cuyo comportamiento se calibra continuamente con telemetría de
mainnet. Primer módulo implementado: **adopción de versiones** — la red
sintética replica la distribución de versiones real y reproduce la
dinámica de actualización cuando aparece un release nuevo.

[Warnet]: https://github.com/bitcoin-dev-project/warnet

## Estado

- [x] Telemetría: ingesta de snapshots de Bitnodes a SQLite (`twin/bitnodes_ingest.py`)
- [x] Modelo de adopción: hazard en tiempo discreto con perfiles e imitación, calibrable contra curvas reales (`twin/adoption_model.py`)
- [x] Orquestador: genera la red desde la telemetría y ejecuta el loop de upgrades contra Warnet o en dry-run (`twin/orchestrator.py`)
- [ ] Validador: distancia entre curva emergente y curva real + recalibración
- [ ] Perfiles calibrados persistidos (JSON) y cargados por el orquestador
- [ ] Más módulos: mempool/fees, hashrate por pool, latencias

## Uso rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cd twin

# 1. Foto de mainnet (1 request a Bitnodes; ver rate limits en el módulo)
python3 bitnodes_ingest.py ingest
python3 bitnodes_ingest.py report

# 2. Modelo de adopción: calibración de ejemplo + gráfico
python3 adoption_model.py

# 3. Red sintética que replica mainnet + simulación de un release
python3 orchestrator.py gen-network --nodes 20 --out ../networks/network.yaml
python3 orchestrator.py run --nodes 20 --new-version 29.0 --backend dry-run
```

Con un cluster Warnet corriendo (Minikube/Docker Desktop), reemplazar
`--backend dry-run` por `--backend warnet` para ver los upgrades en vivo.

## Telemetría continua

Los snapshots de Bitnodes viven 60 días en su servidor: cuanto antes
arranque el cron, más historia propia se acumula.

```cron
0 */12 * * * cd /ruta/a/bitcoin-twin/twin && python3 bitnodes_ingest.py ingest >> ../data/ingest.log 2>&1
```

## Licencia

MIT. Construido sobre Warnet (MIT) y Bitcoin Core (MIT).
