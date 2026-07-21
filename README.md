# bitcoin-twin

Gemelo digital adaptativo del ecosistema Bitcoin.

En lugar de una testnet con condiciones inventadas: una red sintética de
nodos reales (Bitcoin Core sobre [Warnet]) poblada por agentes cuyo
comportamiento se calibra continuamente contra telemetría de mainnet.

Primer módulo implementado y cerrado: **adopción de versiones**. La red
sintética replica la distribución de versiones real de mainnet y
reproduce la dinámica de actualización cuando aparece un release nuevo,
con un loop de validación y recalibración contra los datos reales.

[Warnet]: https://github.com/bitcoin-dev-project/warnet

## El loop

```
telemetría de mainnet (bitnod.es, GitHub releases)
        │  bitnodes_ingest.py ingest        (cron diario -> SQLite)
        ▼
modelo de adopción (hazard discreto: perfiles + imitación + CVEs)
        │  validator.py calibrate           (fit contra la curva real)
        ▼
orquestador de agentes
        │  orchestrator.py run --profiles   (upgrades tick a tick)
        ▼
red sintética Warnet (nodos con versiones mixtas, réplica de mainnet)
        │  orchestrator.py gen-network      (muestreo del snapshot real)
        ▼
validador
        │  validator.py compare             (RMSE, error máx, veredicto)
        └──────────► ↻ recalibra el modelo
```

## Estado

- [x] Telemetría: ingesta diaria de bitnod.es a SQLite. (bitnodes.io,
      la fuente histórica del ecosistema, murió el 2026-05-03 cuando su
      dominio expiró; 13 años de datos públicos se perdieron. La serie
      histórica propia de este proyecto se acumula desde 2026-07-21.)
- [x] Modelo de adopción calibrable (5 perfiles, efecto imitación,
      releases de seguridad, nodos que nunca actualizan).
- [x] Orquestador con backends dry-run y Warnet (upgrade en caliente vía
      patch de imagen del pod).
- [x] Generación de red sintética que replica la distribución real
      (por versión desplegable major.minor, renormalizada, con cobertura
      explícita).
- [x] Validador: métricas de distancia sim-vs-real, veredicto y
      recalibración con perfiles persistidos en JSON.
- [ ] Primera calibración con sustancia contra Core 31.1 (requiere ~3-4
      semanas de snapshots; en curso).
- [ ] Primer deploy en Warnet y demo del dashboard.
- [ ] Redes mixtas Core+Knots (Knots es hoy la 2ª implementación de
      mainnet, >15% de los nodos; requiere construir la imagen).
- [ ] Crawler propio (base: ayeowch/bitnodes, open source) para
      independizar la telemetría.
- [ ] Módulos futuros: mempool/fees, hashrate por pool, latencias.

## Uso rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd twin

python3 bitnodes_ingest.py ingest        # snapshot de mainnet -> SQLite
python3 bitnodes_ingest.py report        # distribución actual
python3 orchestrator.py gen-network --nodes 20 --out ../networks/network.yaml
python3 orchestrator.py run --nodes 20 --new-version 31.1 --backend dry-run
python3 validator.py calibrate --version 31.1 --out profiles.json
python3 validator.py compare --sim adoption_sim.csv --version 31.1
```

Con un cluster Warnet (Minikube / Docker Desktop): deployar el
network.yaml generado y correr con `--backend warnet` para ver los
upgrades en vivo en el dashboard.

## Telemetría continua

Un snapshot diario alcanza (idempotente; bitnod.es no expone API, se
parsea su tabla pública con un request por corrida). En WSL, la opción
confiable es el Programador de tareas de Windows:

```
wsl -d Ubuntu -- bash -c "cd ~/projects/bitcoin-twin/twin && python3 bitnodes_ingest.py ingest >> ../data/ingest.log 2>&1"
```

## Licencia

MIT. Construido sobre Warnet (MIT) y Bitcoin Core (MIT).
