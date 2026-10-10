"""
Pull A — NationBuilder Insights (Tableau).

Pulls the full "Zip State Federal District" Insights view and writes it to
`insights_data.csv`, keyed by signup_id. No filtering: every record the view
returns is written, so the comparison step can match on the full population.

Columns written:
    signup_id, registered_state, registered_zip, federal_district

Run:
    .venv\\Scripts\\python.exe pull_insights.py
"""

from __future__ import annotations

import os

import pandas as pd
from tableau_api_lib.utils.querying import get_view_data_dataframe

from nb_insights import insights_connection

# The "Zip State Federal District" view. Lives in the Insights workbook
# APItemplate_ZipStateAndFedDistrict2_RM (contentUrl slug APItemplate_ZipStateAnd2).
# The original APItemplate_ZipStateAndFedDistrict view was emptied/retired on the
# Insights side; this is its replacement with the same column schema.
VIEW_ID = "1e0b44c9-b910-4cb1-81c0-0892cd82ade3"
VIEW_NAME = "Zip State Federal District"

OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")
OUTPUT_CSV = "insights_data.csv"

# Insights column -> our canonical name.
COLUMN_MAP = {
    "signup_id": "signup_id",
    "registered_state": "registered_state",
    "registered_zip_clean": "registered_zip",
    "federal_district": "federal_district",
}


def _out(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def _norm_zip(value: object) -> str:
    """Zero-padded 5-digit ZIP as text, or '' when there are no digits.

    The Insights view stores the ZIP as a number (e.g. 1002), which drops the
    leading zero; pad it back to five digits so it compares cleanly later.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ""
    return digits[:5].zfill(5)


def pull_insights() -> pd.DataFrame:
    """Return the full Insights view as a canonical DataFrame keyed by signup_id."""
    with insights_connection() as conn:
        df = get_view_data_dataframe(conn, view_id=VIEW_ID)

    # Keep only the fields we compare, renamed to canonical names. Any missing
    # source column is created empty so the output schema is stable.
    out = pd.DataFrame()
    for src, dst in COLUMN_MAP.items():
        out[dst] = df[src] if src in df.columns else ""

    # Normalize: signup_id as a nullable integer, state upper/stripped, zip as
    # padded text.
    out["signup_id"] = pd.to_numeric(out["signup_id"], errors="coerce").astype("Int64")
    out["registered_state"] = (
        out["registered_state"].astype(str).str.strip().str.upper().replace("NAN", "")
    )
    out["registered_zip"] = out["registered_zip"].map(_norm_zip)
    out["federal_district"] = (
        out["federal_district"].astype(str).str.strip().str.upper().replace("NAN", "")
    )

    # Drop rows with no signup_id (can't be matched) and de-dupe on signup_id.
    out = out.dropna(subset=["signup_id"]).drop_duplicates(subset=["signup_id"])
    return out.reset_index(drop=True)


def main() -> None:
    df = pull_insights()
    path = _out(OUTPUT_CSV)
    df.to_csv(path, index=False)
    print(f"Insights pull: {len(df):,} records -> {path}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
