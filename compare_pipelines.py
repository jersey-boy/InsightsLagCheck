"""
Compare the two pipelines (single-stage join).

Objective: for every signup_id present in BOTH pipelines, compare the three
shared fields and report where they differ:

    registered_state, registered_zip, federal_district

Inputs (both full, keyed on signup_id, same column names):
    insights_data.csv       - Pull A (NationBuilder Insights / Tableau view)
    nationbuilder_data.csv  - Pull B (NationBuilder V2 API, authoritative)

Because the V2 pull returns registered_address + federal_district inline for
every signup, there is no longer any two-stage (bulk index + per-record detail)
dance: this is a straight inner join.

For each field a `*_match` flag is produced:
    MATCH        - both sides have a value and they agree
    MISMATCH     - both sides have a value and they differ
    (blank)      - not comparable (one side blank)

`all_match` is TRUE only when none of the three fields mismatched.

Outputs:
    comparison.csv / .xlsx             - every matched signup_id, all 3 fields
    comparison_mismatches.csv / .xlsx  - only rows with at least one mismatch

Run (after insights_data.csv and nationbuilder_data.csv exist):
    .venv\\Scripts\\python.exe compare_pipelines.py
"""

from __future__ import annotations

import os

import pandas as pd

OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")

INSIGHTS_CSV = "insights_data.csv"
NATIONBUILDER_CSV = "nationbuilder_data.csv"

COMPARISON_CSV = "comparison.csv"
COMPARISON_XLSX = "comparison.xlsx"
MISMATCHES_CSV = "comparison_mismatches.csv"
MISMATCHES_XLSX = "comparison_mismatches.xlsx"

# Fields compared, in output order. Each becomes insights_<f> / nb_<f> / <f>_match.
COMPARE_FIELDS = ["registered_state", "registered_zip", "federal_district"]

# ZIP-like columns typed as text in the XLSX so Excel keeps leading zeros.
ZIP_COLUMNS = ["insights_registered_zip", "nb_registered_zip"]


def _out(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def _norm(value: object) -> str:
    """Upper/stripped string for comparison."""
    if value is None:
        return ""
    return str(value).strip().upper()


def _norm_zip(value: object) -> str:
    """First five digits of a ZIP as text, or '' when there are no digits."""
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:5].zfill(5) if digits else ""


def _read_side(path: str, prefix: str) -> pd.DataFrame:
    """Read a pull CSV, key on signup_id, dedupe, and prefix the compared cols."""
    df = pd.read_csv(path, dtype=str).fillna("")
    df["signup_id"] = pd.to_numeric(df["signup_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["signup_id"]).drop_duplicates(subset=["signup_id"])
    keep = ["signup_id"] + [c for c in COMPARE_FIELDS if c in df.columns]
    df = df[keep]
    rename = {c: f"{prefix}_{c}" for c in COMPARE_FIELDS if c in df.columns}
    return df.rename(columns=rename)


def _field_flag(field: str, ins: str, nb: str) -> str:
    """MATCH / MISMATCH / '' (not comparable) for one field.

    ZIP is compared on its first five digits; state and district on an
    upper/stripped basis. A blank on either side means "not comparable".
    """
    if field == "registered_zip":
        a, b = _norm_zip(ins), _norm_zip(nb)
    else:
        a, b = _norm(ins), _norm(nb)
    if a == "" or b == "":
        return ""
    return "MATCH" if a == b else "MISMATCH"


def _write_xlsx_zip_as_text(frame: pd.DataFrame, path: str) -> None:
    """Write to .xlsx with ZIP columns typed as text (keeps leading zeros)."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="comparison")
        ws = writer.sheets["comparison"]
        cols = list(frame.columns)
        for name in ZIP_COLUMNS:
            if name not in cols:
                continue
            col_idx = cols.index(name) + 1
            for row in range(2, len(frame) + 2):
                ws.cell(row=row, column=col_idx).number_format = "@"


def build_comparison() -> pd.DataFrame:
    """Inner-join the two pulls on signup_id and compute per-field match flags."""
    insights = _read_side(_out(INSIGHTS_CSV), "insights")
    nb = _read_side(_out(NATIONBUILDER_CSV), "nb")

    merged = insights.merge(nb, on="signup_id", how="inner")

    # Assemble output in a stable column order: per field, insights / nb / flag.
    out = pd.DataFrame({"signup_id": merged["signup_id"]})
    for field in COMPARE_FIELDS:
        ins_col = f"insights_{field}"
        nb_col = f"nb_{field}"
        ins_vals = merged[ins_col] if ins_col in merged else ""
        nb_vals = merged[nb_col] if nb_col in merged else ""
        out[ins_col] = ins_vals
        out[nb_col] = nb_vals
        out[f"{field}_match"] = [
            _field_flag(field, str(a), str(b)) for a, b in zip(ins_vals, nb_vals)
        ]

    match_cols = [f"{f}_match" for f in COMPARE_FIELDS]
    out["all_match"] = ~(out[match_cols] == "MISMATCH").any(axis=1)
    return out.sort_values("signup_id").reset_index(drop=True)


def main() -> None:
    comparison = build_comparison()

    match_cols = [f"{f}_match" for f in COMPARE_FIELDS]
    mismatches = comparison[(comparison[match_cols] == "MISMATCH").any(axis=1)]

    comparison.to_csv(_out(COMPARISON_CSV), index=False)
    _write_xlsx_zip_as_text(comparison, _out(COMPARISON_XLSX))
    mismatches.to_csv(_out(MISMATCHES_CSV), index=False)
    _write_xlsx_zip_as_text(mismatches, _out(MISMATCHES_XLSX))

    total = len(comparison)
    print(f"Compared {total:,} signup_ids present in both pipelines.")
    print("")
    for field in COMPARE_FIELDS:
        col = comparison[f"{field}_match"]
        m = int((col == "MATCH").sum())
        x = int((col == "MISMATCH").sum())
        nc = int((col == "").sum())
        print(f"  {field:18s} match={m:>7,}  mismatch={x:>7,}  not-comparable={nc:>7,}")
    print("")
    print(f"Rows with at least one mismatch: {len(mismatches):,}")
    print(f"Rows where all fields match:     {int(comparison['all_match'].sum()):,}")
    print("")
    print(f"Wrote: {_out(COMPARISON_CSV)} / .xlsx")
    print(f"Wrote: {_out(MISMATCHES_CSV)} / .xlsx")


if __name__ == "__main__":
    main()
