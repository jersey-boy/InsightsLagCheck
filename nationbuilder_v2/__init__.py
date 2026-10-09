"""NationBuilder V2 API client (OAuth 2.0).

Vendored from the ZipStateFed project. Provides a thin OAuth 2.0 client over the
NationBuilder V2 REST API and a parallel full-nation signup pull, used here to
pull the authoritative registered state / zip / federal_district for every
signup so the InsightsLagCheck comparison can run against V2 instead of V1.

See V2_OAUTH_SETUP.md for the OAuth flow and the confirmed signups schema.
"""

from nationbuilder_v2.client import NationBuilderV2Client
from nationbuilder_v2.exceptions import NationBuilderV2Error

__all__ = ["NationBuilderV2Client", "NationBuilderV2Error"]
