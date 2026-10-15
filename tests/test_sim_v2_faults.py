import numpy as np

from app.sim.v2.faults import LIBRARY, NO_ONSET, FaultInjector, FaultMode, modes_for, sensor_fault_layer

KINDS = ["engine", "brakes", "tires", "landing_gear", "hydraulics", "electrical", "avionics", "apu", "ecs"]


def test_library_complete():
    for k in KINDS:
        assert len(modes_for(k)) >= 2
    assert len(LIBRARY) == 18
    assert hasattr(sensor_fault_layer, "start_fault")


def test_hazard_statistics():
    mode = FaultMode("m", "k", "00", base_hazard=0.01, ramp_cycles=10)
    n, T = 4000, 50
    inj = FaultInjector(5, {"k": n}, library={"m": mode})
    st = {"age_cycles": np.zeros(n)}
    for c in range(T):
        inj.step("k", st, c)
    frac = float((inj.onset[("k", "m")] != NO_ONSET).mean())
    expect = 1 - np.exp(-0.01 * T)
    assert abs(frac - expect) < 0.03


def test_severity_ramp_monotone_and_ground_truth():
    for shape in ("linear", "exponential"):
        mode = FaultMode("m", "k", "00", base_hazard=0.2, ramp_cycles=20, shape=shape)
        inj = FaultInjector(1, {"k": 30}, library={"m": mode})
        st: dict = {}
        prev = np.zeros(30)
        for c in range(60):
            inj.step("k", st, c)
            s = st["fault_m"]
            assert np.all(s >= prev) and s.max() <= 1.0
            prev = s.copy()
        gt = inj.ground_truth()
        assert len(gt) == int((inj.onset[("k", "m")] != NO_ONSET).sum()) > 0
        for g in gt:
            tr = [v for _, v in g["severity_trajectory"]]
            assert g["provenance"] == "SIMULATED" and g["onset_cycle"] == g["severity_trajectory"][0][0]
            assert tr == sorted(tr) and tr[-1] == st["fault_m"][g["unit"]]


def test_driver_and_age_raise_hazard():
    m = FaultMode("m", "k", "00", 0.01, age_ref_cycles=100, driver="d", driver_gain=2.0)
    assert m.hazard(100.0, 0.5) > m.hazard(0.0, 0.0)
    assert float(m.hazard(0.0, 0.0)) == 0.01


def test_clear_logs_and_resets():
    mode = FaultMode("m", "k", "00", 50.0, ramp_cycles=5)
    inj = FaultInjector(1, {"k": 3}, library={"m": mode})
    st: dict = {}
    inj.step("k", st, 0)
    inj.clear("k", [1], 4, st)
    assert st["fault_m"][1] == 0 and inj.onset[("k", "m")][1] == NO_ONSET
    assert [g["cleared_cycle"] for g in inj.ground_truth() if g["unit"] == 1] == [4]


def test_determinism_and_resume_bit_identity():
    def run(cuts):
        inj = FaultInjector(9, {"engine": 40, "brakes": 40})
        st = {"engine": {}, "brakes": {}}
        snap = None
        for c in range(300):
            if c in cuts:
                snap = inj.snapshot()
                inj = FaultInjector(9, {"engine": 40, "brakes": 40})
                inj.restore(snap)
            for k in st:
                inj.step(k, st[k], c, age=np.full(40, c * 5.0))
        return inj.state_hash()

    assert run(set()) == run(set()) == run({100, 200})
    assert run(set()) != FaultInjector(9, {"engine": 40, "brakes": 40}).state_hash()
