from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "prompt",
        "full_prompt",
        "raw_prompt",
        "full_text",
        "raw_text",
        "customer_identity",
        "client_identity",
        "local_key",
        "access_token",
        "refresh_token",
        "content",
        "text",
        "excerpt",
        "question",
        "answer",
        "raw_content",
    }
)
SENSITIVE_VALUE = re.compile(
    r"(?:\bbearer\s+\S+|\b(?:sk|app|dify)[-_][A-Za-z0-9_-]{16,})",
    re.IGNORECASE,
)


def reject_sensitive(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in SENSITIVE_KEYS:
                raise ValueError("sensitive audit field is prohibited")
            reject_sensitive(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            reject_sensitive(nested)
        return
    if isinstance(value, str) and SENSITIVE_VALUE.search(value):
        raise ValueError("sensitive audit value is prohibited")
