"""Small privacy helpers shared by data-minimising features.

The HMAC secret is deliberately not kept in the repository.  A keyed HMAC
lets the application recognise a repeat visitor for one narrowly defined
purpose without storing the visitor's IP address.
"""
from __future__ import annotations

import hashlib
import hmac
import os

PRIVACY_HMAC_SECRET_ENV = "EEK_PRIVACY_HMAC_SECRET"


class PrivacyConfigurationError(RuntimeError):
    """Raised when a feature that needs the server-side HMAC secret is used."""


def pseudonymize_ip(ip: str, purpose: str) -> str:
    """Return a purpose-bound, non-reversible identifier for an IP address.

    The purpose is part of the signed input so an identifier used for voting
    cannot be correlated with the one used for internal statistics.
    """
    secret = os.environ.get(PRIVACY_HMAC_SECRET_ENV)
    if not secret:
        raise PrivacyConfigurationError(
            f"{PRIVACY_HMAC_SECRET_ENV} must be configured before using IP-based features."
        )
    message = f"{purpose}:{ip.strip()}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
