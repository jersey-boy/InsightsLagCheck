"""Extract the fields the Insights-vs-NationBuilder comparison needs.

Projects each NationBuilder V2 signup record down to:
    signup_id, registered_state, registered_zip, federal_district

These are the authoritative values the comparison checks against the Insights
pull. `registered_address` is sideloaded via
`extra_fields[signups]=registered_address`; `federal_district` is a top-level
attribute. No inclusion filter is applied here — this project compares every
signup that also appears in Insights (unlike ZipStateFed, which filters).
"""

from __future__ import annotations

from typing import Any, Generator

from nationbuilder_v2.client import NationBuilderV2Client

# Extra fields that must be requested from V2 to populate the projection. Only
# the registered address is sideloaded; federal_district is already top-level.
EXTRA_FIELDS = ["registered_address"]

# Column order for the projected output.
COLUMNS = [
    "signup_id",
    "registered_state",
    "registered_zip",
    "federal_district",
]


def _norm_zip(value: object) -> str:
    """Normalize a ZIP to its first five digits (handles ZIP+4 and blanks)."""
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:5] if digits else ""


def project_signup(record: dict[str, Any]) -> dict[str, Any]:
    """Project one V2 signup record to the fields we need.

    registered_state / registered_zip come from the sideloaded
    `registered_address` (which can be null); federal_district is a top-level
    attribute (e.g. "NY13"). Values are upper/stripped for clean comparison.
    """
    attrs = record.get("attributes", {}) or {}
    addr = attrs.get("registered_address") or {}
    return {
        "signup_id": record.get("id"),
        "registered_state": (addr.get("state") or "").strip().upper(),
        "registered_zip": _norm_zip(addr.get("zip")),
        "federal_district": (attrs.get("federal_district") or "").strip().upper(),
    }


def extract_all_signups(
    client: NationBuilderV2Client | None = None,
    concurrency: int = 20,
    progress=None,
    on_refresh=None,
) -> list[dict[str, Any]]:
    """Pull ALL signups in parallel and project them (no filtering).

    Builds a client from the environment if one is not supplied. `on_refresh`
    is passed to from_env() to persist the rotated refresh token.
    """
    own_client = client is None
    client = client or NationBuilderV2Client.from_env(on_refresh=on_refresh)
    try:
        records = client.fetch_all_signups(
            extra_fields=EXTRA_FIELDS,
            concurrency=concurrency,
            progress=progress,
        )
        return [project_signup(r) for r in records]
    finally:
        if own_client:
            client.close()


def iter_projected_signups(
    client: NationBuilderV2Client,
    page_size: int = 100,
    max_pages: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield projected rows serially (for sampling; prefer extract_all_signups
    for full pulls)."""
    for record in client.iter_signups(
        extra_fields=EXTRA_FIELDS, page_size=page_size, max_pages=max_pages
    ):
        yield project_signup(record)
