import pathlib

import pytest
from pydantic import ValidationError

from site3sm.config_models import load_settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_load_real_settings_yaml():
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    assert settings.issuer.cik10 == "0001650729"
    assert settings.periods.include_opening_balance_sheet is True
    assert settings.validation.tolerance_usd_millions == 0.1
    # genuinely-open Gate 0 items stay null rather than a guessed default
    assert settings.periods.fiscal_year_rule is None
    assert settings.cash_tie.cf_ending_cash_line_id is None


def test_max_requests_per_second_cannot_exceed_sec_limit(tmp_path):
    bad = tmp_path / "settings.yaml"
    bad.write_text(
        "issuer:\n  name: X\n  cik: '1'\nperiods: {}\n"
        "sec:\n  max_requests_per_second: 11\n"
        "sheets: {}\n"
        "render:\n  number_formats:\n    USD: x\n    shares: x\n    USD/shares: x\n    ratio: x\n    days: x\n"
        "validation: {}\ncash_tie: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_settings(bad)


def test_unknown_field_rejected(tmp_path):
    bad = tmp_path / "settings.yaml"
    bad.write_text(
        "issuer:\n  name: X\n  cik: '1'\nperiods: {}\nsec: {}\nsheets: {}\n"
        "render:\n  number_formats:\n    USD: x\n    shares: x\n    USD/shares: x\n    ratio: x\n    days: x\n"
        "validation: {}\ncash_tie: {}\nnot_a_real_key: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_settings(bad)
