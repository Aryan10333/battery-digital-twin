"""Derived quantities used by the Phase 4 EDA.

Everything here is computed from the Phase 3 tables in ``data/processed/`` without
modifying them. Three of these functions exist to work around Phase 3 defects that
are documented but not yet fixed in the pipeline (phase3_pipeline/output_tables.md,
section 5):

* ``add_timing`` computes real idle time; the pipeline's ``rest_before_h`` is the
  spacing between discharge starts and includes the charge itself.
* ``clean_relaxation`` turns a missing relaxation tail into NaN instead of 0.
* ``flag_shared_charge`` marks the discharge that inherited the previous cycle's
  charge record because no charge was logged between them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter

from common.config import load_config, resolve

# Voltage grid for incremental capacity analysis: 10 mV steps across the discharge.
ICA_GRID = np.linspace(2.8, 4.0, 121)
# Two incremental-capacity peaks are visible in these cells' discharges:
# the dominant one around 3.4-3.5 V, and a smaller secondary one around 3.7-3.85 V.
ICA_MAIN_WINDOW = (3.25, 3.60)
ICA_SECONDARY_WINDOW = (3.60, 3.95)


def load_tables() -> dict[str, pd.DataFrame]:
    p = resolve("processed")
    return {
        "cyc": pd.read_parquet(p / "cycle_summary.parquet")
                 .sort_values(["battery_id", "cycle_id"]).reset_index(drop=True),
        "imp": pd.read_parquet(p / "impedance.parquet"),
        "regen": pd.read_parquet(p / "regeneration_summary.parquet"),
    }


def load_discharge_telemetry(battery_id: str | None = None) -> pd.DataFrame:
    filters = [("cycle_type", "==", "discharge")]
    if battery_id:
        filters.append(("battery_id", "==", battery_id))
    return pd.read_parquet(resolve("processed") / "telemetry.parquet", filters=filters)


# --------------------------------------------------------------------------- timing

def add_timing(cyc: pd.DataFrame) -> pd.DataFrame:
    """Add wall-clock idle measures.

    ``idle_chg_to_dis_h``  gap between the end of the paired charge record and the
                           start of this discharge.
    ``gap_since_prev_dis_h`` gap between the end of the previous discharge record and
                           the start of this one.
    ``idle_total_h``       that gap minus the paired charge's duration: time the cell
                           spent neither charging nor discharging (impedance sweeps
                           included).
    """
    g = cyc.sort_values(["battery_id", "cycle_id"]).copy()
    sec = lambda s: pd.to_timedelta(s, unit="s")
    dis_end = g.cycle_start_time + sec(g.record_duration_s)
    chg_end = g.charge_start_time + sec(g.charge_record_duration_s)
    prev_end = dis_end.groupby(g.battery_id).shift(1)
    g["idle_chg_to_dis_h"] = (g.cycle_start_time - chg_end).dt.total_seconds() / 3600
    g["gap_since_prev_dis_h"] = (g.cycle_start_time - prev_end).dt.total_seconds() / 3600
    g["idle_total_h"] = g.gap_since_prev_dis_h - g.charge_record_duration_s / 3600
    return g


def flag_shared_charge(cyc: pd.DataFrame) -> pd.DataFrame:
    """Flag discharges whose paired charge was already used by an earlier discharge.

    Happens when two discharges run back to back with no charge logged between them
    (cycle 90 of B0005, B0006, B0007). The later discharge's charge columns are a copy
    of the earlier cycle's, so they say nothing about its own state.
    """
    g = cyc.copy()
    g["qc_charge_shared"] = g.duplicated(["battery_id", "charge_start_time"], keep="first")
    return g


def clean_relaxation(cyc: pd.DataFrame) -> pd.DataFrame:
    """Treat a zero-length relaxation tail as missing rather than as zero recovery."""
    g = cyc.copy()
    no_tail = g.relax_duration_s <= 0
    g["relax_tail_missing"] = no_tail
    g["relax_duration_s"] = g.relax_duration_s.mask(no_tail)
    g["v_recovery_v"] = g.v_recovery_v.mask(no_tail)
    return g


def prepare_cycles(cyc: pd.DataFrame) -> pd.DataFrame:
    """Apply all EDA-side corrections to the cycle table."""
    return clean_relaxation(flag_shared_charge(add_timing(cyc)))


# ------------------------------------------------------------------ discharge curves

def active_qv(g: pd.DataFrame, active_current_a: float = 0.5
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Discharged charge Q (Ah), voltage and time over the loaded segment of one cycle."""
    g = g.sort_values("t_rel_s")
    t = g.t_rel_s.to_numpy()
    v = g.voltage_v.to_numpy()
    i = np.abs(g.current_a.to_numpy())
    act = np.where(i > active_current_a)[0]
    s = slice(act[0], act[-1] + 1)
    t, v, i = t[s], v[s], i[s]
    q = np.concatenate([[0.0], np.cumsum(np.diff(t) * (i[1:] + i[:-1]) / 2)]) / 3600
    return q, v, t


def q_on_voltage_grid(q: np.ndarray, v: np.ndarray, grid: np.ndarray = ICA_GRID) -> np.ndarray:
    """Discharged charge as a function of voltage, sampled on a fixed grid.

    Voltage falls during discharge, so the arrays are reversed. A running maximum
    makes voltage monotone, which removes small measurement wiggles that would
    otherwise break interpolation.
    """
    v_mono = np.maximum.accumulate(v[::-1])
    return np.interp(grid, v_mono, q[::-1])


def ica_curve(q: np.ndarray, v: np.ndarray, grid: np.ndarray = ICA_GRID) -> np.ndarray:
    """Incremental capacity -dQ/dV (Ah/V) on the grid, Savitzky-Golay smoothed."""
    qg = q_on_voltage_grid(q, v, grid)
    return -savgol_filter(np.gradient(qg, grid), 11, 2)


def _tallest_peak(ica: np.ndarray, peaks: np.ndarray, window: tuple[float, float]
                  ) -> tuple[float, float]:
    lo, hi = window
    inside = [p for p in peaks if lo <= ICA_GRID[p] < hi]
    if not inside:
        return np.nan, np.nan
    p = max(inside, key=lambda j: ica[j])
    return float(ICA_GRID[p]), float(ica[p])


def discharge_curve_features(tel: pd.DataFrame, cutoffs: dict[str, float]) -> pd.DataFrame:
    """Per-cycle features that need the full discharge curve.

    ``q_to_cutoff_ah`` charge delivered until voltage first reaches the documented
                       cut-off (linearly interpolated), for testing whether deeper
                       discharges inflate recorded capacity.
    ``ica_p1_v/_h``    voltage and height of the main incremental-capacity peak
                       (~3.4-3.5 V); present in every cycle.
    ``ica_p2_v/_h``    the secondary peak (~3.7-3.85 V); not resolvable in early
                       life, NaN where no peak is found.
    ``dt_median_s``    median sampling interval -- a confound for curve features.
    """
    rows = []
    for (bid, cid), g in tel.groupby(["battery_id", "cycle_id"]):
        q, v, t = active_qv(g)
        cut = cutoffs[bid]
        below = np.where(v <= cut)[0]
        if len(below) == 0:
            q_cut = q[-1]
        elif below[0] == 0:
            q_cut = q[0]
        else:
            k = below[0]
            f = (v[k - 1] - cut) / (v[k - 1] - v[k])
            q_cut = q[k - 1] + f * (q[k] - q[k - 1])

        ica = ica_curve(q, v)
        peaks, _ = find_peaks(ica, prominence=0.02)
        p1_v, p1_h = _tallest_peak(ica, peaks, ICA_MAIN_WINDOW)
        p2_v, p2_h = _tallest_peak(ica, peaks, ICA_SECONDARY_WINDOW)

        rows.append({
            "battery_id": bid, "cycle_id": int(cid),
            "q_to_cutoff_ah": float(q_cut), "q_active_ah": float(q[-1]),
            "ica_p1_v": p1_v, "ica_p1_h": p1_h, "ica_p2_v": p2_v, "ica_p2_h": p2_h,
            "dt_median_s": float(np.median(np.diff(t))), "n_active": int(len(t)),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- shape

def two_segment_fit(x: np.ndarray, y: np.ndarray, min_seg: int = 15) -> dict:
    """Best two-piece linear fit, searching every breakpoint. A crude knee test:
    a knee shows up as a steeper second slope."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    tss = ((y - y.mean()) ** 2).sum()
    best = None
    for k in range(min_seg, len(x) - min_seg):
        p1 = np.polyfit(x[:k], y[:k], 1)
        p2 = np.polyfit(x[k:], y[k:], 1)
        sse = (((np.polyval(p1, x[:k]) - y[:k]) ** 2).sum()
               + ((np.polyval(p2, x[k:]) - y[k:]) ** 2).sum())
        if best is None or sse < best["sse"]:
            best = {"sse": sse, "break_x": x[k], "slope_1": p1[0], "slope_2": p2[0]}
    p = np.polyfit(x, y, 1)
    best["linear_slope"] = p[0]
    best["linear_r2"] = 1 - ((np.polyval(p, x) - y) ** 2).sum() / tss
    best["two_segment_r2"] = 1 - best["sse"] / tss
    return best


def backward_slope(y: pd.Series, window: int) -> pd.Series:
    """Rolling least-squares slope over the previous ``window`` points (causal)."""
    x = np.arange(window, dtype=float)
    xc = x - x.mean()
    denom = (xc ** 2).sum()
    return y.rolling(window).apply(lambda w: float(np.dot(xc, w - w.mean()) / denom), raw=True)


# ------------------------------------------------------------------------ screening

def screen_indicators(df: pd.DataFrame, indicators: list[str], target: str = "soh_pct_self",
                      group: str = "battery_id", trend: str = "cycle_id") -> pd.DataFrame:
    """Rank candidate health indicators against a target.

    Beyond pooled Pearson and Spearman this reports:

    * per-battery Spearman range and whether the sign agrees across batteries;
    * ``detrended_spearman`` -- correlation of each indicator's residual against the
      target's residual after removing a per-battery linear trend in cycle count. An
      indicator that only correlates because both drift with age scores near zero; one
      that follows the cycle-to-cycle ups and downs (e.g. regeneration) scores high;
    * ``abs_spearman_with_cycle`` -- how much of the indicator is plain age.
    """
    rows = []
    for h in indicators:
        s = df[[h, target, trend, group]].dropna()
        per, resid = [], []
        for _, g in s.groupby(group):
            per.append(g[h].corr(g[target], method="spearman"))
            x = g[trend].to_numpy(float)
            rh = g[h].to_numpy() - np.polyval(np.polyfit(x, g[h], 1), x)
            rt = g[target].to_numpy() - np.polyval(np.polyfit(x, g[target], 1), x)
            resid.append(pd.Series(rh).corr(pd.Series(rt), method="spearman"))
        per = np.array(per)
        rows.append({
            "indicator": h, "n": len(s),
            "pooled_pearson": s[h].corr(s[target]),
            "pooled_spearman": s[h].corr(s[target], method="spearman"),
            "per_battery_min": np.nanmin(per), "per_battery_max": np.nanmax(per),
            "sign_consistent": bool(np.all(np.sign(per) == np.sign(np.nanmedian(per)))),
            "detrended_spearman": float(np.nanmedian(resid)),
            "abs_spearman_with_cycle": abs(s[h].corr(s[trend], method="spearman")),
        })
    return (pd.DataFrame(rows)
              .sort_values("pooled_spearman", key=np.abs, ascending=False)
              .reset_index(drop=True))


# ------------------------------------------------------------------------ impedance

def impedance_with_context(imp: pd.DataFrame, cyc: pd.DataFrame) -> pd.DataFrame:
    """Attach to every EIS sweep the type of operation that preceded it and the most
    recent discharge's cycle, capacity and SOH.

    Operation types come from the telemetry table, which records every charge and
    discharge ``op_index``; consecutive sweeps inherit the last non-impedance op.
    """
    ops = (pd.read_parquet(resolve("processed") / "telemetry.parquet",
                           columns=["battery_id", "op_index", "cycle_type"])
             .drop_duplicates(["battery_id", "op_index"])
             .rename(columns={"cycle_type": "after"}))
    m = pd.merge_asof(imp.sort_values("op_index"), ops.sort_values("op_index"),
                      on="op_index", by="battery_id", direction="backward",
                      allow_exact_matches=False)
    m = pd.merge_asof(
        m.sort_values("op_index"),
        cyc[["battery_id", "op_index", "cycle_id", "capacity_ah", "soh_pct_self"]]
           .sort_values("op_index"),
        on="op_index", by="battery_id", direction="backward")
    return m.sort_values(["battery_id", "op_index"]).reset_index(drop=True)


def cutoffs() -> dict[str, float]:
    return dict(load_config()["cutoff_voltage_v"])
