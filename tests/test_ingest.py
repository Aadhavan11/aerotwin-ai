"""Generic ingestion, simulator exports, and data-quality regression tests."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.ingest import (
    AcmsSnapshot, Aircraft, DefectLog, MappingError, PartInstallEvent, PartRemovalEvent, QarParameterRow,
    RowError, ShopVisit, UtilisationDay, WorkOrder, load_mapping, read_export, report_to_json, validate_records,
)
from app.sim.clock import START
from app.sim.v2.drivers import LEGS_PER_DAY
from app.sim.v2.world import World
from scripts import make_synthetic_exports as synthetic

MAPPINGS = Path(__file__).resolve().parents[1] / "app/ingest/mappings"
COMMON = {"source": "generic-test", "provenance": "operator-export"}
KINDS = ("removals", "utilisation", "defects", "workorders", "acms", "qar")


@pytest.fixture(autouse=True)
def short_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(synthetic, "N_CYCLES", 90)


def mapping(kind: str) -> Any:
    return load_mapping(MAPPINGS / f"{kind}_generic.yaml")


def load_exports(directory: Path) -> dict[str, Any]:
    return {kind: read_export(directory / f"{kind}.csv", mapping(kind), provenance="SIMULATED") for kind in KINDS}


def check(report: Any, name: str) -> Any:
    return next(c for c in report.checks if c.name == name)


def removal(**values: Any) -> PartRemovalEvent:
    return PartRemovalEvent(**{**COMMON, "part_number": "PN", "serial": "SN1", "tail": "T1", "position": "P1",
                               "installed_at": START, "removed_at": START + timedelta(days=100), "removal_reason": "scheduled", **values})


def write_rows(path: Path, headers: list[str], rows: list[dict[str, Any]], delimiter: str = ",") -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, headers, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def test_clean_round_trip_against_world(tmp_path: Path) -> None:
    truth = synthetic.generate(tmp_path, 7, {})
    exports = load_exports(tmp_path)
    assert all(not result.errors for result in exports.values())
    world = World(7, truth["tails"], stochastic=False)
    installed = {serial: START for serial in world.registry.parts}
    daily: dict[tuple[str, str], list[float]] = {}
    sampled = []
    for cycle in range(1, truth["n_cycles"] + 1):
        rows = world.run(1)
        if cycle in {1, truth["n_cycles"]}:
            sampled.extend(rows)
        day = (START + timedelta(days=(cycle - 1) // LEGS_PER_DAY)).date().isoformat()
        for i, tail in enumerate(world.tails):
            sums = daily.setdefault((tail, day), [0.0, 0, 0.0])
            sums[0] += float(world._ctx.flight_hours[i])
            sums[1] += 1
            sums[2] += float(world._ctx.apu_hours[i])
    actual = exports["removals"].records
    assert len(actual) == truth["clean_removal_count"] == len(world.removals()) > 0
    for parsed, original in zip(actual, world.removals()):
        assert (parsed.serial, parsed.tail) == (original.serial, original.tail)
        expected = "nff_suspected" if original.nff else "unscheduled" if original.unscheduled else "scheduled"
        assert parsed.removal_reason == expected
        assert parsed.removed_at == datetime.fromisoformat(original.time)
        assert parsed.installed_at == installed[original.serial]
        installed[original.new_serial or original.serial] = parsed.removed_at
        assert parsed.tsn_h == pytest.approx(original.tsn_h, abs=1e-9, rel=0)
        assert parsed.csn == original.csn
    assert len(exports["workorders"].records) == len(world.removals())
    for parsed, original in zip(exports["workorders"].records, world.removals()):
        assert parsed.tail == original.tail and parsed.task_text == original.reason_text
        assert parsed.opened_at == parsed.closed_at == datetime.fromisoformat(original.time)
    assert len(exports["utilisation"].records) == len(daily)
    for parsed in exports["utilisation"].records:
        hours, cycles, apu = daily[(parsed.tail, parsed.date.isoformat())]
        assert parsed.flight_hours == pytest.approx(hours, abs=1e-9, rel=0)
        assert parsed.cycles == cycles
        assert parsed.apu_hours == pytest.approx(apu, abs=1e-9, rel=0)
    for snapshot in exports["acms"].records:
        original = next(r for r in sampled if r["tail"] == snapshot.tail and datetime.fromisoformat(r["ts"]) == snapshot.ts)
        prefix = original["component_id"].split("/", 1)[1]
        assert snapshot.parameters == {f"{prefix}/{k}": v for k, v in original["values"].items()}
    for row in exports["qar"].records:
        original = next(r for r in sampled if r["tail"] == row.tail and datetime.fromisoformat(r["ts"]) == row.ts)
        assert row.value == original["values"][row.parameter]
    assert all(record.provenance == "SIMULATED" for result in exports.values() for record in result.records)
    assert check(validate_records(**{k: v for k, v in exports.items() if k != "workorders"}), "parse_errors").passed


@pytest.mark.parametrize(("mess_key", "check_name"), [
    ("duplicate_rate", "duplicate_rows"), ("missing_serial_rate", "missing_serials"),
    ("swapped_date_rate", "removal_before_install"), ("mixed_timezone_rate", "timezone_format_anomalies"),
])
def test_mess_detected(tmp_path: Path, mess_key: str, check_name: str) -> None:
    synthetic.generate(tmp_path, 7, {mess_key: 1.0})
    exports = load_exports(tmp_path)
    assert all(not result.errors for result in exports.values())
    report = validate_records(**{k: v for k, v in exports.items() if k != "workorders"})
    assert not check(report, check_name).passed
    assert check(report, check_name).n_failed > 0


def test_hhmm_and_blank_markers(tmp_path: Path) -> None:
    synthetic.generate(tmp_path / "clean", 7, {})
    synthetic.generate(tmp_path / "mess", 7, {"hhmm_hours": True, "blank_marker_rate": 1.0})
    clean, messy = load_exports(tmp_path / "clean"), load_exports(tmp_path / "mess")
    assert all(not r.errors for r in messy.values())
    assert all(r.apu_hours is None for r in messy["utilisation"].records)
    assert all(r.tsn_h is None and r.csn is None for r in messy["removals"].records)
    for original, parsed in zip(clean["utilisation"].records, messy["utilisation"].records):
        assert parsed.flight_hours == pytest.approx(round(original.flight_hours * 60) / 60, abs=1e-9, rel=0)
    text = (tmp_path / "mess/utilisation.csv").read_text()
    assert ":" in text and ("N/A" in text or "-" in text)


@pytest.mark.parametrize("kind", KINDS)
def test_shipped_mapping(kind: str) -> None:
    assert mapping(kind).target == kind


@pytest.mark.parametrize("location", ["top", "column", "target_field", "timezone"])
def test_mapping_forbidden_key(tmp_path: Path, location: str) -> None:
    data = yaml.safe_load((MAPPINGS / "qar_generic.yaml").read_text())
    if location == "top":
        data["unexpected_key"] = True
    elif location == "column":
        data["columns"]["value"]["unexpected_key"] = True
    elif location == "timezone":
        data["timezone"]["unexpected_key"] = True
    else:
        data["columns"]["unexpected_key"] = {"source": "a", "type": "str"}
    path = tmp_path / "mapping.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(MappingError, match="unexpected_key.*allowed keys"):
        load_mapping(path)


def test_mapping_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "mapping.yaml"
    path.write_text("name: [")
    with pytest.raises(MappingError):
        load_mapping(path)


def test_reports_json() -> None:
    report = validate_records(removals=[removal()])
    parsed = json.loads(report_to_json(report))
    assert parsed["usable_for_reliability_fit"] == "partial"  # Only a single removal date of coverage.
    assert report.model_dump(mode="json") == parsed
    assert all(len(c.examples) <= 5 for c in report.checks)


def test_deterministic_manifest(tmp_path: Path) -> None:
    mess = {"duplicate_rate": 0.5, "missing_serial_rate": 0.4, "mixed_timezone_rate": 0.5, "blank_marker_rate": 0.3, "hhmm_hours": True}
    manifests = []
    for name, seed in [("a", 7), ("b", 7), ("c", 8)]:
        directory = tmp_path / name
        synthetic.generate(directory, seed, mess)
        manifest = json.loads((directory / "manifest.json").read_text())
        assert all(hashlib.sha256((directory / file).read_bytes()).hexdigest() == digest for file, digest in manifest["files"].items())
        manifests.append(manifest)
    assert manifests[0] == manifests[1]
    assert manifests[0] != manifests[2]


def test_multiline_defect(tmp_path: Path) -> None:
    path = tmp_path / "defects.csv"
    text = 'First line, "quoted"\nSecond line'
    write_rows(path, list(mapping("defects").columns), [{"tail": "T1", "date": "2026-01-01", "ata": "32", "text": text}])
    result = read_export(path, mapping("defects"))
    assert not result.errors and result.row_count == 1
    assert result.records[0].text == text


@pytest.mark.parametrize(("unit", "raw", "expected"), [
    ("hours", "1,234.5", 1234.5), ("hhmm_to_hours", "12:30", 12.5),
    ("minutes_to_hours", "750", 12.5), ("seconds_to_hours", "45000", 12.5),
])
def test_numeric_conversions(tmp_path: Path, unit: str, raw: str, expected: float) -> None:
    spec = mapping("utilisation")
    spec.columns["flight_hours"].unit = unit
    spec.columns["flight_hours"].thousands_sep = ","
    path = tmp_path / "util.csv"
    write_rows(path, list(spec.columns), [{"tail": "T1", "date": "2026-01-01", "flight_hours": raw, "cycles": "3"}])
    result = read_export(path, spec)
    assert not result.errors
    assert result.records[0].flight_hours == expected


@pytest.mark.parametrize("raw", ["1:99", "1:60", "1:ab"])
def test_invalid_hhmm_collected(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "util.csv"
    write_rows(path, list(mapping("utilisation").columns), [{"tail": "T1", "date": "2026-01-01", "flight_hours": raw, "cycles": 1}])
    result = read_export(path, mapping("utilisation"))
    assert not result.records and result.errors[0].column == "flight_hours" and result.errors[0].row_number == 1


@pytest.mark.parametrize(("mode", "raw", "expected_hour", "anomaly"), [
    ("utc", "2026-01-01 10:00", 10, True), ("local_offset", "2026-01-01 10:00", 8, False),
    ("utc", "2026-01-01T10:00:00+02:00", 8, False), ("local_offset", "2026-01-01T10:00:00Z", 10, True),
])
def test_timezone_modes(tmp_path: Path, mode: str, raw: str, expected_hour: int, anomaly: bool) -> None:
    spec = mapping("qar")
    spec.timezone.mode = mode
    spec.timezone.default_offset = "+02:00"
    path = tmp_path / "qar.csv"
    write_rows(path, list(spec.columns), [{"tail": "T1", "ts": raw, "parameter": "p", "value": 1}])
    result = read_export(path, spec)
    assert not result.errors
    assert result.records[0].ts == datetime(2026, 1, 1, expected_hour, tzinfo=timezone.utc)
    assert (check(validate_records(qar=result), "timezone_format_anomalies").n_failed > 0) is anomaly


def test_custom_datetime_format(tmp_path: Path) -> None:
    spec = mapping("qar")
    spec.timezone.datetime_formats.append("%d/%m/%Y %H:%M")
    path = tmp_path / "qar.csv"
    write_rows(path, list(spec.columns), [{"tail": "T1", "ts": "31/12/2026 15:30", "parameter": "p", "value": 1}])
    result = read_export(path, spec)
    assert not result.errors and result.records[0].ts.month == 12


def test_calendar_date_does_not_shift_with_offset(tmp_path: Path) -> None:
    spec = mapping("utilisation")
    spec.timezone.mode = "local_offset"
    spec.timezone.default_offset = "+02:00"
    spec.timezone.datetime_formats.append("%d/%m/%Y")
    path = tmp_path / "util.csv"
    write_rows(path, list(spec.columns), [{"tail": "T1", "date": "31/12/2026", "flight_hours": 1, "cycles": 1}])
    result = read_export(path, spec)
    assert not result.errors and result.records[0].date == date(2026, 12, 31)


def test_unknown_enum_collected(tmp_path: Path) -> None:
    path = tmp_path / "removals.csv"
    write_rows(path, list(mapping("removals").columns), [{"part_number": "PN", "serial": "SN", "position": "P", "removal_reason": "unexpected"}])
    result = read_export(path, mapping("removals"))
    assert not result.records and result.errors[0].column == "removal_reason"
    assert "allowed values" in result.errors[0].message and "UNSCHED" in result.errors[0].message


@pytest.mark.parametrize(("raw", "expected"), [("Y", True), ("N", False), ("true", True), ("false", False), ("1", True), ("0", False)])
def test_boolean_parse(tmp_path: Path, raw: str, expected: bool) -> None:
    spec = mapping("removals")
    path = tmp_path / "rem.csv"
    write_rows(path, list(spec.columns), [{"part_number": "PN", "position": "P", "scheduled": raw}])
    result = read_export(path, spec)
    assert not result.errors and result.records[0].scheduled is expected


def test_missing_headers(tmp_path: Path) -> None:
    path = tmp_path / "qar.csv"
    write_rows(path, ["tail", "parameter", "value"], [{"tail": "T1", "parameter": "p", "value": 1}])
    result = read_export(path, mapping("qar"))
    assert "ts" in result.missing_columns and not result.records
    assert len(result.errors) == 1 and result.errors[0].row_number == 0
    write_rows(path, ["tail", "ts", "parameter", "value"], [{"tail": "T1", "ts": START.isoformat(), "parameter": "p", "value": 1}])
    result = read_export(path, mapping("qar"))
    assert result.missing_columns == ["unit"] and not result.errors and result.records[0].unit is None


def test_key_fields_duplicates(tmp_path: Path) -> None:
    spec = mapping("removals")
    spec.key_fields = ["serial"]
    path = tmp_path / "rem.csv"
    write_rows(path, list(spec.columns), [{"part_number": "PN1", "position": "P", "serial": "S"}, {"part_number": "PN2", "position": "P", "serial": "S"}])
    result = read_export(path, spec)
    assert not result.errors
    assert check(validate_records(removals=result), "duplicate_rows").n_failed == 1


def test_orphan_removal_info() -> None:
    report = validate_records(removals=[removal(installed_at=None)])
    result = check(report, "orphan_removals")
    assert result.severity == "info" and result.passed and result.n_failed == 1
    assert "left-censored" in result.message.lower()
    outside_install = validate_records(removals=[removal()], window=(START.date() + timedelta(days=1), START.date() + timedelta(days=101)))
    assert check(outside_install, "orphan_removals").n_failed == 1


def test_intervals_and_separate_installs() -> None:
    second = removal(tail="T2")
    report = validate_records(removals=[removal(), second, removal(serial="SN2")])
    assert check(report, "serial_double_install").n_failed == 1
    assert not check(report, "overlapping_position_installs").passed
    install = PartInstallEvent(**COMMON, part_number="PN", serial="SN1", tail="T1", position="P1", installed_at=START)
    report = validate_records(removals=[removal(installed_at=None, removed_at=START - timedelta(days=1))], installs=[install])
    assert check(report, "removal_before_install").n_failed == 1
    assert report.usable_for_reliability_fit == "no"
    # Adjacent intervals and duplicate representations of the same install do not overlap.
    report = validate_records(removals=[removal(), removal(serial="SN2", installed_at=START + timedelta(days=100), removed_at=START + timedelta(days=120))], installs=[install])
    assert check(report, "overlapping_position_installs").passed
    assert check(report, "serial_double_install").passed


def test_remaining_quality_checks() -> None:
    util = [UtilisationDay(**COMMON, tail="T1", date=date(2026, 1, d), flight_hours=h, cycles=c) for d, h, c in [(1, 1, 1), (2, 1, 1), (7, 8, 2), (8, -1, 31)]]
    rem = removal(tsn_h=-1, csn=-2, ata_chapter="81", scheduled=True, removal_reason="shop_finding_confirmed", shop_finding="nff",
                  installed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), removed_at=datetime(2026, 1, 10, tzinfo=timezone.utc))
    acms = AcmsSnapshot(**COMMON, tail="T1", ts=START, parameters={"p": float("nan")})
    qar = QarParameterRow(**COMMON, tail="T1", ts=START, parameter="p", value=float("inf"))
    report = validate_records(removals=[rem], utilisation=util, acms=[acms], qar=[qar])
    for name in ["utilisation_gaps", "utilisation_spikes", "negative_or_implausible", "ata_validity", "unscheduled_consistency", "nff_shop_consistency", "coverage_window"]:
        assert not check(report, name).passed, name
    assert check(report, "negative_or_implausible").n_failed == 4


@pytest.mark.parametrize("ata", ["00", "80", "32-10"])
def test_valid_ata(ata: str) -> None:
    assert check(validate_records(removals=[removal(ata_chapter=ata)]), "ata_validity").passed


def test_usability_thresholds() -> None:
    util = [UtilisationDay(**COMMON, tail="T1", date=START.date() + timedelta(days=i), flight_hours=1, cycles=1) for i in range(100)]
    clean = [removal()]
    assert validate_records(removals=clean, utilisation=util).usable_for_reliability_fit == "yes"
    few = validate_records(removals=clean, utilisation=util, errors=[RowError(1, "x", "bad", "bad")])
    assert few.usable_for_reliability_fit == "partial" and "parse_errors" in few.reasons
    many = validate_records(removals=clean, errors=[RowError(1, "x", "bad", "bad")])
    assert many.usable_for_reliability_fit == "no" and "parse_errors" in many.reasons
    assert validate_records().usable_for_reliability_fit == "no"
    assert validate_records(removals=clean * 2).usable_for_reliability_fit == "no"
    assert validate_records(removals=clean * 2, utilisation=util).usable_for_reliability_fit == "partial"
    assert check(validate_records(removals=[removal(removal_reason=None)]), "unscheduled_consistency").severity == "warning"


@pytest.mark.parametrize(("model", "kwargs"), [
    (PartRemovalEvent, {"part_number": "PN", "serial": "S", "position": "P", "removed_at": "2026-01-01T12:00:00"}),
    (PartInstallEvent, {"part_number": "PN", "serial": "S", "position": "P", "installed_at": "2026-01-01T12:00:00"}),
    (DefectLog, {"tail": "T", "date": "2026-01-01", "ata": "32", "text": "x", "rectified_at": "2026-01-01T12:00:00"}),
    (WorkOrder, {"wo_id": "W", "tail": "T", "opened_at": "2026-01-01T12:00:00", "task_text": "x"}),
    (ShopVisit, {"serial": "S", "part_number": "PN", "shop_in": "2026-01-01T12:00:00"}),
    (AcmsSnapshot, {"tail": "T", "ts": "2026-01-01T12:00:00", "parameters": {}}),
    (QarParameterRow, {"tail": "T", "ts": "2026-01-01T12:00:00", "parameter": "p", "value": 1}),
])
def test_schema_rejects_naive(model: Any, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        model(**COMMON, **kwargs)


def test_schema_extra_and_utc() -> None:
    with pytest.raises(ValidationError):
        Aircraft(**COMMON, tail="T1", type="generic", in_service_date=date(2026, 1, 1), unexpected=True)
    parsed = QarParameterRow(**COMMON, tail="T", ts="2026-01-01T12:00:00+02:00", parameter="p", value=1)
    assert parsed.ts == datetime(2026, 1, 1, 10, tzinfo=timezone.utc) and parsed.ts.utcoffset() == timedelta(0)


def test_acms_groups_and_rejects_missing_value(tmp_path: Path) -> None:
    spec = mapping("acms")
    path = tmp_path / "acms.csv"
    rows = [{"tail": "T1", "ts": START.isoformat(), "phase": "cruise", "parameter": p, "value": v} for p, v in [("a", 1), ("b", 2), ("c", "N/A")]]
    write_rows(path, list(spec.columns), rows)
    result = read_export(path, spec)
    assert result.row_count == 3 and len(result.records) == 1 and result.records[0].parameters == {"a": 1, "b": 2}
    assert len(result.errors) == 1 and result.errors[0].row_number == 3


@pytest.mark.parametrize("delimiter", ["\t", ";"])
def test_tsv_and_semicolon(tmp_path: Path, delimiter: str) -> None:
    spec = mapping("qar")
    spec.delimiter = delimiter
    path = tmp_path / "qar.txt"
    write_rows(path, list(spec.columns), [{"tail": "T1", "ts": START.isoformat(), "parameter": "p", "value": 1}], delimiter)
    assert not read_export(path, spec).errors


def test_cli_json_or_path(tmp_path: Path) -> None:
    config = tmp_path / "mess.json"
    config.write_text('{"hhmm_hours": true}')
    synthetic.main(["--out", str(tmp_path / "a"), "--mess", str(config)])
    synthetic.main(["--out", str(tmp_path / "b"), "--mess", config.read_text()])
    assert (tmp_path / "a/manifest.json").read_bytes() == (tmp_path / "b/manifest.json").read_bytes()
