# EV Battery Health Digital Twin

A data-driven lithium-ion battery digital twin built on the NASA PCoE battery aging
dataset: SOH estimation, probabilistic RUL prognostics, anomaly detection, scenario
simulation, and a tool-using engineer copilot.

Full plan and phase sequence: [PROJECT_PLAN.md](PROJECT_PLAN.md).

## Repository layout

Files are organised **by project phase**. Each phase folder holds its own code, docs,
notebooks and outputs.

```
battery-digital-twin/
├── PROJECT_PLAN.md              16-phase execution plan
├── common/                      shared config + repo-root resolution
├── configs/paths.yaml           all paths and constants (no absolute paths in code)
├── data/                        raw / interim / processed  (git-ignored)
│
├── phase1_domain/
│   └── domain_primer.md         battery fundamentals, degradation mechanisms, SOH/RUL
│
├── phase2_data_audit/
│   ├── audit_raw_nasa.py        structural audit of all 38 raw .mat files
│   ├── data_dictionary.md       verified schema, protocol, per-battery inventory
│   └── outputs/                 audit table + JSON
│
├── phase3_pipeline/
│   ├── nasa_loader.py           .mat -> telemetry / cycles / impedance
│   ├── quality.py               QC checks and flags
│   ├── targets.py               SOH, EOL, RUL
│   ├── schemas.py               pandera data contracts
│   ├── build_dataset.py         one-command build
│   ├── test_pipeline.py         27 regression + leakage tests
│   ├── pipeline_report.md       implementation report, findings, corrections
│   ├── phase3_findings.ipynb    findings with code, output and figures
│   └── outputs/                 build summary
│
└── phase4_eda/
    ├── eda_features.py          derived quantities (idle time, dQ/dV peaks, screening)
    ├── test_eda_features.py     11 tests
    ├── phase4_eda_report.ipynb  EDA report: trajectories, regeneration, curves,
    │                            impedance, health-indicator screening, findings
    └── outputs/                 hi_screening.csv, data_quality_log.csv
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows
python -m pip install -r requirements.txt
python -m pip install -e .
```

`requirements-deep.txt` (PyTorch) and `requirements-app.txt` (FastAPI, Streamlit) are
installed later, at phases 8 and 12.

## Data

Raw data is **not** in version control. Download the NASA PCoE "Battery Data Set"
archive and extract it to `data/raw/nasa/`. The exact archive SHA256, download date and
expected bundle layout are recorded in
[phase2_data_audit/data_dictionary.md](phase2_data_audit/data_dictionary.md).

## Build and test

```bash
python -m phase3_pipeline.build_dataset      # -> data/processed/*.parquet
python -m pytest -q                          # -> 38 passed
```

The build is reproducible: deleting `data/processed/` and re-running reproduces all four
parquet files byte-identically.

## Status

| Phase | Status |
|---|---|
| 0 — Environment & scaffold | done |
| 1 — Domain foundations | done |
| 2 — Dataset acquisition & audit | done |
| 3 — Canonical data pipeline | done |
| 4 — EDA | done |
| 5 — Targets & evaluation protocol | next |
| 6–16 | planned |

**Scope note:** this is a **cell-level** digital twin. The NASA data is individual
experimental cells, not a production EV pack. Pack-level behaviour is a documented
extension, not a claim of the current system.
