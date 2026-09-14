"""Deterministic, selection-aware replacement for the legacy raw JSON merger."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from Pool.resources.produce.sub_scripts.models_scan_and_generate import run_ingestion
from Pool.resources.produce.build_pool import _serialized, _write_if_changed, compile_pool
from sgar_mvp.src.model_selection import require_registered_models

def merge_models():
    rows = run_ingestion(None, None, "${base_url}/v1", "")
    require_registered_models(rows, complete=True)
    _write_if_changed(ROOT / "Pool/resources/json/models.json", _serialized(rows))
    return compile_pool(write=True)

if __name__ == "__main__":
    print(merge_models())
