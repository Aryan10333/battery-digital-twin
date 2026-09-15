"""Parse NASA PCoE battery ``.mat`` files into canonical tables.

Design notes recorded during the Phase 2/3 audit, all verified against the data
rather than the bundle READMEs (which disagree with the files in three places,
see phase2_data_audit/data_dictionary.md Finding 1):

* Discharge cycles expose ``Current_load`` / ``Voltage_load``; charge cycles
  expose ``Current_charge`` / ``Voltage_charge``. The READMEs claim otherwise.
* The impedance field is ``Rectified_Impedance`` (capital I).
* Current sign convention: charge positive, discharge negative.
* Each discharge record continues past load removal into a relaxation tail
  where the terminal voltage recovers (typically ~9% of the record, e.g. B0005
  cycle 1 recovers 2.61 V -> 3.28 V over 343 s). Durations, energy and end
  voltage are therefore measured over the *active* segment only; the tail is
  summarised separately because the recovery itself is a health signal.
* Operation ordering differs per battery (B0005 begins ``CDCDCD...`` while
  B0018 begins ``CICDICD...``), so charge/discharge pairing is done by file
  position, never by assuming a fixed interleave.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

# Field names as they actually appear in the files.
CHARGE_FIELDS = ("Voltage_measured", "Current_measured", "Temperature_measured",
                 "Current_charge", "Voltage_charge", "Time")
DISCHARGE_FIELDS = ("Voltage_measured", "Current_measured", "Temperature_measured",
                    "Current_load", "Voltage_load", "Time", "Capacity")
IMPEDANCE_FIELDS = ("Sense_current", "Battery_current", "Current_ratio",
                    "Battery_impedance", "Rectified_Impedance", "Re", "Rct")


@dataclass
class ParsedBattery:
    telemetry: pd.DataFrame
    cycles: pd.DataFrame
    impedance: pd.DataFrame


def _matlab_time(vec) -> datetime:
    """MATLAB date vector [Y M D h m s] -> datetime (seconds may be fractional)."""
    y, mo, d, h, mi, s = (float(x) for x in np.atleast_1d(vec)[:6])
    return datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=s)


def _arr(data: dict, key: str) -> np.ndarray:
    return np.atleast_1d(data[key]).astype(float).ravel()


def _scalar(data: dict, key: str) -> float:
    v = np.atleast_1d(data[key]).ravel()
    return float(np.real(v[0])) if v.size else np.nan


def _span(t: np.ndarray, mask: np.ndarray) -> float:
    """Elapsed seconds covered by a boolean mask, NaN if it selects nothing."""
    if not mask.any():
        return np.nan
    idx = np.where(mask)[0]
    return float(t[idx[-1]] - t[idx[0]])


def _summarise_discharge(data: dict, cfg: dict) -> dict:
    """Aggregate one discharge record, separating the active segment from the
    post-load relaxation tail."""
    t = _arr(data, "Time")
    v = _arr(data, "Voltage_measured")
    i = _arr(data, "Current_measured")
    temp = _arr(data, "Temperature_measured")

    active = np.abs(i) > cfg["ingestion"]["active_current_a"]
    out: dict[str, float] = {
        "capacity_ah": _scalar(data, "Capacity"),
        "n_samples": int(t.size),
        "record_duration_s": float(t[-1] - t[0]) if t.size else np.nan,
        "discharge_duration_s": _span(t, active),
        "n_active_samples": int(active.sum()),
    }

    if active.any():
        last = np.where(active)[0][-1]
        va, ia, ta = v[active], i[active], t[active]
        # Trapezoidal integration over the active segment only.
        out["energy_wh"] = float(np.trapezoid(np.abs(va * ia), ta) / 3600.0)
        out["discharge_ah"] = float(np.trapezoid(np.abs(ia), ta) / 3600.0)
        out["v_min_v"] = float(va.min())
        out["v_max_v"] = float(va.max())
        out["v_mean_v"] = float(va.mean())
        out["v_end_active_v"] = float(v[last])
        out["i_mean_a"] = float(ia.mean())
        out["temp_max_c"] = float(temp[active].max())
        out["temp_mean_c"] = float(temp[active].mean())
        out["temp_rise_c"] = float(temp[active].max() - temp[active][0])
        # Relaxation tail: how far the terminal voltage recovers once the load
        # is removed. Rises with internal resistance, so it is a health signal.
        out["relax_duration_s"] = float(t[-1] - t[last])
        out["v_recovery_v"] = float(v[-1] - v[last])
    else:
        for k in ("energy_wh", "discharge_ah", "v_min_v", "v_max_v", "v_mean_v",
                  "v_end_active_v", "i_mean_a", "temp_max_c", "temp_mean_c",
                  "temp_rise_c", "relax_duration_s", "v_recovery_v"):
            out[k] = np.nan
    return out


def _summarise_charge(data: dict, cfg: dict) -> dict:
    """Aggregate one charge record into CC and CV phase statistics.

    A single spurious large-negative current sample appears at the start of some
    charge records (e.g. -4.03 A in B0005 cycle 1); requiring positive current
    for the active mask excludes it.
    """
    t = _arr(data, "Time")
    v = _arr(data, "Voltage_measured")
    i = _arr(data, "Current_measured")
    temp = _arr(data, "Temperature_measured")

    ing = cfg["ingestion"]
    active = i > ing["idle_current_a"]
    cc = i > ing["cc_current_a"]

    out: dict[str, float] = {
        "charge_n_samples": int(t.size),
        "charge_duration_s": _span(t, active),
        "charge_record_duration_s": float(t[-1] - t[0]) if t.size else np.nan,
    }

    if cc.any() and active.any():
        first_act = np.where(active)[0][0]
        last_cc = np.where(cc)[0][-1]
        last_act = np.where(active)[0][-1]
        out["cc_duration_s"] = float(t[last_cc] - t[first_act])
        out["cv_duration_s"] = float(max(t[last_act] - t[last_cc], 0.0))
        out["v_cc_to_cv_v"] = float(v[last_cc])
        out["charge_ah"] = float(np.trapezoid(i[active], t[active]) / 3600.0)
        out["charge_temp_max_c"] = float(temp[active].max())
        denom = out["cv_duration_s"]
        out["cc_cv_ratio"] = float(out["cc_duration_s"] / denom) if denom > 0 else np.nan
    else:
        for k in ("cc_duration_s", "cv_duration_s", "v_cc_to_cv_v", "charge_ah",
                  "charge_temp_max_c", "cc_cv_ratio"):
            out[k] = np.nan
    return out


def _telemetry_frame(data: dict, cycle_type: str, keys: tuple[str, str]) -> pd.DataFrame:
    ext_i, ext_v = keys
    return pd.DataFrame({
        "t_rel_s": _arr(data, "Time"),
        "voltage_v": _arr(data, "Voltage_measured"),
        "current_a": _arr(data, "Current_measured"),
        "temperature_c": _arr(data, "Temperature_measured"),
        "current_ext_a": _arr(data, ext_i),
        "voltage_ext_v": _arr(data, ext_v),
        "cycle_type": cycle_type,
    })


def parse_battery(mat_path: Path, battery_id: str, cfg: dict) -> ParsedBattery:
    """Parse one ``.mat`` file into telemetry, per-discharge-cycle and impedance tables."""
    mat = loadmat(str(mat_path), simplify_cells=True)
    keys = [k for k in mat if not k.startswith("__")]
    if battery_id not in keys:
        raise ValueError(f"{mat_path.name}: expected struct {battery_id!r}, found {keys}")
    ops = np.atleast_1d(mat[battery_id]["cycle"])

    telemetry, discharges, charges, impedances = [], [], [], []

    for op_index, op in enumerate(ops):
        op_type = str(op["type"])
        start = _matlab_time(op["time"])
        ambient = float(op["ambient_temperature"])
        data = op["data"]

        if op_type == "discharge":
            rec = {"op_index": op_index, "cycle_start_time": start,
                   "ambient_temp_c": ambient, **_summarise_discharge(data, cfg)}
            discharges.append(rec)
            tf = _telemetry_frame(data, "discharge", ("Current_load", "Voltage_load"))
        elif op_type == "charge":
            rec = {"op_index": op_index, "charge_start_time": start,
                   **_summarise_charge(data, cfg)}
            charges.append(rec)
            tf = _telemetry_frame(data, "charge", ("Current_charge", "Voltage_charge"))
        elif op_type == "impedance":
            impedances.append({
                "op_index": op_index, "impedance_time": start, "ambient_temp_c": ambient,
                "re_ohm": _scalar(data, "Re"), "rct_ohm": _scalar(data, "Rct"),
            })
            continue
        else:  # unknown operation type; record nothing but do not fail the run
            continue

        tf.insert(0, "op_index", op_index)
        tf.insert(0, "battery_id", battery_id)
        tf["ambient_temp_c"] = ambient
        telemetry.append(tf)

    cycles = pd.DataFrame(discharges)
    if cycles.empty:
        raise ValueError(f"{mat_path.name}: no discharge cycles found")

    # cycle_id indexes DISCHARGE operations in file order, 1-based. Capacity (the
    # SOH target) is only recorded on discharge, so this is the natural aging index.
    cycles = cycles.sort_values("op_index").reset_index(drop=True)
    cycles.insert(0, "cycle_id", np.arange(1, len(cycles) + 1))
    cycles.insert(0, "battery_id", battery_id)

    # Pair each discharge with the most recent preceding charge by file position.
    charge_df = pd.DataFrame(charges)
    if not charge_df.empty:
        charge_df = charge_df.sort_values("op_index")
        cycles = pd.merge_asof(cycles, charge_df, on="op_index",
                               direction="backward", suffixes=("", "_chg"))

    # Backward-fill impedance: only measurements taken at or before the discharge
    # may be used. Never interpolate from a future measurement -- that is leakage.
    imp_df = pd.DataFrame(impedances)
    if not imp_df.empty:
        imp_df = imp_df.sort_values("op_index")
        cycles = pd.merge_asof(
            cycles, imp_df[["op_index", "re_ohm", "rct_ohm", "impedance_time"]],
            on="op_index", direction="backward")
        # How stale is the joined impedance, in operations?
        cycles["impedance_age_ops"] = (
            cycles["op_index"]
            - imp_df.set_index("op_index").index.to_series()
            .reindex(cycles["op_index"], method="ffill").values
        )
        imp_df.insert(0, "battery_id", battery_id)

    # Map telemetry rows onto the discharge cycle they belong to: each charge is
    # attributed to the discharge that follows it.
    tel = pd.concat(telemetry, ignore_index=True) if telemetry else pd.DataFrame()
    if not tel.empty:
        lut = cycles[["op_index", "cycle_id"]].sort_values("op_index")
        tel = tel.sort_values("op_index")
        tel = pd.merge_asof(tel, lut, on="op_index", direction="forward")

    return ParsedBattery(telemetry=tel, cycles=cycles, impedance=imp_df)
