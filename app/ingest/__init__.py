"""Public generic ingestion API."""
from .adapters import ColumnSpec, IngestResult, MappingError, MappingSpec, RowError, TimezoneSpec, load_mapping, read_export
from .schema import Aircraft, AcmsSnapshot, DefectLog, PartInstallEvent, PartRemovalEvent, QarParameterRow, ShopVisit, UtilisationDay, WorkOrder
from .validate import CheckResult, ValidationReport, report_to_json, validate_records

__all__ = [
    "Aircraft", "AcmsSnapshot", "DefectLog", "PartInstallEvent", "PartRemovalEvent", "QarParameterRow", "ShopVisit", "UtilisationDay", "WorkOrder",
    "ColumnSpec", "IngestResult", "MappingError", "MappingSpec", "RowError", "TimezoneSpec", "load_mapping", "read_export",
    "CheckResult", "ValidationReport", "report_to_json", "validate_records",
]
