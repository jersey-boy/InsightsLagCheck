# InsightsAPIZipState

Python tools that access the **NationBuilder Insights** reporting system (built
on Tableau) via the Tableau REST API, and validate member ZIP / state / federal
district data integrity.

## Setup

1. Create and activate a virtual environment, then install dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your credentials:

   ```bash
   cp .env.example .env
   ```

   The comparison flow needs two sets of credentials:

   - **NationBuilder Insights** (Tableau) — a personal access token and site
     details, for the Insights pull.
   - **NationBuilder V2** (OAuth 2.0) — a registered OAuth app's client id /
     secret plus a refresh token, for the NationBuilder pull. Obtaining the
     refresh token is a one-time browser step documented in
     [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md).

   `.env` is gitignored and must never be committed.

## Scripts

| Script | Purpose |
|--------|---------|
| `nb_insights.py` | Shared connection helper. Loads `.env`, provides `insights_connection()` context manager. Run directly for a sign-in smoke test. |
| `list_reports.py` | Lists all Insights workbooks, views, and data sources; writes each to CSV. |
| `pull_zip_state_federal.py` | Pulls the "Zip State Federal District" view, runs two independent checks (ZIP vs. state, federal district vs. state), writes the full dataset plus a problems report per check, and — when NationBuilder credentials are set — cross-checks against the NB API and writes an Insights-vs-NationBuilder discrepancy report. Also the Lambda entry point. |
| `sources.py` | Original sample: lists data source fields. |

> **Two NationBuilder client packages live in this repo.** `nationbuilder_v2/`
> (OAuth 2.0, V2 API) is the current client used by the comparison flow below.
> `nationbuilder/` is the legacy **V1** client, still used only by
> `pull_zip_state_federal.py` (the standalone validator / current Lambda
> handler). The V1 package will be retired when that script is migrated.

### Pipeline comparison (Insights vs NationBuilder)

This is the project's primary flow: it pulls both systems in full and compares
every `signup_id` that appears in **both**, field by field, with no filtering.
It answers "where does Insights disagree with the authoritative NationBuilder
data?" — the "lag check" the project is named for.

The NationBuilder side uses the **V2 API over OAuth 2.0** (see
[`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md)). V2 returns each signup's
`registered_address` and `federal_district` inline and supports page-number
pagination, so the whole nation is pulled in one parallel pass — there is no
longer a two-stage "bulk index then per-record detail" workaround.

| Script | Purpose |
|--------|---------|
| `pull_insights.py` | Pull A. Pulls the full "Zip State Federal District" Insights view and writes `insights_data.csv` (one row per `signup_id`: `registered_state`, `registered_zip`, `federal_district`). No filtering. |
| `pull_nationbuilder.py` | Pull B. Pulls **every** signup from the NationBuilder V2 API in a single parallel pass and writes `nationbuilder_data.csv` (`signup_id`, `registered_state`, `registered_zip`, `federal_district`). `--max-pages N` pulls a small serial sample for a quick test. |
| `compare_pipelines.py` | Inner-joins the two pulls on `signup_id` and compares the three fields (see below). |
| `nationbuilder_v2/` | NationBuilder V2 API client: OAuth 2.0 refresh-token flow (`client.py`), the signup field projection (`extract.py`), and errors (`exceptions.py`). |

**How the comparison works** (`compare_pipelines.py`):

It inner-joins `insights_data.csv` with `nationbuilder_data.csv` on `signup_id`
(every id present in **both**) and, for each of `registered_state`,
`registered_zip`, and `federal_district`, emits a `*_match` flag:

- `MATCH` — both sides have a value and they agree,
- `MISMATCH` — both sides have a value and they differ,
- blank — not comparable (one side is empty). ZIPs are compared on their first
  five digits; state and district are compared upper/stripped.

An `all_match` column is `TRUE` only when no field mismatched. It writes
`comparison.csv` / `.xlsx` (every matched `signup_id`, all three fields with
flags) and `comparison_mismatches.csv` / `.xlsx` (only rows with at least one
mismatch).

Because all three fields are compared for every shared `signup_id`, this catches
discrepancies a federal-district-only check would miss — e.g. a member whose
`federal_district` happens to agree but whose registered state or ZIP has
drifted out of sync between the two systems.

Run the comparison end to end:

```bash
.venv\Scripts\python.exe pull_insights.py
.venv\Scripts\python.exe pull_nationbuilder.py
.venv\Scripts\python.exe compare_pipelines.py
# quick NB sample (first 5 pages only) while testing:
.venv\Scripts\python.exe pull_nationbuilder.py --max-pages 5
```

All generated `*.csv` / `*.xlsx` files contain member PII and are gitignored.

### Validation scripts

Run any script with:

```bash
.venv/bin/python <script>.py
```

## Validation output

`pull_zip_state_federal.py` runs two independent checks and produces three report
pairs plus the full dataset — six CSV/XLSX files, or eight when the NationBuilder
cross-check is enabled (all gitignored, as they contain member PII):

**Full dataset** (every record, both problem-flag columns):

- `zip_state_federal_district.csv` / `.xlsx` — the `.xlsx` types the ZIP column
  as text so Excel keeps leading zeros.

**Test 1 — ZIP / state** (only rows that failed this check):

- `zip_state_problems.csv` / `.xlsx` — carries the `zip_state_problem` flag.

**Test 2 — federal district / state** (only rows that failed this check):

- `federal_district_problems.csv` / `.xlsx` — carries the
  `federal_district_problem` flag.
- When NationBuilder credentials are configured (`NATIONBUILDER_SLUG` /
  `NATIONBUILDER_ACCESS_TOKEN`), this report also carries two cross-check
  columns: `nb_federal_district` (the authoritative value from the NationBuilder
  V1 API for that `signup_id`) and `federal_district_match`
  (`MATCH`/`MISMATCH`/blank). The Insights `federal_district` is unreliable, so
  the NB value is looked up for each flagged record (one API call per flagged
  `signup_id`, run in parallel) and shown side by side. The lookup is a no-op
  when NB credentials are unset.

**Report 3 — Insights vs NationBuilder** (only when NB credentials are set):

- `insights_vs_nationbuilder.csv` / `.xlsx` — compares three fields —
  `state`, `zip`, and `federal_district` — between Insights and NationBuilder for
  the **union of all flagged records** (anything flagged by either test). Each
  field has its own `*_match` flag (`MATCH`/`MISMATCH`/blank-when-not-comparable),
  and the report keeps **only records where at least one field differs**. The NB
  values come from the member's `registered_address` (state, zip) and the
  top-level `federal_district`. This catches discrepancies the per-test reports
  miss — e.g. a ZIP or state that disagrees between the two systems.

The two per-test problem reports are independent; a member flagged by both checks
appears in both files. A passing check leaves the flag blank. Problem messages
are explicit, e.g. `ZIP STATE PROBLEM. SHOULD BE VA`,
`ZIP STATE PROBLEM. ZIP IS BLANK`, `ZIP STATE PROBLEM. ZIP NOT FOUND`,
`FEDERAL DISTRICT PROBLEM`, `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK`.

### Output location and S3 upload

By default the files are written to the current directory. Two optional
environment variables (see `.env.example`) change this:

- `OUTPUT_DIR` — directory to write into. Defaults to `.`; set it to `/tmp` when
  running in AWS Lambda (the only writable path there).
- `S3_BUCKET` (+ optional `S3_PREFIX`, `S3_REGION`) — when set, every output file
  is uploaded to `s3://<bucket>/<prefix>/<filename>`. Object names are
  **date-stamped** with the run date, e.g. `zip_state_problems_YYYYMMDD.csv`, so
  a history accumulates in the bucket (local file names stay undated). A
  presigned URL is also generated per object (valid up to 7 days; tune with
  `PRESIGN_EXPIRY_SECONDS`) and kept in the run result. When unset, output stays
  local only.

Email delivery (see below) sends the reports as **attachments**, so S3 is not
required for email — the two are independent.

No AWS keys are configured in this project: locally `boto3` uses your AWS
profile/environment, and in Lambda it uses the function's IAM execution role.
`boto3` is listed in `requirements.txt` for local use; it ships in the Lambda
runtime already, so it does not need to be packaged for deployment.

### Email notification

When `SES_SENDER` is set and a recipient list exists (`RECIPIENTS_S3_URI` or a
local `RECIPIENTS_FILE`, one address per line, `#` comments), the run emails the
**problem reports as file attachments** via Amazon SES — the two per-test reports
plus the Insights-vs-NationBuilder report when the NB cross-check is enabled. The
full dataset is never attached (it is uploaded to S3 for reference). Attachments
are named with the run date. The body carries the summary counts and the `EMAIL_MESSAGE` note.
An attachment is skipped (with a note in the body) if it would push the message
past the SES ~10 MB limit. Email is a no-op unless `SES_SENDER` is set.

> An earlier version emailed presigned S3 **links** instead of attachments. That
> approach is preserved in `_build_link_email()` for easy revert (see
> `DEPLOY.md`).

## ZIP reference file

> For a deep dive on the reference file layout and what every error message
> means, see **[`ZIP_REFERENCE_AND_ERRORS.md`](ZIP_REFERENCE_AND_ERRORS.md)**.

ZIP → state validation uses the USPS **`ZIP_Locale_Detail.xlsx`** workbook, which
must sit alongside the scripts (it is gitignored). The workbook has three sheets
that are merged into a single ZIP → state lookup (~41,500 ZIPs):

| Sheet | Header row | Notes |
|-------|-----------|-------|
| `Detail` | 1 | Main locale list; authoritative, wins on conflicts. |
| `Unique` | 3 | Unique / single-entity ZIPs (universities, PO-box-only, etc.). |
| `Other`  | 3 | Remaining ZIPs. |

Using `Detail` alone omits ~195 valid ZIPs that live on `Unique`/`Other`, so all
three are combined.

### Physical vs. delivery ZIP

The `ZIP_MODE` constant in `pull_zip_state_federal.py` selects which ZIP keys the
lookup:

- `"delivery"` (default) — keyed by the delivery ZIP. Broadest coverage; only ~50
  member ZIPs end up `ZIP NOT FOUND` (placeholder / unassigned ZIPs).
- `"physical"` — keyed by the physical (facility) ZIP. Narrower coverage, so many
  valid member ZIPs get flagged `ZIP NOT FOUND`; use only if you specifically want
  to validate against the physical facility ZIP.

The workbook only carries a *physical* state, so both modes validate the ZIP
against that physical state — the mode only changes which ZIP column is the key.

## Specs

Full requirements, design, and tasks live in `.kiro/specs/`:

- `insights-nationbuilder-comparison/` — the Insights-vs-NationBuilder V2
  comparison flow (the primary pipeline described above).
- `zip-state-federal-validation/` — the legacy `pull_zip_state_federal.py`
  validator (ZIP/state + district/state checks; current Lambda handler).

## Running as an AWS Lambda

`pull_zip_state_federal.handler` is the Lambda entry point. It writes outputs to
`/tmp`, uploads them to S3, and returns presigned download URLs. Build the
deployment zip with `python build_lambda_zip.py` (bundles the non-pandas deps as
Linux wheels; pandas/numpy come from the AWS-managed pandas layer). See
**[`DEPLOY.md`](DEPLOY.md)** for the full step-by-step (IAM, Secrets Manager,
layer ARN, scheduling); a container-image path is included there as a fallback.

## Notes

- Requires the `ZIP_Locale_Detail.xlsx` reference workbook alongside the scripts
  (see [ZIP reference file](#zip-reference-file)). It is gitignored.
- Set `NB_INSIGHTS_API_VERSION=3.23` in `.env` to match the server (the server
  is on REST API 3.23; a lower value logs a warning and can break sign-out).
- On Windows, run scripts with `.venv\Scripts\python.exe <script>.py` instead of
  the `.venv/bin/python` shown above.
