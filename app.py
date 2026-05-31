"""SyncBridge — FastAPI app.

Serves three things, all with ZERO config:
  • A server-rendered HTML UI  (/, /flows/{id}, /runs, /runs/{id})
  • A REST API                 (/api/flows, /api/flows/{id}/run, /api/runs, ...)
  • An inbound webhook trigger (/hooks/{webhook_key})

Every connector runs in MOCK mode — no real network calls. APScheduler fires
scheduled flows in the background.
"""
import json
from pathlib import Path

from fastapi import Body, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from syncbridge import config, connectors, db, engine, scheduler

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "syncbridge" / "templates"))

app = FastAPI(title="SyncBridge", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE / "syncbridge" / "static")),
          name="static")


def _pretty(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return value
    return json.dumps(value, indent=2, ensure_ascii=False)


templates.env.filters["pretty"] = _pretty


@app.on_event("startup")
def _startup() -> None:
    db.init_db(seed=True)
    if config.ENABLE_SCHEDULER:
        scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.shutdown()


def _ctx(request: Request, **extra) -> dict:
    base = {
        "request": request,
        "demo_banner": config.DEMO_BANNER,
        "connectors": connectors.CONNECTORS,
    }
    base.update(extra)
    return base


def _flow_view(conn, flow) -> dict:
    d = dict(flow)
    d["trigger_config"] = json.loads(d["trigger_config"] or "{}")
    return d


# === HTML UI ==================================================================

@app.get("/", response_class=HTMLResponse)
def ui_flows(request: Request):
    with db.connect() as conn:
        st = db.stats(conn)
        flows = [dict(f) for f in db.list_flows(conn)]
        recent = [dict(r) for r in db.list_runs(conn, limit=8)]
    return templates.TemplateResponse(
        request, "flows.html",
        _ctx(request, stats=st, flows=flows, recent=recent),
    )


@app.get("/flows/{flow_id}", response_class=HTMLResponse)
def ui_flow_detail(request: Request, flow_id: int):
    with db.connect() as conn:
        flow = db.get_flow(conn, flow_id)
        if flow is None:
            return HTMLResponse("Flow not found", status_code=404)
        flow = _flow_view(conn, flow)
        steps = [dict(s) for s in db.get_steps(conn, flow_id)]
        runs = [dict(r) for r in db.list_runs(conn, flow_id=flow_id, limit=10)]
    return templates.TemplateResponse(
        request, "flow_detail.html",
        _ctx(request, flow=flow, steps=steps, runs=runs),
    )


@app.post("/flows/{flow_id}/run")
def ui_run_flow(flow_id: int):
    run_id = engine.run_flow(flow_id, trigger_source="manual")
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def ui_runs(request: Request):
    with db.connect() as conn:
        runs = [dict(r) for r in db.list_runs(conn, limit=100)]
    return templates.TemplateResponse(
        request, "runs.html", _ctx(request, runs=runs)
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def ui_run_detail(request: Request, run_id: int):
    with db.connect() as conn:
        run = db.get_run(conn, run_id)
        if run is None:
            return HTMLResponse("Run not found", status_code=404)
        run = dict(run)
        logs = [dict(l) for l in db.get_run_logs(conn, run_id)]
    return templates.TemplateResponse(
        request, "run_detail.html", _ctx(request, run=run, logs=logs)
    )


@app.post("/reset")
def ui_reset():
    db.reset_db()
    return RedirectResponse("/", status_code=303)


# === REST API =================================================================

@app.get("/api/flows")
def api_list_flows():
    with db.connect() as conn:
        out = []
        for f in db.list_flows(conn):
            d = dict(f)
            d["trigger_config"] = json.loads(d["trigger_config"] or "{}")
            d["steps"] = [
                {k: s[k] for k in ("position", "name", "connector", "action")}
                for s in db.get_steps(conn, f["id"])
            ]
            out.append(d)
    return {"flows": out}


@app.get("/api/flows/{flow_id}")
def api_get_flow(flow_id: int):
    with db.connect() as conn:
        flow = db.get_flow(conn, flow_id)
        if flow is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        d = dict(flow)
        d["trigger_config"] = json.loads(d["trigger_config"] or "{}")
        d["steps"] = [dict(s) | {"config": json.loads(s["config"])}
                      for s in db.get_steps(conn, flow_id)]
    return d


@app.post("/api/flows")
def api_create_flow(payload: dict = Body(...)):
    """Create a flow with steps. See README for the request body shape."""
    required = ("name", "trigger_type")
    if not all(payload.get(k) for k in required):
        return JSONResponse(
            {"error": f"required fields: {', '.join(required)}"}, status_code=400)
    with db.connect() as conn:
        flow_id = db.create_flow(
            conn, name=payload["name"],
            description=payload.get("description", ""),
            trigger_type=payload["trigger_type"],
            trigger_config=payload.get("trigger_config", {}),
            enabled=payload.get("enabled", True),
        )
        for i, step in enumerate(payload.get("steps", []), start=1):
            db.add_step(conn, flow_id, step.get("position", i), step["name"],
                        step["connector"], step["action"],
                        {"params": step.get("params", {})})
    return JSONResponse({"id": flow_id}, status_code=201)


@app.post("/api/flows/{flow_id}/run")
def api_run_flow(flow_id: int, payload: dict | None = Body(default=None)):
    with db.connect() as conn:
        if db.get_flow(conn, flow_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
    run_id = engine.run_flow(flow_id, trigger_source="api", payload=payload)
    return _run_json(run_id)


@app.get("/api/runs")
def api_list_runs(flow_id: int | None = None, limit: int = 50):
    with db.connect() as conn:
        runs = [dict(r) for r in db.list_runs(conn, flow_id=flow_id, limit=limit)]
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def api_get_run(run_id: int):
    return _run_json(run_id)


# === Inbound webhook ==========================================================

@app.post("/hooks/{webhook_key}")
async def inbound_webhook(webhook_key: str, request: Request):
    """Trigger a webhook-flow. The JSON body becomes the flow's trigger payload."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty/invalid body is fine
        payload = {}
    with db.connect() as conn:
        flow = db.find_flow_by_webhook_key(conn, webhook_key)
        if flow is None:
            return JSONResponse(
                {"error": f"no webhook flow for key {webhook_key!r}"}, status_code=404)
        flow_id = flow["id"]
    run_id = engine.run_flow(flow_id, trigger_source="webhook", payload=payload)
    return _run_json(run_id)


def _run_json(run_id: int):
    with db.connect() as conn:
        run = db.get_run(conn, run_id)
        if run is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        run = dict(run)
        run["trigger_payload"] = json.loads(run["trigger_payload"] or "{}")
        run["logs"] = [
            dict(l) | {"input": json.loads(l["input"]),
                       "output": json.loads(l["output"])}
            for l in db.get_run_logs(conn, run_id)
        ]
    return run
