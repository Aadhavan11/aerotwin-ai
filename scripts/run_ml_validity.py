"""Run the ML validity pack and the detection back-test; outputs go to out/ml-validity/ (gitignored).

    python scripts/run_ml_validity.py --quick     # tiny training, 2 fault modes, < 90 s
    python scripts/run_ml_validity.py             # full: default training config + whole fault library (tens of minutes)
    python scripts/run_ml_validity.py --only backtest|validity
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("out/ml-validity"))
    ap.add_argument("--only", choices=("backtest", "validity"))
    a = ap.parse_args(argv)
    from app.ml import backtest, validity

    a.out.mkdir(parents=True, exist_ok=True)
    if a.only != "validity":
        res = backtest.run_backtest(backtest.quick_config() if a.quick else backtest.BacktestConfig(), log_fn=print)
        (a.out / "backtest.json").write_text(json.dumps(res, indent=1, default=float))
        (a.out / "backtest.md").write_text(backtest.to_markdown(res))
        print("wrote", a.out / "backtest.json")
    if a.only != "backtest":
        cfg = validity.quick_config() if a.quick else validity.tg.default_config()
        res = validity.run_validity(cfg, aged_seeds=(501,) if a.quick else (501, 502), n_boot=200 if a.quick else 1000, log_fn=print)
        for p in validity.write_outputs(res, a.out):
            print("wrote", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
