import json
from pathlib import Path
from typing import Dict, Any, List

TRACE_PATH = Path(__file__).resolve().parent / "traces.json"


def read_traces() -> List[Dict[str, Any]]:
    if not TRACE_PATH.exists():
        return []
    try:
        return json.loads(TRACE_PATH.read_text())
    except json.JSONDecodeError:
        return []


def save_trace(trace: Dict[str, Any]) -> None:
    traces = read_traces()
    traces.insert(0, trace)
    TRACE_PATH.write_text(json.dumps(traces[:50], indent=2))


def clear_traces() -> None:
    TRACE_PATH.write_text("[]")
