# Tasks — Insights vs NationBuilder Comparison

Implementation history. Checked items are built and verified against the live
`democratsabroad` Insights site and NationBuilder V2 API.

Latest full run (V2): ~179,701 `signup_id`s present in both pipelines; 9 rows
with at least one field mismatch. (Counts drift run-to-run as live data changes.)

---

## Phase 1 — Two full pulls + single-stage compare (V1, superseded)

- [x] 1. `pull_insights.py` — full Insights view → `insights_data.csv`
  (178k rows, no filter). _Requirements: 1_
- [x] 2. V1 NationBuilder pulls — a serial `/people` bulk index pull plus a
  resumable per-record detail pull.
  - _Why superseded:_ the V1 index lacked `registered_address` and paginated
    serially (~38 min), forcing a two-stage design.
- [x] 3. `compare_pipelines.py` (two-stage) — bulk `federal_district` check then
  per-record detail resolution for the non-matches.

## Phase 2 — Convert NationBuilder side to V2 / OAuth 2.0

- [x] 4. Vendor the `nationbuilder_v2/` package from ZipStateFed
  (`client.py`, `extract.py`, `exceptions.py`, `__init__.py`); trim `extract.py`
  to no filter / no custom fields. _Requirements: 2_
- [x] 5. Add V2 OAuth env vars to `.env` / `.env.example`
  (`NATIONBUILDER_CLIENT_ID/SECRET/REDIRECT_URI/REFRESH_TOKEN`). Reuse the
  ZipStateFed OAuth app (shared client id/secret). _Requirements: 2.1, 4.3_
- [x] 6. One-time OAuth authorize handshake to mint a **dedicated** refresh
  token for this project; persisted to `.env`. _Requirements: 2.3, 4.3_
- [x] 7. Rewrite `pull_nationbuilder.py` as a single parallel V2 pull →
  `nationbuilder_data.csv` (188,998 signups; ~minutes). `--max-pages N` for a
  serial sample. _Requirements: 2_
- [x] 8. Simplify `compare_pipelines.py` to a single-stage inner join of the two
  full CSVs; per-field `MATCH`/`MISMATCH`/blank + `all_match`; write
  `comparison*.csv/.xlsx`. Retire `pull_nationbuilder_bulk.py`. _Requirements: 3_
- [x] 9. End-to-end verify on live data (179,701 shared ids; 9 mismatches, which
  include state/zip drift a district-only check would miss). _Requirements: 3_
- [x] 10. Documentation — README (new V2 flow + legacy note), `V2_OAUTH_SETUP.md`
  (adapted from ZipStateFed, incl. shared-app / separate-refresh-token design),
  and this spec. _Requirements: all_

## Phase 3 — AWS Lambda + S3 + email (ToDoList #2)

- [x] 11. Lambda deployment of the comparison flow.
  - [x] 11.1 Refactor the three scripts to expose in-process functions
    (`pull_insights()`, `pull_full(concurrency, on_refresh)`,
    `build_comparison(insights_df, nationbuilder_df)`, `write_comparison_files`).
  - [x] 11.2 `lambda_pipeline.py` — `run()` + `handler()` chaining both pulls +
    compare in one process; S3 upload (date-stamped) + SES email of the
    mismatches report; status file + JSON result.
  - [x] 11.3 Credentials from a single combined Secrets Manager secret
    (`nb/insightslag/prod`: `NATIONBUILDER_*` + `NB_INSIGHTS_*`); rotate the V2
    refresh token back into it each run.
  - [x] 11.4 `build_lambda_zip.py` (bundles `tableau-api-lib` + non-pandas deps
    as Linux wheels; pandas/numpy from the AWS-managed layer).
  - [x] 11.5 `aws/` IAM + env templates, `Dockerfile`/`.dockerignore` fallback,
    `.env.example` AWS section, `DEPLOY.md`.
  - [x] 11.6 Verified `run()` locally (S3/SES unset -> local files only).
  - [x] 11.7 Created the live AWS resources (bucket
    `insightslagcheck-reports-068238656047`, secret `nb/insightslag/prod`,
    execution + scheduler roles, function `insightslagcheck-comparison`, nightly
    schedule at 06:00 UTC) in account 068238656047 / eu-west-2. Verified with a
    live end-to-end invoke that emailed the report.

## Phase 3b — Expanded to 5 fields across 3 Insights workbooks

- [x] 14. Insights source split into three workbooks (MostCommon: state/zip;
  Custom: membership_country/us_citizen; Electoral: federal_district), pulled and
  outer-joined on `signup_id`. Comparison extended to all five fields, with
  mojibake/accent folding so country names match across systems. Deployed +
  verified live.
- [x] 15. Retired the legacy V1 `nationbuilder/` package, `pull_zip_state_federal.py`,
  `sources.py`, `list_reports.py`, the ZIP reference workbook, and the
  `zip-state-federal-validation` spec. `nb_insights.py` kept (shared by the
  current Insights pull).

## Phase 4 — Not yet started

- [ ] 12. GitHub Actions + Serverless Framework v4 deployment (ToDoList #3).
