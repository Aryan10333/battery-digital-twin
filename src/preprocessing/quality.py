"""Quality checks. Flags are recorded as columns; rows are never silently dropped.

Capacity regeneration is deliberately NOT flagged as an anomaly -- it is real
physics and a documented property of this dataset (docs/01_domain_primer.md
section 2.7). It is counted so the magnitude can be reported.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def check_telemetry(tel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Per-cycle counts of out-of-range and malformed telemetry samples."""
    q = cfg["quality"]
    vlo, vhi = q["voltage_v"]
    ilo, ihi = q["current_a"]
    tlo, thi = q["temperature_c"]

    tel = tel.copy()
    tel["_v_bad"] = ~tel["voltage_v"].between(vlo, vhi)
    tel["_i_bad"] = ~tel["current_a"].between(ilo, ihi)
    tel["_t_bad"] = ~tel["temperature_c"].between(tlo, thi)
    tel["_nan"] = tel[["voltage_v", "current_a", "temperature_c"]].isna().any(axis=1)

    agg = (tel.groupby(["battery_id", "op_index"], as_index=False)
              .agg(n_samples=("t_rel_s", "size"),
                   n_voltage_out_of_range=("_v_bad", "sum"),
                   n_current_out_of_range=("_i_bad", "sum"),
                   n_temperature_out_of_range=("_t_bad", "sum"),
                   n_nan=("_nan", "sum")))

    # Time must be strictly increasing within an operation.
    mono = (tel.sort_values(["battery_id", "op_index", "t_rel_s"])
               .groupby(["battery_id", "op_index"])["t_rel_s"]
               .apply(lambda s: bool((s.diff().dropna() <= 0).any()))
               .rename("time_not_monotonic").reset_index())
    return agg.merge(mono, on=["battery_id", "op_index"], how="left")


def flag_cycles(cycles: pd.DataFrame, cfg: dict, telemetry_qc: pd.DataFrame | None = None
                ) -> pd.DataFrame:
    """Attach per-cycle QC flags and a combined ``qc_flags`` string column."""
    q = cfg["quality"]
    cut = cfg.get("cutoff_voltage_v", {})
    g = cycles.copy()

    g["qc_short_record"] = g["n_samples"] < q["min_samples_per_cycle"]
    g["qc_no_active_segment"] = g["n_active_samples"].fillna(0) == 0
    g["qc_capacity_missing"] = g["capacity_ah"].isna()
    g["qc_capacity_implausible"] = ~g["capacity_ah"].between(0.1, 2.5)
    g["qc_no_charge_pair"] = g.get("charge_duration_s", pd.Series(np.nan, index=g.index)).isna()

    # Aborted charge operations: a handful of charge records carry only a few
    # seconds of CC and effectively zero delivered charge (e.g. cycle 31 in
    # B0005/6/7 is 9 s / 0.0 Ah). Charge-phase features are meaningless there.
    g["qc_charge_aborted"] = (g["cc_duration_s"] < 60.0) | (g["charge_ah"] < 0.1)
    # Partial charge: every battery's cycle 1 begins from an already partially
    # charged cell (~0.8 Ah delivered against a ~1.9 Ah norm), so its CC/CV split
    # is not comparable with the rest of the trajectory. Tested physically -- a
    # full charge must deliver at least as much as the following discharge
    # extracts -- rather than against a median, which drifts down as cells age.
    g["qc_charge_partial"] = (
        (g["charge_ah"] < 0.8 * g["capacity_ah"]) & ~g["qc_charge_aborted"]
    )

    # Did the discharge actually reach its documented cut-off voltage?
    nominal = g["battery_id"].map(cut)
    g["cutoff_nominal_v"] = nominal
    # Tolerance allows for the measured minima sitting slightly either side of nominal.
    g["qc_cutoff_mismatch"] = (g["v_min_v"] - nominal).abs() > 0.25

    flag_cols = [c for c in g.columns if c.startswith("qc_")]
    g["qc_flags"] = [
        ",".join(c[3:] for c in flag_cols if bool(row[c])) or "ok"
        for _, row in g[flag_cols].iterrows()
    ]
    g["qc_any"] = g["qc_flags"] != "ok"

    if telemetry_qc is not None:
        g = g.merge(telemetry_qc, on=["battery_id", "op_index"],
                    how="left", suffixes=("", "_tel"))
    return g


def regeneration_summary(cycles: pd.DataFrame) -> pd.DataFrame:
    """Count and size capacity-regeneration events per battery. Not a defect."""
    rows = []
    for bid, grp in cycles.groupby("battery_id", sort=True):
        cap = grp.sort_values("cycle_id")["capacity_ah"].to_numpy(dtype=float)
        d = np.diff(cap)
        up = d[d > 0]
        rows.append({
            "battery_id": bid,
            "n_steps": int(d.size),
            "n_regen": int(up.size),
            "regen_pct": round(float(up.size / d.size * 100), 1) if d.size else np.nan,
            "regen_max_ah": round(float(up.max()), 4) if up.size else np.nan,
            "regen_mean_ah": round(float(up.mean()), 4) if up.size else np.nan,
            "regen_max_pp_soh": round(float(up.max() / cap[0] * 100), 2) if up.size else np.nan,
        })
    return pd.DataFrame(rows)
