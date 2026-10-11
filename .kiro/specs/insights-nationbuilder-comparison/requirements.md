# Requirements — Insights vs NationBuilder Comparison

## Introduction

Democrats Abroad member ("signup") data is reported through **NationBuilder
Insights** (a Tableau-based reporting layer), but the authoritative source is
NationBuilder itself. The Insights values can lag or drift from NationBuilder —
this is the "lag" the project (InsightsLagCheck) is named for.

This feature pulls the same population from **both** systems and compares, field
by field, every `signup_id` that appears in both, reporting where they disagree —
checking Insights **against** the authoritative NationBuilder data.

The NationBuilder side uses the **V2 API over OAuth 2.0**. (An earlier version
used the V1 API with a two-stage bulk-index-then-per-record-detail pull; V2
returns the needed fields inline and paginates by page number, so a single
parallel pull replaced that.)

## Glossary

- **Signup** — a member/person record, identified by `signup_id`.
- **Pull A / Insights** — the "Zip State Federal District" Insights view, pulled
  via the Tableau REST API (`pull_insights.py` → `insights_data.csv`).
- **Pull B / NationBuilder** — every signup from the NationBuilder V2 API
  (`pull_nationbuilder.py` → `nationbuilder_data.csv`).
- **Compared fields** — `registered_state`, `registered_zip`, `federal_district`.
- **Match flag** — per field: `MATCH`, `MISMATCH`, or blank (not comparable when
  one side is empty).

## Requirements

### Requirement 1 — Pull the full Insights population (Pull A)

**User Story:** As a data steward, I want every signup in the Insights view, so
the comparison covers the whole population rather than a filtered subset.

#### Acceptance Criteria

1. WHEN Pull A runs THEN it SHALL fetch the full "Zip State Federal District"
   view and write `insights_data.csv` with columns `signup_id`,
   `registered_state`, `registered_zip`, `federal_district`.
2. WHEN writing THEN it SHALL key on `signup_id` (deduped), normalize ZIP to a
   5-digit string, and upper/strip state and district.
3. The pull SHALL apply **no** inclusion filter.

**Rationale:** the comparison is only meaningful over the shared population;
filtering would hide discrepancies.

### Requirement 2 — Pull the full NationBuilder population via V2 (Pull B)

**User Story:** As a data steward, I want the authoritative registered
state/zip/federal_district for every signup, pulled efficiently.

#### Acceptance Criteria

1. WHEN Pull B runs THEN it SHALL authenticate to the NationBuilder V2 API using
   OAuth 2.0 (refresh-token flow) and pull every signup with
   `extra_fields[signups]=registered_address`.
2. WHEN pulling THEN it SHALL fetch pages in parallel (page-number pagination)
   and write `nationbuilder_data.csv` with the same four columns as Pull A.
3. WHEN the access token is needed THEN the client SHALL refresh at startup and
   persist the rotated refresh token (local `.env`; Secrets Manager in a future
   Lambda).
4. WHERE a signup has no registered address THEN its state/zip SHALL be blank
   (not an error).

**Rationale:** V2 returns the authoritative fields inline and supports direct
page addressing, so one parallel pull replaces the old two-stage V1 workaround.

### Requirement 3 — Compare the two pulls

**User Story:** As a data steward, I want a per-field match/mismatch report for
every signup present in both systems.

#### Acceptance Criteria

1. WHEN the comparison runs THEN it SHALL inner-join Pull A and Pull B on
   `signup_id`.
2. FOR each of `registered_state`, `registered_zip`, `federal_district` it SHALL
   emit a `*_match` flag: `MATCH` (both present and equal), `MISMATCH` (both
   present and different), or blank (either side empty).
3. ZIP SHALL be compared on its first five digits; state and district
   upper/stripped.
4. It SHALL emit an `all_match` column (TRUE only when no field mismatched).
5. It SHALL write `comparison.csv`/`.xlsx` (all matched signups) and
   `comparison_mismatches.csv`/`.xlsx` (rows with ≥1 mismatch), with ZIP columns
   typed as text in the XLSX.

**Rationale:** comparing all three fields for every shared id catches
discrepancies a single-field check would miss.

### Requirement 4 — Credentials and privacy

#### Acceptance Criteria

1. WHEN the tools run THEN they SHALL load credentials from the environment
   (`.env` locally), never hardcoded.
2. All generated `*.csv`/`*.xlsx` outputs SHALL be gitignored (they contain
   member PII).
3. The NationBuilder OAuth app MAY be shared with the sibling ZipStateFed
   project, but each project SHALL hold its own refresh token (NationBuilder
   rotates refresh tokens, so a shared one would collide).

## Out of scope (for now)

- GitHub Actions + Serverless Framework deployment (ToDoList item #3).
