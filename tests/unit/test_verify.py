import datetime as dt

from site3sm.verify import _normalize_formula, _serial_to_date


def test_serial_to_date_matches_known_sheets_values():
    # Confirmed live against a real written cell: Sheets serial 44928 read back
    # from a UNFORMATTED_VALUE fetch for period_start 2023-01-02.
    assert _serial_to_date(44928) == dt.date(2023, 1, 2)
    assert _serial_to_date(45291) == dt.date(2023, 12, 31)
    assert _serial_to_date(46019) == dt.date(2025, 12, 28)


def test_serial_to_date_non_numeric_returns_none():
    assert _serial_to_date("not a date") is None
    assert _serial_to_date(None) is None


def test_normalize_formula_strips_quotes_around_plain_sheet_names():
    # Confirmed live: Sheets writes ='Hist_BS'!E21... but reads it back as
    # =Hist_BS!E21... -- its own normalization, not a formula correctness issue.
    written = "='Hist_BS'!E21 - ('Hist_BS'!E35 + 'Hist_BS'!E37 + 'Hist_BS'!E44)"
    read_back = "=Hist_BS!E21 - (Hist_BS!E35 + Hist_BS!E37 + Hist_BS!E44)"
    assert _normalize_formula(written) == _normalize_formula(read_back)


def test_normalize_formula_does_not_touch_double_quoted_string_literals():
    formula = '=IF(ABS(F8)<=\'Checks\'!$F$7,"OK","CHECK")'
    assert '"OK"' in _normalize_formula(formula)
    assert '"CHECK"' in _normalize_formula(formula)


def test_normalize_formula_passes_through_non_strings():
    assert _normalize_formula(42.5) == 42.5
    assert _normalize_formula(None) is None
