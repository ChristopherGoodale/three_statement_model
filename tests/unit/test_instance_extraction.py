import datetime as dt
import pathlib

import pandas as pd

from site3sm.discovery import extract_instance_facts

RAW_FACT_COLUMNS = [
    "concept",
    "numeric_value",
    "period_type",
    "period_start",
    "period_end",
    "period_instant",
    "is_dimensioned",
    "dimension",
    "member",
]


class FakeXbrl:
    def __init__(self, fdf: pd.DataFrame):
        self._fdf = fdf
        self.facts = self

    def to_dataframe(self):
        return self._fdf


class FakeFiling:
    accession_no = "0000000000-26-000001"
    form = "10-K"
    filing_date = dt.date(2026, 2, 1)


class FakeClient:
    def filing_index(self, cik_int, accn_nodash):
        return {}, pathlib.Path("data/raw/fake_index.json")


def test_extract_instance_facts_only_pulls_custom_and_dimensional_lines():
    fdf = pd.DataFrame(
        [
            # custom, non-dimensional -- should be pulled
            {
                "concept": "site:FakeCustomThing",
                "numeric_value": 42.0,
                "period_type": "duration",
                "period_start": "2025-01-01",
                "period_end": "2025-12-31",
                "period_instant": None,
                "is_dimensioned": False,
                "dimension": None,
                "member": None,
            },
            # standard, non-dimensional -- should be SKIPPED (already in companyfacts)
            {
                "concept": "us-gaap:FakeStandardThing",
                "numeric_value": 99.0,
                "period_type": "duration",
                "period_start": "2025-01-01",
                "period_end": "2025-12-31",
                "period_instant": None,
                "is_dimensioned": False,
                "dimension": None,
                "member": None,
            },
            # standard, dimensioned (equity component) -- should be pulled
            {
                "concept": "us-gaap:FakeEquity",
                "numeric_value": 7.0,
                "period_type": "instant",
                "period_start": None,
                "period_end": None,
                "period_instant": "2025-12-31",
                "is_dimensioned": True,
                "dimension": "us-gaap:StatementEquityComponentsAxis",
                "member": "us-gaap:RetainedEarningsMember",
            },
            # standard, dimensioned but WRONG member -- should be excluded
            {
                "concept": "us-gaap:FakeEquity",
                "numeric_value": 3.0,
                "period_type": "instant",
                "period_start": None,
                "period_end": None,
                "period_instant": "2025-12-31",
                "is_dimensioned": True,
                "dimension": "us-gaap:StatementEquityComponentsAxis",
                "member": "us-gaap:CommonStockMember",
            },
        ],
        columns=RAW_FACT_COLUMNS,
    )

    line_items = [
        {"id": "CF_CUSTOM", "concepts": ["site:FakeCustomThing"], "taxonomy_type": "custom", "unit": "USD"},
        {"id": "IS_STANDARD", "concepts": ["us-gaap:FakeStandardThing"], "taxonomy_type": "standard", "unit": "USD"},
        {
            "id": "EQ_RE",
            "concepts": ["us-gaap:FakeEquity"],
            "taxonomy_type": "standard",
            "unit": "USD",
            "dimensions": {"us-gaap:StatementEquityComponentsAxis": "us-gaap:RetainedEarningsMember"},
        },
    ]

    out = extract_instance_facts(FakeXbrl(fdf), FakeFiling(), FakeClient(), line_items, cik_int=1)

    assert set(out["concept"]) == {"site:FakeCustomThing", "us-gaap:FakeEquity"}
    assert len(out) == 2
    assert (out["source"] == "instance").all()
    assert (out["accn"] == "0000000000-26-000001").all()

    custom_row = out[out["concept"] == "site:FakeCustomThing"].iloc[0]
    assert custom_row["period_type"] == "duration"
    assert custom_row["days"] == 365
    assert custom_row["dimensions"] == "{}"

    eq_row = out[out["concept"] == "us-gaap:FakeEquity"].iloc[0]
    assert eq_row["period_type"] == "instant"
    assert eq_row["val"] == 7.0
    assert "RetainedEarningsMember" in eq_row["dimensions"]
