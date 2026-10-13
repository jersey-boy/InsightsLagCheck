# Tasks — Insights vs NationBuilder Comparison

Implementation history, in build order. Checked items are built and verified
against the live `democratsabroad` Insights site, the NationBuilder V2 API, and
(for the AWS phases) account `068238656047` in `eu-west-2`.

Latest full run figures (5 fields, 3 Insights workbooks): ~189,000 `signup_id`s
present in both pipelines; ~10 rows with at least one field mismatch. (Counts
drift run-to-run as live data changes.)

---

## Phase 1 — Two full pulls + compare (V1 NationBuilder, superseded)

- [x] 1. `pull_insights.py` — full Insights view → `insights_data.csv`, no
  filter. _Requirements: 1_
- [x] 2. V1 NationBuilder pulls — a serial `/people` bulk index pull plus a
  resumable per-record detail pull.
  - _Why superseded:_ the V1 index lacked `registered_address` and paginated
    serially (~38 min), forcing a two-stage design.
- [x] 3. `compare_pipelines.py` (two-stage) — bulk `federal_district` check then
  per-record detail resolution for the non-matches.

## Phase 2 — Convert the NationBuilder side to V2 / OAuth 2.0

- [x] 4. Vendor the `nationbuilder_v2/` package from ZipStateFed
  (`client.py`, `extract.py`, `exceptions.py`, `__init__.py`). _Requirements: 2_
- [x] 5. Add V2 OAuth env vars to `.env` / `.env.example`. Reuse the ZipStateFed
  OAuth app (shared client id/secret). _Requirements: 2.1, 5.4_
- [x] 6. One-time OAuth authorize handshake to mint a **dedicated** refresh
  token for this project; persisted to `.env`. _Requirements: 2.4, 5.4_
- [x] 7. Rewrite `pull_nationbuilder.py` as a single parallel V2 pull →
  `nationbuilder_data.csv`. `--max-pages N` for a serial sample. _Requirements: 2_
- [x] 8. Simplify `compare_pipelines.py` to a single-stage inner join of the two
  full CSVs; per-field `MATCH`/`MISMATCH`/blank + `all_match`. Retire the V1
  two-stage bulk puller. _Requirements: 3_
- [x] 9. End-to-end verify on live data. _Requirements: 3_
- [x] 10. Documentation — README, `V2_OAUTH_SETUP.md`, and this spec.

## Phase 3 — AWS Lambda + S3 + SES (ToDoList #2)

- [x] 11. Lambda deployment of the comparison flow.
  - [x] 11.1 Refactor the three scripts to expose in-process functions
    (`pull_insights()`, `pull_full(concurrency, on_refresh)`,
    `build_comparison(insights_df, nationbuilder_df)`, `write_comparison_files`).
  - [x] 11.2 `lambda_pipeline.py` — `run()` + `handler()` chaining both pulls +
    compare in one process; S3 upload (date-stamped) + SES email of the
    mismatches report; status file + JSON result; empty-Insights skip guard.
  - [x] 11.3 Credentials from one combined Secrets Manager secret
    (`nb/insightslag/prod`: `NATIONBUILDER_*` + `NB_INSIGHTS_*`); rotate the V2
    refresh token back into it each run. _Requirements: 4, 5_
  - [x] 11.4 `build_lambda_zip.py` (bundles `tableau-api-lib` + non-pandas deps
    as Linux wheels; pandas/numpy from the AWS-managed layer).
  - [x] 11.5 `aws/` IAM + env templates, `Dockerfile`/`.dockerignore` fallback,
    `.env.example` AWS section, `DEPLOY.md`.
  - [x] 11.6 Verified `run()` locally (S3/SES unset → local files only).
  - [x] 11.7 Created the live AWS resources (bucket, secret, roles, function
    `insightslagcheck-comparison`, nightly schedule at 06:00 UTC). Verified with
    a live end-to-end invoke that emailed the report. _Requirements: 4_

## Phase 4 — Expand to 5 fields across 3 Insights workbooks

- [x] 12. Insights source split into three workbooks (MostCommon: state/zip;
  Custom: membership_country/us_citizen; Electoral: federal_district), pulled and
  **outer-joined** on `signup_id`. _Requirements: 1_
- [x] 13. Extend the V2 projection (`extract.py`) with `membership_country` +
  `us_citizen` from `custom_values`; comparison extended to all five fields.
  _Requirements: 2.2, 3_
- [x] 14. Text normalization: repair UTF-8/Latin-1 mojibake, fold accents, and
  treat Tableau `*` as not-a-value — fixing mass false mismatches on country
  names and `federal_district`. Deployed + verified live. _Requirements: 3.3_
- [x] 15. Retired the legacy V1 `nationbuilder/` package,
  `pull_zip_state_federal.py`, `sources.py`, `list_reports.py`, the ZIP reference
  workbook, and the `zip-state-federal-validation` spec. `nb_insights.py` kept
  (shared by the current Insights pull).

## Phase 5 — Serverless Framework + GitHub Actions (ToDoList #3)

- [x] 16. `serverless.yml` (Serverless Framework **v3**) — defines the function,
  IAM execution role, log group, env vars, pandas layer, and nightly schedule;
  ships the prebuilt `build_lambda_zip.py` artifact; references (does not manage)
  the external bucket + secret. _Requirements: 4_
- [x] 17. `.github/workflows/deploy.yml` — GitHub Actions CI/CD: on push to
  `main`, build the artifact and `serverless deploy`. Static IAM keys +
  Serverless key via repo secrets. _Requirements: 4.4_
- [x] 18. Migrated ownership: tore down the hand-made function/roles/schedule so
  CloudFormation owns them; redeployed via Serverless; verified end-to-end incl.
  a green GitHub Actions run that deployed to the live stack.
- [x] 19. Tutorial series written to `tutorial/` (gitignored) covering every
  layer of the project.

## Tech debt (tracked, not yet done)

- [ ] Upgrade Serverless Framework v3 → v4 (needs a licensed access key; also
  clears the cosmetic python3.12 runtime-schema warning).
- [ ] Replace reused CI credentials (admin AWS keys + the existing/exposed
  Serverless key) with a dedicated least-privilege deploy IAM user + fresh keys;
  consider GitHub OIDC instead of static keys.
