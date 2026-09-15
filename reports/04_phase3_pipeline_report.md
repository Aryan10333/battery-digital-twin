# Phase 3 — Canonical Data Pipeline: Implementation Report

**Status:** complete. **Scope:** Tier 1 (B0005, B0006, B0007, B0018 @ 24 °C).
**Commits:** `fdf9d4d` (pipeline), `bfad24c` (packaging fix).

Converts raw NASA `.mat` files into validated, reproducible analytical tables with
targets and quality flags. Phase 4 (EDA) consumes the output of this phase.

---

## 1. What was built

```
pyproject.toml                        editable install, so notebooks import src.* directly
configs/paths.yaml                    all paths and constants; no absolute paths in src/
src/config.py                         repo-root resolution and config loading
src/ingestion/nasa_loader.py          .mat -> telemetry / cycles / impedance
src/ingestion/build_dataset.py        one-command orchestrator (CLI)
src/preprocessing/quality.py          QC checks, flags, regeneration statistics
src/preprocessing/targets.py          SOH, EOL, RUL, cumulative usage
data_contracts/schemas.py             pandera schemas for each table
tests/test_ingestion.py               27 regression and leakage tests
```

Single entry point:

```bash
python -m src.ingestion.build_dataset
```

### Output tables (`data/processed/`)

| Table | Rows | Cols | Grain |
|---|---|---|---|
| `telemetry` | 2,089,050 | 11 | one sample within one charge/discharge operation |
| `cycle_summary` | 636 | 66 | one discharge cycle |
| `impedance` | 887 | 6 | one EIS measurement |
| `regeneration_summary` | 4 | 7 | one battery |

Telemetry splits 1,903,329 charge / 185,721 discharge samples — charge records are
roughly ten times longer, because the CV phase runs far longer than the discharge.

`cycle_summary` carries, per discharge cycle: measured capacity; discharge-phase
aggregates (duration, energy, V/I/T statistics); relaxation-tail metrics; paired
charge-phase aggregates (CC/CV durations and ratio, charge Ah, transition voltage);
backward-joined impedance with a staleness counter; cumulative usage; targets; and
QC flags.

---

## 2. Design decisions

Each of these was a fork in the road; recording them here because they propagate
into every later phase.

### 2.1 `cycle_id` indexes discharge operations only

Capacity — the SOH target — is recorded only on discharge records, so the discharge
sequence is the natural aging index. `cycle_id` is 1-based and contiguous per
battery. The raw file position is retained as `op_index` for traceability and
for all backward-looking joins.

Consequence: a battery's "616 cycles" in the raw file is 168 modelling cycles.

### 2.2 EOL is defined on absolute capacity, not on an SOH percentage

`eol_capacity_ah: 1.4`, matching NASA's own experimental criterion. This was
forced by Phase 2 Finding 3: measured initial capacities are 1.856–2.035 Ah, not
the 2.0 Ah rated value, so a fixed SOH-percentage threshold would land at a
different physical capacity for every cell.

SOH is carried in **both** normalisations for display — `soh_pct_self` (against
each cell's first measured capacity) and `soh_pct_rated` (against 2.0 Ah) — but
neither defines the RUL label.

### 2.3 EOL crossing must be sustained

`eol_sustain_cycles: 5`. EOL is the first cycle from which capacity stays at or
below 1.4 Ah for five consecutive cycles, rather than the first cycle that merely
touches it. Required because capacity regeneration makes the trajectory
non-monotonic.

**Honest result: for these four cells the sustained rule and first-touch agree
exactly.** `eol_cycle` equals `eol_first_touch_cycle` for all three uncensored
batteries. The rule earns its place as a safeguard, not by changing the answer.
Both columns are persisted so the claim is checkable, and the transient-dip
behaviour is covered by a direct unit test rather than resting on the data.

### 2.4 Impedance is joined backward-only

`merge_asof(..., direction="backward")` on `op_index`, plus an
`impedance_age_ops` staleness column. Interpolating between EIS measurements
would pull a future measurement into a present-time row. A test asserts
`impedance_age_ops >= 0` on every row, so this is enforced rather than intended.

### 2.5 Measurements are taken over the active segment, not the whole record

See Finding 1. Durations, energy, end voltage and thermal statistics are computed
over samples where the cell is actually under load or charge.

### 2.6 QC flags, never silent drops

All quality problems are recorded as boolean columns plus a combined `qc_flags`
string. No row is deleted. Downstream phases choose what to exclude, and that
choice is visible in the code that makes it.

Capacity regeneration is deliberately **not** treated as a defect. It is real
physics and is quantified in `regeneration_summary` instead.

---

## 3. Findings

New in Phase 3; the Phase 2 findings are in `docs/02_data_dictionary.md`.

### Finding 1 — discharge records continue past load removal

Each discharge record includes a relaxation tail after the load is disconnected,
during which terminal voltage recovers. B0005 cycle 1: the active discharge ends
at 3347 s and 2.612 V, but the record runs to 3690 s and 3.277 V — a 343 s tail
(9% of the record) with 0.665 V of recovery.

**Impact if missed:** `Time[-1]` overstates discharge duration by ~9% on *every*
cycle, and the final recorded voltage (median 3.525 V for B0005) is not the
discharge cut-off (median 2.660 V) but the relaxed open-circuit voltage. Any
feature built on "end of discharge voltage" from the raw array would be measuring
the wrong physical quantity.

**Handling:** active-segment masking at |I| > 0.5 A. The tail is retained as
`relax_duration_s` and `v_recovery_v` — the recovery magnitude grows with internal
resistance, so it is a health signal in its own right, not noise to discard.
For B0005 it rises from 0.665 V at cycle 1 to 0.935 V at cycle 168.

### Finding 2 — operation ordering differs between batteries

B0005 begins `CDCDCDCDCD…` (charge/discharge alternating, impedance appearing
later). B0018 begins `CIDICIDICIDI…` (impedance interleaved from the start).

**Impact if missed:** any pairing logic assuming a fixed stride would silently
mis-attribute charge records to the wrong discharge cycle for at least one battery.

**Handling:** all pairing is positional via `merge_asof` on `op_index`. Each
discharge is joined to the most recent preceding charge; each charge's telemetry
is attributed forward to the discharge that follows it.

This also explains an asymmetry in the impedance join: B0005/6/7 each have 19
leading cycles with no impedance data (57 null rows total), because their first
EIS measurement comes after cycle 19. B0018 has impedance from cycle 1.

### Finding 3 — cycle 1 is a partial charge on all four cells

Every battery's first charge delivers ~0.8 Ah against a ~1.9 Ah norm, with CC
duration ~1040–1200 s against a ~2500–3000 s median. The cells began the
experiment already partly charged (B0005's first charge record starts at 3.873 V).

**Impact:** cycle 1's CC/CV split is not comparable with the rest of the
trajectory, and CC-duration is one of the strongest planned health indicators.
Left unflagged it would appear as a spurious "healthy cell with short CC time",
inverting the feature's meaning at exactly the reference point other features are
normalised against.

**Handling:** `qc_charge_partial`.

### Finding 4 — aborted charge operations

Five charge records are effectively empty:

| Battery | Cycle | CC duration | Charge Ah |
|---|---|---|---|
| B0005 | 31 | 9.0 s | 0.00 |
| B0006 | 31 | 9.0 s | 0.00 |
| B0007 | 31 | 9.0 s | 0.00 |
| B0018 | 46 | 32.4 s | 0.20 |
| B0018 | 56 | 15.4 s | 0.00 |

That B0005/6/7 all fail at cycle 31 confirms these cells were cycled
synchronously on one testbed — a single rig interruption, not three independent
cell faults.

**Handling:** `qc_charge_aborted`.

### Finding 5 — early-life discharges overshoot the documented cut-off

21 cycles discharge materially deeper than their documented cut-off, and they are
confined to early life:

| Battery | Nominal cut-off | Affected cycles | Median V_min there |
|---|---|---|---|
| B0006 | 2.5 V | 10–26 (7 cycles) | 2.188 V |
| B0007 | 2.2 V | 4–29 (14 cycles) | 1.852 V |

Separately, every battery's median measured minimum sits 0.04–0.10 V *below* its
nominal cut-off, consistent with the cut-off triggering a sample late given the
9–19 s sampling interval.

**Status: open.** Flagged as `qc_cutoff_mismatch`, not resolved. It is an
experimental artifact rather than a parsing error, but deeper discharges extract
more capacity, so these cycles may show inflated capacity relative to their true
state of health. Phase 4 must check whether the early-life capacity trajectory of
B0006 and B0007 is distorted at these cycles.

### Finding 6 — telemetry volumes are identical across B0005/6/7

All three produce exactly 591,458 telemetry rows. Verified as genuine rather than
a parsing bug: voltage sums differ (2,427,311 / 2,427,482 / 2,424,825). Same
testbed, same protocol, synchronous logging — consistent with Finding 4.

---

## 4. Corrections made during Phase 3

### 4.1 `qc_charge_partial` threshold — wrong rule, replaced

**First attempt:** flag when `charge_ah < 0.5 × (battery median charge_ah)`.

**Failure:** caught 1 of the 4 known partial charges. The median is computed over
the full trajectory, and delivered charge declines as cells age, so the median
drifts down toward the value being tested against. The threshold moved with the
degradation it was supposed to be independent of.

**Fix:** a physical, self-normalising rule — `charge_ah < 0.8 × capacity_ah`. A
full charge must deliver at least as much as the following discharge extracts.
Caught all 4, with no false positives across the other 632 cycles.

**Lesson carried forward:** in a degrading system, any threshold defined against a
whole-trajectory statistic is suspect. It is also a leakage pattern — the original
rule used the full-trajectory median, which is not available at inference time.

### 4.2 `egg-info` build artifacts committed, then untracked

`pip install -e .` generated `battery_digital_twin.egg-info/`, which was picked up
by `git add -A` in commit `fdf9d4d`. Removed from tracking in `bfad24c` and
`*.egg-info/`, `build/`, `dist/` added to `.gitignore`.

### 4.3 Audit script output path

`scripts/audit_raw_nasa.py` initially wrote its JSON to a hard-coded `/tmp` path,
which does not resolve on Windows; the table printed but the run exited non-zero.
Redirected to `reports/audit_raw_nasa.json` when the script was moved into the repo.

### 4.4 Carried forward from Phase 2 — an error in the project plan

`docs/PROJECT_PLAN.md` (Phase 13) asserts that NASA offers "almost no thermal
variation" and that the scenario engine would therefore be largely extrapolating.
That is true of Tier 1 but **wrong for the dataset as a whole**, which spans
ambient 4 / 24 / 43 °C and discharge currents 1 / 2 / 4 A. The plan text has not
yet been amended; `docs/02_data_dictionary.md` Finding 8 records the correction.

### 4.5 Claims from earlier phases that Phase 3 confirmed

Not corrections, but worth recording as verified rather than assumed:

* Discharge fields really are `Current_load` / `Voltage_load`, against the bundle
  README's claim of `Current_charge` / `Voltage_charge`. The plan was right and
  the official documentation is wrong.
* B0007 is right-censored, as the plan predicted before any data was downloaded.
* Current sign convention is charge-positive, discharge-negative (−2.0 A observed).

---

## 5. Validation

### 5.1 Reproducibility

`data/processed/` was deleted and rebuilt from `data/raw/` with one command. All
four parquet files came back **byte-identical** (SHA256 match). This is the
Phase 3 gate condition from the project plan.

### 5.2 Test suite — 27 tests, all passing

Coverage by category:

* **Structure** — expected batteries; discharge counts 168/168/168/132; `cycle_id` contiguous from 1.
* **Targets** — `soh_pct_self` starts at exactly 100; initial capacities to 3 decimals; EOL cycles 125/109/97; B0007 censored with all-NaN RUL; RUL decrements by exactly 1 per cycle.
* **Leakage guards** — `impedance_age_ops >= 0` on every row; `q_ref_self_ah` equals *first* not *maximum* capacity.
* **EOL rule** — a synthetic transient dip below threshold does not trigger EOL; a trajectory that never crosses returns `None`.
* **Quality** — the five known aborted charges are flagged; all four cycle-1 partial charges are flagged; clean-cycle count is exactly 606.
* **Contracts** — `cycle_summary` and a 50k telemetry sample validate against their pandera schemas; telemetry time is strictly monotonic within every operation.

### 5.3 Physical validation

The strongest evidence the parser is correct is that the extracted quantities
reproduce known battery physics without being fitted to do so:

| Battery | corr(cycle, CC duration) | corr(cycle, CV duration) | corr(CC duration, capacity) | corr(Rct, capacity) |
|---|---|---|---|---|
| B0005 | −0.821 | +0.836 | +0.822 | −0.943 |
| B0006 | −0.863 | +0.809 | +0.862 | −0.983 |
| B0007 | −0.748 | +0.829 | +0.747 | −0.960 |
| B0018 | −0.619 | +0.540 | +0.586 | −0.237 |

CC duration shortens with age, CV duration lengthens to compensate, and
charge-transfer resistance rises as capacity falls — the mechanism described in
`docs/01_domain_primer.md` §2.8, recovered from the data.

### 5.4 Targets and regeneration as built

| Battery | Cycles | Cap first | Cap last | EOL cycle | Censored | Regen steps | Max regen |
|---|---|---|---|---|---|---|---|
| B0005 | 168 | 1.856 | 1.325 | 125 | no | 36 (21.6%) | +0.088 Ah (4.76 pp) |
| B0006 | 168 | 2.035 | 1.186 | 109 | no | 27 (16.2%) | +0.152 Ah (7.46 pp) |
| B0007 | 168 | 1.891 | 1.432 | — | **yes** | 47 (28.1%) | +0.098 Ah (5.19 pp) |
| B0018 | 132 | 1.855 | 1.341 | 97 | no | 21 (16.0%) | +0.131 Ah (7.08 pp) |

Regeneration reaches 7.5 percentage points of SOH in a single step — an order of
magnitude above measurement noise, and decisive for why the sustained EOL rule
exists.

---

## 6. Known limitations and open items

1. **`qc_cutoff_mismatch` is unexplained** (Finding 5). 21 early-life cycles in B0006/B0007. May distort early capacity readings; Phase 4 must assess.
2. **B0018 behaves differently from the other three.** Weaker correlations on every health indicator (CC −0.62 vs −0.82; Rct −0.24 vs −0.94), sparser impedance (staleness to 13 operations vs 5), and fewer cycles (132 vs 168). Treat carefully in LOBO folds — it may be the hardest held-out cell, or it may be partly defective data.
3. **57 cycles have no impedance** (the first 19 of B0005/6/7). Backward-fill cannot fill a leading gap. Phase 6 must decide: drop, impute with an explicit indicator, or restrict impedance features to cycles ≥ 20.
4. **N is very small.** Four cells for SOH, three for RUL. No pipeline decision fixes this; it governs every generalisation claim in Phases 7–14.
5. **Tier 2/3 batteries are not parsed.** The loader handles their file structure, but the Finding 6 quality filter from Phase 2 (aborted runs, zero capacities) and cycle-level ambient handling (Finding 7) are not yet implemented.
6. **No incremental-capacity features yet.** dQ/dV and ΔQ(V) are Phase 6, not Phase 3. Telemetry retains full curves so they can be computed.

---

## 7. Reproduction

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python -m src.ingestion.build_dataset      # -> data/processed/*.parquet
python -m pytest tests/ -q                 # -> 27 passed
```

Raw data is not in version control. `docs/02_data_dictionary.md` §1 records the
archive SHA256, download date and source for re-acquisition.

---

## 8. Handoff to Phase 4

`data/processed/cycle_summary.parquet` is the EDA input. Priorities carried forward:

1. Plot the four degradation trajectories with the 1.4 Ah EOL line and the three EOL crossings marked.
2. Re-plot against `life_fraction` — do the trajectories collapse onto one curve? This determines how realistic cross-battery generalisation is.
3. Characterise regeneration against `rest_before_h`, which is already computed.
4. Investigate Finding 5 — is early-life capacity in B0006/B0007 distorted?
5. Investigate B0018's divergence (limitation 2).
6. Screen the candidate health indicators already in `cycle_summary` (CC/CV durations and ratio, `v_recovery_v`, `temp_rise_c`, Re, Rct) with Pearson *and* Spearman, before adding curve-derivative features in Phase 6.
