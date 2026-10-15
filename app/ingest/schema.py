"""Canonical ingestion records. Times are aware and normalised to UTC."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator

Provenance = Literal["operator-export", "SIMULATED"]
RemovalReason = Literal["scheduled", "unscheduled", "nff_suspected", "shop_finding_confirmed", "other"]
ShopFinding = Literal["confirmed", "nff", "no_data"]


class IngestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    provenance: Provenance
    _ingest_key: tuple[Any, ...] | None = PrivateAttr(default=None)
    _timezone_anomaly_rows: int = PrivateAttr(default=0)

    @field_validator("*", mode="after")
    @classmethod
    def aware_utc(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("datetime must be timezone-aware")
            return value.astimezone(timezone.utc)
        return value


class Aircraft(IngestRecord):
    tail: str
    type: str
    msn: str | None = None
    in_service_date: date


class UtilisationDay(IngestRecord):
    tail: str
    date: date
    flight_hours: float
    cycles: int
    apu_hours: float | None = None


class PartRemovalEvent(IngestRecord):
    part_number: str
    serial: str
    tail: str | None = None
    position: str
    installed_at: datetime | None = None
    removed_at: datetime | None = None
    tsn_h: float | None = None
    csn: int | None = None
    removal_reason: RemovalReason | None = None
    scheduled: bool | None = None
    shop_finding: ShopFinding | None = None
    ata_chapter: str | None = None


class PartInstallEvent(IngestRecord):
    part_number: str
    serial: str
    tail: str | None = None
    position: str
    installed_at: datetime
    tsn_h: float | None = None
    csn: int | None = None


class DefectLog(IngestRecord):
    tail: str
    date: date
    ata: str
    text: str
    mel_ref: str | None = None
    deferral_category: str | None = None
    rectified_at: datetime | None = None


class WorkOrder(IngestRecord):
    wo_id: str
    tail: str
    opened_at: datetime
    closed_at: datetime | None = None
    ata: str | None = None
    task_text: str
    kind: str | None = None


class ShopVisit(IngestRecord):
    serial: str
    part_number: str
    shop_in: datetime
    shop_out: datetime | None = None
    findings: str | None = None
    shop_finding: ShopFinding | None = None


class AcmsSnapshot(IngestRecord):
    tail: str
    ts: datetime
    phase: str | None = None
    parameters: dict[str, float]


class QarParameterRow(IngestRecord):
    tail: str
    ts: datetime
    parameter: str
    value: float
    unit: str | None = None
