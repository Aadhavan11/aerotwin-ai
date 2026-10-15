"""Export a small SIMULATED fleet through the generic ingestion formats."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

# Support direct execution as well as importing from the repository root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest import load_mapping
from app.sim.clock import START
from app.sim.v2.drivers import LEGS_PER_DAY
from app.sim.v2.world import World

N_CYCLES = int(365 * LEGS_PER_DAY)
MAPPINGS = Path(__file__).resolve().parents[1] / "app" / "ingest" / "mappings"
MESS_DEFAULTS = {
    "duplicate_rate": 0.0, "missing_serial_rate": 0.0, "swapped_date_rate": 0.0,
    "hhmm_hours": False, "mixed_timezone_rate": 0.0, "blank_marker_rate": 0.0,
}


def _mess_config(mess: dict[str, Any]) -> dict[str, Any]:
    unknown = set(mess) - MESS_DEFAULTS.keys()
    if unknown:
        raise ValueError(f"unknown mess keys: {', '.join(sorted(unknown))}")
    config = {**MESS_DEFAULTS, **mess}
    for key, value in config.items():
        if key == "hhmm_hours":
            if not isinstance(value, bool):
                raise ValueError("hhmm_hours must be a bool")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be a rate between 0 and 1")
    return config


def generate(out_dir: Path, seed: int, mess: dict[str, Any]) -> dict[str, Any]:
    """Generate 2,190 world cycles (365 days), retaining sparse real telemetry.

    N_CYCLES is a module constant so tests can shorten the simulation without
    changing the CLI's one-year default. Operational days are counted from START.
    """
    config = _mess_config(mess)
    rng = random.Random(seed)
    tails = [f"SIM-{index:03d}" for index in range(1, 5)]
    world = World(seed, tails, stochastic=False)
    tables: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ("removals", "utilisation", "defects", "workorders", "acms", "qar")}
    installed = {serial: START.isoformat() for serial in world.registry.parts}
    daily: dict[tuple[str, str], dict[str, Any]] = {}
    removal_index = 0
    for cycle in range(1, N_CYCLES + 1):
        telemetry = world.run(1)
        ctx = world._ctx
        day = (START + timedelta(days=(cycle - 1) // LEGS_PER_DAY)).date().isoformat()
        for index, tail in enumerate(tails):
            row = daily.setdefault((tail, day), {"tail": tail, "date": day, "flight_hours": 0.0, "cycles": 0, "apu_hours": 0.0})
            row["flight_hours"] += float(ctx.flight_hours[index])
            row["cycles"] += 1
            row["apu_hours"] += float(ctx.apu_hours[index])
        for removal in world.removals()[removal_index:]:
            reason = "nff_suspected" if removal.nff else "unscheduled" if removal.unscheduled else "scheduled"
            tables["removals"].append({
                "part_number": world.registry.parts[removal.serial].part_no, "serial": removal.serial, "tail": removal.tail,
                "position": removal.position, "installed_at": installed[removal.serial], "removed_at": removal.time,
                "tsn_h": removal.tsn_h, "csn": int(removal.csn), "removal_reason": reason, "scheduled": not removal.unscheduled,
                "shop_finding": "nff" if removal.nff else "no_data", "ata_chapter": None,
            })
            # Repairs, overhauls and NFF returns keep the serial; replacements get a new one.
            installed[removal.new_serial or removal.serial] = removal.time
            tables["workorders"].append({
                "wo_id": f"SIM-WO-{len(tables['workorders']) + 1:06d}", "tail": removal.tail,
                "opened_at": removal.time, "closed_at": removal.time, "ata": None,
                "task_text": removal.reason_text, "kind": removal.action,
            })
        removal_index = len(world.removals())
        if cycle == 1 or cycle % int(30 * LEGS_PER_DAY) == 0 or cycle == N_CYCLES:
            sampled = set()
            for row in telemetry:
                if row["tail"] in sampled:
                    continue
                sampled.add(row["tail"])
                for parameter, value in sorted(row["values"].items()):
                    tables["acms"].append({"tail": row["tail"], "ts": row["ts"], "phase": None,
                                           "parameter": f"{row['component_id'].split('/', 1)[1]}/{parameter}", "value": value})
                parameter = sorted(row["values"])[0]
                tables["qar"].append({"tail": row["tail"], "ts": row["ts"], "parameter": parameter, "value": row["values"][parameter], "unit": None})
        world.rows.clear()
    tables["utilisation"] = list(daily.values())
    truth = {
        "provenance": "SIMULATED", "seed": seed, "tails": tails, "n_cycles": N_CYCLES,
        "calendar_days": float(world._ctx.calendar_days[0]), "component_kinds": world.kinds,
        "faults": world.ground_truth(), "clean_removal_count": len(tables["removals"]), "mess": config,
    }
    messy_removals = []
    for clean in tables["removals"]:
        row = dict(clean)
        if rng.random() < config["missing_serial_rate"]:
            row["serial"] = ""
        if rng.random() < config["swapped_date_rate"]:
            row["installed_at"], row["removed_at"] = row["removed_at"], row["installed_at"]
        messy_removals.append(row)
        if rng.random() < config["duplicate_rate"]:
            messy_removals.append(dict(row))
    tables["removals"] = messy_removals
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for kind, rows in tables.items():
        mapping = load_mapping(MAPPINGS / f"{kind}_generic.yaml")
        path = out_dir / f"{kind}.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(mapping.columns), lineterminator="\n")
            writer.writeheader()
            for clean in rows:
                row = dict(clean)
                for key, value in row.items():
                    if mapping.columns[key].type == "datetime" and value is not None and rng.random() < config["mixed_timezone_rate"]:
                        dt = datetime.fromisoformat(value)
                        row[key] = dt.astimezone(timezone(timedelta(hours=2))).isoformat() if rng.random() < 0.5 else dt.replace(tzinfo=None).isoformat()
                    if key in {"apu_hours", "tsn_h", "csn"} and value is not None and rng.random() < config["blank_marker_rate"]:
                        row[key] = rng.choice(["N/A", "-"])
                if kind == "utilisation" and config["hhmm_hours"]:
                    # Mess mode deliberately rounds to a minute; clean numeric hours stay lossless.
                    minutes = round(row["flight_hours"] * 60)
                    row["flight_hours"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
                writer.writerow(row)
        paths.append(path)
    truth_path = out_dir / "ground_truth.json"
    truth_path.write_text(json.dumps(truth, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    paths.append(truth_path)
    manifest = {"files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return truth


def main(argv: Sequence[str] | None = None) -> None:
    """CLI: --mess accepts either a JSON object or a path to one."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--mess", default="{}")
    args = parser.parse_args(argv)
    raw = args.mess.strip()
    config = json.loads(raw if raw.startswith("{") else Path(raw).read_text(encoding="utf-8"))
    generate(args.out, args.seed, config)


if __name__ == "__main__":
    main()
