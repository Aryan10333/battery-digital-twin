import os, glob, warnings, json
import numpy as np
from scipy.io import loadmat
warnings.filterwarnings("ignore")

ROOT = "data/raw/nasa/extracted"
rows = []
for path in sorted(glob.glob(os.path.join(ROOT, "*", "*.mat"))):
    bundle = os.path.basename(os.path.dirname(path)).split(".")[0].strip()
    bid = os.path.splitext(os.path.basename(path))[0]
    try:
        m = loadmat(path, simplify_cells=True)
    except Exception as e:
        rows.append({"bid": bid, "bundle": bundle, "error": f"{type(e).__name__}"})
        continue
    key = [k for k in m if not k.startswith("__")]
    if not key:
        rows.append({"bid": bid, "bundle": bundle, "error": "no struct"}); continue
    b = m[key[0]]
    cyc = b["cycle"] if isinstance(b, dict) and "cycle" in b else None
    if cyc is None:
        rows.append({"bid": bid, "bundle": bundle, "error": "no cycle"}); continue
    cyc = np.atleast_1d(cyc)
    types, amb, caps = [], [], []
    for c in cyc:
        try:
            t = c["type"]; types.append(t); amb.append(float(c["ambient_temperature"]))
            if t == "discharge":
                d = c["data"]
                if "Capacity" in d:
                    v = np.atleast_1d(d["Capacity"]).astype(float)
                    caps.append(float(v.flat[0]) if v.size else np.nan)
        except Exception:
            pass
    caps = np.array([c for c in caps], dtype=float)
    valid = caps[~np.isnan(caps)] if caps.size else np.array([])
    nd = types.count("discharge")
    rec = {
        "bid": bid, "bundle": bundle, "struct": key[0],
        "n_cycles": len(cyc), "charge": types.count("charge"),
        "disch": nd, "imped": types.count("impedance"),
        "amb": sorted(set(int(a) for a in amb)) if amb else [],
        "cap_n": int(valid.size),
    }
    if valid.size:
        first, last = valid[0], valid[-1]
        rec.update({
            "cap_first": round(float(first), 3), "cap_last": round(float(last), 3),
            "cap_min": round(float(valid.min()), 3), "cap_max": round(float(valid.max()), 3),
            "fade_pct": round(float((1 - last / first) * 100), 1),
            "hits_1p4": bool((valid <= 1.4).any()),
            "hits_1p6": bool((valid <= 1.6).any()),
            "regen": int((np.diff(valid) > 0).sum()),
            "regen_max": round(float(np.diff(valid).max()), 4) if valid.size > 1 else None,
        })
    rows.append(rec)

hdr = f"{'battery':8s} {'bun':4s} {'cyc':>5s} {'ch':>4s} {'dis':>4s} {'imp':>4s} {'amb':>10s} {'capN':>5s} {'first':>6s} {'last':>6s} {'min':>6s} {'fade%':>6s} {'EOL1.4':>6s} {'regen':>5s}"
print(hdr); print("-" * len(hdr))
for r in rows:
    if "error" in r:
        print(f"{r['bid']:8s} {r['bundle'][:4]:4s} ERROR {r['error']}"); continue
    amb = ",".join(str(a) for a in r["amb"])
    if r["cap_n"]:
        print(f"{r['bid']:8s} {r['bundle'][:4]:4s} {r['n_cycles']:5d} {r['charge']:4d} {r['disch']:4d} {r['imped']:4d} {amb:>10s} {r['cap_n']:5d} {r['cap_first']:6.3f} {r['cap_last']:6.3f} {r['cap_min']:6.3f} {r['fade_pct']:6.1f} {str(r['hits_1p4']):>6s} {r['regen']:5d}")
    else:
        print(f"{r['bid']:8s} {r['bundle'][:4]:4s} {r['n_cycles']:5d} {r['charge']:4d} {r['disch']:4d} {r['imped']:4d} {amb:>10s} {'0':>5s}  -- no capacity --")

with open("reports/audit_raw_nasa.json", "w") as f:
    json.dump(rows, f, indent=2, default=str)
