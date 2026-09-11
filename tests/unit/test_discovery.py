import pandas as pd

from site3sm.discovery import (
    _infer_period_type,
    _infer_unit,
    build_line_items_and_checks,
    face_rows_for_statement,
)

# Synthetic fixture (spec Sec 2 fixture policy): fake concepts, mimics the *shape* of
# an edgartools statement dataframe, not SITE's actual statements.
COLUMNS = [
    "concept",
    "label",
    "level",
    "abstract",
    "dimension",
    "dimension_axis",
    "dimension_member",
    "dimension_member_label",
    "balance",
    "weight",
    "parent_concept",
    "2099-12-31",
]


def _row(**kw):
    base = dict.fromkeys(COLUMNS)
    base.update(kw)
    return base


def make_df(rows):
    df = pd.DataFrame(rows).reindex(columns=COLUMNS)
    # pandas mishandles boolean-indexing an all-object empty frame (drops columns
    # entirely); force these two columns to real bool dtype so empty statements behave.
    df["abstract"] = df["abstract"].fillna(False).astype(bool)
    df["dimension"] = df["dimension"].fillna(False).astype(bool)
    return df


def test_face_rows_excludes_dimensional_breakdowns_on_bs():
    df = make_df(
        [
            _row(concept="fake_TotalWidgets", label="Total widgets", abstract=False, dimension=False),
            _row(
                concept="fake_TotalWidgets",
                label="Blue widgets",
                abstract=False,
                dimension=True,
                dimension_axis="fake:ColorAxis",
                dimension_member_label="Blue",
            ),
        ]
    )
    face, excluded = face_rows_for_statement("BS", df)
    assert len(face) == 1
    assert face.iloc[0]["label"] == "Total widgets"
    assert len(excluded) == 1
    assert "ColorAxis" in excluded[0]["reason"]


def test_face_rows_on_eq_keeps_equity_component_axis_but_not_others():
    df = make_df(
        [
            _row(
                concept="fake_Equity",
                label="Retained Earnings - Ending",
                abstract=False,
                dimension=True,
                dimension_axis="us-gaap:StatementEquityComponentsAxis",
                dimension_member="us-gaap_RetainedEarningsMember",
                dimension_member_label="Retained Earnings",
            ),
            _row(
                concept="fake_Equity",
                label="Parent Company - Ending",
                abstract=False,
                dimension=True,
                dimension_axis="srt:ConsolidatedEntitiesAxis",
                dimension_member_label="Parent Company",
            ),
            _row(concept="fake_RNCI", label="RNCI ending balance", abstract=False, dimension=False),
        ]
    )
    face, excluded = face_rows_for_statement("EQ", df)
    assert len(face) == 2  # the equity-component row and the non-dimensional RNCI row
    assert "Retained Earnings" in face["label"].tolist()[0] or "Retained Earnings" in face["label"].tolist()[1]
    assert len(excluded) == 1
    assert "ConsolidatedEntitiesAxis" in excluded[0]["reason"]


def test_period_type_prefers_companyfacts_over_statement_default():
    # regression test: CF's beginning/ending cash lines are instants even though the
    # rest of the Cash Flow Statement is durations -- this bit us in the first pass.
    assert _infer_period_type("us-gaap_CashAndCashEquivalents", "CF", {"us-gaap:CashAndCashEquivalents": "instant"}) == "instant"
    # custom concept absent from companyfacts falls back to the statement default
    assert _infer_period_type("site_CustomThing", "CF", {}) == "duration"
    assert _infer_period_type("site_CustomThing", "BS", {}) == "instant"


def test_infer_unit_prefers_companyfacts_then_falls_back_to_heuristic():
    assert _infer_unit("us-gaap_Cash", "Cash", {"us-gaap:Cash": "USD"}) == "USD"
    assert _infer_unit("site_CustomShares", "Shares outstanding", {}) == "shares"
    assert _infer_unit("us-gaap_EarningsPerShareBasic", "Basic (in dollars per share)", {}) == "USD/shares"
    assert _infer_unit("site_CustomThing", "Some custom dollar line", {}) == "USD"


def test_build_line_items_marks_subtotal_as_formula_with_template_and_reported_concepts():
    bs_df = make_df(
        [
            _row(concept="fake_CashLine", label="Cash", abstract=False, dimension=False, weight=1.0, parent_concept="fake_TotalAssets"),
            _row(concept="fake_InventoryLine", label="Inventory", abstract=False, dimension=False, weight=1.0, parent_concept="fake_TotalAssets"),
            _row(concept="fake_TotalAssets", label="Total assets", abstract=False, dimension=False),
        ]
    )
    empty = make_df([])
    face_rows_by_stmt = {"IS": empty, "BS": bs_df, "CF": empty, "EQ": empty}

    line_items, checks = build_line_items_and_checks(face_rows_by_stmt, {}, {})

    by_label = {li["label"]: li for li in line_items if li["tab"] == "Hist_BS"}
    total = by_label["Total assets"]
    assert total["kind"] == "formula"
    assert "{BS_CASH}" in total["template"] or "CASH" in total["template"]
    assert total["reported_concepts"] == ["fake:TotalAssets"]
    assert all(li["status"] == "proposed" for li in line_items)

    subtotal_check_ids = [c["id"] for c in checks]
    assert any(cid.startswith("CHK_SUBTOTAL_BS_TOTAL_ASSETS") for cid in subtotal_check_ids)


def test_single_child_parent_is_demoted_to_fact_not_formula():
    # A parent with only ONE calc-linkbase child (e.g. diluted shares' only
    # discoverable child is basic shares -- the dilutive effect isn't its own face
    # line) is not a reliable formula reconstruction; it should render as a fact.
    is_df = make_df(
        [
            _row(concept="fake_Basic", label="Basic", abstract=False, dimension=False, weight=1.0, parent_concept="fake_Diluted"),
            _row(concept="fake_Diluted", label="Diluted", abstract=False, dimension=False),
        ]
    )
    empty = make_df([])
    face_rows_by_stmt = {"IS": is_df, "BS": empty, "CF": empty, "EQ": empty}

    line_items, _ = build_line_items_and_checks(face_rows_by_stmt, {}, {})

    diluted = next(li for li in line_items if li["label"] == "Diluted")
    assert diluted["kind"] == "fact"
    assert "template" not in diluted
