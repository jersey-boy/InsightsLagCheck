# Design — Insights vs NationBuilder Comparison

## Overview

Three small scripts form a linear pipeline:

```
pull_insights.py  ──▶  insights_data.csv       ┐
                                                ├─▶ compare_pipelines.py ─▶ comparison*.csv/.xlsx
pull_nationbuilder.py ─▶ nationbuilder_data.csv ┘
```

Each pull is independent and writes a CSV keyed on `signup_id` with the same
four columns (`signup_id`, `registered_state`, `registered_zip`,
`federal_district`). The comparison inner-joins them and flags per-field
agreement. Decoupling the pulls from the compare means either source can be
re-pulled on its own, and the compare is a fast, offline, repeatable step.

## Source modules

```
pull_insights.py        Pull A: Tableau REST API -> insights_data.csv
pull_nationbuilder.py   Pull B: NationBuilder V2 API -> nationbuilder_data.csv
compare_pipelines.py    Inner join + per-field match flags -> reports
nationbuilder_v2/       V2 OAuth client + signup extractor
  client.py             OAuth 2.0 refresh-token flow; parallel paged pull
  extract.py            project_signup(); extract_all_signups()
  exceptions.py         NationBuilderV2Error
nb_insights.py          Shared Tableau connection (also used by legacy validator)
```

The legacy `nationbuilder/` (V1) package and `pull_zip_state_federal.py` remain
in the repo but are **not** part of this pipeline.

## Components

### 1. Pull A — `pull_insights.py`

- Uses `insights_connection()` (from `nb_insights.py`) to pull the view
  `c7f541b2-87db-4fad-97c5-2e532dbbe956` as a DataFrame.
- Projects to the four canonical columns, normalizes (ZIP → 5-digit text; state
  / district upper-stripped), dedupes on `signup_id`, writes `insights_data.csv`.

### 2. Pull B — `pull_nationbuilder.py` + `nationbuilder_v2/`

- `NationBuilderV2Client.from_env()` builds the client from `NATIONBUILDER_*`
  env vars and refreshes the access token at construction (just-in-time), so a
  run always starts with a valid 24h token. The rotated refresh token is
  persisted via `on_refresh` (local `.env` writer by default).
- `extract_all_signups()` calls `client.fetch_all_signups(extra_fields=
  ["registered_address"])`, which fetches pages in parallel **batches** by
  page number and stops when a whole batch is empty (the stats total is
  unreliable). Each page retries on 429/5xx with backoff.
- `project_signup()` maps each JSON:API record to the four canonical columns.
- The script dedupes on `signup_id` and writes `nationbuilder_data.csv`.
- `--max-pages N` does a small **serial** sample (via `iter_signups`) for quick
  testing without the full ~189k pull.

**Why V2 (not V1):** the V1 `/people` index did not return `registered_address`
and used cursor pagination (serial, ~38 min for the nation), forcing a two-stage
"index then per-record detail" design. V2 returns `registered_address` +
`federal_district` inline and supports direct page-number addressing, so a single
parallel pull (~minutes) replaced both stages. See `V2_OAUTH_SETUP.md`.

### 3. Compare — `compare_pipelines.py`

- Reads both CSVs as strings, keys `signup_id` as a nullable int, dedupes.
- Inner-joins on `signup_id` (the shared population).
- For each compared field, `_field_flag()` returns `MATCH` / `MISMATCH` / blank.
  ZIP is normalized to five digits before comparison; state/district are
  upper-stripped. A blank on either side ⇒ "not comparable" (blank flag).
- Adds `all_match` (TRUE unless some field is `MISMATCH`).
- Writes `comparison.csv`/`.xlsx` (all matched ids) and
  `comparison_mismatches.csv`/`.xlsx` (≥1 mismatch). The XLSX writer types ZIP
  columns as text so Excel keeps leading zeros.

## Data shapes

Both pull CSVs:

| column | notes |
|--------|-------|
| `signup_id` | integer id, unique per file |
| `registered_state` | 2-letter, upper/stripped (may be blank) |
| `registered_zip` | first 5 digits as text (may be blank) |
| `federal_district` | e.g. `NY13`, upper/stripped (may be blank) |

Comparison output adds, per field, `insights_<f>`, `nb_<f>`, `<f>_match`, and a
final `all_match`.

## Error handling & privacy

- OAuth / API failures raise `NationBuilderV2Error` with status + body for
  diagnosis. Per-page transient errors retry before failing.
- Credentials come only from the environment; outputs are gitignored (PII).

## Known characteristics

- Insights `federal_district` is often blank/stale; NationBuilder's is
  authoritative. Blank-on-one-side pairs are reported as "not comparable", not
  as mismatches.
- Comparing all three fields (not just `federal_district`) surfaces state/zip
  drift that a district-only check would miss.
