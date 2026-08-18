# bitcoin-twin

An adaptive digital twin of the Bitcoin network.

Instead of a testnet with invented conditions: a synthetic network of real
nodes (Bitcoin Core on Warnet) populated by agents whose behavior is
continuously calibrated against mainnet telemetry. A reproducible testbench
for studying software deployments, network events, and protocol behavior
before they reach mainnet.

**Live dashboard:** https://javierpmateos.github.io/bitcoin-twin/

First module implemented and closed: version adoption. The synthetic
network replicates mainnet's real version distribution and reproduces the
update dynamics when a new release appears, with a validation-and-
recalibration loop against real data.

## The loop

```
mainnet telemetry (bitnod.es CSV datasets, GitHub releases)
        │  csv_ingest.py ingest            (scheduled -> SQLite)
        ▼
adoption model (discrete-time hazard: profiles + imitation + CVEs)
        │  validator.py calibrate          (fit against the real curve)
        ▼
agent orchestrator
        │  orchestrator.py run --profiles  (tick-by-tick upgrades)
        ▼
synthetic Warnet network (mixed-version nodes, mainnet replica)
        │  orchestrator.py gen-network     (samples the real snapshot)
        ▼
validator
        │  validator.py compare            (RMSE, max error, verdict)
        └──────────► ↻ recalibrates the model
```

## Status

- **Telemetry:** ingests the public CSV datasets that bitnod.es publishes
  ("Full Dataset Snapshots"), into SQLite. Continuous own series since
  **May 2026** (~46k reachable nodes per snapshot). Runs automatically in
  the cloud via GitHub Actions — no dependency on a local machine.
- **Historical telemetry (2016–2019)** recovered from the Internet Archive
  (a public dataset that had been lost when bitnodes.io expired on
  2026-05-03).
- **Adoption model** — calibratable (5 profiles, imitation effect, security
  releases, never-updating nodes).
- **Orchestrator** with dry-run and Warnet backends (hot upgrade via pod
  image patch).
- **Synthetic-network generation** replicating the real distribution (by
  deployable major.minor version, renormalized, with explicit coverage).
- **Validator:** sim-vs-real distance metrics, verdict, and recalibration
  with profiles persisted to JSON.
- **Multi-source auditing:** cross-checks telemetry sources and quantifies
  how far public crawlers disagree.
- Empirical results already produced: adoption of Core 0.16.3 (the
  CVE-2018-17144 fix) reached ~39% of the network in 17 days vs ~90 days
  for a routine release; a ~25% ceiling of nodes that never update.

Planned:

- First substantive calibration against a release captured from day one by
  our own telemetry.
- First Warnet deploy and live dashboard demo.
- Mixed Core+Knots networks (Knots is mainnet's 2nd implementation today,
  ~18% of nodes; requires building the image).
- Own crawler (based on the open-source `ayeowch/bitnodes`) to fully
  independize telemetry.
- Future modules: mempool/fees, hashrate by pool, latencies.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd twin

python3 csv_ingest.py ingest              # mainnet snapshot -> SQLite
python3 csv_ingest.py report              # current distribution
python3 build_dashboard.py --out ../docs/index.html   # regenerate dashboard
python3 orchestrator.py gen-network --nodes 20 --out ../networks/network.yaml
python3 orchestrator.py run --nodes 20 --new-version 31.1 --backend dry-run
python3 validator.py calibrate --version 31.1 --out profiles.json
python3 validator.py compare --sim adoption_sim.csv --version 31.1
```

With a Warnet cluster (Minikube / Docker Desktop): deploy the generated
`network.yaml` and run with `--backend warnet` to watch upgrades live.

## Continuous telemetry

Telemetry updates automatically via GitHub Actions
(`.github/workflows/update-dashboard.yml`): it ingests the latest published
CSV, regenerates the dashboard, and commits the result. GitHub Pages serves
the updated dashboard. The CSV datasets are published roughly weekly; the
daily workflow is idempotent (dates without a new file are skipped).

To backfill historical CSVs:

```bash
python3 csv_ingest.py backfill --since 2026-05-21 --step 1
```

## License

MIT. Built on Warnet (MIT) and Bitcoin Core (MIT).

## Data sources & attribution

This project consumes public network telemetry from several sources. Each
is used in good faith, for open-source research, with attribution:

- **Node telemetry — bitnod.es (BitMEX Research / Localhost Research).**
  The project downloads the public **CSV dataset snapshots** that bitnod.es
  publishes for download ("Full Dataset Snapshots"), rather than scraping
  the site. These datasets are observations of Bitcoin's public P2P network
  (public IP addresses and user agents); the underlying facts are not
  copyrightable, and the project builds its own derived historical series
  rather than mirroring the source's database. A planned milestone is to
  run our own crawler (based on the open-source
  [`ayeowch/bitnodes`](https://github.com/ayeowch/bitnodes), MIT) to obtain
  this data first-hand and remove any third-party dependency.

- **Version-distribution data — DSN Bitcoin Monitoring (KIT),** via
  [`bitcoin-data/bitcoin-stats-archive`](https://github.com/bitcoin-data/bitcoin-stats-archive)
  (branch `dsn-bitcoin-monitoring`). Licensed **CC BY 4.0** — used here with
  attribution, as the license requires. Used for source auditing.

- **Historical node distribution (2016–2019) — Internet Archive.**
  Reconstructed from public web captures of earlier Bitnodes eras
  (bitnodes.21.co, bitnodes.earn.com) preserved by the
  [Wayback Machine](https://web.archive.org/). Used for non-commercial
  research; recovered datasets are committed to this repository.

Data derived from CC BY 4.0 sources retains that attribution requirement.
If you are a data source maintainer and have any concern about how your
data is used here, please open an issue.
## A note on the archived captures

The files in `twin/wayback_raw/` are verbatim copies of public web pages
preserved by the Internet Archive, committed here so that the parsing of the
2016–2019 historical dataset is fully reproducible: anyone can re-run the
parser against the exact bytes it was developed against, or fetch the same
captures independently from
[web.archive.org](https://web.archive.org/).

Those captures include node IP addresses that were publicly announced on
Bitcoin's peer-to-peer network and published on a public website between
2016 and 2019. They are reproduced here unmodified because altering an
archival source would defeat its purpose, and because the same captures
remain publicly retrievable from the Internet Archive regardless.

**This project's own database stores no IP addresses.** The ingest pipeline
reads public dataset snapshots and retains only aggregate counts per client
version (`telemetry.db` contains `ts, total_nodes, source` and
`ts, impl, version, count` — nothing else). No addresses, hosts, or
per-node records are ingested or published by this project.
