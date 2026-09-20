"""Regression tests for the Phase 4 derived quantities.

Expected values are the measured facts reported in phase4_eda_report.ipynb.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from common.config import resolve
from phase4_eda import eda_features as ef

pytestmark = pytest.mark.skipif(
    not (resolve("processed") / "cycle_summary.parquet").exists(),
    reason="run `python -m phase3_pipeline.build_dataset` first",
)


@pytest.fixture(scope="module")
def cyc() -> pd.DataFrame:
    return ef.prepare_cycles(ef.load_tables()["cyc"])


@pytest.fixture(scope="module")
def curves() -> pd.DataFrame:
    return ef.discharge_curve_features(ef.load_discharge_telemetry(), ef.cutoffs())


def test_idle_time_is_never_negative(cyc):
    assert (cyc.idle_total_h.dropna() >= 0).all()
    assert (cyc.idle_chg_to_dis_h.dropna() >= 0).all()


def test_idle_time_is_shorter_than_pipeline_rest(cyc):
    """The pipeline's rest_before_h includes the charge and previous discharge."""
    both = cyc.dropna(subset=["idle_total_h", "rest_before_h"])
    assert (both.idle_total_h < both.rest_before_h).all()


def test_shared_charge_flags_cycle_90_only(cyc):
    flagged = cyc[cyc.qc_charge_shared][["battery_id", "cycle_id"]]
    assert flagged.values.tolist() == [["B0005", 90], ["B0006", 90], ["B0007", 90]]


def test_missing_relaxation_tail_is_nan_not_zero(cyc):
    missing = cyc[cyc.relax_tail_missing]
    assert len(missing) == 54
    assert missing.v_recovery_v.isna().all()
    assert (cyc.v_recovery_v.dropna() > 0).all()


def test_capacity_to_cutoff_matches_recorded_capacity(curves, cyc):
    """Deep discharges past the cut-off do not inflate capacity (Phase 4 section 5)."""
    m = curves.merge(cyc[["battery_id", "cycle_id", "capacity_ah"]])
    assert (m.capacity_ah - m.q_to_cutoff_ah).abs().max() < 0.020


def test_main_ica_peak_found_in_every_cycle(curves):
    assert curves.ica_p1_v.notna().all()
    lo, hi = ef.ICA_MAIN_WINDOW
    assert curves.ica_p1_v.between(lo, hi).all()


def test_secondary_ica_peak_absent_in_first_20_cycles(curves):
    assert curves[curves.cycle_id <= 20].ica_p2_v.isna().all()


def test_sampling_switches_at_cycle_31(curves):
    g = curves[curves.battery_id == "B0005"].set_index("cycle_id").dt_median_s
    assert g.loc[30] == pytest.approx(18.6, abs=0.2)
    assert g.loc[31] == pytest.approx(9.4, abs=0.2)


def test_two_segment_fit_recovers_a_known_break():
    x = np.arange(100, dtype=float)
    y = np.where(x < 60, -0.002 * x, -0.12 - 0.006 * (x - 60))
    fit = ef.two_segment_fit(x, y)
    assert fit["break_x"] == 60
    assert fit["slope_2"] < fit["slope_1"]


def test_backward_slope_is_causal():
    s = pd.Series(np.arange(30, dtype=float) * 2.0)
    slope = ef.backward_slope(s, 10)
    assert slope.iloc[:9].isna().all()
    assert np.allclose(slope.dropna(), 2.0)
    changed = s.copy()
    changed.iloc[20:] = 0.0
    # Changing future values must not change past slopes.
    assert np.allclose(ef.backward_slope(changed, 10).iloc[:20].dropna(), slope.iloc[:20].dropna())


def test_impedance_context_covers_every_sweep(cyc):
    ictx = ef.impedance_with_context(ef.load_tables()["imp"], ef.load_tables()["cyc"])
    assert ictx.after.isin(["charge", "discharge"]).all()
