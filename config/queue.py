import hashlib
import json
from collections.abc import Mapping
from typing import Any


def queue_name(database: Mapping[str, Any]) -> str:
    identity = {key: str(database.get(key, "")) for key in ("HOST", "PORT", "NAME")}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return "premshop_" + digest
