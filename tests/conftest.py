import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Stub the external llm-metadata-harvester package so unit tests can import
# the worker task without the upstream dependency being installed. If the real
# package is present, it is left untouched.
async def _metadata_harvest(*, model_name: str, url: str, api_key: str):
    return {"mock": "metadata"}


harvester = types.ModuleType("llm_metadata_harvester")
harvester_ops = types.ModuleType("llm_metadata_harvester.harvester_operations")
harvester_ops.metadata_harvest = _metadata_harvest
harvester.harvester_operations = harvester_ops

sys.modules.setdefault("llm_metadata_harvester", harvester)
sys.modules.setdefault("llm_metadata_harvester.harvester_operations", harvester_ops)
