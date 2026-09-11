import datetime as dt

from site3sm.verify import _serial_to_date


def test_serial_to_date_matches_known_sheets_values():
    # Confirmed live against a real written cell: Sheets serial 44928 read back
    # from a UNFORMATTED_VALUE fetch for period_start 2023-01-02.
    assert _serial_to_date(44928) == dt.date(2023, 1, 2)
    assert _serial_to_date(45291) == dt.date(2023, 12, 31)
    assert _serial_to_date(46019) == dt.date(2025, 12, 28)


def test_serial_to_date_non_numeric_returns_none():
    assert _serial_to_date("not a date") is None
    assert _serial_to_date(None) is None
