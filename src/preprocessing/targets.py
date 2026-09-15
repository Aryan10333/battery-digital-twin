"""SOH and RUL target construction.

Two decisions are made explicit here because both were flagged in the Phase 2
audit (docs/02_data_dictionary.md, Findings 3 and 5):

1. **EOL is defined on absolute capacity (1.4 Ah)**, matching NASA's own
   experimental criterion, not on a fixed SOH percentage. Measured initial
   capacities are 1.856-2.035 Ah rather than the 2.0 Ah rated value, so a fixed
   SOH threshold would land at a different physical capacity for every cell.
   SOH percentage is carried for display only, in both normalisations.

2. **The EOL crossing must be sustained.** Capacity regeneration makes the
   trajectory non-monotonic (22% of B0005's cycle-to-cycle steps are upward,
   the largest worth 4.76 percentage points of SOH), so a cell can dip below
   the threshold and recover. EOL is the first cycle from which capacity stays
   at or below the threshold for ``eol_sustain_cycles`` consecutive cycles.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def find_eol_cycle(capacity: pd.Series, threshold: float, sustain: int) -> int | None:
    """First cycle index (1-based position) from which capacity stays at or below
    ``threshold`` for ``sustain`` consecutive cycles. None if never sustained.

    Cycles within ``sustain`` of the end of the record qualify only if every
    remaining cycle is below the threshold.
    """
    cap = capacity.to_numpy(dtype=float)
    n = cap.size
    for i in range(n):
        window = cap[i:i + sustain]
        if window.size == 0:
            break
        if np.all(window <= threshold):
            return i + 1  # cycle_id is 1-based
    return None


def add_targets(cycles: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add SOH and RUL columns to a per-battery cycle table."""
    tgt = cfg["targets"]
    eol_cap = tgt["eol_capacity_ah"]
    rated = tgt["rated_capacity_ah"]
    sustain = tgt["eol_sustain_cycles"]

    out = []
    for battery_id, grp in cycles.groupby("battery_id", sort=True):
        g = grp.sort_values("cycle_id").copy()
        cap = g["capacity_ah"]

        # Q_ref, both conventions. The self-referenced value uses the first
        # measured capacity -- a past value, so it is leakage-safe.
        q_first = float(cap.iloc[0])
        g["soh_pct_self"] = cap / q_first * 100.0
        g["soh_pct_rated"] = cap / rated * 100.0
        g["q_ref_self_ah"] = q_first

        # Cumulative usage (backward-looking only).
        g["coulomb_throughput_ah"] = g["discharge_ah"].fillna(0).cumsum()
        g["energy_throughput_wh"] = g["energy_wh"].fillna(0).cumsum()
        t0 = g["cycle_start_time"].iloc[0]
        g["elapsed_days"] = (g["cycle_start_time"] - t0).dt.total_seconds() / 86400.0
        # Rest before this cycle: the wall-clock gap since the previous discharge.
        # This drives capacity regeneration, so it is a legitimate feature.
        g["rest_before_h"] = (
            g["cycle_start_time"].diff().dt.total_seconds() / 3600.0
        )

        eol = find_eol_cycle(cap, eol_cap, sustain)
        first_touch = g.loc[cap <= eol_cap, "cycle_id"]
        g["eol_cycle"] = eol if eol is not None else pd.NA
        g["eol_first_touch_cycle"] = int(first_touch.iloc[0]) if len(first_touch) else pd.NA
        g["is_censored"] = eol is None
        # RUL only exists for cells that actually reach EOL. Censored cells keep
        # NaN rather than an invented horizon.
        g["rul_cycles"] = (eol - g["cycle_id"]) if eol is not None else np.nan
        # Cycles after EOL have negative RUL; drop them from supervised RUL use.
        g["rul_valid"] = g["rul_cycles"].notna() & (g["rul_cycles"] >= 0)
        g["life_fraction"] = (g["cycle_id"] / eol) if eol is not None else np.nan

        out.append(g)

    return pd.concat(out, ignore_index=True)
