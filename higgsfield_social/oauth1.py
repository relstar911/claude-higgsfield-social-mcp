"""Minimal OAuth 1.0a (HMAC-SHA1) request signer.

Needed for X/Twitter v1.1 ``media/upload`` (and v2 endpoints under user-context
OAuth 1.0a). Kept dependency-free and small.

Signature-base rules implemented here:
  * For ``application/x-www-form-urlencoded`` bodies and query params, those
    params ARE included in the signature base.
  * For ``multipart/form-data`` and raw/JSON bodies, the body is NOT included;
    only the oauth_* params (and any query params) are signed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse
from typing import Any


def _quote(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="~")


def build_authorization_header(
    *,
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    signed_params: dict[str, Any] | None = None,
) -> str:
    """Return the value for the ``Authorization`` header.

    ``signed_params`` are the request params (query and/or form-urlencoded body)
    that must participate in the signature. Omit them for multipart/JSON bodies.
    """
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    all_params = {**(signed_params or {}), **oauth_params}
    encoded = sorted(
        (_quote(k), _quote(v)) for k, v in all_params.items()
    )
    param_string = "&".join(f"{k}={v}" for k, v in encoded)

    base_string = "&".join([method.upper(), _quote(url), _quote(param_string)])
    signing_key = f"{_quote(consumer_secret)}&{_quote(token_secret)}"
    digest = hmac.new(
        signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1
    ).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("utf-8")

    header = "OAuth " + ", ".join(
        f'{_quote(k)}="{_quote(v)}"' for k, v in sorted(oauth_params.items())
    )
    return header
