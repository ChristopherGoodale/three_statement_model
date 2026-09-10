import pathlib

import pandas as pd

from site3sm.extract import normalize_companyfacts

# Synthetic fixture (spec Sec 2 fixture policy): obviously fake concepts/values,
# nothing resembling SITE's actual statements.
SYNTHETIC_COMPANYFACTS = {
    "cik": 9999999,
    "entityName": "ACME WIDGETS TEST CO",
    "facts": {
        "us-gaap": {
            "FakeTestRevenue": {
                "label": "Fake Test Revenue",
                "units": {
                    "USD": [
                        {
                            "start": "2020-01-01",
                            "end": "2020-12-31",
                            "val": 111,
                            "accn": "0000000000-20-000001",
                            "fy": 2020,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2021-02-01",
                        },
                        {
                            "start": "2020-01-01",
                            "end": "2020-12-31",
                            "val": 111,
                            "accn": "0000000000-20-000001",
                            "fy": 2020,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2021-02-01",
                        },  # exact duplicate on purpose -- tests dedup
                    ]
                },
            },
            "FakeTestCash": {
                "label": "Fake Test Cash",
                "units": {
                    "USD": [
                        {
                            "end": "2020-12-31",
                            "val": 42,
                            "accn": "0000000000-20-000001",
                            "fy": 2020,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2021-02-01",
                        }
                    ]
                },
            },
        }
    },
}


def test_normalize_companyfacts_flattens_and_dedupes():
    raw_path = pathlib.Path("data/raw/fake.body")
    df = normalize_companyfacts(SYNTHETIC_COMPANYFACTS, raw_path)

    # the exact-duplicate fact collapses to one row
    assert len(df) == 2

    revenue = df[df["concept"] == "us-gaap:FakeTestRevenue"].iloc[0]
    assert revenue["period_type"] == "duration"
    assert revenue["days"] == 366  # 2020 is a leap year, inclusive count
    assert revenue["val"] == 111
    assert revenue["source"] == "companyfacts"
    assert revenue["dimensions"] == "{}"
    assert revenue["raw_path"] == str(raw_path)

    cash = df[df["concept"] == "us-gaap:FakeTestCash"].iloc[0]
    assert cash["period_type"] == "instant"
    assert pd.isna(cash["days"])  # pandas stores missing numerics as NaN, not None
    assert cash["start"] is None


def test_normalize_companyfacts_never_surfaces_fy_fp():
    df = normalize_companyfacts(SYNTHETIC_COMPANYFACTS, pathlib.Path("x"))
    assert "fy" not in df.columns
    assert "fp" not in df.columns


def test_every_row_has_accn_and_raw_path():
    df = normalize_companyfacts(SYNTHETIC_COMPANYFACTS, pathlib.Path("data/raw/fake.body"))
    assert df["accn"].notna().all()
    assert df["raw_path"].notna().all()
