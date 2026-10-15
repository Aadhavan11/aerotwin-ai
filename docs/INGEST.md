# Generic data ingestion

## Purpose

Ingestion prepares canonical, traceable records and a data-quality report. Fitting is separate and lives elsewhere. It does not estimate reliability, infer missing maintenance history, or certify an export as suitable for a particular model. Every record includes `source` (the input file path) and `provenance` (`operator-export` or `SIMULATED`). Schema models forbid extra fields; their datetimes must be timezone-aware and are normalised to UTC.

```python
from app.ingest import load_mapping, read_export, validate_records, report_to_json

removals = read_export("exports/removals.csv", load_mapping("app/ingest/mappings/removals_generic.yaml"))
utilisation = read_export("exports/utilisation.csv", load_mapping("app/ingest/mappings/utilisation_generic.yaml"))
report = validate_records(removals=removals, utilisation=utilisation)
print(report_to_json(report))
```

`read_export` returns `IngestResult(records, errors, row_count, header, missing_columns)`. A `RowError` contains `row_number`, `column`, `raw_value`, `message`, and `source`. Row numbers count logical data records from 1, excluding the header, even when a quoted field spans physical lines. Errors are collected, and later valid rows still load. Malformed CSV or unreadable files are also reported as errors. A missing required source header produces one error at row 0 and skips the file; optional missing headers supply `None`. `row_count` counts rows examined, so it is zero when a required header prevents parsing.

`validate_records` accepts canonical lists or `IngestResult` values for `removals`, `installs`, `utilisation`, `defects`, `acms`, and `qar`, plus `errors` and an optional inclusive `(start_date, end_date)` window. Results supply their own errors; errors from workorders can be passed through `errors=workorders.errors`. `report_to_json` and Pydantic's `model_dump(mode="json")` / `model_dump_json()` produce JSON. The window flags anomalies and controls left-censoring; it does not discard observations.

## Formats

All six shipped templates are generic comma-separated exports with UTF-8 headers. Mappings can instead select tab-separated or semicolon-separated input. Use normal CSV quoting for commas, quotes, and embedded newlines. Missing optional values may be empty or one of the configured markers.

| File / target | Columns |
| --- | --- |
| `removals.csv` / `removals` | `part_number, serial, tail, position, installed_at, removed_at, tsn_h, csn, removal_reason, scheduled, shop_finding, ata_chapter` |
| `utilisation.csv` / `utilisation` | `tail, date, flight_hours, cycles, apu_hours` |
| `defects.csv` / `defects` | `tail, date, ata, text, mel_ref, deferral_category, rectified_at` |
| `workorders.csv` / `workorders` | `wo_id, tail, opened_at, closed_at, ata, task_text, kind` |
| `acms.csv` / `acms` | `tail, ts, phase, parameter, value` (LONG format) |
| `qar.csv` / `qar` | `tail, ts, parameter, value, unit` |

ACMS long rows group by `(tail, ts, phase)` into `AcmsSnapshot.parameters`, a dictionary of parameter names to floating-point values. Duplicate parameter names in one snapshot produce a row error rather than overwrite earlier values. A phase may be missing. QAR retains one parameter per record. No pivoting or parameter units are inferred.

The removals schema supports installation and removal dates in a single `PartRemovalEvent`. `PartInstallEvent` is also public for separate install histories supplied directly to validation. `Aircraft` and `ShopVisit` are canonical public models; these six templates do not supply adapters for them. Blank removal serials are retained as an empty string so the `missing_serials` check identifies them rather than rejecting the row during parsing. TSN/CSN describe time/cycles since new at removal.

## Mapping YAML reference

`load_mapping(path)` validates the YAML and raises `MappingError` for invalid YAML, unknown keys, or invalid mapping definitions. Unknown top-level, timezone, and column-spec keys name the offending key and the allowed keys. Unknown target-field names also fail mapping validation.

Top-level keys:

| Key | Meaning / default |
| --- | --- |
| `name`, `target`, `delimiter`, `columns` | Required; target is one of the six formats above; delimiter is comma, tab, or semicolon. |
| `encoding` | Defaults to `utf-8`. |
| `missing_markers` | Defaults to `["", "N/A", "n/a", "-", "NULL"]`; markers match after trimming whitespace. |
| `enums` | Optional target-field → source-value → canonical-value maps. Every nonmissing value must be mapped, including canonical spellings if accepted. |
| `timezone` | `mode` is `utc` (default) or `local_offset`; `default_offset` defaults to `+00:00`; `datetime_formats` defaults to `["ISO", "%Y-%m-%d %H:%M", "%Y-%m-%d"]`. |
| `key_fields` | Optional mapped target fields used for duplicate detection instead of complete canonical records. |
| `provenance` | Defaults to `operator-export`; `read_export(..., provenance="SIMULATED")` overrides it for synthetic files. |
| `group_by` | Only supported for ACMS; must contain exactly `tail`, `ts`, `phase`. These are also ACMS's default grouping fields. |

Each column requires `source` (the input header) and `type` (`str`, `int`, `float`, `date`, `datetime`, `bool`). Optional keys are `unit`, `thousands_sep` (default none), and `required` (default false). Required values that match a missing marker produce row errors. Even with `required: false`, a missing value for a nonnullable canonical field produces a schema error. Use optional numeric fields for intentionally missing numeric data.

Full removals example, identical to the shipped generic template:

```yaml
# Generic template. Adapt the column names and enum maps to your export. Not tied to any vendor format.
name: removals_generic
target: removals
delimiter: ','
encoding: utf-8
missing_markers:
- ''
- N/A
- n/a
- '-'
- 'NULL'
timezone:
  mode: utc
  default_offset: +00:00
provenance: operator-export
columns:
  part_number:
    source: part_number
    type: str
    required: true
  serial:
    source: serial
    type: str
    required: false
  tail:
    source: tail
    type: str
    required: false
  position:
    source: position
    type: str
    required: true
  installed_at:
    source: installed_at
    type: datetime
    required: false
  removed_at:
    source: removed_at
    type: datetime
    required: false
  tsn_h:
    source: tsn_h
    type: float
    required: false
    unit: hours
  csn:
    source: csn
    type: int
    required: false
  removal_reason:
    source: removal_reason
    type: str
    required: false
  scheduled:
    source: scheduled
    type: bool
    required: false
  shop_finding:
    source: shop_finding
    type: str
    required: false
  ata_chapter:
    source: ata_chapter
    type: str
    required: false
enums:
  removal_reason:
    scheduled: scheduled
    unscheduled: unscheduled
    nff_suspected: nff_suspected
    shop_finding_confirmed: shop_finding_confirmed
    other: other
    U/S: unscheduled
    UNSCHED: unscheduled
    SCHED: scheduled
    NFF: nff_suspected
    CONFIRMED: shop_finding_confirmed
    OTHER: other
  shop_finding:
    confirmed: confirmed
    nff: nff
    no_data: no_data
```

Canonical removal reasons are `scheduled`, `unscheduled`, `nff_suspected`, `shop_finding_confirmed`, and `other`. Shop findings are `confirmed`, `nff`, or `no_data`. Unknown nonmissing enum inputs produce row errors listing allowed source values.

## Conversions and time-zone rules

- `hhmm_to_hours` accepts `12:30` as 12.5 hours, including fractional minutes, and also accepts plain decimal hours for clean numeric exports. Minutes must be below 60; `1:99` is a parse error.
- `minutes_to_hours` divides by 60; `seconds_to_hours` divides by 3,600. `hours` and `kg` are identity units; no other units are currently supported. A configured thousands separator is removed before numeric conversion, e.g. `"1,234.5"` with `thousands_sep: ","` becomes 1234.5.
- Missing markers become `None` for nullable fields and are never converted to zero. Boolean values accept `Y/N`, `true/false`, and `1/0`, case-insensitively.
- Dates accept ISO calendar dates and date/datetime formats listed in the timezone block, retaining the input calendar date without shifting it through UTC. To accept `31/12/2026 15:30`, add `"%d/%m/%Y %H:%M"` to `datetime_formats`. `ISO` uses ISO 8601 parsing, including `Z` and explicit UTC offsets.
- In `utc` mode naive timestamps receive UTC. In `local_offset` mode naive timestamps receive the fixed `default_offset`. Explicit offsets always convert to UTC. This is a fixed-offset conversion; no daylight-saving or named-zone inference occurs.
- The schema rejects naive datetimes provided directly. Adapters preserve a per-row anomaly count for naive timestamps in UTC mode or explicit offsets differing from the default. Validation also flags observation dates outside the requested window. Multiple anomalous timestamps on a row count once; grouped ACMS retains counts from its constituent rows. Naive local-offset inputs are expected and do not themselves warn.

Timestamp anomaly and duplicate-key metadata remain on the in-memory records, outside their public serialised fields. Validate the adapter results before serialising records; reloading only canonical UTC values loses the original input-offset evidence. Retain source exports for audit. Identical-row comparison ignores file paths and compares canonical public values including provenance. Key-based comparison uses configured fields; use consistent mappings across files of the same kind.

## Validation checks

Examples are capped at five per check. Error/warning checks pass when their failure count is zero. Informational left-censoring always passes.

| Name | Severity | What it detects |
| --- | --- | --- |
| `parse_errors` | error when present; otherwise info | Collected row/file parsing errors. |
| `duplicate_rows` | error | Repeated canonical rows or repeated configured keys, within each input kind. |
| `serial_double_install` | error | One serial installed on different tails at overlapping times. |
| `removal_before_install` | error | A removal precedes its embedded or matching separate install. |
| `overlapping_position_installs` | error | Overlapping installation intervals on one tail/position. |
| `orphan_removals` | info, always passes | Left-censored serials with no install in the window. |
| `timezone_format_anomalies` | warning | Naive UTC-mode input, explicit offset differing from default, or dates outside the window. |
| `utilisation_gaps` | warning | Runs of more than three missing calendar days between a tail's utilisation observations. |
| `utilisation_spikes` | warning | Positive daily flight hours above three times that tail's median. |
| `negative_or_implausible` | error | Daily hours outside 0–24, cycles outside 0–30, negative/nonfinite TSN/CSN, or nonfinite ACMS/QAR values. |
| `ata_validity` | error | Chapters outside two-digit 00–80, optionally followed by a two-digit `-YY` subchapter. |
| `unscheduled_consistency` | error for contradictions; warning for missing reasons alone | `scheduled=True` contradicts unscheduled/NFF/confirmed reasons; `scheduled=False` contradicts a scheduled reason. Missing reasons are counted as warnings. |
| `coverage_window` | info; warning for short tails | First/last utilisation, defect, or removal date; spans under 90 days warn. Historical install dates do not extend removal coverage. |
| `missing_serials` | error | Blank removal serials. |
| `nff_shop_consistency` | error | An NFF shop finding paired with `shop_finding_confirmed`. |

Intervals are half-open `[installed_at, removed_at)`; an open install extends indefinitely. Adjacent intervals do not overlap. Matching separate installs and combined rows describe a single interval, not two installations. A separate install is closed by its next matching removal.

## Usability verdict rules

`usable_for_reliability_fit` is:

- `no` if there are no removals, or failures in `serial_double_install`, `removal_before_install`, or `missing_serials`; also if parse errors exceed 5% or duplicate rows exceed 1%.
- `partial` for any remaining failed warning/error check, including smaller parse-error or duplicate fractions.
- `yes` otherwise.

Fractions use the total number of canonical input records plus unique failed `(source, row_number)` entries as denominator (minimum one). Parse-error numerators count `RowError` entries; duplicate numerators count occurrences beyond the first. Grouped ACMS contributes snapshots to this denominator. `reasons` names the checks driving the verdict, or `no_removal_records` when empty. Informational orphan removals alone never reduce usability. These are data-quality gates, not evidence that fitting assumptions hold.

## Synthetic exports

```bash
python scripts/make_synthetic_exports.py --out exports --seed 7
python scripts/make_synthetic_exports.py --out messy --seed 7 --mess '{"duplicate_rate": 0.1, "mixed_timezone_rate": 0.2}'
```

`--mess` also accepts a JSON file path. `generate(out_dir: Path, seed: int, mess: dict) -> dict` returns the ground truth and is importable. Defaults are four tails, 2,190 cycles (`365 * LEGS_PER_DAY`, six legs per day), and `stochastic=False`; simulation time covers 365 days. Utilisation aggregates actual context flight/APU hours and cycles into operational days counted from the simulator start. ACMS and QAR sample actual emitted telemetry at the first/last cycle and every 30 operational days. Workorders derive from actual simulator maintenance records. Defects can have no rows but always include a header. No clean hours, readings, or removal counts are fabricated. Simulator reasons are canonically represented as scheduled, unscheduled, or NFF according to its actual flags; no confirmed shop finding is inferred.

All CSV headers match the generic mappings. Pass `provenance="SIMULATED"` to `read_export` for these files; CSV formats have no extra provenance column. `ground_truth.json` records clean seed/tails/cycle count, elapsed calendar days, component kinds, injected fault history, clean removal count, provenance, and mess settings. `manifest.json` lists SHA-256 digests for the six CSVs and ground truth. It excludes itself to avoid a circular digest. Paths and wall-clock times are absent, so the same seed and mess settings produce identical files for the same simulator version.

Mess settings default to zero/false: `duplicate_rate` duplicates removal rows; `missing_serial_rate` blanks serials; `swapped_date_rate` swaps install/removal times; `mixed_timezone_rate` writes selected datetimes with `+02:00` or without an offset; `blank_marker_rate` replaces selected optional APU/TSN/CSN values with `N/A` or `-`; `hhmm_hours` writes hours rounded to a minute as `HH:MM`. Mess rates must lie in [0, 1], and injection uses a seeded `random.Random(seed)`. Clean numeric values round-trip losslessly; HH:MM mess mode intentionally loses sub-minute precision. Tests shorten `N_CYCLES` to exercise the same real simulator paths quickly.

## Non-compatibility statement

These are generic templates only. There is no claim of compatibility with any AMOS/TRAX/Ramco or other vendor export. Operators must adapt header mappings, enum maps, units, time formats, and offsets to their own exports and inspect the resulting validation report.

## Synthetic recovery != field validity

A synthetic round-trip proves the code paths preserve the simulator's clean exported observations. It does not establish that real operational data fits a reliability model, that simulator priors represent an operator's fleet, or that field failure/removal labels are correct. Field validity requires separate review and fitting.
