"""Pydantic schemas for config/settings.yaml. All config is validated here at startup;
invalid config aborts the run with a clear error (spec Sec 8)."""
from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, ConfigDict, Field


class IssuerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    cik: str

    @property
    def cik10(self) -> str:
        return self.cik.zfill(10)


class PeriodsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor: str = "latest_10k"
    historical_fiscal_years: int = 3
    include_opening_balance_sheet: bool = False
    forecast_years: int | None = None
    fiscal_year_rule: str | None = None


class SecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # SEC's fair-access policy caps at 10 req/sec; spec Sec 9.1/R9 defaults to 8 and
    # requires staying at or below that limit, so the schema itself refuses configs
    # that would exceed it rather than relying on every caller to remember.
    max_requests_per_second: float = Field(default=8, gt=0, le=10)
    max_retries: int = 5
    backoff_base_seconds: float = 1.0
    cache_dir: str = "data/raw"


class SheetsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_target: str = "scratch"


class NumberFormats(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    USD: str
    shares: str
    USD_per_shares: str = Field(alias="USD/shares")
    ratio: str
    days: str


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usd_display_scale: float = 1_000_000
    shares_display_scale: float | None = None
    number_formats: NumberFormats


class ValidationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tolerance_usd_millions: float | None = None
    usd_magnitude_guard: float = 1000
    report_all_nonzero_differences: bool = False


class CashTieConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cf_ending_cash_line_id: str | list[str] | None = None
    bs_cash_line_id: str | list[str] | None = None


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: IssuerConfig
    periods: PeriodsConfig
    sec: SecConfig
    sheets: SheetsConfig
    render: RenderConfig
    validation: ValidationConfig
    cash_tie: CashTieConfig


def load_settings(path: str | pathlib.Path = "config/settings.yaml") -> Settings:
    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    return Settings.model_validate(raw)
