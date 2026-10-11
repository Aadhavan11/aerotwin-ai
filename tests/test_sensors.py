from app.ml import sensors


def test_fourteen_unique_names():
    assert len(sensors.SENSOR_NAMES) == 14
    assert len(set(sensors.SENSOR_NAMES)) == 14


def test_names_match_the_contract():
    # CONTRACTS.md section 5: exact keys used in TelemetryRow.values
    assert sensors.SENSOR_NAMES == [
        "T24", "T30", "T50", "P30", "Nf", "Nc", "Ps30",
        "phi", "NRf", "NRc", "BPR", "htBleed", "W31", "W32",
    ]


def test_kept_and_dropped_partition_the_21_sensors():
    kept = set(sensors.COLUMN_TO_NAME)
    dropped = set(sensors.DROPPED)
    assert kept.isdisjoint(dropped)
    assert kept | dropped == {f"s{i}" for i in range(1, 22)}


def test_raw_layout_is_26_columns():
    assert len(sensors.RAW_COLUMNS) == 26
