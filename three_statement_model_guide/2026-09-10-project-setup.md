# 2026-09-10 - Project Setup

## The "Why" (Design Decisions)

**Repo holds data + notes, not the live model.** The model itself is built by hand in Google Sheets — that's deliberate: the point of this project is to internalize how the three statements link together, and typing every formula yourself (rather than generating it) is where that understanding actually forms. This repo's job is just to version the source data and capture the reasoning behind modeling choices as they're made.

**Data starts as a tracked Excel file, not a database or API pull.** For a single-company, single-pass model there's no ongoing ingestion problem to solve — a spreadsheet of historicals is the right amount of infrastructure. If this pattern repeats across multiple companies later, that's the point to revisit (e.g. a shared `data/` convention across model repos, or pulling straight from SEC EDGAR's XBRL API instead of hand-sourced Excel).

## Core Concepts

N/A for this entry — no modeling logic yet, just scaffolding.

## Implementation Breakdown

- `data/` — holds the historical financials Excel file (moved in manually)
- `README.md` — points at the sheet once it exists, and at the source project brief in `finance_skills_projects.md`
- `.gitignore` — excludes Excel lock files (`~$*.xlsx`) so temp lock artifacts don't get committed while the workbook is open

Future entries in this guide should track actual modeling decisions once the build starts in Sheets — e.g. how revenue is driven, how the balance sheet is plugged/balanced (cash or revolver sweep), and how circularity (interest expense depending on the debt schedule, which depends on cash flow, which depends on interest expense) gets handled.
