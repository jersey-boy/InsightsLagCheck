"""Extract the fields the Insights-vs-NationBuilder comparison needs.

Projects each NationBuilder V2 signup record down to:
    signup_id, registered_state, registered_zip, federal_district,
    membership_country, us_citizen

These are the authoritative values the comparison checks against the Insights
pull. Two things are sideloaded via extra_fields: `registered_address` (for
state/zip) and `custom_values` (for membership_country + us_citizen, which are
NationBuilder *custom fields*, not top-level attributes). `federal_district` is
a top-level attribute. No inclusion filter is applied here — this project
compares every signup that also appears in Insights.
"""

from __future__ import annotations

from typing import Any, Generator

from nationbuilder_v2.client import NationBuilderV2Client

# Extra fields requested from V2:
#   registered_address -> registered_state / registered_zip
#   custom_values      -> membership_country / us_citizen (both custom fields)
# federal_district is already a top-level attribute.
EXTRA_FIELDS = ["registered_address", "custom_values"]

# Column order for the projected output (matches pull_insights COLUMNS).
COLUMNS = [
    "signup_id",
    "registered_state",
    "registered_zip",
    "federal_district",
    "membership_country",
    "us_citizen",
]


def _norm_zip(value: object) -> str:
    """Normalize a ZIP to its first five digits (handles ZIP+4 and blanks)."""
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:5] if digits else ""


def _norm_citizen(value: object) -> str:
    """Canonical TRUE / FALSE / '' for us_citizen.

    NationBuilder returns this custom field as a Python bool; Insights gives
    1.0 / 0.0. Collapse both (and common string encodings) to a single form so
    the two sides compare cleanly.
    """
    if value is None:
        return ""
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    s = str(value).strip().lower()
    if s in {"1", "1.0", "true", "yes", "t", "y"}:
        return "TRUE"
    if s in {"0", "0.0", "false", "no", "f", "n"}:
        return "FALSE"
    return ""


def project_signup(record: dict[str, Any]) -> dict[str, Any]:
    """Project one V2 signup record to the fields we need.

    registered_state / registered_zip come from the sideloaded
    `registered_address` (can be null); federal_district is top-level;
    membership_country and us_citizen come from `custom_values`. Strings are
    upper/stripped and us_citizen is canonicalized for clean comparison.
    """
    attrs = record.get("attributes", {}) or {}
    addr = attrs.get("registered_address") or {}
    custom = attrs.get("custom_values") or {}
    return {
        "signup_id": record.get("id"),
        "registered_state": (addr.get("state") or "").strip().upper(),
        "registered_zip": _norm_zip(addr.get("zip")),
        "federal_district": (attrs.get("federal_district") or "").strip().upper(),
        "membership_country": (custom.get("membership_country") or "").strip().upper(),
        "us_citizen": _norm_citizen(custom.get("us_citizen")),
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
