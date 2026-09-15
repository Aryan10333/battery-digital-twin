"""Build the canonical dataset from raw NASA .mat files.

Regenerates everything under data/processed/ from data/raw/ in one command:

    python -m src.ingestion.build_dataset
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.config import load_config, resolve
from src.ingestion.nasa_loader import parse_battery
from src.preprocessing.quality import check_telemetry, flag_cycles, regeneration_summary
from src.preprocessing.targets import add_targets


def build(tier: str = "tier1", write: bool = True) -> dict[str, pd.DataFrame]:
    cfg = load_config()
    group = cfg[tier]
    raw_dir = resolve("raw_nasa") / group["bundle"]

    tel_parts, cyc_parts, imp_parts = [], [], []
    for bid in group["batteries"]:
        path = raw_dir / f"{bid}.mat"
        if not path.exists():
            raise FileNotFoundError(f"missing raw file: {path}")
        parsed = parse_battery(path, bid, cfg)
        tel_parts.append(parsed.telemetry)
        cyc_parts.append(parsed.cycles)
        if not parsed.impedance.empty:
            imp_parts.append(parsed.impedance)
        print(f"  {bid}: {len(parsed.cycles):4d} discharge cycles, "
              f"{len(parsed.telemetry):7d} telemetry rows, "
              f"{len(parsed.impedance):4d} impedance measurements")

    telemetry = pd.concat(tel_parts, ignore_index=True)
    cycles = pd.concat(cyc_parts, ignore_index=True)
    impedance = pd.concat(imp_parts, ignore_index=True) if imp_parts else pd.DataFrame()

    tel_qc = check_telemetry(telemetry, cfg)
    cycles = flag_cycles(cycles, cfg, tel_qc)
    cycles = add_targets(cycles, cfg)
    regen = regeneration_summary(cycles)

    tables = {"telemetry": telemetry, "cycle_summary": cycles,
              "impedance": impedance, "regeneration_summary": regen}

    if write:
        out = resolve("processed")
        out.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            if df.empty:
                continue
            df.to_parquet(out / f"{name}.parquet", index=False)
            print(f"  wrote {name}.parquet  rows={len(df):>7d} cols={df.shape[1]}")

        summary = {
            "tier": tier,
            "batteries": list(group["batteries"]),
            "n_cycles": int(len(cycles)),
            "n_telemetry_rows": int(len(telemetry)),
            "per_battery": {
                bid: {
                    "cycles": int((cycles.battery_id == bid).sum()),
                    "eol_cycle": (None if pd.isna(g["eol_cycle"].iloc[0])
                                  else int(g["eol_cycle"].iloc[0])),
                    "censored": bool(g["is_censored"].iloc[0]),
                    "capacity_first_ah": round(float(g["capacity_ah"].iloc[0]), 4),
                    "capacity_last_ah": round(float(g["capacity_ah"].iloc[-1]), 4),
                    "qc_flagged_cycles": int(g["qc_any"].sum()),
                }
                for bid, g in cycles.groupby("battery_id", sort=True)
            },
        }
        rep = resolve("reports")
        rep.mkdir(parents=True, exist_ok=True)
        (rep / "build_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"  wrote {rep / 'build_summary.json'}")

    return tables


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", default="tier1", help="config key naming the battery group")
    ap.add_argument("--no-write", action="store_true", help="parse but do not write parquet")
    args = ap.parse_args()
    print(f"Building canonical dataset ({args.tier})")
    build(tier=args.tier, write=not args.no_write)
    print("done.")


if __name__ == "__main__":
    main()
