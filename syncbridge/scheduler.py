"""APScheduler integration — runs scheduled flows automatically.

On startup we register one interval job per enabled flow whose trigger_type is
'schedule', using the flow's `interval_seconds` (kept short in the demo so the
dashboard shows fresh runs quickly). Each job simply calls the same
`engine.run_flow()` the UI and API use.
"""
import json

from apscheduler.schedulers.background import BackgroundScheduler

from . import db, engine

_scheduler: BackgroundScheduler | None = None


def _run_scheduled(flow_id: int) -> None:  # pragma: no cover - timing dependent
    try:
        engine.run_flow(flow_id, trigger_source="schedule")
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] flow {flow_id} errored: {exc}")


def start() -> BackgroundScheduler:
    """Start the scheduler and register all scheduled flows. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = BackgroundScheduler(daemon=True)
    with db.connect() as conn:
        flows = db.list_flows(conn)
    for f in flows:
        if f["trigger_type"] != "schedule" or not f["enabled"]:
            continue
        cfg = json.loads(f["trigger_config"] or "{}")
        interval = int(cfg.get("interval_seconds", 60))
        sched.add_job(
            _run_scheduled, "interval", seconds=interval, args=[f["id"]],
            id=f"flow-{f['id']}", replace_existing=True,
            coalesce=True, max_instances=1,
        )
    sched.start()
    _scheduler = sched
    return sched


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
