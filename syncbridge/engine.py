"""The flow execution engine.

`run_flow()` executes every step of a flow in order through the connectors,
resolving each step's params from a context built out of the trigger payload and
previous steps' outputs ({{trigger.x}} and {{steps.N.field}} templates). It
records a `run` plus one `run_log` per step (input, output, status, timing). A
failing step marks the run failed and stops execution.
"""
import json
import re
import time
from datetime import datetime, timezone

from . import connectors, db

_TOKEN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _lookup(context: dict, path: str):
    """Resolve a dotted path against the context. Missing → None so that a step
    referencing an absent field fails validation just like a real connector."""
    cur = context
    for part in path.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            elif part.isdigit() and int(part) in cur:
                cur = cur[int(part)]
            else:
                return None
        else:
            return None
    return cur


def resolve(value, context: dict):
    """Recursively resolve {{...}} templates inside strings/lists/dicts."""
    if isinstance(value, str):
        # If the whole string is a single token, preserve the resolved type
        # (including None when the referenced field is missing).
        m = _TOKEN.fullmatch(value.strip())
        if m:
            return _lookup(context, m.group(1).strip())
        return _TOKEN.sub(
            lambda mm: "" if _lookup(context, mm.group(1).strip()) is None
            else str(_lookup(context, mm.group(1).strip())),
            value,
        )
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, context) for k, v in value.items()}
    return value


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def run_flow(flow_id: int, trigger_source: str = "manual",
             payload: dict | None = None, _seed_ts: str | None = None) -> int:
    """Execute a flow end-to-end and return the new run id."""
    with db.connect() as conn:
        flow = db.get_flow(conn, flow_id)
        if flow is None:
            raise ValueError(f"no flow with id {flow_id}")
        steps = db.get_steps(conn, flow_id)
        trigger_cfg = json.loads(flow["trigger_config"] or "{}")
        if payload is None:
            payload = trigger_cfg.get("sample_payload", {})
        run_id = db.create_run(conn, flow_id, trigger_source, payload,
                               started_at=_seed_ts)

    context: dict = {"trigger": payload, "steps": {}}
    overall = "success"
    run_start = time.perf_counter()

    for step in steps:
        cfg = json.loads(step["config"] or "{}")
        resolved_params = resolve(cfg.get("params", {}), context)
        step_start = time.perf_counter()
        try:
            output = connectors.dispatch(step["connector"], step["action"],
                                         resolved_params)
            status = "success"
        except Exception as exc:  # noqa: BLE001 - any failure fails the step
            output = {"error": str(exc), "error_type": type(exc).__name__}
            status = "failed"
        duration = _ms(step_start)

        context["steps"][step["position"]] = output if status == "success" else {}

        with db.connect() as conn:
            db.add_log(conn, run_id, position=step["position"],
                       step_name=step["name"], connector=step["connector"],
                       action=step["action"], status=status,
                       input_=resolved_params, output=output,
                       duration_ms=duration, created_at=_seed_ts)

        if status == "failed":
            overall = "failed"
            break

    duration_ms = _ms(run_start)
    finished = _seed_ts
    if finished is None:
        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        db.finalize_run(conn, run_id, overall, duration_ms, finished_at=finished)
    return run_id
