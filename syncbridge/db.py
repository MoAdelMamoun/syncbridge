"""SQLite persistence for SyncBridge — flows, steps, runs and per-step logs.

The schema is created on first use and seeded with a few obviously-fictional
sample flows. The seed also EXECUTES those flows through the (mock) engine so
the dashboard shows real run history with genuine per-step logs immediately —
including one honestly-failed run for the success-rate KPI.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

TRIGGER_TYPES = ["manual", "schedule", "webhook"]
RUN_STATUSES = ["running", "success", "failed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS flows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    trigger_type   TEXT NOT NULL DEFAULT 'manual',
    trigger_config TEXT NOT NULL DEFAULT '{}',
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_id   INTEGER NOT NULL REFERENCES flows(id),
    position  INTEGER NOT NULL,
    name      TEXT NOT NULL,
    connector TEXT NOT NULL,
    action    TEXT NOT NULL,
    config    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_id        INTEGER NOT NULL REFERENCES flows(id),
    status         TEXT NOT NULL DEFAULT 'running',
    trigger_source TEXT NOT NULL DEFAULT 'manual',
    trigger_payload TEXT NOT NULL DEFAULT '{}',
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    duration_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS run_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    position     INTEGER NOT NULL,
    step_name    TEXT NOT NULL,
    connector    TEXT NOT NULL,
    action       TEXT NOT NULL,
    status       TEXT NOT NULL,
    input        TEXT NOT NULL DEFAULT '{}',
    output       TEXT NOT NULL DEFAULT '{}',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""


def init_db(seed: bool = True) -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        already = conn.execute("SELECT COUNT(*) AS c FROM flows").fetchone()["c"]
    if seed and not already:
        _seed()


def reset_db() -> None:
    with connect() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS run_logs;"
            "DROP TABLE IF EXISTS runs;"
            "DROP TABLE IF EXISTS steps;"
            "DROP TABLE IF EXISTS flows;"
        )
    init_db(seed=True)


# --- flows --------------------------------------------------------------------

def create_flow(conn, *, name: str, description: str, trigger_type: str,
                trigger_config: dict, enabled: bool = True) -> int:
    ts = _now()
    cur = conn.execute(
        "INSERT INTO flows (name, description, trigger_type, trigger_config, "
        "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, description, trigger_type, json.dumps(trigger_config),
         1 if enabled else 0, ts, ts),
    )
    return cur.lastrowid


def add_step(conn, flow_id: int, position: int, name: str, connector: str,
             action: str, config_: dict) -> int:
    cur = conn.execute(
        "INSERT INTO steps (flow_id, position, name, connector, action, config) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (flow_id, position, name, connector, action, json.dumps(config_)),
    )
    return cur.lastrowid


def get_flow(conn, flow_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM flows WHERE id = ?", (flow_id,)).fetchone()


def list_flows(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.*, "
        " (SELECT COUNT(*) FROM steps s WHERE s.flow_id = f.id) AS step_count, "
        " (SELECT COUNT(*) FROM runs r WHERE r.flow_id = f.id) AS run_count "
        "FROM flows f ORDER BY f.id"
    ).fetchall()


def get_steps(conn, flow_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM steps WHERE flow_id = ? ORDER BY position", (flow_id,)
    ).fetchall()


def find_flow_by_webhook_key(conn, key: str) -> sqlite3.Row | None:
    for f in conn.execute(
        "SELECT * FROM flows WHERE trigger_type = 'webhook'"
    ).fetchall():
        cfg = json.loads(f["trigger_config"] or "{}")
        if cfg.get("webhook_key") == key:
            return f
    return None


# --- runs & logs --------------------------------------------------------------

def create_run(conn, flow_id: int, trigger_source: str, payload: dict,
               started_at: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO runs (flow_id, status, trigger_source, trigger_payload, "
        "started_at) VALUES (?, 'running', ?, ?, ?)",
        (flow_id, trigger_source, json.dumps(payload), started_at or _now()),
    )
    return cur.lastrowid


def add_log(conn, run_id: int, *, position: int, step_name: str, connector: str,
            action: str, status: str, input_: dict, output: dict,
            duration_ms: int, created_at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO run_logs (run_id, position, step_name, connector, action, "
        "status, input, output, duration_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, position, step_name, connector, action, status,
         json.dumps(input_), json.dumps(output), duration_ms,
         created_at or _now()),
    )


def finalize_run(conn, run_id: int, status: str, duration_ms: int,
                 finished_at: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, duration_ms = ?, finished_at = ? WHERE id = ?",
        (status, duration_ms, finished_at or _now(), run_id),
    )


def get_run(conn, run_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT r.*, f.name AS flow_name FROM runs r "
        "JOIN flows f ON f.id = r.flow_id WHERE r.id = ?", (run_id,)
    ).fetchone()


def get_run_logs(conn, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM run_logs WHERE run_id = ? ORDER BY position", (run_id,)
    ).fetchall()


def list_runs(conn, flow_id: int | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if flow_id is not None:
        return conn.execute(
            "SELECT r.*, f.name AS flow_name FROM runs r JOIN flows f "
            "ON f.id = r.flow_id WHERE r.flow_id = ? ORDER BY r.id DESC LIMIT ?",
            (flow_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT r.*, f.name AS flow_name FROM runs r JOIN flows f "
        "ON f.id = r.flow_id ORDER BY r.id DESC LIMIT ?", (limit,)
    ).fetchall()


def stats(conn) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute("SELECT status, started_at FROM runs").fetchall()
    total = len(rows)
    success = sum(1 for r in rows if r["status"] == "success")
    runs_today = sum(1 for r in rows if r["started_at"][:10] == today)
    flows = conn.execute("SELECT COUNT(*) AS c FROM flows").fetchone()["c"]
    rate = round(100 * success / total) if total else 0
    return {
        "flows": flows,
        "total_runs": total,
        "runs_today": runs_today,
        "success_rate": rate,
    }


# --- demo seed ----------------------------------------------------------------

def _seed() -> None:
    """Create fictional flows, then run them through the mock engine so the
    dashboard has genuine run history (logs, timings, one failed run)."""
    from . import engine  # local import to avoid a cycle

    with connect() as conn:
        # 1) Welcome new signups (webhook-triggered)
        f1 = create_flow(
            conn,
            name="Welcome new signups",
            description="When a signup webhook fires: add the contact to the CRM, "
                        "email them a welcome, and ping the growth channel.",
            trigger_type="webhook",
            trigger_config={
                "webhook_key": "demo-signups",
                "sample_payload": {"name": "Acme Corp", "email": "newuser@example.com"},
            },
        )
        add_step(conn, f1, 1, "Add to CRM", "crm", "upsert_contact",
                 {"params": {"name": "{{trigger.name}}", "email": "{{trigger.email}}"}})
        add_step(conn, f1, 2, "Send welcome email", "email", "send",
                 {"params": {"to": "{{trigger.email}}",
                             "subject": "Welcome to Acme (demo)!",
                             "body": "Hi {{trigger.name}}, thanks for signing up."}})
        add_step(conn, f1, 3, "Notify #growth", "slack", "post_message",
                 {"params": {"channel": "#growth-demo",
                             "text": "New signup: {{trigger.name}} ({{trigger.email}}) — CRM id {{steps.1.contact_id}}"}})

        # 2) Daily sales digest (scheduled)
        f2 = create_flow(
            conn,
            name="Daily sales digest",
            description="On a schedule: pull yesterday's sales from an API, log them "
                        "to a spreadsheet, and post a digest to the reports channel.",
            trigger_type="schedule",
            trigger_config={"interval_seconds": 45, "sample_payload": {}},
        )
        add_step(conn, f2, 1, "Fetch sales totals", "http", "request",
                 {"params": {"method": "GET",
                             "url": "https://api.example.com/v1/sales/summary"}})
        add_step(conn, f2, 2, "Append to spreadsheet", "spreadsheet", "append_row",
                 {"params": {"sheet": "Sales Log (demo)",
                             "values": ["{{steps.1.json.window}}",
                                        "{{steps.1.json.orders}}",
                                        "{{steps.1.json.sales_total_usd}}"]}})
        add_step(conn, f2, 3, "Post digest", "slack", "post_message",
                 {"params": {"channel": "#reports-demo",
                             "text": "Sales digest — {{steps.1.json.orders}} orders, "
                                     "$ {{steps.1.json.sales_total_usd}} ({{steps.1.json.window}})"}})

        # 3) Lead follow-up (manual)
        f3 = create_flow(
            conn,
            name="Lead follow-up",
            description="Manually triggered: upsert the lead, wait a moment, then "
                        "send a personalised follow-up email.",
            trigger_type="manual",
            trigger_config={"sample_payload": {"name": "Globex Inc",
                                               "email": "lead@example.com"}},
        )
        add_step(conn, f3, 1, "Upsert lead in CRM", "crm", "upsert_contact",
                 {"params": {"name": "{{trigger.name}}", "email": "{{trigger.email}}"}})
        add_step(conn, f3, 2, "Wait 1s", "delay", "wait", {"params": {"seconds": 1}})
        add_step(conn, f3, 3, "Send follow-up", "email", "send",
                 {"params": {"to": "{{trigger.email}}",
                             "subject": "Following up, {{trigger.name}}",
                             "body": "Just checking in — would a quick demo help?"}})

    # Generate genuine run history at staggered timestamps (all today).
    now = datetime.now(timezone.utc)

    def ago(minutes: float) -> str:
        return (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    engine.run_flow(f2, trigger_source="schedule", _seed_ts=ago(180))
    engine.run_flow(f1, trigger_source="webhook",
                    payload={"name": "Globex Inc", "email": "ops@example.com"},
                    _seed_ts=ago(95))
    engine.run_flow(f3, trigger_source="manual", _seed_ts=ago(42))
    engine.run_flow(f2, trigger_source="schedule", _seed_ts=ago(20))
    # One honestly-failed run: the signup webhook fired without an email address,
    # so the "Send welcome email" step fails validation (real connector behaviour).
    engine.run_flow(f1, trigger_source="webhook",
                    payload={"name": "Initech (no email, demo)"}, _seed_ts=ago(8))
