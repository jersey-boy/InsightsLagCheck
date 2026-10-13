# Design — Insights vs NationBuilder Comparison

## Overview

The pipeline pulls member data from two systems, lines them up by `signup_id`,
and flags per-field disagreements. It runs two ways from one codebase:

- **Locally** — three scripts passing CSVs between them (easy to inspect).
- **In AWS Lambda** — one orchestrator (`lambda_pipeline.py`) that runs both
  pulls and the compare **in-process** (DataFrames in memory, no CSV round-trip),
  then uploads to S3 and emails via SES.

```
pull_insights.py       (3 Insights workbooks, outer-joined)  ─┐
                                                              ├─▶ compare ─▶ comparison*.csv/.xlsx
pull_nationbuilder.py  (NationBuilder V2, parallel pull)     ─┘
```

The three core modules each expose a plain function that returns a pandas
DataFrame, so the same tested logic drives both the CLI path and the Lambda
orchestrator. Decoupling the pulls from the compare means either source can be
re-pulled independently, and the compare is a fast, offline, repeatable step.

## Source modules

```
pull_insights.py        Pull A: 3 Tableau workbooks, outer-joined -> insights_data.csv
pull_nationbuilder.py   Pull B: NationBuilder V2 API (parallel)   -> nationbuilder_data.csv
compare_pipelines.py    Inner join + per-field match flags        -> comparison reports
lambda_pipeline.py      Orchestrator: run()/handler() for AWS Lambda (in-process)
nb_insights.py          Shared Tableau connection + credential/secret loading
nationbuilder_v2/       V2 OAuth client + signup extractor
  client.py             OAuth 2.0 refresh-token flow; parallel paged pull
  extract.py            project_signup(); extract_all_signups()
  exceptions.py         NationBuilderV2Error
build_lambda_zip.py     Builds the deployment artifact (Linux wheels)
serverless.yml          Infrastructure-as-code (function, role, log group, schedule)
.github/workflows/      GitHub Actions CI/CD (deploy on push to main)
aws/                    IAM + env templates; Dockerfile container-image fallback
```

(The legacy V1 `nationbuilder/` package and `pull_zip_state_federal.py` validator
that this pipeline replaced have been removed from the repo.)

## The five compared fields

| Field | Insights source workbook | NationBuilder V2 source |
|-------|--------------------------|--------------------------|
| `registered_state` | MostCommon | `registered_address.state` |
| `registered_zip` | MostCommon | `registered_address.zip` (first 5 digits) |
| `federal_district` | Electoral | `federal_district` (top-level attr) |
| `membership_country` | Custom | `custom_values.membership_country` |
| `us_citizen` | Custom | `custom_values.us_citizen` |

`membership_country` and `us_citizen` are NationBuilder **custom fields** (under
`custom_values`), not top-level attributes — the V2 pull must request
`extra_fields[signups]=registered_address,custom_values` to get them.

## Components

### 1. Pull A — `pull_insights.py`

- Uses `insights_connection()` (from `nb_insights.py`) to pull three views, keyed
  by the `INSIGHTS_VIEWS` map:
  - MostCommon `29265167-24f1-4bb9-bc8a-1a8dda6d4bb3` → state, zip
  - Custom `ec79c191-dfc9-42b1-9edc-5d8bee3b56c2` → country, us_citizen
  - Electoral `7553b0e6-3211-403a-aaed-6038e0f82fa8` → federal_district
- **Outer-joins** the three on `signup_id` so a signup missing from one workbook
  is kept (its missing field blank) rather than dropped.
- Normalizes: ZIP → 5-digit text; state/district/country upper-stripped;
  `us_citizen` → `TRUE`/`FALSE`. Dedupes on `signup_id`; writes `insights_data.csv`.

### 2. Pull B — `pull_nationbuilder.py` + `nationbuilder_v2/`

- `NationBuilderV2Client.from_env()` builds the client from `NATIONBUILDER_*`
  env vars and refreshes the access token at construction (just-in-time), so a
  run always starts with a valid 24h token. The rotated refresh token is
  persisted via `on_refresh` (local `.env` writer by default; a Secrets Manager
  writer in Lambda).
- `extract_all_signups()` calls `client.fetch_all_signups(extra_fields=
  ["registered_address", "custom_values"])`, which fetches pages in parallel
  **batches** by page number and stops when a whole batch is empty (the stats
  total is unreliable). Each page retries on 429/5xx with backoff.
- `project_signup()` maps each JSON:API record to the six canonical columns.
- The script dedupes on `signup_id` and writes `nationbuilder_data.csv`.
- `--max-pages N` does a small **serial** sample (via `iter_signups`) for quick
  testing without the full ~189k pull.

**Why V2 (not V1):** the V1 `/people` index did not return `registered_address`
and used cursor pagination (serial, ~38 min for the nation), forcing a two-stage
"index then per-record detail" design. V2 returns the needed fields inline and
supports direct page-number addressing, so a single parallel pull (~minutes)
replaced both stages. See `V2_OAUTH_SETUP.md`.

### 3. Compare — `compare_pipelines.py`

- Reads both CSVs as strings (or accepts in-memory DataFrames), keys `signup_id`
  as a nullable int, dedupes.
- **Inner-joins** on `signup_id` (members present in both systems).
- For each compared field, `_field_flag()` returns `MATCH` / `MISMATCH` / blank.
  Text goes through `_norm()`: repair mojibake → fold accents → upper/strip →
  map Tableau `*` and blank to `""`. ZIP compares on its first five digits.
  `us_citizen` is already `TRUE`/`FALSE` on both sides. A blank on either side ⇒
  "not comparable" (blank flag), never a mismatch.
- Adds `all_match` (TRUE unless some field is `MISMATCH`).
- `write_comparison_files()` writes `comparison.csv`/`.xlsx` (all matched ids)
  and `comparison_mismatches.csv`/`.xlsx` (≥1 mismatch). The XLSX writer types
  ZIP columns as text so Excel keeps leading zeros.

### 4. Orchestrator — `lambda_pipeline.py`

- `run()` chains `pull_insights.pull_insights()` →
  `pull_nationbuilder.pull_full(on_refresh=...)` → `build_comparison(df, df)` →
  `write_comparison_files()`, all in memory, then (when configured) uploads to S3
  and emails the mismatches report via SES. `handler(event, context)` wraps
  `run()` for Lambda and returns a JSON summary.
- Loads both credential sets from one Secrets Manager secret at startup, and the
  V2 refresh-token rotation is written back to that secret.
- **Empty-Insights guard:** if Pull A returns zero rows, it emails a "skipped —
  no data" notice and exits cleanly rather than crash or email noise.

## Data shapes

Both pull CSVs share six columns:

| column | notes |
|--------|-------|
| `signup_id` | integer id, unique per file |
| `registered_state` | 2-letter, upper/stripped (may be blank) |
| `registered_zip` | first 5 digits as text (may be blank) |
| `federal_district` | e.g. `NY13`, upper/stripped (may be blank) |
| `membership_country` | country name, upper/stripped (may be blank) |
| `us_citizen` | `TRUE` / `FALSE` / blank |

Comparison output adds, per field, `insights_<f>`, `nb_<f>`, `<f>_match`, plus a
final `all_match`.

## Deployment design

- **Infrastructure-as-code (`serverless.yml`, Serverless v3):** CloudFormation
  owns the Lambda, its IAM execution role, the log group, and the nightly
  EventBridge schedule (`cron(0 6 * * ? *)`). Memory 3008 MB / timeout 900 s (two
  full ~189k-row pulls held in memory). The AWS-managed pandas layer supplies
  pandas/numpy; `build_lambda_zip.py` ships the other deps as Linux wheels.
- **Referenced, not managed:** the S3 reports bucket and the Secrets Manager
  secret hold live state (report history; the rotating refresh token), so they
  are created once by hand and only referenced by the stack — never
  created/destroyed by it.
- **CI/CD (GitHub Actions):** on push to `main`, build the artifact and
  `serverless deploy`. Credentials come from repo secrets.

## Error handling & privacy

- OAuth / API failures raise `NationBuilderV2Error` with status + body for
  diagnosis. Per-page transient errors retry before failing.
- `run()` never raises: failures are captured in the returned dict and the
  status file, so a Lambda always gets a clean response.
- Credentials come only from the environment / Secrets Manager; outputs are
  gitignored (member PII).

## Known characteristics / design notes

- Comparing all five fields (not just `federal_district`) surfaces state/zip/
  country drift a district-only check would miss.
- Blank-on-one-side pairs are "not comparable," not mismatches — this keeps the
  report free of noise from fields one system simply lacks.
- Text normalization (mojibake repair + accent folding + Tableau `*` handling) is
  load-bearing: without it, encoding/sentinel artifacts produce tens of thousands
  of false mismatches. See the tutorial series (`tutorial/`, gitignored) for the
  war stories.
