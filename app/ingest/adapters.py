"""Strict mapping definitions and row-aware generic CSV/TSV adapters."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .schema import AcmsSnapshot, DefectLog, IngestRecord, PartRemovalEvent, Provenance, QarParameterRow, UtilisationDay, WorkOrder

MODELS: dict[str, type[IngestRecord]] = {
    "removals": PartRemovalEvent, "utilisation": UtilisationDay, "defects": DefectLog,
    "workorders": WorkOrder, "acms": AcmsSnapshot, "qar": QarParameterRow,
}


class MappingError(ValueError):
    """An invalid mapping file, rather than an invalid export row."""


class ColumnSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    type: Literal["str", "int", "float", "date", "datetime", "bool"]
    unit: Literal["hhmm_to_hours", "minutes_to_hours", "seconds_to_hours", "hours", "kg"] | None = None
    thousands_sep: str | None = None
    required: bool = False

    @field_validator("thousands_sep")
    @classmethod
    def nonempty_separator(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("thousands_sep must not be empty")
        return value


def _offset(value: str) -> timezone:
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", value):
        raise ValueError("default_offset must have form +HH:MM or -HH:MM")
    hours, minutes = map(int, value[1:].split(":"))
    if hours > 23 or minutes > 59:
        raise ValueError("default_offset is out of range")
    return timezone(timedelta(minutes=(hours * 60 + minutes) * (-1 if value[0] == "-" else 1)))


class TimezoneSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["utc", "local_offset"] = "utc"
    default_offset: str = "+00:00"
    datetime_formats: list[str] = Field(default_factory=lambda: ["ISO", "%Y-%m-%d %H:%M", "%Y-%m-%d"])

    @field_validator("default_offset")
    @classmethod
    def valid_offset(cls, value: str) -> str:
        _offset(value)
        return value


class MappingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    target: Literal["removals", "utilisation", "defects", "workorders", "acms", "qar"]
    delimiter: Literal[",", "\t", ";"]
    encoding: str = "utf-8"
    missing_markers: list[str] = Field(default_factory=lambda: ["", "N/A", "n/a", "-", "NULL"])
    columns: dict[str, ColumnSpec]
    enums: dict[str, dict[str, str]] = Field(default_factory=dict)
    timezone: TimezoneSpec = Field(default_factory=TimezoneSpec)
    key_fields: list[str] | None = None
    provenance: Provenance = "operator-export"
    group_by: list[str] | None = None

    @model_validator(mode="after")
    def valid_fields(self) -> MappingSpec:
        allowed = set(MODELS[self.target].model_fields) - {"source", "provenance"}
        if self.target == "acms":
            allowed = (allowed - {"parameters"}) | {"parameter", "value"}
        for key in self.columns:
            if key not in allowed:
                raise ValueError(f"unknown column key '{key}'; allowed keys: {', '.join(sorted(allowed))}")
        for key in [*self.enums, *(self.key_fields or [])]:
            if key not in self.columns:
                raise ValueError(f"unknown field '{key}'; allowed keys: {', '.join(sorted(self.columns))}")
        if self.group_by is not None and (self.target != "acms" or set(self.group_by) != {"tail", "ts", "phase"} or len(self.group_by) != 3):
            raise ValueError("group_by is supported only for acms and must contain tail, ts, phase")
        return self


@dataclass
class RowError:
    row_number: int
    column: str
    raw_value: Any
    message: str
    source: str = ""


@dataclass
class IngestResult:
    records: list[BaseModel] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    row_count: int = 0
    header: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)


def load_mapping(path: Path | str) -> MappingSpec:
    """Load YAML, reporting forbidden keys together with their allowed siblings."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return MappingSpec.model_validate(data)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            loc = error["loc"]
            if error["type"] == "extra_forbidden":
                parent = TimezoneSpec if loc[0] == "timezone" else ColumnSpec if loc[0] == "columns" else MappingSpec
                messages.append(f"unknown key '{loc[-1]}' at {'.'.join(map(str, loc))}; allowed keys: {', '.join(parent.model_fields)}")
            else:
                messages.append(f"{'.'.join(map(str, loc))}: {error['msg']}")
        raise MappingError("; ".join(messages)) from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MappingError(f"cannot load mapping {path}: {exc}") from exc


def _parse_datetime(raw: str, spec: TimezoneSpec) -> datetime:
    for fmt in spec.datetime_formats:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")) if fmt.upper() in {"ISO", "ISO8601"} else datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid datetime; allowed formats: {spec.datetime_formats}")


def _datetime(raw: str, spec: TimezoneSpec) -> tuple[datetime, bool]:
    parsed = _parse_datetime(raw, spec)
    default = _offset(spec.default_offset)
    naive = parsed.utcoffset() is None
    anomaly = (naive and spec.mode == "utc") or (not naive and parsed.utcoffset() != default.utcoffset(None))
    if naive:
        parsed = parsed.replace(tzinfo=timezone.utc if spec.mode == "utc" else default)
    return parsed.astimezone(timezone.utc), anomaly


def _convert(raw: str, spec: ColumnSpec, tz: TimezoneSpec) -> tuple[Any, bool]:
    if spec.type == "str":
        return raw, False
    if spec.type == "datetime":
        return _datetime(raw, tz)
    if spec.type == "date":
        try:
            return date.fromisoformat(raw), False
        except ValueError:
            return _parse_datetime(raw, tz).date(), False
    if spec.type == "bool":
        values = {"y": True, "n": False, "true": True, "false": False, "1": True, "0": False}
        if raw.lower() not in values:
            raise ValueError("invalid bool; allowed values: Y, N, true, false, 1, 0")
        return values[raw.lower()], False
    number = raw.replace(spec.thousands_sep, "") if spec.thousands_sep else raw
    if spec.unit == "hhmm_to_hours" and ":" in number:
        match = re.fullmatch(r"([+-]?\d+):(\d{2}(?:\.\d+)?)", number)
        if match is None or float(match[2]) >= 60:
            raise ValueError("invalid HH:MM hours; minutes must be less than 60")
        value = abs(int(match[1])) + float(match[2]) / 60
        if number.startswith("-"):
            value = -value
    else:
        value = float(number)
    value /= {"minutes_to_hours": 60, "seconds_to_hours": 3600}.get(spec.unit, 1)
    if spec.type == "int":
        if not value.is_integer():
            raise ValueError("expected an integer")
        return int(value), False
    return value, False


def read_export(path: Path | str, mapping: MappingSpec, provenance: Provenance | None = None) -> IngestResult:
    """Collect row errors; successful ACMS long rows are grouped into snapshots."""
    result = IngestResult()
    source = str(path)
    groups: dict[tuple[Any, ...], AcmsSnapshot] = {}
    try:
        with Path(path).open(encoding=mapping.encoding, newline="") as stream:
            reader = csv.DictReader(stream, delimiter=mapping.delimiter, strict=True)
            result.header = list(reader.fieldnames or [])
            result.missing_columns = list(dict.fromkeys(s.source for s in mapping.columns.values() if s.source not in result.header))
            required_absent = [s.source for s in mapping.columns.values() if s.required and s.source not in result.header]
            if required_absent:
                result.errors.append(RowError(0, ", ".join(required_absent), None, "required mapped source column absent", source))
                return result
            for number, row in enumerate(reader, 1):
                result.row_count = number
                values: dict[str, Any] = {}
                row_errors: list[RowError] = []
                anomaly = False
                if None in row:
                    row_errors.append(RowError(number, "", row[None], "more values than header columns", source))
                for target, spec in mapping.columns.items():
                    raw = row.get(spec.source)
                    value = raw.strip() if raw is not None else None
                    if value is None or value in mapping.missing_markers:
                        values[target] = "" if mapping.target == "removals" and target == "serial" else None
                        if spec.required:
                            row_errors.append(RowError(number, target, raw, "required value is missing", source))
                        continue
                    try:
                        if target in mapping.enums:
                            enum = mapping.enums[target]
                            if value not in enum:
                                raise ValueError(f"unknown enum value; allowed values: {', '.join(enum)}")
                            value = enum[value]
                        values[target], flag = _convert(value, spec, mapping.timezone)
                        anomaly |= flag
                    except (ValueError, OverflowError) as exc:
                        row_errors.append(RowError(number, target, raw, str(exc), source))
                if row_errors:
                    result.errors.extend(row_errors)
                    continue
                try:
                    common = {"source": source, "provenance": provenance if provenance is not None else mapping.provenance}
                    if mapping.target == "acms":
                        parameter, value = values.pop("parameter", None), values.pop("value", None)
                        if not isinstance(parameter, str) or not parameter or value is None:
                            raise ValueError("acms requires a parameter and numeric value")
                        record = AcmsSnapshot(**common, **values, parameters={parameter: value})
                    else:
                        record = MODELS[mapping.target](**common, **values)
                    record._timezone_anomaly_rows = int(anomaly)
                    if mapping.key_fields:
                        record._ingest_key = tuple(values.get(k) if k in values else row.get(mapping.columns[k].source) for k in mapping.key_fields)
                    if mapping.target == "acms":
                        key = tuple(getattr(record, k) for k in (mapping.group_by or ["tail", "ts", "phase"]))
                        if key in groups:
                            existing = groups[key]
                            if parameter in existing.parameters:
                                result.errors.append(RowError(number, "parameter", parameter, "duplicate ACMS parameter in snapshot", source))
                            else:
                                existing.parameters[parameter] = value
                                existing._timezone_anomaly_rows += int(anomaly)
                            continue
                        groups[key] = record
                    result.records.append(record)
                except ValidationError as exc:
                    for error in exc.errors():
                        column = str(error["loc"][0])
                        spec = mapping.columns.get(column)
                        result.errors.append(RowError(number, column, row.get(spec.source) if spec else None, error["msg"], source))
                except ValueError as exc:
                    result.errors.append(RowError(number, "", None, str(exc), source))
    except (OSError, UnicodeError, LookupError, csv.Error) as exc:
        result.errors.append(RowError(result.row_count + 1, "", None, str(exc), source))
    return result
