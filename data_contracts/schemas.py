"""Pandera schemas for the canonical tables. The build fails loudly on violation
rather than writing a silently malformed dataset."""
from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

# Physical plausibility bounds, deliberately wider than the QC bounds in
# configs/paths.yaml: a schema violation means the parser is wrong, whereas a QC
# flag means the measurement is suspect but genuine.
TELEMETRY = DataFrameSchema(
    {
        "battery_id": Column(str),
        "op_index": Column("int64", Check.ge(0)),
        "cycle_id": Column("int64", Check.ge(1), nullable=True),
        "cycle_type": Column(str, Check.isin(["charge", "discharge"])),
        "t_rel_s": Column(float, Check.ge(0)),
        "voltage_v": Column(float, Check.in_range(0.0, 5.0)),
        "current_a": Column(float, Check.in_range(-10.0, 10.0)),
        "temperature_c": Column(float, Check.in_range(-20.0, 100.0)),
        "ambient_temp_c": Column(float),
    },
    strict=False,
    coerce=True,
)

CYCLE_SUMMARY = DataFrameSchema(
    {
        "battery_id": Column(str),
        "cycle_id": Column("int64", Check.ge(1)),
        "capacity_ah": Column(float, Check.in_range(0.0, 3.0)),
        "soh_pct_self": Column(float, Check.in_range(0.0, 130.0)),
        "soh_pct_rated": Column(float, Check.in_range(0.0, 130.0)),
        "discharge_duration_s": Column(float, Check.gt(0), nullable=True),
        "energy_wh": Column(float, Check.ge(0), nullable=True),
        "cc_duration_s": Column(float, Check.ge(0), nullable=True),
        "cv_duration_s": Column(float, Check.ge(0), nullable=True),
        "rul_cycles": Column(float, nullable=True),
        "is_censored": Column(bool),
        "qc_flags": Column(str),
        # Impedance is joined backward-only; a negative staleness would mean a
        # future measurement leaked into the row.
        "impedance_age_ops": Column(float, Check.ge(0), nullable=True),
    },
    strict=False,
    coerce=True,
)

IMPEDANCE = DataFrameSchema(
    {
        "battery_id": Column(str),
        "op_index": Column("int64", Check.ge(0)),
        "re_ohm": Column(float, nullable=True),
        "rct_ohm": Column(float, nullable=True),
    },
    strict=False,
    coerce=True,
)

SCHEMAS = {
    "telemetry": TELEMETRY,
    "cycle_summary": CYCLE_SUMMARY,
    "impedance": IMPEDANCE,
}


def validate(name: str, df):
    """Validate a named table, returning the (possibly coerced) frame."""
    if name not in SCHEMAS:
        return df
    return SCHEMAS[name].validate(df, lazy=True)
