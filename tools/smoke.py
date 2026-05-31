"""End-to-end smoke check of the SyncBridge engine (no web server, no network).

Seeds the demo, runs a flow end-to-end through the mock connectors, and asserts
the run + per-step logs were recorded with resolved field mapping. Also checks
the failed-run path and the webhook-key lookup. Uses a throwaway temp database
and the scheduler stays off.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SYNCBRIDGE_DB_PATH"] = _tmp.name
os.environ["SYNCBRIDGE_ENABLE_SCHEDULER"] = "false"

from syncbridge import db, engine  # noqa: E402


def main():
    db.init_db(seed=True)

    with db.connect() as conn:
        flows = db.list_flows(conn)
        print("seeded flows:")
        for f in flows:
            print(f"  #{f['id']} {f['name']} [{f['trigger_type']}] "
                  f"steps={f['step_count']} runs={f['run_count']}")
        st = db.stats(conn)
        print("stats:", st)

        # Find the manual "Lead follow-up" flow and run it fresh.
        manual = next(f for f in flows if f["trigger_type"] == "manual")

    run_id = engine.run_flow(manual["id"], trigger_source="manual")
    print("\nfresh manual run:", run_id)

    with db.connect() as conn:
        run = db.get_run(conn, run_id)
        logs = db.get_run_logs(conn, run_id)
        assert run["status"] == "success", "manual run should succeed"
        assert len(logs) == 3, f"expected 3 step logs, got {len(logs)}"
        # Field mapping: the email step's resolved input must carry the trigger email.
        email_log = next(l for l in logs if l["connector"] == "email")
        resolved_in = json.loads(email_log["input"])
        assert resolved_in["to"] == "lead@example.com", "template not resolved"
        out = json.loads(email_log["output"])
        assert out["mock"] is True and out["accepted"] == ["lead@example.com"]
        print("  steps:", [(l["position"], l["connector"], l["status"]) for l in logs])

        # Webhook lookup works.
        wf = db.find_flow_by_webhook_key(conn, "demo-signups")
        assert wf is not None, "webhook flow not found by key"

    # Failed-run path: trigger the signup webhook flow with no email. The very
    # first step (CRM upsert) requires an email, so the run fails there and the
    # remaining steps never execute — exactly how a real engine behaves.
    fail_id = engine.run_flow(wf["id"], trigger_source="webhook",
                              payload={"name": "Initech (demo)"})
    with db.connect() as conn:
        frun = db.get_run(conn, fail_id)
        flogs = db.get_run_logs(conn, fail_id)
        assert frun["status"] == "failed", "missing email should fail the run"
        statuses = [l["status"] for l in flogs]
        assert statuses == ["failed"], f"unexpected step path: {statuses}"
        assert "missing required field" in json.loads(flogs[0]["output"])["error"]
        print("\nfailed run path:", statuses, "→", frun["status"],
              "(", json.loads(flogs[0]["output"])["error"], ")")

    print("\nOK: engine + persistence + field mapping healthy")
    Path(_tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
