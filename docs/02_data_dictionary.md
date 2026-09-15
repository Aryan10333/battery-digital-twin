# NASA Li-ion Battery Aging Dataset — Data Dictionary & Structural Audit

Phase 2 deliverable. Everything below was measured from the downloaded files, not assumed.
Reproduce with `scripts/audit_raw_nasa.py`.

## 1. Acquisition

| Item | Value |
|---|---|
| Archive | `5.+Battery+Data+Set.zip` |
| Bytes | 209,708,670 |
| SHA256 | `82302a7db4fc1b34e0b6676326610438d43b816bdf11a69d1d012a464ef2f92e` |
| Downloaded | 2026-09-14 |
| Source | NASA PCoE Prognostics Data Repository — "Battery Data Set" |
| Location | `data/raw/nasa/` (archive kept; extracted to `data/raw/nasa/extracted/`) |

Six nested bundles, per-bundle SHA256 recorded in `data/raw/nasa/bundle_manifest.json`:

| # | Bundle | `.mat` files |
|---|---|---|
| 1 | `BatteryAgingARC-FY08Q4` | 4 |
| 2 | `BatteryAgingARC_25_26_27_28_P1` | 4 |
| 3 | `BatteryAgingARC_25-44` | 18 |
| 4 | `BatteryAgingARC_45_46_47_48` | 4 |
| 5 | `BatteryAgingARC_49_50_51_52` | 4 |
| 6 | `BatteryAgingARC_53_54_55_56` | 4 |

**38 files, but only 34 unique batteries** — see Finding 2.

---

## 2. File structure (verified)

```
B0005.mat
└── B0005                      MATLAB struct, name == battery id
    └── cycle                  struct array, one element per operation
        ├── type               'charge' | 'discharge' | 'impedance'
        ├── ambient_temperature  °C (int)
        ├── time               MATLAB date vector [Y M D h m s]
        └── data               fields depend on type
```

Load with `loadmat(path, simplify_cells=True)`; the struct key equals the battery id.

### Field reference

**`type == 'charge'`** — all `float64` arrays of equal length (one cycle's time series)

| Field | Unit | Notes |
|---|---|---|
| `Voltage_measured` | V | battery terminal voltage |
| `Current_measured` | A | battery current |
| `Temperature_measured` | °C | battery surface temperature |
| `Current_charge` | A | current at the charger |
| `Voltage_charge` | V | voltage at the charger |
| `Time` | s | relative to cycle start, begins at 0.0 |

**`type == 'discharge'`** — as above, but the load-side fields are named differently, plus:

| Field | Unit | Notes |
|---|---|---|
| `Current_load` | A | **not** `Current_charge` — see Finding 1 |
| `Voltage_load` | V | **not** `Voltage_charge` — see Finding 1 |
| `Capacity` | Ah | **scalar, one per discharge cycle** — the only source of the SOH target |

**`type == 'impedance'`** — complex-valued spectra plus two scalars

| Field | Unit | Notes |
|---|---|---|
| `Sense_current`, `Battery_current`, `Current_ratio` | A / ratio | raw EIS quantities, complex |
| `Battery_impedance` | Ω | computed from raw data, complex |
| `Rectified_Impedance` | Ω | calibrated/smoothed — **capital `I`**, see Finding 1 |
| `Re` | Ω | estimated electrolyte resistance (scalar) |
| `Rct` | Ω | estimated charge-transfer resistance (scalar) |

---

## 3. Experimental protocol (confirmed against bundle READMEs)

**Bundle 1 — B0005, B0006, B0007, B0018 @ 24 °C.** The canonical group.

* Charge: CC at **1.5 A** until **4.2 V**, then CV until charge current falls below **20 mA**.
* Discharge: CC at **2 A** until terminal voltage reaches a **per-cell cut-off**:

| Battery | Cut-off |
|---|---|
| B0005 | 2.7 V |
| B0006 | 2.5 V |
| B0007 | 2.2 V |
| B0018 | 2.5 V |

* Impedance: EIS sweep **0.1 Hz – 5 kHz**.
* EOL criterion: **30% fade, 2.0 Ah → 1.4 Ah**.

Every number in the Phase 1 primer marked *"verify in Phase 2"* is confirmed correct.

**Other groups vary deliberately** — this is wider condition coverage than the project plan assumed:

| Group | Ambient | Discharge current | Cut-offs | Stop criterion |
|---|---|---|---|---|
| B0005–18 | 24 °C | 2 A | 2.2–2.7 V | 1.4 Ah |
| B0029–32 | **43 °C** | **4 A** | 2.0–2.7 V | — |
| B0033–34 | 24 °C | **4 A** | 2.0–2.2 V | **1.6 Ah (20% fade)** |
| B0036 | 24 °C | 2 A | 2.7 V | **1.6 Ah** |
| B0045–48 | **4 °C** | **1 A** | 2.0–2.7 V | 1.4 Ah |

---

## 4. Per-battery inventory (measured)

Bundle 1 only — the clean group. Full table for all 34 batteries in `reports/audit_raw_nasa.txt`.

| Battery | Cycles | Charge | Discharge | Impedance | Ambient | Cap first | Cap last | Cap min | Fade % | Reaches 1.4 Ah | Regen steps |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B0005 | 616 | 170 | 168 | 278 | 24 | 1.856 | 1.325 | 1.287 | 28.6 | yes | 36 |
| B0006 | 616 | 170 | 168 | 278 | 24 | 2.035 | 1.186 | 1.154 | 41.7 | yes | 27 |
| B0007 | 616 | 170 | 168 | 278 | 24 | 1.891 | 1.432 | **1.4005** | 24.3 | **no** | 47 |
| B0018 | 319 | 134 | 132 | 53 | 24 | 1.855 | 1.341 | 1.341 | 27.7 | yes | 21 |

Note the cycle counts: 616 is *all operations*. The modelling index is the **discharge** count (168 / 132).

---

## 5. Findings

### Finding 1 — the bundle READMEs disagree with the files. Trust the files.

* READMEs state that discharge cycles carry `Current_charge` / `Voltage_charge`. The actual fields are **`Current_load` / `Voltage_load`**.
* READMEs write `Rectified_impedance`; the actual field is **`Rectified_Impedance`**.
* The README also describes `Capacity` as "capacity for discharge till 2.7V" even for cells whose documented cut-off is 2.2 V or 2.5 V. Treat the cut-off table in §3 as authoritative and verify empirically per cell.

**Action:** the parser keys off the observed field names, and asserts on them rather than assuming.

### Finding 2 — 38 files, 34 unique batteries

`B0025`, `B0026`, `B0027`, `B0028` appear in both bundle 2 and bundle 3 and are **byte-identical**
(SHA256 match on all four). Deduplicate on battery id at ingestion, preferring bundle 3.

### Finding 3 — initial capacity is not 2.0 Ah, so `Q_ref` is a real decision

| Battery | First measured capacity | SOH[0] if `Q_ref` = 2.0 Ah rated |
|---|---|---|
| B0005 | 1.8565 Ah | 92.8% |
| B0006 | 2.0353 Ah | **101.8%** |
| B0007 | 1.8911 Ah | 94.6% |
| B0018 | 1.8550 Ah | 92.8% |

Neither option is free:

* `Q_ref` = **2.0 Ah rated** → cells start at 92.8–101.8% SOH. B0006 starts *above* 100%.
* `Q_ref` = **first measured capacity** → every cell starts at exactly 100%, but the fixed 1.4 Ah EOL then lands at a *different* SOH% per cell (75.4% for B0005, 68.8% for B0006).

**Recommendation:** define **EOL on absolute capacity (1.4 Ah)**, matching NASA's own criterion, and
treat SOH% purely as a presentation normalisation. This keeps the RUL label consistent across cells
regardless of which `Q_ref` is used for display. Record the choice in the experiment manifest.

### Finding 4 — B0007 never reaches EOL (right-censored)

Minimum capacity is **1.4005 Ah**; zero discharge cycles at or below 1.4 Ah. It gets within 0.5 mAh
and stops. Consequences:

* The clean group yields **three** cells with a defined `N_EOL`, not four.
* B0007 must be excluded from RUL training or handled as explicitly censored. It stays usable for SOH.
* This was anticipated in the project plan; it is now confirmed quantitatively.

### Finding 5 — capacity regeneration is frequent and large

B0005: **36 of 167** cycle-to-cycle steps are upward (22%). Largest single jump **+0.0883 Ah = 4.76
percentage points of SOH**; mean upward step +0.0115 Ah. B0007 has 47 upward steps.

This is far above measurement noise and confirms §2.7 of the primer empirically. It means:

* SOH is genuinely non-monotonic — any monotonicity assumption is violated by the data.
* The EOL crossing rule **must** be defined explicitly (first sustained crossing over *k* cycles, not first touch).

### Finding 6 — bundles 3–6 have severely compromised capacity data

| Battery | n discharge | Cycles < 0.5 Ah | Zeros | Median cap |
|---|---|---|---|---|
| B0033 | 197 | 9 | 0 | 1.441 |
| B0042 | 112 | **47** | 1 | 1.407 |
| B0053 | 56 | 1 | 1 | 1.046 |

Symptoms across these groups: exact-zero capacities, first-cycle capacities as low as 0.068 Ah on a
~2 Ah cell, and trajectories where final capacity *exceeds* initial capacity by large margins
(B0033 "fades" −1822%, i.e. it starts broken and climbs).

These are aborted/partial runs interleaved with valid ones, not a decoding error. The NASA README for
B0045–48 concedes the point directly: *"there are several discharge runs where the capacity was very
low. Reasons for this have not been fully analyzed."*

### Finding 7 — ambient temperature varies *within* some batteries

`B0042`, `B0043`, `B0044` each report ambient temperatures of **4, 22 and 24 °C** across their own
cycles; `B0038`–`B0041` mix 24 °C with 44 °C or 4 °C. These are multi-condition experiments, not
fixed-temperature ones. Any per-battery "ambient temperature" attribute would be wrong — ambient must
be carried at **cycle level**.

### Finding 8 — condition coverage is better than the plan assumed

The project plan stated NASA offers "almost no thermal variation". True of bundle 1, **false of the
dataset as a whole**: it spans ambient 4 / 24 / 43 °C and discharge currents 1 / 2 / 4 A. If the
Finding 6 quality problems can be filtered rather than discarded, the Phase 13 scenario engine has
genuine empirical support for temperature and load scenarios instead of pure extrapolation.

---

## 6. Recommended scope

**Tier 1 — MVP (Phases 3–11).** B0005, B0006, B0007, B0018. Clean, single-temperature, documented,
complete. 168/168/168/132 discharge cycles. Accept that N=4 for SOH and N=3 for RUL, and let that
govern every generalisation claim.

**Tier 2 — condition variation (Phase 13, after QC).** B0029–32 (43 °C, 4 A), B0045–48 (4 °C, 1 A),
B0033/34/36 (24 °C, 4 A). Requires the Finding 6 filter and cycle-level ambient handling from
Finding 7. This is what gives the scenario engine real support.

**Tier 3 — excluded from modelling.** B0025–28 (only ~4% fade, barely aged), B0038–44, B0049–56.
Keep for QC methodology demonstration only.

---

## 7. Open questions for Phase 3

1. What distinguishes a valid discharge run from an aborted one in Tier 2? Candidate rule: discharge duration and final voltage consistent with the documented cut-off.
2. Does `cycle_id` count all operations or discharge operations only? (Plan requires this be fixed and documented — discharge-only is the natural index given `Capacity` lives there.)
3. How sparse is impedance relative to discharge, per battery, and what is the maximum backward-fill gap?
4. Is `Time` within a cycle strictly monotonic, and is the sampling rate constant?
5. Do the measured discharge cut-off voltages match the documented per-cell values in §3?
