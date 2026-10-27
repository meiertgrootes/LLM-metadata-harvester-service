import asyncio
import json
import os
from pathlib import Path
from typing import Any


async def metadata_harvest(
    *, model_name: str, url: str, api_key: str
) -> dict[str, Any]:
    """Return stable metadata and record calls without retaining the API key."""
    await asyncio.sleep(0)
    record = {
        "model": model_name,
        "url": url,
        "api_key_supplied": bool(api_key),
    }
    state_file = Path(os.environ.get("HARVESTER_STUB_STATE", "/test-state/calls.jsonl"))
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")

    print(f"deterministic harvester stub processed {url}")
    if "stub-failure" in url:
        raise RuntimeError("deterministic stub failure")
    return {"source": "integration-stub", "model": model_name, "url": url}
