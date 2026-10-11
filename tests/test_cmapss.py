import json

import pytest

from app.ml import cmapss
from app.ml.sensors import SENSOR_NAMES

needs_data = pytest.mark.skipif(
    not cmapss.data_available(), reason="NASA FD001 files not in data/cmapss"
)


# ---- synthetic fallback (always runs) -------------------------------------
def test_synthetic_is_deterministic():
    a = cmapss.synthetic_trajectories(seed=7, n_units=5)
    b = cmapss.synthetic_trajectories(seed=7, n_units=5)
    assert a == b
    assert a != cmapss.synthetic_trajectories(seed=8, n_units=5)


def test_synthetic_shape_and_keys():
    t = cmapss.synthetic_trajectories(seed=1, n_units=3)
    assert list(t) == [1, 2, 3]
    for rows in t.values():
        assert 150 <= len(rows) <= 350
        assert [r["cycle"] for r in rows] == list(range(1, len(rows) + 1))
        assert rows[-1]["true_rul"] == 0
        assert all(list(r["values"]) == SENSOR_NAMES for r in rows)


def test_synthetic_is_json_serialisable():
    json.dumps(cmapss.synthetic_trajectories(seed=1, n_units=1)[1])


def test_synthetic_train_and_test_populations_differ():
    tr = cmapss.trajectories("train", synthetic=True)
    te = cmapss.trajectories("test", synthetic=True)
    assert tr[1][0]["values"] != te[1][0]["values"]


# ---- real data -------------------------------------------------------------
@needs_data
def test_real_shapes():
    tr, te = cmapss.load_train(), cmapss.load_test()
    assert (len(tr), tr.unit.nunique()) == (20631, 100)
    assert (len(te), te.unit.nunique()) == (13096, 100)
    assert tr.isna().sum().sum() == 0 and te.isna().sum().sum() == 0


@needs_data
def test_train_rul_is_zero_at_failure_and_clipped_target_is_capped():
    tr = cmapss.load_train()
    assert tr.groupby("unit").rul.min().eq(0).all()
    assert tr.rul_clipped.max() == cmapss.RUL_CAP
    assert tr.rul.max() > cmapss.RUL_CAP  # unclipped value is kept


@needs_data
def test_test_rul_matches_nasa_file_at_last_cycle():
    te, final = cmapss.load_test(), cmapss.load_final_rul()
    last = te.sort_values("cycle").groupby("unit").tail(1).set_index("unit").rul
    assert (last.sort_index() == final.sort_index()).all()


@needs_data
def test_train_split_is_by_unit_disjoint_and_deterministic():
    fit, calib = cmapss.split_train_units()
    assert set(fit).isdisjoint(calib)
    assert sorted(fit + calib) == list(range(1, 101))
    assert (fit, calib) == cmapss.split_train_units()


@needs_data
def test_trajectories_default_is_the_held_out_test_pile():
    t = cmapss.trajectories()
    assert len(t) == 100
    assert t[41][-1]["true_rul"] == 18  # golden-engine candidate, RUL file value
    for rows in t.values():
        assert [r["cycle"] for r in rows] == list(range(1, len(rows) + 1))
        assert all(list(r["values"]) == SENSOR_NAMES for r in rows)
        assert all(r["true_rul"] == rows[-1]["true_rul"] + (len(rows) - r["cycle"]) for r in rows)
