"""Regression tests for the ingestion pipeline.

Values are the measured ground truth recorded in docs/02_data_dictionary.md.
They exist to catch silent changes in parsing behaviour, so they are exact.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data_contracts.schemas import validate
from src.config import load_config, resolve
from src.preprocessing.targets import find_eol_cycle

PROCESSED = resolve("processed")
pytestmark = pytest.mark.skipif(
    not (PROCESSED / "cycle_summary.parquet").exists(),
    reason="run `python -m src.ingestion.build_dataset` first",
)


@pytest.fixture(scope="module")
def cycles() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED / "cycle_summary.parquet")


@pytest.fixture(scope="module")
def telemetry() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED / "telemetry.parquet")


# --- structure -------------------------------------------------------------

def test_expected_batteries(cycles):
    assert sorted(cycles.battery_id.unique()) == ["B0005", "B0006", "B0007", "B0018"]


@pytest.mark.parametrize("bid,n", [("B0005", 168), ("B0006", 168), ("B0007", 168), ("B0018", 132)])
def test_discharge_cycle_counts(cycles, bid, n):
    assert (cycles.battery_id == bid).sum() == n


def test_cycle_id_is_contiguous_from_one(cycles):
    for bid, g in cycles.groupby("battery_id"):
        assert g.cycle_id.tolist() == list(range(1, len(g) + 1)), bid


# --- targets ---------------------------------------------------------------

def test_soh_self_starts_at_100(cycles):
    first = cycles.sort_values("cycle_id").groupby("battery_id").soh_pct_self.first()
    assert np.allclose(first.to_numpy(), 100.0)


@pytest.mark.parametrize("bid,cap", [("B0005", 1.8565), ("B0006", 2.0353),
                                     ("B0007", 1.8911), ("B0018", 1.8550)])
def test_initial_capacity(cycles, bid, cap):
    g = cycles[cycles.battery_id == bid].sort_values("cycle_id")
    assert g.capacity_ah.iloc[0] == pytest.approx(cap, abs=1e-3)


@pytest.mark.parametrize("bid,eol", [("B0005", 125), ("B0006", 109), ("B0018", 97)])
def test_eol_cycles(cycles, bid, eol):
    g = cycles[cycles.battery_id == bid]
    assert int(g.eol_cycle.iloc[0]) == eol


def test_b0007_is_censored(cycles):
    """B0007 bottoms out at 1.4005 Ah and never crosses the 1.4 Ah threshold."""
    g = cycles[cycles.battery_id == "B0007"]
    assert bool(g.is_censored.iloc[0])
    assert g.rul_cycles.isna().all()
    assert g.capacity_ah.min() > 1.4


def test_capacity_within_physical_range(cycles):
    assert cycles.capacity_ah.between(1.0, 2.2).all()


def test_rul_decreases_by_one_per_cycle(cycles):
    for bid, g in cycles.groupby("battery_id"):
        if bool(g.is_censored.iloc[0]):
            continue
        d = g.sort_values("cycle_id").rul_cycles.diff().dropna().unique()
        assert d.tolist() == [-1.0], bid


# --- leakage guards --------------------------------------------------------

def test_impedance_join_is_backward_only(cycles):
    """Negative staleness would mean a future impedance measurement leaked in."""
    age = cycles.impedance_age_ops.dropna()
    assert (age >= 0).all()


def test_q_ref_uses_first_not_max_capacity(cycles):
    """Self-referenced SOH must use the first measured capacity (a past value),
    never the maximum over the whole trajectory (which is future information)."""
    for bid, g in cycles.groupby("battery_id"):
        g = g.sort_values("cycle_id")
        assert g.q_ref_self_ah.iloc[0] == pytest.approx(g.capacity_ah.iloc[0])


# --- EOL rule --------------------------------------------------------------

def test_sustained_eol_ignores_a_transient_dip():
    """A single dip below threshold that recovers must not trigger EOL --
    capacity regeneration makes exactly this happen."""
    cap = pd.Series([2.0, 1.9, 1.35, 1.8, 1.7, 1.6, 1.5, 1.3, 1.3, 1.3, 1.3, 1.3])
    assert find_eol_cycle(cap, threshold=1.4, sustain=5) == 8


def test_sustained_eol_returns_none_when_never_crossed():
    cap = pd.Series([2.0, 1.8, 1.6, 1.5, 1.45])
    assert find_eol_cycle(cap, threshold=1.4, sustain=5) is None


# --- quality ---------------------------------------------------------------

def test_known_aborted_charges_are_flagged(cycles):
    """Cycle 31 of B0005/6/7 is a 9-second, 0.0 Ah charge record."""
    for bid in ["B0005", "B0006", "B0007"]:
        row = cycles[(cycles.battery_id == bid) & (cycles.cycle_id == 31)]
        assert bool(row.qc_charge_aborted.iloc[0]), bid


def test_cycle_one_flagged_as_partial_charge(cycles):
    first = cycles[cycles.cycle_id == 1]
    assert first.qc_charge_partial.all()


def test_most_cycles_are_clean(cycles):
    assert (~cycles.qc_any).sum() == 606


# --- schemas ---------------------------------------------------------------

def test_cycle_summary_matches_contract(cycles):
    validate("cycle_summary", cycles)


def test_telemetry_matches_contract(telemetry):
    validate("telemetry", telemetry.sample(50_000, random_state=0))


def test_telemetry_time_is_monotonic_within_operation(telemetry):
    sample = telemetry[telemetry.battery_id == "B0018"]
    bad = (sample.groupby("op_index").t_rel_s
           .apply(lambda s: bool((s.diff().dropna() <= 0).any())))
    assert not bad.any()
