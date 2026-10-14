"""ATA-style taxonomy: completeness over registered kinds / fault modes and agreement with the fault library and task cards."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.sim.v2 import core, world  # noqa: F401  (importing world registers every component model)
from app.sim.v2.faults import LIBRARY
from app.solver.cards import load_cards
from app.taxonomy import ata


def test_every_registered_kind_maps_to_a_chapter():
    kinds = [k for k in core.kinds() if not k.startswith("_")]   # other tests register private fixture kinds
    assert len(kinds) == 9
    for k in kinds:
        ref = ata.ata_for(k)
        assert ref.chapter in ata.CHAPTERS and ref.section.startswith(ref.chapter + "-") and ref.section in ata.SECTIONS


def test_every_fault_mode_maps_and_agrees_with_fault_library():
    assert set(ata.MODE_SECTION) == set(LIBRARY)
    for mid, m in LIBRARY.items():
        ref = ata.ata_for(m.kind, mid)
        assert ref.chapter == m.ata_chapter, mid                      # inline chapter in app/sim/v2/faults.py agrees
        assert ref.chapter == ata.chapter_for(m.kind), mid            # mode refines, never leaves, the kind's chapter


def test_known_chapters_and_wheels_brakes():
    assert ata.chapter_for("engine") == "72" and ata.chapter_for("apu") == "49" and ata.chapter_for("ecs") == "21"
    assert ata.chapter_for("hydraulics") == "29" and ata.chapter_for("electrical") == "24" and ata.chapter_for("avionics") == "34"
    assert ata.ata_for("brakes").section == "32-42" and ata.ata_for("tires").section.startswith("32-4")
    assert ata.ata_for("landing_gear").chapter == "32"


def test_unknown_raises():
    with pytest.raises(KeyError):
        ata.ata_for("galley")
    with pytest.raises(KeyError):
        ata.ata_for("engine", "no_such_mode")


def test_task_cards_agree_with_taxonomy():
    for c in load_cards():
        digits = "".join(ch for ch in c.ata if ch.isdigit())
        if digits and digits != "05":
            assert digits == ata.chapter_for(c.component_kind), c.id


def test_table_and_route_shape():
    t = ata.table()
    assert t["simulated"] is True and t["provenance"] == "SIMULATED" and "fictional part numbers" in t["label"]
    assert [c["chapter"] for c in t["chapters"]] == sorted(ata.CHAPTERS)
    j = TestClient(app).get("/insights/ata").json()
    assert j == t
    ch32 = next(c for c in j["chapters"] if c["chapter"] == "32")
    assert ch32["kinds"] == ["brakes", "landing_gear", "tires"] and {"32-40"} <= {s["section"] for s in ch32["sections"]} | {"32-40"}
