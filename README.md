# InsightsLagCheck

Detects where **NationBuilder Insights** (the Tableau-based reporting layer) has
fallen out of sync with the authoritative **NationBuilder** member data — the
"lag" the project is named for.

It pulls the same population from both systems and compares, field by field,
every `signup_id` that appears in **both**, reporting only the records (and
fields) that disagree. It runs locally as three scripts or unattended as a
scheduled AWS Lambda that emails the report.

## What it compares

For every signup present in both systems, five fields are compared:

| Field | Insights source | NationBuilder (V2) source |
|-------|-----------------|---------------------------|
| `registered_state` | MostCommon workbook | `registered_address.state` |
| `registered_zip` | MostCommon workbook | `registered_address.zip` (first 5 digits) |
| `federal_district` | Electoral workbook | `federal_district` (e.g. `NY13`) |
| `membership_country` | Custom workbook | `custom_values.membership_country` |
| `us_citizen` | Custom workbook | `custom_values.us_citizen` |

Each field gets a `*_match` flag: `MATCH`, `MISMATCH`, or blank (not comparable —
one side empty). Text values are normalized before comparison (upper/stripped,
ZIP to five digits, `us_citizen` to TRUE/FALSE, and accents/encoding folded so
country names like `Côte d'Ivoire` match across the two systems).

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
   # .venv/bin/python -m pip install -r requirements.txt          # macOS/Linux
   ```

2. Copy `.env.example` to `.env` and fill in your credentials. Two sets are
   needed:

   - **NationBuilder Insights** (Tableau) — a personal access token and site
     details, for the Insights pull.
   - **NationBuilder V2** (OAuth 2.0) — a registered OAuth app's client id /
     secret plus a refresh token, for the NationBuilder pull. Getting the
     refresh token is a one-time browser step documented in
     [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md).

   `.env` is gitignored and must never be committed.

## Scripts

| Script | Purpose |
|--------|---------|
| `pull_insights.py` | Pull A. Pulls three Insights workbooks (MostCommon, Custom, Electoral) and outer-joins them on `signup_id` into `insights_data.csv` (the five compared fields). No filtering. |
| `pull_nationbuilder.py` | Pull B. Pulls **every** signup from the NationBuilder V2 API in one parallel pass into `nationbuilder_data.csv`. `--max-pages N` pulls a small serial sample for a quick test. |
| `compare_pipelines.py` | Inner-joins the two pulls on `signup_id` and writes the comparison reports. |
| `lambda_pipeline.py` | AWS Lambda entry point (`lambda_pipeline.handler`): runs both pulls + the compare in one process, uploads to S3, and emails the report. Also runs locally via `main()`. |
| `nb_insights.py` | Shared Tableau connection helper (loads `.env` / Secrets Manager, provides `insights_connection()`). |
| `nationbuilder_v2/` | NationBuilder V2 API client: OAuth 2.0 refresh-token flow (`client.py`), the signup field projection (`extract.py`), and errors (`exceptions.py`). |

## Running the comparison

The three Insights workbooks (keyed on `signup_id`) are:

- `APItemplate_InsightsCheck_MostCommon_RM` → `registered_state`, `registered_zip`
- `ApItemplate_InsightsCheckCustom_RM` → `membership_country`, `us_citizen`
- `APItemplate_InsightsCheckElectoral_RM` → `federal_district`

`pull_insights.py` pulls all three and joins them, so a signup present in any of
them is represented. `pull_nationbuilder.py` pulls the whole nation from V2 (it
returns `registered_address` + `custom_values` inline and paginates by page
number, so the full pull is a single parallel pass of a few minutes).
`compare_pipelines.py` then inner-joins the two on `signup_id`.

Run it end to end locally:

```bash
.venv\Scripts\python.exe pull_insights.py
.venv\Scripts\python.exe pull_nationbuilder.py
.venv\Scripts\python.exe compare_pipelines.py
# quick NB sample (first 5 pages only) while testing:
.venv\Scripts\python.exe pull_nationbuilder.py --max-pages 5
```

### Output

Two report pairs, written to `OUTPUT_DIR` (default `.`), all gitignored (member
PII):

- `comparison.csv` / `.xlsx` — every matched `signup_id`, all five fields with
  their `*_match` flags plus an `all_match` column.
- `comparison_mismatches.csv` / `.xlsx` — only rows with at least one mismatch.

The `.xlsx` files type the ZIP columns as text so Excel preserves leading zeros.

## Running as an AWS Lambda

The comparison runs unattended as an AWS Lambda. `lambda_pipeline.handler` is the
entry point: it runs Pull A (Insights) and Pull B (NationBuilder V2) in-process,
compares them, writes the reports to `/tmp`, uploads them to S3 (date-stamped),
and emails the mismatches report (plus the full comparison CSV) as attachments
via SES. If the Insights pull returns no rows, it emails a "skipped" notice
instead of failing silently.

- **Credentials** come from a single AWS Secrets Manager secret
  (`nb/insightslag/prod`) holding both the `NATIONBUILDER_*` (V2 OAuth) and
  `NB_INSIGHTS_*` (Tableau) keys. The function rotates the V2 refresh token back
  into that secret after each run.
- **Deployment is infrastructure-as-code**: `serverless.yml` (Serverless
  Framework v3) defines the function, IAM role, log group, and schedule, and a
  **GitHub Actions** workflow (`.github/workflows/deploy.yml`) deploys on every
  push to `main`. CloudFormation owns those resources; the S3 bucket and the
  Secrets Manager secret are external (they hold live state) and are referenced,
  not created.
- **Build**: the deploy ships a prebuilt artifact from `build_lambda_zip.py`
  (bundles the non-pandas deps — including `tableau-api-lib` — as Linux wheels;
  pandas/numpy come from the AWS-managed pandas layer).
- **Sizing**: 3008 MB memory / 900 s timeout (two full ~189k-row pulls are held
  in memory at once).
- **Schedule**: a nightly EventBridge rule runs it daily at 06:00 UTC.

See **[`DEPLOY.md`](DEPLOY.md)** for the full step-by-step (prerequisites, the
required GitHub secrets, manual deploy, troubleshooting, and future upgrades);
a container-image path is included there as an alternative.

## Spec

Full requirements, design, and tasks live in
`.kiro/specs/insights-nationbuilder-comparison/`.

## Notes

- The NationBuilder V2 OAuth app is shared with the sibling ZipStateFed project,
  but each project holds its **own** refresh token (NationBuilder rotates the
  refresh token on every refresh, so a shared one would collide). See
  [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md).
- Set `NB_INSIGHTS_API_VERSION=3.23` in `.env` to match the Insights server.
- On Windows, run scripts with `.venv\Scripts\python.exe <script>.py`.
