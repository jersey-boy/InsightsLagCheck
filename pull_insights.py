"""
Pull A — NationBuilder Insights (Tableau).

Pulls the member data from THREE Insights workbooks and joins them on signup_id
into one record per member, written to `insights_data.csv`. No filtering: every
signup any of the three views returns is kept, so the comparison can match on the
full population.

The fields are split across three workbooks on the Insights side:

    MostCommon (APItemplate_InsightsCheck_MostCommon_RM)
        -> registered_state, registered_zip
    Custom     (ApItemplate_InsightsCheckCustom_RM)
        -> membership_country, us_citizen
    Electoral  (APItemplate_InsightsCheckElectoral_RM)
        -> federal_district

Columns written (one row per signup_id):
    signup_id, registered_state, registered_zip, federal_district,
    membership_country, us_citizen

Run:
    .venv\\Scripts\\python.exe pull_insights.py
"""

from __future__ import annotations

import os

import pandas as pd
from tableau_api_lib.utils.querying import get_view_data_dataframe

from nb_insights import insights_connection

OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")
OUTPUT_CSV = "insights_data.csv"

# The three Insights views, keyed on signup_id. Each entry maps the view's
# source columns to our canonical output names. Columns listed here that are
# absent from a view are simply skipped (the join fills them as blank).
INSIGHTS_VIEWS = {
    "MostCommon": {
        "view_id": "29265167-24f1-4bb9-bc8a-1a8dda6d4bb3",
        "columns": {
            "registered_state": "registered_state",
            "registered_zip": "registered_zip",
        },
    },
    "Custom": {
        "view_id": "ec79c191-dfc9-42b1-9edc-5d8bee3b56c2",
        "columns": {
            "membership_country": "membership_country",
            "us_citizen": "us_citizen",
        },
    },
    "Electoral": {
        "view_id": "7553b0e6-3211-403a-aaed-6038e0f82fa8",
        "columns": {
            "federal_district": "federal_district",
        },
    },
}

# Final canonical column order.
COLUMNS = [
    "signup_id",
    "registered_state",
    "registered_zip",
    "federal_district",
    "membership_country",
    "us_citizen",
]

# Columns that get the plain upper/strip treatment.
UPPER_STRIP_COLUMNS = ["registered_state", "federal_district", "membership_country"]


def _out(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def _norm_zip(value: object) -> str:
    """Zero-padded 5-digit ZIP as text, or '' when there are no digits."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ""
    return digits[:5].zfill(5)


def _norm_citizen(value: object) -> str:
    """Canonical TRUE / FALSE / '' for the us_citizen flag.

    Insights returns it as a float (1.0 / 0.0 / NaN); NationBuilder returns a
    bool. Collapse the common encodings so the two sides compare cleanly.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip().lower()
    if s in {"1", "1.0", "true", "yes", "t", "y"}:
        return "TRUE"
    if s in {"0", "0.0", "false", "no", "f", "n"}:
        return "FALSE"
    return ""  # unrecognized -> not comparable


def _upper_strip(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().replace("NAN", "")


def _pull_one(conn, label: str, spec: dict) -> pd.DataFrame:
    """Pull a single view and return signup_id + its canonical columns."""
    df = get_view_data_dataframe(conn, view_id=spec["view_id"])
    out = pd.DataFrame()
    out["signup_id"] = pd.to_numeric(df["signup_id"], errors="coerce").astype("Int64")
    for src, dst in spec["columns"].items():
        out[dst] = df[src] if src in df.columns else ""
    out = out.dropna(subset=["signup_id"]).drop_duplicates(subset=["signup_id"])
    return out


def pull_insights() -> pd.DataFrame:
    """Pull all three Insights views and outer-join them on signup_id.

    Returns one canonical row per signup_id, with every compared field present
    (blank where a view had no value). Normalization matches the comparison:
    state/district/country upper-stripped, zip 5-digit, us_citizen TRUE/FALSE.
    """
    with insights_connection() as conn:
        frames = {
            label: _pull_one(conn, label, spec)
            for label, spec in INSIGHTS_VIEWS.items()
        }

    # Outer-join so a signup present in ANY view is represented.
    merged: pd.DataFrame | None = None
    for frame in frames.values():
        merged = frame if merged is None else merged.merge(
            frame, on="signup_id", how="outer"
        )
    assert merged is not None

    # Ensure every expected column exists, then normalize each.
    for col in COLUMNS:
        if col not in merged.columns:
            merged[col] = ""
    for col in UPPER_STRIP_COLUMNS:
        merged[col] = _upper_strip(merged[col])
    merged["registered_zip"] = merged["registered_zip"].map(_norm_zip)
    merged["us_citizen"] = merged["us_citizen"].map(_norm_citizen)

    merged = merged[COLUMNS].dropna(subset=["signup_id"]).drop_duplicates(
        subset=["signup_id"]
    )
    return merged.sort_values("signup_id").reset_index(drop=True)


def main() -> None:
    df = pull_insights()
    path = _out(OUTPUT_CSV)
    df.to_csv(path, index=False)
    print(f"Insights pull (3 views joined): {len(df):,} records -> {path}")
    print(f"Columns: {list(df.columns)}")
    for col in COLUMNS[1:]:
        nonblank = int((df[col].astype(str).str.strip() != "").sum())
        print(f"  {col:20s} non-blank: {nonblank:,}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
