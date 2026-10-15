"""Run the v2 simulator sensitivity harness: ``python -m scripts.run_sensitivity [--quick] [--out out/sensitivity]``."""
from __future__ import annotations

import argparse
import sys

from app.sim.v2 import sensitivity


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="tiny fleet, 2 seeds, 6 params (CI)")
    ap.add_argument("--out", default="out/sensitivity", help="output directory")
    ap.add_argument("--max-params", type=int, default=None, help="limit tornado parameters (round-robin over groups)")
    ap.add_argument("--include-anchors", action="store_true",
                    help="also perturb anchor/structural parameters (excluded by default; effects are scale artifacts)")
    a = ap.parse_args(argv)
    overrides = {"max_params": a.max_params} if a.max_params else None
    res = sensitivity.run_all(quick=a.quick, overrides=overrides, include_anchors=a.include_anchors, progress=lambda m: print(m, file=sys.stderr))
    for p in sensitivity.write_outputs(res, a.out):
        print(p)
    print(f"runtime {res['runtime_s']['total']:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
