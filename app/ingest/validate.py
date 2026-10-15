"""Data-quality checks; fitting and reliability estimation live elsewhere."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .adapters import IngestResult, RowError
from .schema import AcmsSnapshot, DefectLog, IngestRecord, PartInstallEvent, PartRemovalEvent, QarParameterRow, UtilisationDay


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    severity: Literal["info", "warning", "error"]
    passed: bool
    n_failed: int
    examples: list[str] = Field(max_length=5)
    message: str


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counts: dict[str, int]
    checks: list[CheckResult]
    usable_for_reliability_fit: Literal["yes", "partial", "no"]
    reasons: list[str]


def _records(value: Iterable[Any] | IngestResult) -> list[Any]:
    return list(value.records if isinstance(value, IngestResult) else value)


def _dates(record: IngestRecord) -> list[date]:
    return [v.date() if isinstance(v, datetime) else v for v in record.__dict__.values() if isinstance(v, date)]


def validate_records(
    *, removals: Iterable[PartRemovalEvent] | IngestResult = (), installs: Iterable[PartInstallEvent] | IngestResult = (),
    utilisation: Iterable[UtilisationDay] | IngestResult = (), defects: Iterable[DefectLog] | IngestResult = (),
    acms: Iterable[AcmsSnapshot] | IngestResult = (), qar: Iterable[QarParameterRow] | IngestResult = (),
    errors: Iterable[RowError] = (), window: tuple[date, date] | None = None,
) -> ValidationReport:
    """Validate canonical lists (or IngestResults, which also supply file errors)."""
    inputs = {"removals": removals, "installs": installs, "utilisation": utilisation, "defects": defects, "acms": acms, "qar": qar}
    data = {kind: _records(rows) for kind, rows in inputs.items()}
    parse = list(errors)
    for value in inputs.values():
        if isinstance(value, IngestResult):
            for error in value.errors:
                if error not in parse:
                    parse.append(error)
    rem, ins, util = data["removals"], data["installs"], data["utilisation"]
    all_records = [r for rows in data.values() for r in rows]
    checks: list[CheckResult] = []

    def add(name: str, severity: Literal["info", "warning", "error"], failures: list[str], message: str, informational: bool = False) -> None:
        checks.append(CheckResult(name=name, severity=severity, passed=not failures or informational, n_failed=len(failures), examples=failures[:5], message=message))

    add("parse_errors", "error" if parse else "info", [f"{e.source}:{e.row_number}:{e.column}: {e.message}" for e in parse], "Export parsing errors.")
    duplicates = []
    for kind, rows in data.items():
        seen = set()
        for record in rows:
            key = repr(record._ingest_key) if record._ingest_key is not None else json.dumps(record.model_dump(mode="json", exclude={"source"}), sort_keys=True)
            if key in seen:
                duplicates.append(f"{kind}: {key}")
            seen.add(key)
    add("duplicate_rows", "error", duplicates, "Repeated canonical rows or mapping key_fields (source file names excluded).")

    # A combined removal row also supplies an install. Separate installs are closed
    # by their next matching removal; identical combined/separate intervals merge.
    starts: dict[tuple[str, str, str | None, datetime], datetime | None] = {}
    backwards: list[str] = []
    for removal in rem:
        start = removal.installed_at
        matches = [i.installed_at for i in ins if (i.serial, i.position, i.tail) == (removal.serial, removal.position, removal.tail)]
        if start is None and matches:
            before = [s for s in matches if removal.removed_at is None or s <= removal.removed_at]
            start = max(before) if before else min(matches)
        if start is not None and removal.removed_at is not None and removal.removed_at < start:
            backwards.append(f"{removal.serial}/{removal.position}: {removal.removed_at} < {start}")
        if start is not None:
            key = (removal.serial, removal.position, removal.tail, start)
            end = removal.removed_at
            previous = starts.get(key)
            starts[key] = min(previous, end) if previous is not None and end is not None else previous or end
    for install in ins:
        key = (install.serial, install.position, install.tail, install.installed_at)
        if key not in starts:
            ends = [r.removed_at for r in rem if (r.serial, r.position, r.tail) == key[:3] and r.removed_at is not None and r.removed_at >= install.installed_at]
            starts[key] = min(ends) if ends else None
    intervals = [(serial, pos, tail, start, end or datetime.max.replace(tzinfo=timezone.utc)) for (serial, pos, tail, start), end in starts.items()]
    double, overlap = [], []
    for index, first in enumerate(intervals):
        for second in intervals[index + 1:]:
            s1, p1, t1, a1, b1 = first
            s2, p2, t2, a2, b2 = second
            if max(a1, a2) >= min(b1, b2):
                continue
            if s1 and s1 == s2 and t1 is not None and t2 is not None and t1 != t2:
                double.append(f"{s1}: {t1} / {t2}")
            if t1 is not None and (t1, p1) == (t2, p2):
                overlap.append(f"{t1}/{p1}: {s1} / {s2}")
    add("serial_double_install", "error", double, "Same serial installed on different tails during overlapping half-open intervals.")
    add("removal_before_install", "error", backwards, "Removal precedes the matching serial/position install.")
    add("overlapping_position_installs", "error", overlap, "Overlapping installs in the same tail/position; open installs extend indefinitely.")
    installed_serials = {s for s, _, _, start in starts if window is None or window[0] <= start.date() <= window[1]}
    orphan = [f"{r.serial}: left-censored" for r in rem if r.serial not in installed_serials]
    add("orphan_removals", "info", orphan, "Left-censored removals have no install record in the window; this never fails.", True)

    anomalies = []
    for record in all_records:
        count = record._timezone_anomaly_rows
        outside = window is not None and any(d < window[0] or d > window[1] for d in _dates(record))
        anomalies.extend([f"{record.source}: naive UTC input, differing explicit offset, or outside window"] * max(count, int(outside)))
    add("timezone_format_anomalies", "warning", anomalies, "Rows with naive input in UTC mode, an explicit offset differing from the default, or dates outside the window.")
    by_tail: dict[str, list[UtilisationDay]] = defaultdict(list)
    for record in util:
        by_tail[record.tail].append(record)
    gaps, spikes, implausible = [], [], []
    for tail, rows in by_tail.items():
        days = sorted({r.date for r in rows})
        for first, last in zip(days, days[1:]):
            missing = (last - first).days - 1
            if missing > 3:
                gaps.append(f"{tail}: {missing} missing days between {first} and {last}")
        typical = median(r.flight_hours for r in rows)
        for row in rows:
            if row.flight_hours > 0 and row.flight_hours > 3 * typical:
                spikes.append(f"{tail} {row.date}: {row.flight_hours} hours (median {typical})")
            if not math.isfinite(row.flight_hours) or not 0 <= row.flight_hours <= 24 or not 0 <= row.cycles <= 30:
                implausible.append(f"{tail} {row.date}: hours={row.flight_hours}, cycles={row.cycles}")
    add("utilisation_gaps", "warning", gaps, "Missing calendar-day runs longer than three days within each tail's utilisation coverage.")
    add("utilisation_spikes", "warning", spikes, "Positive daily hours exceeding three times the tail median.")
    for record in [*rem, *ins]:
        if any(v is not None and (not math.isfinite(v) or v < 0) for v in (record.tsn_h, record.csn)):
            implausible.append(f"{record.serial}: negative or non-finite TSN/CSN")
    for record in [*data["acms"], *data["qar"]]:
        values = record.parameters.values() if isinstance(record, AcmsSnapshot) else [record.value]
        if any(not math.isfinite(v) for v in values):
            implausible.append(f"{record.tail} {record.ts}: non-finite parameter")
    add("negative_or_implausible", "error", implausible, "Hours outside 0–24, cycles outside 0–30, negative TSN/CSN, or non-finite measurements.")
    ata_errors = []
    for record in [*rem, *data["defects"]]:
        ata = record.ata_chapter if isinstance(record, PartRemovalEvent) else record.ata
        if ata is not None and (re.fullmatch(r"\d{2}(?:-\d{2})?", ata, flags=re.ASCII) is None or int(ata[:2]) > 80):
            ata_errors.append(f"{record.tail}: {ata}")
    add("ata_validity", "error", ata_errors, "ATA must be a two-digit chapter 00–80, optionally followed by -YY.")
    inconsistent, unknown = [], []
    for record in rem:
        if record.removal_reason is None:
            unknown.append(f"{record.serial}: removal_reason missing")
        elif (record.scheduled is True and record.removal_reason in {"unscheduled", "nff_suspected", "shop_finding_confirmed"}) or (
            record.scheduled is False and record.removal_reason == "scheduled"
        ):
            inconsistent.append(f"{record.serial}: scheduled={record.scheduled}, reason={record.removal_reason}")
    add("unscheduled_consistency", "error" if inconsistent else "warning", inconsistent + unknown, "Scheduled flags conflict with reasons; missing reasons are warnings only.")
    coverage: dict[str, list[date]] = defaultdict(list)
    for record in [*util, *data["defects"], *rem]:
        if record.tail is not None:
            # Removal coverage refers to removal dates, not historical installation dates.
            days = [record.removed_at.date()] if isinstance(record, PartRemovalEvent) and record.removed_at is not None else []
            if not isinstance(record, PartRemovalEvent):
                days = [record.date]
            coverage[record.tail].extend(days)
    short = [f"{tail}: {min(days)} to {max(days)} ({(max(days) - min(days)).days} days)" for tail, days in sorted(coverage.items()) if days and (max(days) - min(days)).days < 90]
    add("coverage_window", "warning" if short else "info", short, "Per-tail utilisation/defect/removal coverage shorter than 90 days; endpoints are inclusive observations.")
    if not short:
        checks[-1].examples = [f"{tail}: {min(days)} to {max(days)}" for tail, days in sorted(coverage.items()) if days][:5]
    add("missing_serials", "error", [f"{r.tail}/{r.position}: blank serial" for r in rem if not r.serial.strip()], "Removal rows require a nonblank serial.")
    add("nff_shop_consistency", "error", [r.serial for r in rem if r.shop_finding == "nff" and r.removal_reason == "shop_finding_confirmed"],
        "NFF shop finding conflicts with confirmed removal reason.")

    denominator = max(1, len(all_records) + len({(e.source, e.row_number) for e in parse}))
    hard = {"serial_double_install", "removal_before_install", "missing_serials"}
    no_reasons = [c.name for c in checks if not c.passed and (
        c.name in hard or c.name == "parse_errors" and c.n_failed / denominator > 0.05 or c.name == "duplicate_rows" and c.n_failed / denominator > 0.01
    )]
    if not rem:
        no_reasons.append("no_removal_records")
    lesser = [c.name for c in checks if not c.passed and c.severity in {"warning", "error"}]
    verdict = "no" if no_reasons else "partial" if lesser else "yes"
    return ValidationReport(counts={k: len(v) for k, v in data.items()}, checks=checks, usable_for_reliability_fit=verdict, reasons=no_reasons or lesser)


def report_to_json(report: ValidationReport) -> str:
    """Return a JSON-serialisable report without custom encoders."""
    return report.model_dump_json(indent=2)
