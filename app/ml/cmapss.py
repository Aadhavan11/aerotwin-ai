"""NASA C-MAPSS FD001 loader and engine trajectory source.

Data flow and the split that keeps the demo honest
--------------------------------------------------
FD001 ships two piles of 100 engines:

* ``train``: run to failure. Used to fit and calibrate the models. Split once,
  by unit, into fit units and calibration units (``split_train_units``).
* ``test``: truncated before failure, with the true remaining life of each
  engine in ``RUL_FD001.txt``. Never used for fitting or calibration.

The simulator replays ``test`` engines (``trajectories()`` default), so every
prediction shown in the demo is on an engine the models never learned from,
and the true RUL is known for each row.

If the NASA files are missing, ``synthetic_trajectories`` generates
C-MAPSS-shaped run-to-failure data instead. ``data_source()`` says which one
is in use; anything user-facing must show it.

RUL conventions (do not mix them): ``true_rul`` is the real remaining cycles,
unclipped. ``rul_clipped`` is ``min(true_rul, RUL_CAP)`` and is only a
training target.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from app.ml.sensors import COLUMN_TO_NAME, RAW_COLUMNS, SENSOR_NAMES

SEED = 1234
RUL_CAP = 125

DATA_DIR = Path(
    os.environ.get("AEROTWIN_DATA_DIR")
    or Path(__file__).resolve().parents[2] / "data" / "cmapss"
)
FILES = ("train_FD001.txt", "test_FD001.txt", "RUL_FD001.txt")
# Mirror of the NASA files. Licence field on the mirror reads "other";
# record the NASA Prognostics Data Repository as the original in DATA-SOURCES.md.
SOURCE_URL = (
    "https://huggingface.co/datasets/SoyVitou/NASA-C-MAPSS-Turbofan-Engine"
    "/resolve/main/data"
)

Split = Literal["train", "test"]


# --------------------------------------------------------------------------
# Real data
# --------------------------------------------------------------------------
def data_available() -> bool:
    return all((DATA_DIR / f).is_file() for f in FILES)


def data_source() -> str:
    return "nasa-cmapss-fd001" if data_available() else "synthetic-cmapss-shaped"


def _read_raw(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Run: python -m app.ml.cmapss --download"
        )
    df = pd.read_csv(path, sep=r"\s+", header=None, names=RAW_COLUMNS)
    df = df.rename(columns=COLUMN_TO_NAME)
    return df[["unit", "cycle", *SENSOR_NAMES]].sort_values(["unit", "cycle"]).reset_index(drop=True)


def load_final_rul() -> pd.Series:
    """True remaining cycles at the last observed cycle of each test unit."""
    path = DATA_DIR / "RUL_FD001.txt"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run: python -m app.ml.cmapss --download")
    rul = pd.read_csv(path, header=None, names=["rul"])["rul"]
    rul.index = pd.RangeIndex(1, len(rul) + 1, name="unit")
    return rul


def load_train() -> pd.DataFrame:
    """Train engines with ``rul`` (true) and ``rul_clipped`` columns."""
    df = _read_raw("train_FD001.txt")
    last = df.groupby("unit")["cycle"].transform("max")
    df["rul"] = last - df["cycle"]
    df["rul_clipped"] = df["rul"].clip(upper=RUL_CAP)
    return df


def load_test() -> pd.DataFrame:
    """Test engines with ``rul`` (true) and ``rul_clipped`` for every row.

    NASA gives the RUL only at each unit's last cycle; earlier rows follow as
    ``final_rul + (last_cycle - cycle)``. This labels every window, which lets
    anomaly metrics use thousands of windows instead of 100.
    """
    df = _read_raw("test_FD001.txt")
    final = load_final_rul()
    last = df.groupby("unit")["cycle"].transform("max")
    df["rul"] = df["unit"].map(final) + (last - df["cycle"])
    df["rul_clipped"] = df["rul"].clip(upper=RUL_CAP)
    return df


def split_train_units(
    seed: int = SEED, calib_frac: float = 0.15
) -> tuple[list[int], list[int]]:
    """Deterministic split of train units into (fit, calibration), by unit.

    Splitting by unit, never by row, because windows of one engine are
    strongly correlated and a row split leaks.
    """
    units = np.array(sorted(_read_raw("train_FD001.txt")["unit"].unique()))
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(units)
    n_calib = max(1, int(round(len(units) * calib_frac)))
    calib = sorted(int(u) for u in shuffled[:n_calib])
    fit = sorted(int(u) for u in shuffled[n_calib:])
    return fit, calib


def _rows(df: pd.DataFrame) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    values = df[SENSOR_NAMES].to_numpy(dtype="float64")
    cycles = df["cycle"].to_numpy()
    ruls = df["rul"].to_numpy()
    units = df["unit"].to_numpy()
    for i in range(len(df)):
        out.setdefault(int(units[i]), []).append(
            {
                "cycle": int(cycles[i]),
                "values": {n: float(v) for n, v in zip(SENSOR_NAMES, values[i])},
                "true_rul": int(ruls[i]),
            }
        )
    return out


# --------------------------------------------------------------------------
# Synthetic fallback (C-MAPSS-shaped)
# --------------------------------------------------------------------------
# Per-sensor constants measured on train_FD001.txt (first 10 cycles, last 10
# cycles, first 30 cycles): healthy mean, spread between engines, mean shift
# at end of life, within-engine noise. These make the fallback look like the
# real data; it is still synthetic and must be labelled as such.
_SYNTH: dict[str, tuple[float, float, float, float]] = {
    #        healthy_mean, unit_spread, eol_delta, noise_std
    "T24": (642.374, 0.2518, +1.224, 0.2972),
    "T30": (1586.8, 2.542, +14.64, 3.958),
    "T50": (1402.65, 4.483, +24.75, 3.898),
    "P30": (553.958, 0.5032, -2.33, 0.4),
    "Nf": (2388.06, 0.04667, +0.1625, 0.03086),
    "Nc": (9055.99, 7.495, +39.55, 4.164),
    "Ps30": (47.35, 0.1484, +0.7492, 0.09974),
    "phi": (521.921, 0.4333, -1.991, 0.2986),
    "NRf": (2388.06, 0.04729, +0.1661, 0.02946),
    "NRc": (8137.33, 7.451, +27.83, 3.062),
    "BPR": (8.41804, 0.01848, +0.09706, 0.01972),
    "htBleed": (392.253, 0.7497, +3.789, 0.9148),
    "W31": (38.9355, 0.09007, -0.4644, 0.09821),
    "W32": (23.3621, 0.05346, -0.2784, 0.06012),
}
_DRIFT_SHARPNESS = 4.0  # exponential: slow at first, fast near failure


def synthetic_trajectories(
    seed: int = SEED, n_units: int = 100, life_range: tuple[int, int] = (150, 350)
) -> dict[int, list[dict]]:
    """Run-to-failure data shaped like C-MAPSS: ``x(t) = base + delta * g(t/T) + noise``.

    ``g`` is an exponential ramp from 0 to 1. Engines fail at cycle ``T``
    (``true_rul`` is 0 on the last row). Deterministic for a given seed.
    """
    rng = np.random.default_rng(seed)
    b = _DRIFT_SHARPNESS
    out: dict[int, list[dict]] = {}
    for unit in range(1, n_units + 1):
        life = int(rng.integers(life_range[0], life_range[1] + 1))
        base = {n: rng.normal(_SYNTH[n][0], _SYNTH[n][1]) for n in SENSOR_NAMES}
        wear = {n: _SYNTH[n][2] * rng.normal(1.0, 0.15) for n in SENSOR_NAMES}
        t = np.arange(1, life + 1)
        ramp = (np.exp(b * t / life) - 1.0) / (np.exp(b) - 1.0)
        series = {
            n: base[n] + wear[n] * ramp + rng.normal(0.0, _SYNTH[n][3], size=life)
            for n in SENSOR_NAMES
        }
        out[unit] = [
            {
                "cycle": int(t[i]),
                "values": {n: float(series[n][i]) for n in SENSOR_NAMES},
                "true_rul": int(life - t[i]),
            }
            for i in range(life)
        ]
    return out


# --------------------------------------------------------------------------
# Public entry point used by the simulator
# --------------------------------------------------------------------------
def trajectories(
    split: Split = "test", *, synthetic: bool | None = None, seed: int = SEED
) -> dict[int, list[dict]]:
    """``unit -> ordered rows {cycle, values: {sensor: float}, true_rul}``.

    ``split="test"`` (default) returns engines the models never learned from;
    use this for the simulation. ``split="train"`` is for training code only.
    ``synthetic=None`` falls back to generated data when the NASA files are
    absent; ``True`` forces it. Units are in ascending order and rows in
    ascending cycle order, so replay is deterministic.
    """
    if synthetic is None:
        synthetic = not data_available()
    if synthetic:
        # Different seeds keep the synthetic "train" and "test" populations disjoint.
        return synthetic_trajectories(seed + (0 if split == "train" else 1))
    return _rows(load_train() if split == "train" else load_test())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def download() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = DATA_DIR / name
        tmp = dest.with_suffix(".part")
        print(f"fetching {name} ...", flush=True)
        with urllib.request.urlopen(f"{SOURCE_URL}/{name}", timeout=60) as r:
            tmp.write_bytes(r.read())
        tmp.replace(dest)
    print(f"saved to {DATA_DIR}")


def check() -> None:
    print("data source:", data_source())
    if data_available():
        tr, te = load_train(), load_test()
        fit, calib = split_train_units()
        print(f"train rows={len(tr)} units={tr.unit.nunique()}  fit={len(fit)} calib={len(calib)}")
        print(f"test  rows={len(te)} units={te.unit.nunique()}")
    else:
        print(f"NASA files not found in {DATA_DIR}; run with --download")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--download", action="store_true", help="fetch FD001 files into data/cmapss")
    p.add_argument("--check", action="store_true", help="print what was found")
    args = p.parse_args()
    if args.download:
        download()
    if args.check or not args.download:
        check()
    sys.exit(0)
