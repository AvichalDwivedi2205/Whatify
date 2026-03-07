from __future__ import annotations

import hashlib
import uuid

_NAMESPACE = uuid.UUID("2f940bb3-a104-4f2f-a869-4cf23995ab6d")


def stable_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{uuid.uuid5(_NAMESPACE, seed).hex[:16]}"


def hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
