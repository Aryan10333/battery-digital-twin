# Phase 3 Output Tables — Column Reference

What every column in `data/processed/` means, how it is computed, its units and real
range, and what to watch out for when using it. Ranges are measured from the Tier 1 build
(B0005, B0006, B0007, B0018).

| Table | Grain (one row = …) | Rows | Cols | Primary use |
|---|---|---|---|---|
| [`telemetry`](#1-telemetry) | one sample within one charge or discharge operation | 2,089,050 | 11 | curve-shape features (dQ/dV, ΔQ(V)) in Phase 6 |
| [`cycle_summary`](#2-cycle_summary) | one discharge cycle | 636 | 66 | **the modelling table** — features, targets, QC |
| [`impedance`](#3-impedance) | one EIS measurement | 887 | 6 | resistance trends; source of `re_ohm`/`rct_ohm` joins |
| [`regeneration_summary`](#4-regeneration_summary) | one battery | 4 | 7 | reporting capacity-regeneration magnitude |

**How the tables join.** All share `battery_id`. `op_index` is the operation's position in
the raw `.mat` file and is the only key that places charge, discharge and impedance
records on one timeline. `cycle_id` exists only for charge/discharge data.

```
telemetry.(battery_id, op_index)   -- many-to-one -->  one raw operation
telemetry.(battery_id, cycle_id)   -- many-to-one -->  cycle_summary.(battery_id, cycle_id)
impedance  --(backward as-of join on op_index)-->      cycle_summary.re_ohm / rct_ohm
```

> **How each table is built from the raw `.mat` files** — step by step, with runnable
> code and real output — is in [§0](#0-how-the-tables-are-computed-from-the-mat-files).
>
> **Known issues are flagged inline with ⚠.** Three were found while writing this
> reference and are summarised in [§5](#5-known-issues-found-while-documenting).

---

## 0. How the tables are computed from the `.mat` files

A step-by-step walkthrough of the build, using **B0005** as the running example.
Each step is simplified from the pipeline code in `nasa_loader.py`, `targets.py` and
`quality.py` for readability, but computes the same values — the snippets end by
checking their result against the built tables.

The code blocks run in order and share state. Run them from the repository root
with the project environment active. **All output below is real**, captured by
executing the code.

```
B0005.mat ──► operations (0.1–0.2)
   ├─ charge / discharge arrays ──► telemetry                  (0.3, 0.8)
   ├─ discharge aggregates ─┐
   ├─ charge aggregates ────┼─ pair (0.6) ─┐
   └─ impedance Re, Rct ────┴─► impedance ─┴─ as-of join (0.7) ─► cycle_summary
                                                  └─ targets (0.9), usage (0.10), QC (0.11)
cycle_summary.capacity_ah ──► regeneration_summary               (0.12)
```

### 0.1 Load a raw `.mat` file

Each battery is one MATLAB file holding a struct named after the battery. Inside is a
single flat array, `cycle`, of **operations** — charge, discharge and impedance mixed
together in the order they were run. `simplify_cells=True` turns MATLAB structs into
plain Python dicts, which removes a great deal of indexing noise.

```python
import numpy as np
import pandas as pd
from scipy.io import loadmat
from common.config import load_config, resolve

CFG = load_config()
path = resolve("raw_nasa") / CFG["tier1"]["bundle"] / "B0005.mat"
mat = loadmat(str(path), simplify_cells=True)

ops = np.atleast_1d(mat["B0005"]["cycle"])          # flat array of operations
types = pd.Series([str(o["type"]) for o in ops])

print("struct keys  :", [k for k in mat if not k.startswith("__")])
print("operations   :", len(ops))
print(types.value_counts().to_string())
print("first 12 ops :", " ".join(t[0].upper() for t in types[:12]))
print("fields of one operation:", list(ops[0].keys()))
```

Output:

```text
struct keys  : ['B0005']
operations   : 616
impedance    278
charge       170
discharge    168
first 12 ops : C D C D C D C D C D C D
fields of one operation: ['type', 'ambient_temperature', 'time', 'data']
```

616 operations, but only **168 are discharges** — and capacity is recorded only on
discharges. That is why every modelling table is indexed by discharge, not by operation.

### 0.2 Operation metadata and timestamps

Every operation carries a type, an ambient temperature and a start time. The start time
is a MATLAB *date vector* — `[year, month, day, hour, minute, second]` with fractional
seconds — which must be converted before any time arithmetic is possible.

The position in the array becomes `op_index`. It is the only key that puts charge,
discharge and impedance operations on one shared timeline, so every later join uses it.

```python
from datetime import datetime, timedelta

def matlab_time(vec):
    y, mo, d, h, mi, s = (float(x) for x in np.atleast_1d(vec)[:6])
    return datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=s)

meta = pd.DataFrame({
    "op_index":  range(len(ops)),
    "type":      types,
    "start":     [matlab_time(o["time"]) for o in ops],
    "ambient_c": [float(o["ambient_temperature"]) for o in ops],
})

print("raw date vector of op 0:", np.round(np.asarray(ops[0]["time"], float), 3))
print()
print(meta.head(6).to_string(index=False))
```

Output:

```text
raw date vector of op 0: [2.0080e+03 4.0000e+00 2.0000e+00 1.3000e+01 8.0000e+00 1.7921e+01]

 op_index      type                   start  ambient_c
        0    charge 2008-04-02 13:08:17.921       24.0
        1 discharge 2008-04-02 15:25:41.593       24.0
        2    charge 2008-04-02 16:37:51.984       24.0
        3 discharge 2008-04-02 19:43:48.406       24.0
        4    charge 2008-04-02 20:55:40.812       24.0
        5 discharge 2008-04-03 00:01:06.687       24.0
```

### 0.3 `telemetry` — flatten one operation into rows

A charge or discharge operation's `data` dict holds equal-length arrays: one time series
per sensor. Flattening means stacking those arrays side by side, one row per sample, and
stamping each row with the operation it came from.

The raw files name the external-device channels differently by operation type —
`Current_load`/`Voltage_load` on discharges, `Current_charge`/`Voltage_charge` on
charges. They are unified into `current_ext_a`/`voltage_ext_v`, disambiguated by
`cycle_type`. Impedance operations are skipped: their arrays are frequency spectra, not
time series.

```python
op = ops[1]
assert op["type"] == "discharge"
d = op["data"]

tel_op = pd.DataFrame({
    "battery_id":     "B0005",
    "op_index":       1,
    "t_rel_s":        np.asarray(d["Time"], float),
    "voltage_v":      np.asarray(d["Voltage_measured"], float),
    "current_a":      np.asarray(d["Current_measured"], float),
    "temperature_c":  np.asarray(d["Temperature_measured"], float),
    "current_ext_a":  np.asarray(d["Current_load"], float),   # "Current_charge" on charge ops
    "voltage_ext_v":  np.asarray(d["Voltage_load"], float),   # "Voltage_charge" on charge ops
    "cycle_type":     "discharge",
    "ambient_temp_c": float(op["ambient_temperature"]),
})

print("rows x cols:", tel_op.shape)
print(tel_op.head(4).drop(columns=["battery_id", "ambient_temp_c"]).round(3).to_string(index=False))
print()
print(f"current range: {tel_op.current_a.min():.3f} .. {tel_op.current_a.max():.3f} A"
      "  (discharge is negative)")

tel = pd.read_parquet(resolve("processed") / "telemetry.parquet",
                      filters=[("battery_id", "==", "B0005"), ("op_index", "==", 1)])
cols = ["t_rel_s", "voltage_v", "current_a", "temperature_c", "current_ext_a", "voltage_ext_v"]
print("matches telemetry table:", np.allclose(tel[cols].to_numpy(), tel_op[cols].to_numpy()))
```

Output:

```text
rows x cols: (197, 10)
 op_index  t_rel_s  voltage_v  current_a  temperature_c  current_ext_a  voltage_ext_v cycle_type
        1    0.000      4.191     -0.005         24.330         -0.001          0.000  discharge
        1   16.781      4.191     -0.001         24.326         -0.001          4.206  discharge
        1   35.703      3.975     -2.013         24.389         -1.998          3.062  discharge
        1   53.781      3.952     -2.014         24.545         -1.998          3.030  discharge

current range: -2.018 .. 0.001 A  (discharge is negative)
matches telemetry table: True
```

Repeat for every charge and discharge operation of every battery and concatenate: that is
the whole `telemetry` table. The only column added afterwards is `cycle_id` (§0.8).

### 0.4 `cycle_summary` — aggregate one discharge

The discharge record does not end when the cell reaches its cut-off: it keeps sampling
after the load is disconnected, while the voltage relaxes back up. So the first step is
an **active mask** — samples drawing more than 0.5 A. Every discharge aggregate is
computed inside that mask; the tail after it is summarised separately.

Energy and delivered charge are numerical integrals over time using the trapezoid rule,
divided by 3600 to convert seconds to hours.

```python
t = tel_op.t_rel_s.to_numpy()
v = tel_op.voltage_v.to_numpy()
i = tel_op.current_a.to_numpy()
T = tel_op.temperature_c.to_numpy()

active = np.abs(i) > CFG["ingestion"]["active_current_a"]        # 0.5 A
idx = np.where(active)[0]
first, last = idx[0], idx[-1]

summary = {
    "capacity_ah":          float(np.atleast_1d(d["Capacity"])[0]),   # read, not computed
    "record_duration_s":    t[-1] - t[0],
    "discharge_duration_s": t[last] - t[first],
    "energy_wh":            np.trapezoid(np.abs(v[active] * i[active]), t[active]) / 3600,
    "discharge_ah":         np.trapezoid(np.abs(i[active]), t[active]) / 3600,
    "v_min_v":              v[active].min(),
    "v_mean_v":             v[active].mean(),
    "temp_max_c":           T[active].max(),
    "temp_rise_c":          T[active].max() - T[active][0],
    "relax_duration_s":     t[-1] - t[last],                          # the post-load tail
    "v_recovery_v":         v[-1] - v[last],
}

print(f"samples: {len(t)} total, {active.sum()} active, load removed at index {last}")
print()
for k, val in summary.items():
    print(f"  {k:22s} {val:10.4f}")

cyc = pd.read_parquet(resolve("processed") / "cycle_summary.parquet")
row = cyc[(cyc.battery_id == "B0005") & (cyc.cycle_id == 1)].iloc[0]
print()
print("matches cycle_summary:", all(np.isclose(row[k], val) for k, val in summary.items()))
```

Output:

```text
samples: 197 total, 178 active, load removed at index 179

  capacity_ah                1.8565
  record_duration_s       3690.2340
  discharge_duration_s    3311.2340
  energy_wh                  6.5726
  discharge_ah               1.8512
  v_min_v                    2.6125
  v_mean_v                   3.5537
  temp_max_c                38.9041
  temp_rise_c               14.5150
  relax_duration_s         343.2970
  v_recovery_v               0.6647

matches cycle_summary: True
```

Note `capacity_ah` (1.8565, read from the file) and `discharge_ah` (integrated from
current) are computed independently and agree closely. Across all 636 cycles the median
disagreement is 7.6 mAh — a useful check that the active mask is right.

If a record ends exactly at load removal, `t[-1] == t[last]` and both tail columns come
out as 0. That is the source of known issue 2: those zeros mean "no tail recorded", not
"no recovery".

### 0.5 `cycle_summary` — aggregate the preceding charge

A charge runs in two phases: **constant current** (CC) at 1.5 A until the terminal
voltage reaches 4.2 V, then **constant voltage** (CV) while the current decays towards
20 mA. The split point is found from the current alone:

- *active* = current above 10 mA and **positive** (this also discards a single spurious
  −4 A sample found at the start of some charge records);
- *CC* = current above 1.0 A. The CC phase ends at the last CC sample; everything active
  after it is CV.

The example uses the charge before cycle 2. Cycle 1's charge is a partial charge (the
cells started the experiment partly charged), so it is not representative.

```python
ch = ops[2]["data"]
assert ops[2]["type"] == "charge" and ops[3]["type"] == "discharge"   # op 2 precedes cycle 2

t = np.asarray(ch["Time"], float)
v = np.asarray(ch["Voltage_measured"], float)
i = np.asarray(ch["Current_measured"], float)

active = i > CFG["ingestion"]["idle_current_a"]      # > 10 mA
cc     = i > CFG["ingestion"]["cc_current_a"]        # > 1.0 A

first_act = np.where(active)[0][0]
last_cc   = np.where(cc)[0][-1]
last_act  = np.where(active)[0][-1]

charge = {
    "charge_duration_s": t[last_act] - t[first_act],
    "cc_duration_s":     t[last_cc] - t[first_act],
    "cv_duration_s":     max(t[last_act] - t[last_cc], 0.0),
    "v_cc_to_cv_v":      v[last_cc],
    "charge_ah":         np.trapezoid(i[active], t[active]) / 3600,
}
charge["cc_cv_ratio"] = charge["cc_duration_s"] / charge["cv_duration_s"]

print(f"samples: {len(t)} | CC samples: {cc.sum()} | current at CC end: {i[last_cc]:.3f} A"
      f" | current at charge end: {i[last_act]:.4f} A")
print()
for k, val in charge.items():
    print(f"  {k:18s} {val:10.4f}")

row = cyc[(cyc.battery_id == "B0005") & (cyc.cycle_id == 2)].iloc[0]
print()
print("matches cycle_summary:", all(np.isclose(row[k], val) for k, val in charge.items()))
```

Output:

```text
samples: 940 | CC samples: 542 | current at CC end: 1.007 A | current at charge end: 0.0109 A

  charge_duration_s  10109.3280
  cc_duration_s       3704.8910
  cv_duration_s       6404.4370
  v_cc_to_cv_v           4.2130
  charge_ah              1.8821
  cc_cv_ratio            0.5785

matches cycle_summary: True
```

The CV phase here is about 1.7× longer than the CC phase. As the cell ages the CC phase
shrinks and the CV phase grows, which is why `cc_duration_s` and `cc_cv_ratio` are
strong health indicators.

### 0.6 `cycle_id` and charge/discharge pairing

`cycle_id` numbers the discharge operations 1, 2, 3, … in file order.

Each discharge then needs *its* charge. Pairing by a fixed stride ("the charge is one
position back") works for B0005 but not for every battery. Instead the pipeline uses
`pd.merge_asof(..., direction="backward")`: for each discharge, take the **most recent
charge at an earlier `op_index`**, whatever lies between them. The backward direction
also guarantees a discharge is never paired with a charge that happened after it.

B0018 is the case that makes this necessary:

```python
mat18 = loadmat(str(resolve("raw_nasa") / CFG["tier1"]["bundle"] / "B0018.mat"), simplify_cells=True)
ops18 = np.atleast_1d(mat18["B0018"]["cycle"])
meta18 = pd.DataFrame({"op_index": range(len(ops18)),
                       "type": [str(o["type"]) for o in ops18]})

print("B0005 first 8 ops:", " ".join(t[0].upper() for t in meta.type[:8]))
print("B0018 first 8 ops:", " ".join(t[0].upper() for t in meta18.type[:8]))
print()

disc = meta18.loc[meta18.type == "discharge", ["op_index"]].reset_index(drop=True)
disc.insert(0, "cycle_id", np.arange(1, len(disc) + 1))
chg = meta18.loc[meta18.type == "charge", ["op_index"]].assign(paired_charge_op=lambda x: x.op_index)

paired = pd.merge_asof(disc, chg, on="op_index", direction="backward")
paired["ops_between"] = paired.op_index - paired.paired_charge_op
print("B0018 pairing:")
print(paired.head(5).to_string(index=False))
```

Output:

```text
B0005 first 8 ops: C D C D C D C D
B0018 first 8 ops: C I D I C I D I

B0018 pairing:
 cycle_id  op_index  paired_charge_op  ops_between
        1         2                 0            2
        2         6                 4            2
        3        10                 8            2
        4        14                12            2
        5        17                16            1
```

For its first four cycles B0018 places an impedance sweep between each charge and its
discharge, so the charge is **two** positions back. At cycle 5 the pattern changes and it
is one position back. The stride is not even constant *within* one battery, so no fixed
stride rule can work — the as-of join handles both cases without special-casing.

### 0.7 `impedance`, and joining it onto cycles

Impedance operations contribute two fitted scalars per sweep, `Re` and `Rct`. Those
become the `impedance` table.

They are then attached to each cycle with the same backward as-of join: a cycle receives
the **latest sweep taken before it**, never an interpolation towards a later one.
`impedance_age_ops` records how many operations old that sweep is — it is always ≥ 1,
which is the leakage guarantee.

```python
imp = pd.DataFrame([
    {"op_index": k,
     "re_ohm":  float(np.real(np.atleast_1d(o["data"]["Re"])[0])),
     "rct_ohm": float(np.real(np.atleast_1d(o["data"]["Rct"])[0]))}
    for k, o in enumerate(ops) if o["type"] == "impedance"
])
print(f"B0005 impedance sweeps: {len(imp)} | first at op_index {imp.op_index.min()}")
print()

disc5 = meta.loc[meta.type == "discharge", ["op_index"]].reset_index(drop=True)
disc5.insert(0, "cycle_id", np.arange(1, len(disc5) + 1))

joined = pd.merge_asof(disc5, imp.assign(sweep_op=imp.op_index), on="op_index", direction="backward")
joined["impedance_age_ops"] = joined.op_index - joined.sweep_op
window = joined[(joined.cycle_id >= 18) & (joined.cycle_id <= 23)]
print(window.round(4).to_string(index=False))

ref = cyc[cyc.battery_id == "B0005"].set_index("cycle_id").loc[window.cycle_id]
same = np.allclose(ref.rct_ohm.fillna(-1), window.rct_ohm.fillna(-1).to_numpy()) and \
       np.allclose(ref.impedance_age_ops.fillna(-1), window.impedance_age_ops.fillna(-1).to_numpy())
print()
print("matches cycle_summary:", same)
```

Output:

```text
B0005 impedance sweeps: 278 | first at op_index 40

 cycle_id  op_index  re_ohm  rct_ohm  sweep_op  impedance_age_ops
       18        36     NaN      NaN       NaN                NaN
       19        38     NaN      NaN       NaN                NaN
       20        41  0.0447   0.0695      40.0                1.0
       21        45  0.0448   0.0680      44.0                1.0
       22        49  0.0451   0.0685      48.0                1.0
       23        53  0.0442   0.0680      52.0                1.0

matches cycle_summary: True
```

Cycles 18 and 19 get `NaN`: B0005's first sweep comes after them, and a backward join
has nothing earlier to carry. That is where the 57 leading impedance nulls come from
(19 cycles × B0005, B0006, B0007).

### 0.8 `telemetry.cycle_id` — attributing samples to cycles

Telemetry rows need a `cycle_id` so a cycle's curves can be pulled out later. A discharge
sample belongs to its own discharge. A charge sample is attributed to the discharge that
**follows** it — the charge prepares the cell for that discharge. That is a *forward*
as-of join on `op_index`.

```python
lut = disc5[["op_index", "cycle_id"]]
chg_dis_ops = meta.loc[meta.type != "impedance", ["op_index", "type"]]

attributed = pd.merge_asof(chg_dis_ops, lut, on="op_index", direction="forward")
print(attributed.head(6).to_string(index=False))
print("...")
print(attributed.tail(3).to_string(index=False))
```

Output:

```text
 op_index      type  cycle_id
        0    charge       1.0
        1 discharge       1.0
        2    charge       2.0
        3 discharge       2.0
        4    charge       3.0
        5 discharge       3.0
...
 op_index      type  cycle_id
      612    charge     168.0
      613 discharge     168.0
      615    charge       NaN
```

The final row is a charge with no discharge after it, so it gets no `cycle_id` — the
source of the 15 null `cycle_id` values in `telemetry` (a 5-sample charge stub at
`op_index` 615 in each of B0005, B0006 and B0007).

### 0.9 Targets — SOH, EOL, RUL

Targets are computed per battery from the capacity sequence alone.

- **SOH** two ways: against the cell's **first** measured capacity (a past value, so
  leakage-safe) and against the 2.0 Ah rated value.
- **EOL** is the first cycle from which capacity stays at or below **1.4 Ah for 5
  consecutive cycles**. Sustained, because capacity regeneration can take a cell below
  the threshold and back above it.
- **RUL** = EOL cycle − current cycle. **Life fraction** = cycle / EOL cycle.

```python
g = cyc[cyc.battery_id == "B0005"].sort_values("cycle_id")[["cycle_id", "capacity_ah"]].copy()

q_first = g.capacity_ah.iloc[0]
g["soh_pct_self"]  = g.capacity_ah / q_first * 100
g["soh_pct_rated"] = g.capacity_ah / CFG["targets"]["rated_capacity_ah"] * 100

def find_eol(cap, threshold=1.4, sustain=5):
    c = cap.to_numpy()
    for k in range(len(c)):
        if np.all(c[k:k + sustain] <= threshold):
            return k + 1                                  # cycle_id is 1-based
    return None

eol = find_eol(g.capacity_ah)
g["rul_cycles"]    = eol - g.cycle_id
g["life_fraction"] = g.cycle_id / eol

print(f"q_ref_self_ah = {q_first:.4f}   eol_cycle = {eol}")
print()
print(g[(g.cycle_id >= 121) & (g.cycle_id <= 128)].round(3).to_string(index=False))
print()
b7 = cyc[cyc.battery_id == "B0007"].sort_values("cycle_id").capacity_ah
print(f"B0007: min capacity {b7.min():.4f} Ah -> find_eol returns {find_eol(b7)}  (censored, RUL = NaN)")

ref = cyc[cyc.battery_id == "B0005"].sort_values("cycle_id")
print("matches cycle_summary:",
      all(np.allclose(ref[c].to_numpy(), g[c].to_numpy())
          for c in ["soh_pct_self", "soh_pct_rated", "rul_cycles", "life_fraction"]))
```

Output:

```text
q_ref_self_ah = 1.8565   eol_cycle = 125

 cycle_id  capacity_ah  soh_pct_self  soh_pct_rated  rul_cycles  life_fraction
      121        1.438        77.472         71.913           4          0.968
      122        1.417        76.346         70.868           3          0.976
      123        1.407        75.787         70.349           2          0.984
      124        1.401        75.476         70.060           1          0.992
      125        1.397        75.234         69.835           0          1.000
      126        1.391        74.942         69.564          -1          1.008
      127        1.386        74.669         69.311          -2          1.016
      128        1.380        74.357         69.022          -3          1.024

B0007: min capacity 1.4005 Ah -> find_eol returns None  (censored, RUL = NaN)
matches cycle_summary: True
```

Cycle 124 is still above 1.4 Ah; from cycle 125 onward every cycle is at or below it, so
EOL = 125 and RUL counts down to 0 there, then goes negative. `rul_valid` keeps only
rows where RUL exists and is ≥ 0 — the rows usable for RUL training.

Notice `soh_pct_self` is **75.2%** at EOL, not 70%. EOL is defined on absolute capacity
(1.4 Ah), and B0005 started at 1.8565 Ah rather than the 2.0 Ah rated value — so the
same physical threshold lands at a different SOH percentage for every cell.

### 0.10 Cumulative usage

Running totals turn a per-cycle table into an aging clock that does not depend on cycle
count. All are backward-looking: each row uses only its own and earlier cycles.

```python
g = cyc[cyc.battery_id == "B0005"].sort_values("cycle_id")

usage = pd.DataFrame({
    "cycle_id":              g.cycle_id,
    "discharge_ah":          g.discharge_ah,
    "coulomb_throughput_ah": g.discharge_ah.cumsum(),
    "elapsed_days":          (g.cycle_start_time - g.cycle_start_time.iloc[0]).dt.total_seconds() / 86400,
    "rest_before_h":         g.cycle_start_time.diff().dt.total_seconds() / 3600,
})
print(usage.head(4).round(3).to_string(index=False))
print("matches cycle_summary:",
      np.allclose(usage.coulomb_throughput_ah, g.coulomb_throughput_ah) and
      np.allclose(usage.rest_before_h.fillna(-1), g.rest_before_h.fillna(-1)))
```

Output:

```text
 cycle_id  discharge_ah  coulomb_throughput_ah  elapsed_days  rest_before_h
        1         1.851                  1.851         0.000            NaN
        2         1.841                  3.692         0.179          4.302
        3         1.830                  5.522         0.358          4.288
        4         1.830                  7.352         0.535          4.259
matches cycle_summary: True
```

⚠ `rest_before_h` is computed as the gap between consecutive discharge **start** times.
That gap contains the previous discharge and the whole charge, which is why it is ~4.3 h
here rather than a true idle time. This is known issue 3.

### 0.11 Quality flags

Flags are simple boolean rules evaluated on the aggregated columns. Rows are flagged,
never removed.

```python
q = cyc[cyc.battery_id == "B0005"].copy()
nominal = CFG["cutoff_voltage_v"]["B0005"]                       # 2.7 V

q["qc_charge_aborted"]  = (q.cc_duration_s < 60) | (q.charge_ah < 0.1)
q["qc_charge_partial"]  = (q.charge_ah < 0.8 * q.capacity_ah) & ~q.qc_charge_aborted
q["qc_cutoff_mismatch"] = (q.v_min_v - nominal).abs() > 0.25

cols = ["cycle_id", "cc_duration_s", "charge_ah", "capacity_ah", "v_min_v",
        "qc_charge_aborted", "qc_charge_partial", "qc_cutoff_mismatch"]
print(q[q.cycle_id.isin([1, 2, 31, 32])][cols].round(3).to_string(index=False))

ref = cyc[cyc.battery_id == "B0005"]
print("matches cycle_summary:",
      all((ref[c].to_numpy() == q[c].to_numpy()).all()
          for c in ["qc_charge_aborted", "qc_charge_partial", "qc_cutoff_mismatch"]))
```

Output:

```text
 cycle_id  cc_duration_s  charge_ah  capacity_ah  v_min_v  qc_charge_aborted  qc_charge_partial  qc_cutoff_mismatch
        1       1072.953      0.780        1.856    2.612              False               True               False
        2       3704.891      1.882        1.846    2.587              False              False               False
       31          9.031      0.007        1.852    2.629               True              False               False
       32       3630.313      1.849        1.831    2.631              False              False               False
matches cycle_summary: True
```

- **Cycle 1:** 0.78 Ah charged against 1.86 Ah then discharged — a partial charge.
- **Cycle 31:** a 9-second, 0.007 Ah charge — aborted. B0006 and B0007 fail at the same
  cycle, so it was one testbed interruption.
- `qc_flags` joins the names of the flags that fired (or `ok`); `qc_any` is true if any did.

### 0.12 `regeneration_summary`

Take each battery's capacity sequence, difference consecutive cycles, and count the
steps that go **up**.

```python
cap = cyc[cyc.battery_id == "B0005"].sort_values("cycle_id").capacity_ah.to_numpy()
steps = np.diff(cap)
up = steps[steps > 0]

regen = {
    "n_steps":          len(steps),
    "n_regen":          len(up),
    "regen_pct":        round(float(len(up) / len(steps) * 100), 1),
    "regen_max_ah":     round(float(up.max()), 4),
    "regen_mean_ah":    round(float(up.mean()), 4),
    "regen_max_pp_soh": round(float(up.max() / cap[0] * 100), 2),
}
print(regen)

ref = pd.read_parquet(resolve("processed") / "regeneration_summary.parquet").set_index("battery_id").loc["B0005"]
print("matches regeneration_summary:", all(np.isclose(ref[k], val) for k, val in regen.items()))
```

Output:

```text
{'n_steps': 167, 'n_regen': 36, 'regen_pct': 21.6, 'regen_max_ah': 0.0883, 'regen_mean_ah': 0.0115, 'regen_max_pp_soh': 4.76}
matches regeneration_summary: True
```

### 0.13 Assemble and write

The pipeline performs §0.1–0.12 for all four batteries, concatenates the per-battery
frames, and writes Parquet. The whole thing is one command:

```bash
python -m phase3_pipeline.build_dataset
```

Output:

```text
Building canonical dataset (tier1)
  B0005:  168 discharge cycles,  591458 telemetry rows,  278 impedance measurements
  B0006:  168 discharge cycles,  591458 telemetry rows,  278 impedance measurements
  B0007:  168 discharge cycles,  591458 telemetry rows,  278 impedance measurements
  B0018:  132 discharge cycles,  314676 telemetry rows,   53 impedance measurements
  wrote telemetry.parquet  rows=2089050 cols=11
  wrote cycle_summary.parquet  rows=    636 cols=66
  wrote impedance.parquet  rows=    887 cols=6
  wrote regeneration_summary.parquet  rows=      4 cols=7
  wrote phase3_pipeline/outputs/build_summary.json
done.
```

Rebuilding from scratch reproduces all four Parquet files byte-for-byte.

---

## 1. `telemetry`

Raw time series for every charge and discharge operation, flattened to one row per
sample. Impedance operations are not included — their spectra are a different shape and
live in `impedance`.

**Size profile:** 1,903,329 charge samples vs 185,721 discharge samples (≈10:1). A 2 A
discharge lasts about an hour; a charge's CV tail can run for hours while the current
decays to 20 mA, and is sampled more densely.

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `battery_id` | str | — | 4 values | Cell identifier (`B0005` …) |
| `op_index` | int | — | 0 – 615 | Position of the parent operation in the raw file. Groups samples into operations. |
| `t_rel_s` | float | s | 0 – 10,820 | Time since the start of *this operation*. Resets to 0 for each operation; strictly increasing within one. |
| `voltage_v` | float | V | −0.001 – 8.39 ⚠ | Battery terminal voltage (`Voltage_measured`). |
| `current_a` | float | A | −4.51 – 1.54 | Battery current (`Current_measured`). **Sign convention: charge positive, discharge negative.** |
| `temperature_c` | float | °C | 21.8 – 42.3 | Cell surface temperature (`Temperature_measured`). |
| `current_ext_a` | float | A | −4.51 – 2.00 | Current measured **at the external device**: the charger for charge rows (`Current_charge`), the load for discharge rows (`Current_load`). |
| `voltage_ext_v` | float | V | −0.007 – 5.00 | Voltage at the external device, same charger/load split. |
| `cycle_type` | str | — | `charge`, `discharge` | Operation type. |
| `ambient_temp_c` | float | °C | 24 | Chamber ambient temperature. Constant for Tier 1; carried per row because it varies *within* some Tier 2 batteries. |
| `cycle_id` | float | — | 1 – 168, 15 nulls | The discharge cycle this sample belongs to. A **discharge** sample gets its own cycle; a **charge** sample is attributed to the discharge that *follows* it. |

### Notes

- **Why `_ext` columns exist.** The raw files name these fields differently for charge
  and discharge. Rather than four mostly-empty columns, they are unified and
  disambiguated by `cycle_type`. Always filter by `cycle_type` before interpreting them.
- **`cycle_id` is float, not int,** because it has nulls: 15 samples from a short charge
  stub at `op_index` 615 in B0005/6/7 — a charge that begins after the final discharge,
  so there is no following cycle to attribute it to. Safe to drop.
- **⚠ 17 anomalous samples, all in charge records.** Two NaN rows; a first sample of
  8.39 V at `op_index` 84 in B0005 (8.08 V in B0006); and the op-615 stubs reading ~4.98 V
  or slightly negative. These look like start-of-record transients. Charge-phase features
  are computed from samples with positive current, which excludes the t=0 transients, so
  the features themselves are most likely unaffected — but see issue 1 in §5: the QC
  columns meant to catch this cannot see charge records.

---

## 2. `cycle_summary`

The modelling table. One row per **discharge** operation, because capacity — the SOH
target — is recorded once per discharge. Each row also carries the aggregates of the
charge that preceded it, the latest impedance measurement available at that point,
quality flags, and targets.

Row counts: B0005 168, B0006 168, B0007 168, B0018 132. Key: `(battery_id, cycle_id)`.

Columns are grouped below in the order they appear.

### 2.1 Identity and time

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `battery_id` | str | — | 4 values | Cell identifier. |
| `cycle_id` | int | — | 1 – 168 | **Discharge counter**, 1-based and contiguous per battery. This is the aging axis for all modelling. It is *not* the raw operation count — B0005 has 616 operations but 168 cycles. |
| `op_index` | int | — | 1 – 613 | Raw-file position of this discharge. Used for the backward-looking joins. |
| `cycle_start_time` | datetime | — | 2008-04-02 → 2008-08-20 | Wall-clock start of the discharge, converted from the MATLAB date vector. |
| `ambient_temp_c` | float | °C | 24 | Chamber temperature. |

### 2.2 Discharge measurements

All computed over the **active segment only** — samples with |current| > 0.5 A. Each raw
discharge record continues after the load is removed; including that tail would inflate
durations by ~10% and report a rest voltage as the end-of-discharge voltage.

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `capacity_ah` | float | Ah | 1.154 – 2.035 | **Measured discharge capacity** — the `Capacity` scalar from the raw file. The basis of every target. |
| `n_samples` | int | — | 179 – 371 | Samples in the whole record, tail included. |
| `record_duration_s` | float | s | 2,743 – 3,690 | Full record length, tail included. **Not** the discharge duration. |
| `discharge_duration_s` | float | s | 2,089 – 3,655 | Time under load. Falls as the cell ages — less charge to deliver at a fixed 2 A. |
| `n_active_samples` | int | — | 170 – 369 | Samples inside the active segment. |
| `energy_wh` | float | Wh | 3.91 – 7.24 | Delivered energy, ∫\|V·I\| dt over the active segment. Falls slightly faster than capacity (B0006: energy to 55.7% of initial vs capacity to 58.3%), because aged cells also deliver their charge at lower voltage. |
| `discharge_ah` | float | Ah | 1.167 – 2.041 | Delivered charge, ∫\|I\| dt over the active segment. An independent recomputation of capacity. |
| `v_min_v` | float | V | 1.737 – 2.700 | Lowest voltage under load. Should sit at the documented cut-off; see `qc_cutoff_mismatch`. |
| `v_max_v` | float | V | 3.901 – 4.035 | Highest voltage under load — just after the load is applied, below 4.2 V because of the immediate IR drop. |
| `v_mean_v` | float | V | 3.345 – 3.575 | Mean voltage under load. Lower means a larger internal voltage drop. |
| `v_end_active_v` | float | V | 1.737 – 2.700 | Voltage at the last loaded sample. In practice identical to `v_min_v`. |
| `i_mean_a` | float | A | −2.013 – −1.989 | Mean load current. A near-constant −2.0 A confirms the protocol was followed. |
| `temp_max_c` | float | °C | 36.3 – 42.1 | Peak cell temperature under load. |
| `temp_mean_c` | float | °C | 29.9 – 34.2 | Mean temperature under load. |
| `temp_rise_c` | float | °C | 12.9 – 17.6 | Peak minus temperature at load start. Joule heating scales with internal resistance, so this tends to rise with age. |

**Validation.** `discharge_ah` is computed independently by integrating current, while
`capacity_ah` comes straight from the file. They agree to a median of **7.6 mAh**
(max 18.6 mAh, correlation 0.9993). That agreement is evidence both that the
active-segment masking is right and that the integration is correct.

### 2.3 Relaxation tail

What happens after the load is disconnected. Kept deliberately: voltage recovery depends
on internal resistance, so it is a health signal rather than trailing junk.

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `relax_duration_s` | float | s | 0 – 685 | Time from the last loaded sample to the end of the record. |
| `v_recovery_v` | float | V | 0 – 1.41 | Voltage rise over the tail: final voltage minus voltage at load removal. Grows with age (≈0.67 → 0.94 V for B0005). |

> **⚠ Zero means "not recorded", not "no recovery".** In 54 cycles (25 in B0006, 29 in
> B0007) the record ends exactly at load removal, so there is no tail to measure. Both
> columns read `0` there. A value of 0 V recovery is physically implausible and will bias
> any model that consumes it. Treat `relax_duration_s == 0` as missing. See issue 2 in §5.

### 2.4 Paired charge (the charge preceding this discharge)

Joined positionally: each discharge takes the most recent charge operation earlier in the
file. Positional rather than stride-based, because the operation pattern differs between
batteries.

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `charge_start_time` | datetime | — | — | Wall-clock start of the paired charge. |
| `charge_n_samples` | int | — | 381 – 3,900 | Samples in the charge record. |
| `charge_duration_s` | float | s | 50 – 10,810 | Time with positive current above 10 mA — the whole active charge, CC + CV. |
| `charge_record_duration_s` | float | s | 1,674 – 10,820 | Full charge record length. |
| `cc_duration_s` | float | s | 9 – 4,093 | **Constant-current phase duration**: from first active sample to the last sample above 1.0 A. **One of the strongest health indicators** — shortens with age because an aged cell stores less charge and its higher resistance pushes terminal voltage to 4.2 V sooner. Correlates +0.75 to +0.86 with capacity. |
| `cv_duration_s` | float | s | 41 – 8,962 | **Constant-voltage phase duration**: from end of CC to the last active sample. Lengthens with age to compensate for the shorter CC phase. |
| `v_cc_to_cv_v` | float | V | 4.197 – 4.294 | Terminal voltage at the CC→CV transition. Should be ≈4.2 V; mild overshoots above 4.21 V occur in 34 cycles. |
| `charge_ah` | float | Ah | 0.007 – 2.066 | Charge delivered, ∫I dt over the active charge. |
| `charge_temp_max_c` | float | °C | 23.1 – 36.1 | Peak temperature during charge. Much cooler than discharge — lower current. |
| `cc_cv_ratio` | float | — | 0.005 – 0.738 | `cc_duration_s / cv_duration_s`. A dimensionless version of the CC/CV shift, less sensitive to absolute timing than either duration alone. |

**Before using any charge column, filter out `qc_charge_aborted` and `qc_charge_partial`.**
The minima above (9 s CC, 0.007 Ah) come from those records, and at cycle 1 a short CC
phase means "started partly charged", not "degraded".

### 2.5 Impedance (backward-joined)

Joined from `impedance` with an as-of join on `op_index`, **backward only**: each cycle
takes the most recent EIS measurement taken *before* it. Interpolating between
measurements would pull a future value into the row.

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `re_ohm` | float | Ω | 0.036 – 0.079, 57 nulls | Electrolyte resistance — the ohmic part of the impedance. |
| `rct_ohm` | float | Ω | 0.060 – 0.107, 57 nulls | Charge-transfer resistance — resistance to the electrode reaction. Rises strongly with degradation: −0.94 to −0.98 correlation with capacity for B0005/6/7, but only −0.24 for B0018. |
| `impedance_time` | datetime | — | 57 nulls | When the joined measurement was taken. |
| `impedance_age_ops` | float | ops | 1 – 13, 57 nulls | How stale the joined value is, in raw operations. **Always ≥ 1** — that is the leakage guarantee, and it is unit-tested. Typically 1–5; up to 13 for B0018. |

**The 57 nulls are the first 19 cycles of B0005, B0006 and B0007,** which precede their
first EIS measurement. Backward-fill cannot fill a leading gap. Phase 6 must choose: drop
those cycles, impute with a missingness indicator, or restrict impedance features to
cycle ≥ 20.

### 2.6 Quality flags

Problems are recorded, never deleted. Downstream code decides what to exclude, visibly.

| Column | Type | True count | Condition | Meaning |
|---|---|---|---|---|
| `qc_short_record` | bool | 0 | `n_samples < 50` | Truncated discharge record. |
| `qc_no_active_segment` | bool | 0 | no sample with \|I\| > 0.5 A | Discharge never drew load. |
| `qc_capacity_missing` | bool | 0 | `capacity_ah` is NaN | No target. |
| `qc_capacity_implausible` | bool | 0 | outside 0.1 – 2.5 Ah | Physically impossible capacity for this cell. |
| `qc_no_charge_pair` | bool | 0 | no preceding charge found | Charge columns unavailable. |
| `qc_charge_aborted` | bool | **5** | CC < 60 s **or** charge < 0.1 Ah | Charge record effectively empty. B0005/6/7 cycle 31 (one shared testbed interruption); B0018 cycles 46 and 56. |
| `qc_charge_partial` | bool | **4** | `charge_ah < 0.8 × capacity_ah` | Charge delivered far less than the next discharge extracted. Cycle 1 of every battery — the cells began partly charged. |
| `cutoff_nominal_v` | float | — | — | Documented cut-off for this battery (2.7 / 2.5 / 2.2 / 2.5 V). Reference value, not a flag. |
| `qc_cutoff_mismatch` | bool | **21** | \|`v_min_v` − nominal\| > 0.25 V | Discharged far past the documented cut-off. B0006 cycles 10–26, B0007 cycles 4–29. **Unresolved:** a deeper discharge extracts more charge, so capacity here may be overstated. |
| `qc_flags` | str | — | — | Comma-joined names of the flags that fired, e.g. `charge_partial`, or `ok`. Values present: `ok` (606), `cutoff_mismatch` (21), `charge_aborted` (5), `charge_partial` (4). |
| `qc_any` | bool | **30** | any flag fired | Convenience filter. `~qc_any` gives the 606 clean cycles. |

**Telemetry QC counts** (merged from a per-operation telemetry check):

| Column | Type | Range | Meaning |
|---|---|---|---|
| `n_samples_tel` | int | 179 – 371 | Telemetry samples in this operation (matches `n_samples`). |
| `n_voltage_out_of_range` | int | 0 | Samples outside 0 – 4.3 V. |
| `n_current_out_of_range` | int | 0 | Samples outside ±5 A. |
| `n_temperature_out_of_range` | int | 0 | Samples outside 0 – 80 °C. |
| `n_nan` | int | 0 | Samples with a missing V, I or T. |
| `time_not_monotonic` | bool | 0 True | Time failed to increase within the operation. |

> **⚠ These six columns only describe the discharge record.** They are joined on the
> discharge's own `op_index`, so anomalies in the *paired charge* record never reach this
> table. All read zero — yet `telemetry` contains 17 out-of-range or NaN samples, every
> one in a charge record. A zero here does not mean the row's charge data is clean. See
> issue 1 in §5.

### 2.7 Targets and cumulative usage

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `soh_pct_self` | float | % | 56.7 – 100 | SOH against **this cell's first measured capacity**. Every cell starts at exactly 100. Leakage-safe, because the reference is a past value. |
| `soh_pct_rated` | float | % | 57.7 – 101.8 | SOH against the **2.0 Ah rated capacity**. Cells start between 92.8 and 101.8 — B0006 starts above 100. |
| `q_ref_self_ah` | float | Ah | 1.855 – 2.035 | The reference used for `soh_pct_self`: first measured capacity, constant per battery. |
| `coulomb_throughput_ah` | float | Ah | 1.85 – 278.9 | Cumulative charge delivered through this cycle. A usage-based aging clock, alternative to cycle count. |
| `energy_throughput_wh` | float | Wh | 6.6 – 983.7 | Cumulative energy delivered. |
| `elapsed_days` | float | days | 0 – 55.2 | Wall-clock time since this battery's first discharge. Relevant to calendar aging. |
| `rest_before_h` ⚠ | float | h | 3.48 – 310.4, 4 nulls | Time between the **start** of the previous discharge and the start of this one. **Despite the name, this is not rest** — see below. Null for cycle 1 of each battery. |
| `eol_cycle` | float | cycle | 97 – 125, 168 nulls | End-of-life cycle for the battery: first cycle from which capacity stays ≤ 1.4 Ah for 5 consecutive cycles. Constant per battery. Null for B0007. |
| `eol_first_touch_cycle` | float | cycle | 97 – 125, 168 nulls | First cycle with capacity ≤ 1.4 Ah, without the sustained requirement. Kept for comparison — it equals `eol_cycle` for all three cells here. |
| `is_censored` | bool | — | 168 True | Battery never reached EOL (B0007, all rows). Its true RUL is unknown. |
| `rul_cycles` | float | cycles | −59 – 124, 168 nulls | `eol_cycle − cycle_id`. Negative after EOL. Null for censored B0007 — never an invented value. |
| `rul_valid` | bool | — | 331 True | `rul_cycles` is present **and ≥ 0**. **Use this as the RUL training filter.** |
| `life_fraction` | float | — | 0.008 – 1.54, 168 nulls | `cycle_id / eol_cycle`. 1.0 is EOL; above 1.0 is post-EOL. |

**RUL row accounting:**

| Rows | Count |
|---|---|
| Total | 636 |
| Censored (B0007, no label) | 168 |
| Have `rul_cycles` | 468 |
| — of which post-EOL (`rul_cycles < 0`) | 137 |
| **Usable for RUL training (`rul_valid`)** | **331** |

Those 331 rows come from only **three** batteries. That is the real sample size for RUL.

**⚠ About `rest_before_h`.** It measures discharge-start to discharge-start, so it
includes the previous discharge (~1 h) and the whole intervening charge (~3 h). That is
why its minimum is 3.48 h and its median 4.87 h — it is dominated by how long a cycle
takes, not by idle time. The quantity capacity regeneration actually depends on is
**idle time before the discharge**. A cleaner measure, computable from existing
columns, is the gap between the end of the paired charge and the start of this
discharge: median **0.55 h**, range 0.02 – 34 h.

This matters beyond naming: the Phase 3 findings notebook tests "does a longer rest
precede a capacity jump?" using `rest_before_h`, and reports only a weak relationship.
That test used the wrong quantity and should be re-run. See issue 3 in §5.

---

## 3. `impedance`

One row per EIS measurement. Only the fitted scalar parameters are kept; the full
complex spectra (`Battery_impedance`, `Rectified_Impedance`) remain in the raw files.

Rows: B0005 278, B0006 278, B0007 278, B0018 53.

| Column | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `battery_id` | str | — | 4 values | Cell identifier. |
| `op_index` | int | — | 1 – 614 | Raw-file position. The as-of join key into `cycle_summary`. |
| `impedance_time` | datetime | — | 2008-04-18 → 2008-08-20 | When the sweep started. The earliest is 16 days after the first discharge — the leading impedance gap. |
| `ambient_temp_c` | float | °C | 24 | Chamber temperature. |
| `re_ohm` | float | Ω | 0.036 – 0.079 | **Electrolyte resistance** (`Re`), from the high-frequency intercept of the spectrum. Rises as electrolyte degrades. |
| `rct_ohm` | float | Ω | 0.060 – 0.107 | **Charge-transfer resistance** (`Rct`), from the semicircle in the spectrum. Rises with SEI growth and interface degradation; the stronger health signal of the two. |

### Notes

- **Use this table, not `cycle_summary`, to study impedance trends.** `cycle_summary`
  repeats the same measurement across every cycle until the next sweep, so plotting it
  there shows artificial flat steps and overweights stale values.
- **Density differs sharply.** B0005/6/7 average 1.65 sweeps per discharge cycle; B0018
  has 53 sweeps across 132 cycles — one per 2.5 cycles. B0018's Rct also starts high (~0.095 Ω) and *falls* early,
  unlike the other three — unresolved whether that is real behaviour or a defective
  record.
- **These are model-fitted values, not direct measurements.** Treat small changes as
  noisy.

---

## 4. `regeneration_summary`

One row per battery, quantifying capacity regeneration — cycles where measured capacity
**rises** above the previous cycle. Regeneration is real physics (relaxation during
idle periods), not a defect, so it is counted rather than removed.

| Column | Type | Unit | Values (B0005 / B0006 / B0007 / B0018) | Meaning |
|---|---|---|---|---|
| `battery_id` | str | — | — | Cell identifier. |
| `n_steps` | int | — | 167 / 167 / 167 / 131 | Cycle-to-cycle transitions (cycles − 1). |
| `n_regen` | int | — | 36 / 27 / 47 / 21 | Transitions where capacity increased. |
| `regen_pct` | float | % | 21.6 / 16.2 / 28.1 / 16.0 | Share of transitions that went up. |
| `regen_max_ah` | float | Ah | 0.088 / 0.152 / 0.098 / 0.131 | Largest single increase. |
| `regen_mean_ah` | float | Ah | 0.012 / 0.029 / 0.008 / 0.032 | Mean size of an increase. |
| `regen_max_pp_soh` | float | pp | 4.76 / 7.46 / 5.19 / 7.08 | Largest increase in percentage points of self-referenced SOH. |

### Notes

- **Every increase counts, however small.** Tiny upward steps from measurement noise are
  included, so `n_regen` and `regen_pct` somewhat overstate *meaningful* regeneration.
  `regen_max_pp_soh` is the robust indicator — jumps of 5–7.5 pp are far above noise.
- **Computed over all cycles,** QC-flagged ones included.
- **Summary only.** For per-cycle analysis, compute `capacity_ah.diff()` on
  `cycle_summary`.

---

## 5. Known issues found while documenting

Profiling each column against its intended meaning turned up three problems. None
affects the capacity values, the targets, or the leakage guarantees. Two could mislead a
model or an analysis if used as-is.

| # | Issue | Affected columns | Effect | Suggested fix |
|---|---|---|---|---|
| 1 | Telemetry QC columns only inspect the **discharge** record | `n_voltage_out_of_range`, `n_current_out_of_range`, `n_temperature_out_of_range`, `n_nan`, `time_not_monotonic` | All read 0, while 17 anomalous charge samples exist (including 8.39 V and 2 NaN rows). False reassurance. | Also merge the check on the paired charge's `op_index`, as `charge_n_*` columns. |
| 2 | Missing relaxation tail recorded as **0** instead of null | `relax_duration_s`, `v_recovery_v` | 54 rows (B0006, B0007) claim zero voltage recovery, which is physically implausible. Biases any model using the feature. | Write NaN when there is no post-load sample. |
| 3 | `rest_before_h` measures discharge-start spacing, **not idle time** | `rest_before_h` | Dominated by cycle duration. The notebook's regeneration-vs-rest analysis used it and should be re-run. | Replace with idle time from paired-charge end to discharge start. |

These are pipeline changes, so fixing them means a rebuild, updating the affected unit
tests, and re-running the findings notebook.

---

## 6. Quick-use recipes

```python
import pandas as pd
cyc = pd.read_parquet("data/processed/cycle_summary.parquet")

# Clean cycles for SOH work
soh = cyc[~cyc.qc_any]

# RUL training rows: labelled, not post-EOL, clean
rul = cyc[cyc.rul_valid & ~cyc.qc_any]

# Charge-phase features: drop malformed charge records specifically
chg = cyc[~cyc.qc_charge_aborted & ~cyc.qc_charge_partial]

# Relaxation features: treat a missing tail as missing (issue 2)
relax = cyc.assign(v_recovery_v=cyc.v_recovery_v.where(cyc.relax_duration_s > 0))

# Impedance trend: use the source table, not the forward-repeated join
imp = pd.read_parquet("data/processed/impedance.parquet")

# Full discharge curve for one cycle (for dQ/dV in Phase 6)
tel = pd.read_parquet("data/processed/telemetry.parquet",
                      filters=[("battery_id", "==", "B0005")])
curve = tel[(tel.cycle_id == 50) & (tel.cycle_type == "discharge")]
```
