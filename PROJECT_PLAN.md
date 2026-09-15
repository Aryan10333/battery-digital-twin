# EV Battery Health Digital Twin — Step-by-Step Execution Plan

Derived from `EV_Battery_Digital_Twin_Project_Blueprint.pdf`.
Sequenced the way you asked: **domain → dataset → EDA → modelling → twin → GenAI**.

Rule for the whole project: **every Python command runs through `.venv`.**

```powershell
# from D:\Aryan\Project\battery-digital-twin
.\.venv\Scripts\Activate.ps1        # or call .venv\Scripts\python.exe directly
python -m jupyter lab               # kernel: "Python (battery-twin)"
```

---

## Phase 0 — Environment & scaffold  *(status: environment DONE)*

| Item | State |
|---|---|
| `.venv/` (Python 3.12.9) | created |
| `requirements.txt` (core: pandas, scipy, sklearn, xgboost, lightgbm, shap, mapie, mlflow, ruptures, pandera) | installed & smoke-tested |
| `requirements-deep.txt` (torch) | deferred to Phase 8 |
| `requirements-app.txt` (fastapi, streamlit, duckdb, anthropic) | deferred to Phase 12 |
| `requirements.lock.txt` (`pip freeze`) | written — regenerate after every install |
| Jupyter kernel `battery-twin` | registered |
| `.gitignore` (excludes `.venv/`, `data/`, `models/`, `mlruns/`) | written |

**Remaining Phase 0 task — repo skeleton:**

```
battery-digital-twin/
├── configs/                 # YAML: paths, split manifests, model hyperparams
├── data_contracts/          # pandera schemas for each table
├── data/
│   ├── raw/                 # NASA .mat as downloaded — NEVER edited
│   ├── interim/             # parsed per-battery parquet
│   └── processed/           # canonical tables + feature store
├── src/
│   ├── ingestion/  preprocessing/  feature_engineering/
│   ├── soh/  rul/  anomaly/  simulation/  twin/
│   ├── api/  agent/
├── notebooks/01_domain_and_data/  02_eda/  03_soh/  04_rul/  05_external_validation/
├── tests/  reports/  models/  dashboards/  docker/
```

Add `src/__init__.py` files and install the package editable (`pip install -e .`) so notebooks do
`from src.ingestion import ...` instead of hacking `sys.path`.

**Done when:** a fresh clone plus `pip install -r requirements.txt` reproduces the environment.

---

## Phase 1 — Domain foundations  *(2 days, no code)*

**Goal:** be able to defend every modelling choice in battery-engineering language. This is the phase
that separates the project from a generic regression demo.

### 1.1 Cell fundamentals

- Li-ion cell anatomy: anode (graphite), cathode (LCO/NMC/LFP), electrolyte, separator, current collectors.
- **Intercalation**: Li-ions shuttle between electrodes. Charge = Li into the anode, discharge = Li back to the cathode.
- Form factors: 18650 cylindrical (NASA uses these, nominal **2.0 Ah**), pouch (Oxford), prismatic.
- Cell → module → pack. Your data is **cell-level**; the blueprint requires you to say this out loud.

### 1.2 The vocabulary you must own

| Term | Meaning | Why it matters here |
|---|---|---|
| **SOC** | State of Charge — % of *current* capacity available now | short-term; NOT your target |
| **SOH** | State of Health — `Q(t) / Q_ref × 100` | your primary regression target |
| **RUL** | Remaining Useful Life — cycles until EOL | your prognostics target |
| **EOL** | End-of-Life threshold | NASA: 2.0 Ah → **1.4 Ah = 70% SOH** (30% fade); industry often uses 80% |
| **DOD** | Depth of Discharge per cycle | a degradation stressor |
| **C-rate** | current ÷ nominal capacity (2 A on a 2 Ah cell = 1C) | load stress |
| **CC-CV** | Constant Current then Constant Voltage charging | the NASA protocol; CC-phase duration is a top health indicator |
| **Cut-off voltage** | discharge stop voltage (2.2–2.7 V across NASA cells) | differs *per battery* — do not assume uniform |
| **Capacity fade** | loss of usable Ah | what SOH measures |
| **Power fade** | rise in internal resistance | what the impedance signals measure |
| **EIS** | Electrochemical Impedance Spectroscopy → `Re` (electrolyte resistance), `Rct` (charge-transfer resistance) | an independent degradation channel |
| **Knee point** | the cycle where fade sharply accelerates | the thing worth predicting early |
| **Capacity regeneration** | temporary capacity *recovery* after a rest period | ⚠ NASA data is full of these upward spikes — do NOT "clean" them away as outliers |

### 1.3 Degradation mechanisms (the physics you cite, not simulate)

- **SEI layer growth** on the anode consumes cyclable lithium → **LLI** (loss of lithium inventory). Accelerated by high temperature; dominant early-life; roughly square-root-of-time behaviour.
- **Lithium plating** — metallic Li deposits during fast or cold charging → sharp, often irreversible fade; a common knee-point cause.
- **LAM** (loss of active material) — particle cracking, binder decay, driven by the mechanical stress of deep cycling.
- **Impedance rise** — thicker SEI plus electrolyte decomposition → higher Re/Rct → power fade.
- **Stressors, roughly ranked**: high temperature, high C-rate, deep DOD, high-SOC dwell, fast charging at low temperature.
- **Calendar vs cycle aging** — NASA is cycle aging *with rest periods*, which is exactly why regeneration appears.

### 1.4 Prognostics framing

- PHM (Prognostics & Health Management): condition monitoring → diagnostics → prognostics.
- Two prediction regimes, always evaluated separately: **early-life** (hard, few cycles observed) and **near-EOL** (easy). A single MAE number hides this distinction.
- Data-driven vs physics-based vs hybrid approaches. You are doing **data-driven with physics-informed features**.

### 1.5 Reading list (do this, then stop reading and start coding)

1. NASA PCoE battery data set **README / experiment description** — the canonical protocol description.
2. Saha & Goebel, *Battery Data Set*, NASA Ames Prognostics Data Repository — source documentation for your primary dataset.
3. One survey — search "lithium-ion battery RUL prediction review". Skim it for the *feature / health-indicator* tables, not the model zoo.
4. Severson et al. 2019, *Data-driven prediction of battery cycle life before capacity degradation* (Nature Energy) — the reference standard for leakage-safe early prediction and ΔQ(V) features.
5. Birkl et al. — Oxford Battery Degradation Dataset 1 documentation.
6. Attia et al. 2022 — "knees in battery aging" — for precise knee-point language.

**Deliverable:** `phase1_domain/domain_primer.md` — your own 2–3 page write-up: glossary, degradation
mechanisms, why SOH and RUL are defined the way they are, and the limitations of cell-level data.

**Done when:** you can answer, unaided — *"Why does CC-charge time shorten as a cell ages?"*
(The cell holds less charge, so the constant-current phase transfers less before hitting 4.2 V.)

---

## Phase 2 — Dataset acquisition & structural audit  *(1–2 days)*

**Goal:** know exactly what is inside every file before writing a parser.

### 2.1 Acquire

- Download the **NASA Li-ion Battery Aging** set (the BatteryAgingARC bundles) into `data/raw/nasa/`.
- Record in `docs/data_manifest.md`: source URL, download date, file list, SHA256 per file, total size. The blueprint explicitly requires version and date, because these repository URLs move.
- Do not touch `data/raw/` again after this.

### 2.2 Expected structure — **verify this table, do not trust it**

`.mat` MATLAB structs, one per battery (e.g. `B0005.mat`). Each holds a `cycle` array; each element has:

| Field | Contents |
|---|---|
| `type` | `'charge'` / `'discharge'` / `'impedance'` |
| `ambient_temperature` | °C — 24 for the classic group; other groups run at 4 and 43 |
| `time` | cycle start as a 6-element datetime vector |
| `data` | the measurement struct — fields depend on `type` |

- **charge**: `Voltage_measured, Current_measured, Temperature_measured, Current_charge, Voltage_charge, Time`
- **discharge**: the same, plus `Current_load, Voltage_load` and **`Capacity`** — the only place your SOH target lives
- **impedance**: `Sense_current, Battery_current, Current_ratio, Battery_impedance, Rectified_Impedance, Re, Rct`

Expected protocol: CC charge at 1.5 A to 4.2 V, then CV until current falls to roughly 20 mA;
discharge at a constant 2 A to a per-cell cut-off voltage; periodic EIS sweep from 0.1 Hz to 5 kHz.
The classic group is **B0005, B0006, B0007, B0018** at 24 °C, roughly 130–170 cycles each.

⚠ **Every number above is from prior knowledge of this dataset. Step 2.3 is where you confirm it against the actual files.**

### 2.3 Structural audit (your first real code)

Notebook `notebooks/01_domain_and_data/01_inspect_raw.ipynb`:

```python
from scipy.io import loadmat
m = loadmat("data/raw/nasa/B0005.mat", simplify_cells=True)   # simplify_cells=True saves hours
```

Produce a table answering:

1. How many batteries, and which ambient-temperature groups?
2. Per battery: counts of charge / discharge / impedance cycles. Are they interleaved?
3. Per battery: the **actual** cut-off voltage, charge current, discharge current, nominal capacity.
4. Sampling rate and points per cycle — constant or variable?
5. First and last `Capacity` value per battery. Does every cell actually reach 1.4 Ah?
6. Real elapsed time between cycles — the rest periods that produce capacity regeneration.
7. Unit checks: Ah vs mAh, current sign convention (is discharge negative?), °C vs K.

**Deliverable:** `phase2_data_audit/data_dictionary.md` — every field with unit, dtype and range, a per-battery
summary table, and a "surprises found" section.

**Done when:** you can state, per battery: cycle count, capacity start → end, % fade, ambient
temperature, cut-off voltage, and whether it crosses EOL.

---

## Phase 3 — Parsing to a canonical schema  *(2 days)*

**Goal:** turn `.mat` chaos into reproducible, validated tables. Write this as `src/` modules that
notebooks import — not as notebook cells.

### 3.1 Three tables, not one

**A. `telemetry`** (long, within-cycle, one row per sample) — the blueprint's canonical schema:
`battery_id, cycle_id, cycle_type, timestamp, t_rel_s, voltage_v, current_a, temperature_c, ambient_temp_c`

**B. `cycle_summary`** (one row per discharge cycle) — what modelling actually consumes:
`battery_id, cycle_id, cycle_start_time, capacity_ah, soh_pct, energy_wh, charge_duration_s, discharge_duration_s, coulomb_throughput_ah, internal_resistance_ohm, rct_ohm, …features`

**C. `impedance`** — sparse, measured only every N cycles. Join onto `cycle_summary` with
**backward-fill only**; interpolating from a future measurement is leakage.

### 3.2 Pipeline steps

1. `src/ingestion/nasa_loader.py` — `.mat` → per-battery dataframes, unit normalisation, sign conventions.
2. `src/preprocessing/segment.py` — assign a monotonic `cycle_id`. Decide and **document** whether it counts all cycles or discharge cycles only — this choice propagates all the way into RUL.
3. `src/preprocessing/quality.py` — QC checks:
   - missing / NaN counts per field
   - impossible values (V ≤ 0 or > 4.3, |I| > 5 A, T < 0 or > 80 °C)
   - duplicate or non-monotonic timestamps
   - truncated cycles (abnormally few samples)
   - write results into a `qc_flags` column; **flag, never silently drop**
4. `src/preprocessing/targets.py` — SOH and RUL, per the definitions frozen in Phase 5.
5. `data_contracts/` — a `pandera` schema per table so the pipeline fails loudly on violation.
6. Write to `data/processed/*.parquet` via pyarrow. Log row counts at every stage.

### 3.3 Reproducibility

- All paths live in `configs/paths.yaml`. Zero hard-coded `D:\...` strings inside `src/`.
- `python -m phase3_pipeline.build_dataset` regenerates everything from `data/raw/` in one command.
- `phase3_pipeline/test_pipeline.py`: known cycle count for B0005, `SOH[0] == 100`, capacity within `[1.0, 2.2]`.

**Deliverable:** `data/processed/cycle_summary.parquet` and `telemetry.parquet`, plus passing tests.

**Done when:** you can delete `data/interim/` and `data/processed/`, run one command, and get byte-comparable files back.

---

## Phase 4 — EDA  *(3 days — the phase that generates your project's insights)*

Notebooks in `notebooks/02_eda/`. Every plot gets a written takeaway, not just an image.

### 4.1 Degradation trajectories (the headline)

- Capacity vs cycle, one line per battery, shared axes.
- SOH % vs cycle with the **70% EOL line** drawn in; mark each battery's EOL crossing cycle.
- Re-plot against *fraction of life* (`cycle / N_EOL`). Do the trajectories collapse onto one curve, or is each cell its own shape? This single plot tells you how much cross-battery generalisation is realistic.
- Fade rate: rolling slope of ΔSOH/Δcycle. Is degradation linear, or is there a **knee**?

### 4.2 Capacity regeneration (handle this explicitly)

- Zoom into the SOH curve. Count the upward jumps, measure their magnitude, and correlate them with the rest gap before that cycle (`cycle_start_time` diff).
- Decide and document: model the raw signal, or a monotone-smoothed envelope? Both are defensible — but the smoothing itself must be **causal** (backward-looking window only).

### 4.3 Within-cycle curve shape

- Discharge voltage vs time for ~10 cycles spanning early to late, colour-graded by cycle. The curve should shift left and the plateau should shorten — this is the visual justification for your engineered features.
- Charge curve: watch the CC→CV transition point move earlier with age.
- Temperature vs time per cycle: peak temperature and time-to-peak drifting with age.
- **Incremental capacity analysis (dQ/dV)** and ΔQ(V): peak height, position and area vs cycle. This is the Severson-style feature family and the most domain-expert thing in your EDA.

### 4.4 Impedance

- `Re` and `Rct` vs cycle per battery — expect a rising trend under heavy noise.
- Correlate `Rct` with SOH. Note the sparsity and state how you will handle it.

### 4.5 Cross-battery comparison

- Small multiples: all batteries × {capacity, mean temperature, discharge duration, Rct}.
- Distribution of cycle count and total coulomb throughput at EOL — are these cells even comparable?
- **N = 4 in the main group.** State it here, and let it govern every later claim about generalisation.

### 4.6 Candidate health-indicator screening

Compute quick per-cycle features and rank them against SOH using **both Pearson and Spearman**
(monotone-but-nonlinear relationships matter here): CC-charge duration, CV-charge duration, CC/CV
ratio, time from 3.8 V to 4.2 V on charge, discharge duration, mean/max/Δ temperature,
time-at-peak-temperature, energy Wh, dQ/dV peak height and position, Re, Rct.

### 4.7 Data quality report

Missingness heatmap, QC-flag counts per battery, sampling-rate consistency, and every surprise you
found — written down, because these become the caveats in your final report.

**Deliverable:** `reports/03_eda_report.md` with embedded figures and a numbered **Findings** list,
e.g. *"F3: B0007 fades slowest and never reaches 1.4 Ah, so it must be excluded from RUL training or handled as censored."*

**Done when:** you can describe in words what a *typical* degradation trajectory looks like, and in what specific ways each battery deviates from it.

---

## Phase 5 — Targets & leakage-safe evaluation design  *(1 day — BEFORE any model)*

The blueprint names leakage the number-one risk. Freeze the protocol first, in writing.

### 5.1 Targets

- `SOH(t) = Q(t) / Q_ref × 100`. Define `Q_ref` explicitly: rated 2.0 Ah, or the cell's own first measured capacity? Pick one, justify it, use it everywhere.
- `RUL(t) = N_EOL − N_t`, where `N_EOL` is the first cycle with SOH ≤ 70%.
- Cells that never cross EOL are **right-censored**. Exclude them from RUL training or extrapolate with a documented method — never invent an `N_EOL`.

### 5.2 Splits — three protocols, evaluated separately

| Protocol | Split | Question it answers |
|---|---|---|
| **P1 within-battery temporal** | first X% of cycles → predict the rest | can we forecast this cell's future? |
| **P2 cross-battery (LOBO)** | leave-one-battery-out CV | does it transfer to an unseen cell? |
| **P3 cross-dataset** | NASA → Oxford, no retraining | Phase 14 |

With four batteries, **LOBO CV is the honest choice** — a single train/test split is pure noise.

### 5.3 Leakage rules — pin these above your desk

- No random row or cycle shuffling. Ever.
- Scalers and imputers fit on **train only**, inside a `sklearn.Pipeline`.
- No feature may touch a future cycle. Rolling windows are backward-only. No `Q_final`, no `N_EOL`, no whole-cell mean or max.
- If `Q_ref` is the first measured capacity, that is a *past* value and is legal. `Q_max` over all cycles is *not*.
- Impedance backward-fill only.
- The EOL-crossing cycle used to build the *label* must never appear as an *input*.

### 5.4 Experiment manifest

`configs/experiment_001.yaml`: train / val / test battery IDs, protocol, observed-life fraction,
prediction horizon, feature list, random seed, dataset version and download date. Every run logs to
MLflow with this manifest attached.

**Deliverable:** `docs/04_evaluation_protocol.md` and `src/splits.py` with `lobo_split()` and `temporal_split()`.

**Done when:** a reviewer can reproduce your exact splits from the manifest alone.

---

## Phase 6 — Feature engineering  *(2 days)*

`src/feature_engineering/` — promote the winners from §4.6 into a versioned feature store.

- **Cycle-count only** — the baseline to beat: `cycle_id` plus polynomial terms.
- **Charge-phase HIs** — CC duration, CV duration, CC/CV ratio, time 3.8→4.2 V, charge Ah.
- **Discharge-phase HIs** — duration, mean and min voltage, voltage-plateau length, energy Wh.
- **Thermal** — mean/max/Δ temperature, time above threshold, time to peak, ambient.
- **Curve shape** — dQ/dV peak height, position and area; ΔQ(V) statistics against a reference cycle (variance, minimum, skew — the Severson features).
- **Impedance** — Re, Rct, and their backward-looking deltas.
- **Cumulative usage** — coulomb throughput, energy throughput, elapsed wall-clock time, rest duration before the cycle.
- **Backward-only temporal** — rolling mean / std / slope over the last {5, 10, 20} cycles for the top HIs, plus EWMA. Never centred windows.

Persist as `data/processed/features_v1.parquet` alongside a `feature_manifest.yaml` recording each
feature's name, formula, window and leakage-safety justification. Version the file (`v1`, `v2`) and never overwrite.

**Done when:** every feature has a one-line written proof that it uses only data available at cycle `t`.

---

## Phase 7 — SOH modelling  *(2–3 days)*

**Stage A — baselines**, all under LOBO CV, all tracked in MLflow:

1. Persistence: `SOH(t) = SOH(t−1)`.
2. Linear / polynomial fit on cycle number alone.
3. Ridge on the full feature set.
4. Random Forest, then **XGBoost / LightGBM**.

**Stage B — blueprint Experiment 1:** does the engineered feature set beat cycle-count-only? Report the delta, not just the winning score.

**Metrics:** MAE, RMSE, R², plus **error stratified by battery age** (early / mid / late life). The blueprint forbids a single headline number.

**Deliverable:** `reports/05_soh_model_report.md` — model comparison table, per-battery LOBO results, error-vs-age plot, residual diagnostics.

---

## Phase 8 — RUL modelling  *(3–4 days)*

The genuinely hard target: predict remaining cycles from a *partial* trajectory.

1. **Baseline** — fit a degradation curve on observed cycles and extrapolate to the 70% line.
2. **Tabular** — XGBoost on features plus observed-life fraction. Try log-RUL as the target.
3. **Temporal** — install `requirements-deep.txt`, then LSTM/GRU/TCN over a rolling window of the last *k* cycles.
4. **Experiment 2** — do temporal models beat XGBoost *specifically at early horizons*?
5. **Experiment 3** — evaluate observing only the first 20% / 40% / 60% of life; plot MAE vs observed-life fraction. This curve is the single most informative result in the project.

**Metrics:** MAE and RMSE in cycles, broken out by prediction horizon and observed-life fraction.
With N = 4, deep models will be data-starved — report that honestly rather than tuning until the number looks good.

---

## Phase 9 — Uncertainty & calibration  *(2 days)*

- Quantile regression (LightGBM `objective="quantile"`) at q = 0.05 / 0.50 / 0.95.
- Bootstrap or deep ensembles.
- **Conformal prediction** via `mapie`, with a split-conformal calibration set that respects battery-level splits.
- Evaluate **empirical coverage** vs nominal, mean interval width, and a calibration curve. A 90% interval that covers 55% of held-out cells is a finding to report, not a bug to hide.

**Target output format:** `SOH = 82.7% | RUL = 146 cycles | 90% PI = [123, 179] | trend = accelerating`

---

## Phase 10 — Anomaly & change-point detection  *(2 days)*

- **Peer-group residual baseline** — expected SOH at cycle `t` from the cohort; score is the standardised residual, using a robust/MAD scale.
- **Isolation Forest** on the cycle-level feature vector.
- **Change-point detection** with `ruptures` (PELT / BinSeg) on the SOH slope, to find the knee or onset of acceleration.
- **Detection lead time** — cycles between first alarm and EOL crossing. This is the operationally meaningful metric (Experiment 6).
- Language discipline: output *"abnormality requiring investigation"*, never *"failure"*, unless the data supports a mechanism.

---

## Phase 11 — Driver analysis  *(1 day)*

SHAP (global and per-battery local), permutation importance, and PDP/ICE for the top drivers.
Cross-check that SHAP rankings stay stable across LOBO folds — instability at N = 4 is expected and
must be reported rather than hidden. Phrase every result as **"signals associated with model-predicted
degradation"**; feature importance is not causal evidence (Experiment 7).

---

## Phase 12 — Digital twin state service  *(2–3 days)*

`src/twin/` — a versioned, auditable state object, not a dashboard:

```
battery_id, as_of_cycle, soh_est, rul_est, rul_interval_90,
degradation_rate_pp_per_cycle, thermal_state, anomaly_score,
health_trend, model_version, feature_version, computed_at
```

Serve with FastAPI (`requirements-app.txt`): `GET /twin/{battery_id}?cycle=420`.
Persist state snapshots to parquet or DuckDB so the twin has **history** and every answer traces back
to a specific model version.

---

## Phase 13 — Scenario / what-if engine  *(2 days)*

`src/simulation/` — `simulate_scenario(battery_id, temperature_delta, load_multiplier, cycling_profile)`:
perturb the operating-condition features, re-run the degradation projection, and return baseline vs
scenario SOH/RUL trajectories **with intervals**.

The critical piece is an **extrapolation guard**. Compare the requested scenario against the training
distribution's support — NASA's 24 °C group offers almost no thermal variation, so a "+5 °C" scenario
is largely unsupported. When a scenario falls outside support, the engine must **refuse or loudly flag
it** rather than emit a confident number. The blueprint singles this out as what makes the twin credible.

---

## Phase 14 — Oxford external validation  *(2 days)*

Map Oxford pouch-cell data into the *same* canonical schema — chemistry, format, protocol and
drive-cycle discharge all differ. Apply the NASA-trained model **with no retraining** (Experiment 5).
Report NASA and Oxford metrics **separately, never pooled**. Degradation in performance is expected;
honestly quantifying that gap is a stronger result than a suspiciously good pooled score.

---

## Phase 15 — Tool layer & GenAI copilot  *(3 days)*

Deterministic tools first, LLM second. The LLM computes **nothing**:

`get_battery_state` · `get_soh` · `predict_rul` · `get_degradation_drivers` · `detect_anomaly` · `compare_batteries` · `simulate_scenario` · `get_model_metadata`

Each is a typed function over the Phase 12/13 services returning structured JSON. Expose them through
tool calling, or an MCP-style server for the advanced version. The system prompt must require answering
only from tool output, and saying so plainly when a tool returns an extrapolation warning or no data.
Evaluate on tool-call correctness, groundedness, and **refusal behaviour on unanswerable questions**.

---

## Phase 16 — UI, packaging, documentation  *(2–3 days)*

Streamlit: battery selector, trajectory with prediction-interval band, twin state card, anomaly
timeline, scenario sliders, copilot chat. Dockerfile. README with an architecture diagram, results,
**and the limitations section** (cell-level not pack-level; N = 4; scenario simulation is not causal).
Tests green in CI.

---

## Critical-path summary

| # | Phase | Days | Gate to the next phase |
|---|---|---|---|
| 0 | Environment & scaffold | 0.5 | env done; scaffold pending |
| 1 | Domain foundations | 2 | domain primer written |
| 2 | Dataset acquisition & audit | 1.5 | data dictionary complete |
| 3 | Parsing → canonical schema | 2 | one-command reproducible build |
| 4 | **EDA** | 3 | numbered findings list |
| 5 | Targets & split protocol | 1 | protocol frozen in writing |
| 6 | Feature engineering | 2 | every feature proven leakage-safe |
| 7 | SOH modelling | 2.5 | beats the cycle-count baseline |
| 8 | RUL modelling | 3.5 | MAE-vs-observed-life curve |
| 9 | Uncertainty | 2 | measured coverage |
| 10 | Anomaly / change-point | 2 | lead time measured |
| 11 | Driver analysis | 1 | SHAP stable across folds |
| 12 | Digital twin service | 2.5 | versioned queryable state |
| 13 | Scenario engine | 2 | extrapolation guard works |
| 14 | Oxford validation | 2 | separately reported metrics |
| 15 | Tool layer + copilot | 3 | grounded; refuses when it should |
| 16 | UI + docs + Docker | 2.5 | portfolio-ready |

**Phases 1–5 are the foundation.** Rushing them is how this project becomes a leaky notebook with an impressive-looking R².

---

## Ten mistakes that would sink this project

1. Random train/test split across cycles — near-duplicate rows land on both sides and accuracy becomes fiction.
2. Normalising with statistics computed over the full dataset, test included.
3. Using `Q_max` or `N_EOL` as a feature input.
4. Deleting capacity-regeneration spikes as "outliers" — they are real physics and a documented trait of this dataset.
5. Reporting one MAE instead of error vs battery age and prediction horizon.
6. Claiming generalisation from four cells.
7. Pooling NASA and Oxford and calling the result cross-dataset validation.
8. Letting the LLM compute or estimate any number.
9. Presenting what-if output as causal.
10. Building the copilot before the RUL model is trustworthy.
