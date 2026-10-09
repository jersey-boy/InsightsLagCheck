"""
Pull B — NationBuilder V2 API (OAuth 2.0).

Pulls EVERY signup from the NationBuilder V2 API in a single parallel pass and
writes the authoritative registered state / zip and federal_district to
`nationbuilder_data.csv`.

This replaces the old two-stage V1 approach (a slow serial index pull followed
by per-record detail GETs). V2 returns `registered_address` and
`federal_district` inline for every signup, and its page-number pagination lets
us fetch all pages concurrently, so the whole ~188k-signup nation comes back in
a few minutes with one call.

Auth: OAuth 2.0 refresh-token flow (see V2_OAUTH_SETUP.md). The client refreshes
the access token at startup and rotates the refresh token back into `.env`.

Columns written:
    signup_id, registered_state, registered_zip, federal_district

Run:
    .venv\\Scripts\\python.exe pull_nationbuilder.py
    .venv\\Scripts\\python.exe pull_nationbuilder.py --max-pages 5   (quick test)
"""

from __future__ import annotations

import argparse
import os

import pandas as pd
from dotenv import load_dotenv

from nationbuilder_v2 import NationBuilderV2Client
from nationbuilder_v2.extract import (
    COLUMNS,
    EXTRA_FIELDS,
    extract_all_signups,
    project_signup,
)

load_dotenv()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")
OUTPUT_CSV = "nationbuilder_data.csv"

# Parallel page-fetch concurrency for the full pull (V2 page-number pagination).
NB_CONCURRENCY = int(os.getenv("NB_LOOKUP_CONCURRENCY", "20"))


def _out(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a clean, de-duped DataFrame keyed by signup_id from projected rows."""
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["signup_id"] = pd.to_numeric(df["signup_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["signup_id"]).drop_duplicates(subset=["signup_id"])
    return df.sort_values("signup_id").reset_index(drop=True)


def pull_full(concurrency: int | None = None, on_refresh=None) -> pd.DataFrame:
    """Pull and project every signup from V2 (parallel). Returns a DataFrame.

    Args:
        concurrency: parallel page-fetch workers (defaults to NB_CONCURRENCY).
        on_refresh: callback passed to the V2 client to persist the rotated
            refresh token (e.g. a Secrets Manager writer in Lambda). When None,
            the client's default local-.env writer is used.
    """

    def _progress(done: int, _total: int) -> None:
        print(f"  ...{done:,} pages attempted", flush=True)

    rows = extract_all_signups(
        concurrency=concurrency or NB_CONCURRENCY,
        progress=_progress,
        on_refresh=on_refresh,
    )
    return _to_frame(rows)


def pull_sample(max_pages: int) -> pd.DataFrame:
    """Pull a small serial sample (first `max_pages` pages) for a quick test."""
    client = NationBuilderV2Client.from_env()
    try:
        rows = [
            project_signup(r)
            for r in client.iter_signups(
                extra_fields=EXTRA_FIELDS, page_size=100, max_pages=max_pages
            )
        ]
    finally:
        client.close()
    return _to_frame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull all NationBuilder V2 signups.")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Pull only the first N pages, serially (0 = full parallel pull).",
    )
    parser.add_argument(
        "--output",
        default=_out(OUTPUT_CSV),
        help="Output CSV path (default: nationbuilder_data.csv).",
    )
    args = parser.parse_args()

    if args.max_pages > 0:
        df = pull_sample(args.max_pages)
    else:
        df = pull_full()

    df.to_csv(args.output, index=False)
    populated = int((df["registered_state"] != "").sum())
    print(f"NationBuilder V2 pull: {len(df):,} signups -> {args.output}")
    print(f"  with a registered state: {populated:,}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
