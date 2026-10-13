# Requirements — Insights vs NationBuilder Comparison

## Introduction

Democrats Abroad member ("signup") data is reported through **NationBuilder
Insights** (a Tableau-based reporting layer), but the authoritative source is
NationBuilder itself. The Insights values can lag or drift from NationBuilder —
this is the "lag" the project (InsightsLagCheck) is named for.

This feature pulls the same population from **both** systems and compares, field
by field, every `signup_id` that appears in both, reporting where they disagree —
checking Insights **against** the authoritative NationBuilder data. It runs
locally as scripts and, in production, as a scheduled AWS Lambda that emails the
report nightly.

The NationBuilder side uses the **V2 API over OAuth 2.0**. (An earlier version
used the V1 API with a two-stage bulk-index-then-per-record-detail pull; V2
returns the needed fields inline and paginates by page number, so a single
parallel pull replaced that.)

## Glossary

- **Signup** — a member/person record, identified by `signup_id`.
- **Pull A / Insights** — three Insights (Tableau) workbooks, pulled via the
  Tableau REST API and outer-joined on `signup_id` (`pull_insights.py` →
  `insights_data.csv`):
  - `APItemplate_InsightsCheck_MostCommon_RM` → `registered_state`, `registered_zip`
  - `ApItemplate_InsightsCheckCustom_RM` → `membership_country`, `us_citizen`
  - `APItemplate_InsightsCheckElectoral_RM` → `federal_district`
- **Pull B / NationBuilder** — every signup from the NationBuilder V2 API
  (`pull_nationbuilder.py` → `nationbuilder_data.csv`).
- **Compared fields** — `registered_state`, `registered_zip`, `federal_district`,
  `membership_country`, `us_citizen`.
- **Match flag** — per field: `MATCH`, `MISMATCH`, or blank (not comparable when
  one side is empty or not a usable value).

## Requirements

### Requirement 1 — Pull the full Insights population (Pull A)

**User Story:** As a data steward, I want every signup from Insights, so the
comparison covers the whole population rather than a filtered subset.

#### Acceptance Criteria

1. WHEN Pull A runs THEN it SHALL pull the three Insights workbooks (MostCommon,
   Custom, Electoral) and **outer-join** them on `signup_id`, so a signup present
   in any workbook is represented.
2. WHEN writing THEN it SHALL produce `insights_data.csv` with columns
   `signup_id`, `registered_state`, `registered_zip`, `federal_district`,
   `membership_country`, `us_citizen`, keyed and deduped on `signup_id`.
3. WHEN normalizing THEN it SHALL render ZIP as a 5-digit string, upper/strip
   state / district / country, and canonicalize `us_citizen` to `TRUE`/`FALSE`.
4. The pull SHALL apply **no** inclusion filter.

**Rationale:** the comparison is only meaningful over the shared population;
filtering would hide discrepancies. An outer join across the three workbooks
avoids silently dropping a signup that is missing from one of them.

### Requirement 2 — Pull the full NationBuilder population via V2 (Pull B)

**User Story:** As a data steward, I want the authoritative values for every
signup, pulled efficiently.

#### Acceptance Criteria

1. WHEN Pull B runs THEN it SHALL authenticate to the NationBuilder V2 API using
   OAuth 2.0 (refresh-token flow) and pull every signup with
   `extra_fields[signups]=registered_address,custom_values`.
2. WHEN projecting THEN it SHALL produce the same six columns as Pull A, where
   `registered_state`/`registered_zip` come from `registered_address`,
   `federal_district` is a top-level attribute, and `membership_country` /
   `us_citizen` come from `custom_values` (they are NationBuilder **custom
   fields**, not top-level attributes).
3. WHEN pulling THEN it SHALL fetch pages in parallel (page-number pagination)
   and write `nationbuilder_data.csv`.
4. WHEN the access token is needed THEN the client SHALL refresh at startup and
   persist the rotated refresh token (local `.env`; AWS Secrets Manager in
   Lambda).
5. WHERE a signup has no registered address or custom value THEN that field
   SHALL be blank (not an error).

**Rationale:** V2 returns the authoritative fields inline and supports direct
page addressing, so one parallel pull replaces the old two-stage V1 workaround.

### Requirement 3 — Compare the two pulls

**User Story:** As a data steward, I want a per-field match/mismatch report for
every signup present in both systems.

#### Acceptance Criteria

1. WHEN the comparison runs THEN it SHALL inner-join Pull A and Pull B on
   `signup_id` (the shared population).
2. FOR each of the five compared fields it SHALL emit a `*_match` flag: `MATCH`
   (both present and equal), `MISMATCH` (both present and different), or blank
   (either side empty / not comparable).
3. WHEN comparing text THEN it SHALL normalize so values differing only by
   encoding or form compare equal: repair UTF-8/Latin-1 **mojibake**, fold
   accents, upper/strip, compare ZIP on its first five digits, and treat
   Tableau's `*` ("multiple values") and blanks as **not a value**.
4. It SHALL emit an `all_match` column (TRUE only when no field mismatched).
5. It SHALL write `comparison.csv`/`.xlsx` (all matched signups) and
   `comparison_mismatches.csv`/`.xlsx` (rows with ≥1 mismatch), with ZIP columns
   typed as text in the XLSX so Excel keeps leading zeros.

**Rationale:** comparing all five fields for every shared id catches drift a
single-field check would miss. The text normalization prevents false mismatches
from encoding/sentinel artifacts (e.g. `Côte d'Ivoire` mojibake, Tableau `*`).

### Requirement 4 — Run unattended in the cloud

**User Story:** As a data steward, I want the comparison to run every night and
email me the mismatches, with no machine of mine switched on.

#### Acceptance Criteria

1. WHEN deployed THEN the pipeline SHALL run as an AWS Lambda
   (`lambda_pipeline.handler`) that performs both pulls and the compare
   in-process (no intermediate CSV round-trip), writes the reports to `/tmp`,
   uploads them to S3 (date-stamped), and emails the mismatches report via SES.
2. WHEN the Insights pull returns zero rows THEN the run SHALL email a short
   "skipped — no data" notice and exit cleanly, rather than fail or email a
   garbage report.
3. WHEN scheduled THEN an EventBridge rule SHALL invoke the function nightly at
   06:00 UTC.
4. The function, its IAM execution role, log group, and schedule SHALL be defined
   as code (Serverless Framework) and deployed via GitHub Actions on push to
   `main`.

**Rationale:** infrastructure-as-code + CI/CD makes the deployment reproducible,
reviewable, and automatic.

### Requirement 5 — Credentials and privacy

#### Acceptance Criteria

1. WHEN the tools run THEN they SHALL load credentials from the environment
   (`.env` locally) or AWS Secrets Manager (in Lambda), never hardcoded.
2. In Lambda, both credential sets (`NATIONBUILDER_*` V2 OAuth and `NB_INSIGHTS_*`
   Tableau) SHALL live in one combined secret (`nb/insightslag/prod`), and the
   function SHALL write the rotated V2 refresh token back to it after each run
   (requires `secretsmanager:PutSecretValue`).
3. All generated `*.csv`/`*.xlsx` outputs SHALL be gitignored (they contain
   member PII).
4. The NationBuilder OAuth app MAY be shared with the sibling ZipStateFed
   project, but each project SHALL hold its own refresh token (NationBuilder
   rotates refresh tokens, so a shared one would collide).

## Tech debt (tracked, not yet done)

- Upgrade Serverless Framework v3 → v4 (needs a licensed access key).
- Replace the reused CI credentials (admin AWS keys + the existing Serverless
  key) with a dedicated least-privilege deploy IAM user and fresh keys; consider
  GitHub OIDC instead of static keys.
