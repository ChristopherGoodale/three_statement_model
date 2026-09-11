"""Template token resolver (spec Sec 9.6 'Template token grammar').

| Token | Resolves to |
|---|---|
| {LINE_ID} | Same tab, same period column |
| {TAB!LINE_ID} | Other tab, same period column, sheet-quoted |
| {LINE_ID@prev} / {TAB!LINE_ID@prev} | Prior period column |
| {TAB!LINE_ID@abs} | Absolute reference to a kind: scalar single cell |
"""
from __future__ import annotations

import re

from site3sm.layout import LayoutError, TabLayout, prev_column

TOKEN_RE = re.compile(r"\{(?:([A-Za-z0-9_]+)!)?([A-Za-z0-9_]+)(?:@(prev|abs))?\}")


def resolve_formula(
    template: str,
    current_tab: str,
    col: str,
    layouts: dict[str, TabLayout],
    active_letters: list[str],
) -> str:
    """Resolve every {...} token in `template` into an A1 formula. Raises LayoutError
    on any of the resolver error cases spec Sec 9.6 names: unknown line_id, unknown
    tab, @prev with no prior period, @abs on a non-scalar, or any unresolved brace
    left after substitution."""

    def repl(match: re.Match) -> str:
        tab_ref, line_id, modifier = match.groups()
        target_tab = tab_ref or current_tab
        if target_tab not in layouts:
            raise LayoutError(f"unknown tab: {target_tab!r}")
        layout = layouts[target_tab]
        row = layout.row_of(line_id)  # raises LayoutError if unknown

        if modifier == "abs":
            if layout.kind_of(line_id) != "scalar":
                raise LayoutError(f"@abs used on non-scalar line_id: {line_id!r}")
            return f"'{target_tab}'!${col}${row}"

        target_col = col
        if modifier == "prev":
            target_col = prev_column(col, active_letters)
            if target_col is None:
                raise LayoutError(f"@prev with no prior period for {line_id!r} at column {col!r}")

        ref = f"{target_col}{row}"
        return f"'{target_tab}'!{ref}" if tab_ref else ref

    resolved = TOKEN_RE.sub(repl, template)
    if "{" in resolved or "}" in resolved:
        raise LayoutError(f"unresolved token(s) left in template: {template!r} -> {resolved!r}")
    return f"={resolved}"


def cell_kind(template: str, current_tab: str) -> str:
    """same_tab | cross_tab -- used to pick font color (spec Sec 9.7)."""
    refs_other_tab = any(
        tab_ref and tab_ref != current_tab for tab_ref, _line_id, _mod in TOKEN_RE.findall(template)
    )
    return "cross_tab" if refs_other_tab else "same_tab"
