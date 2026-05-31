"""Connectors — the integrations a flow step can call.

In the demo, EVERY connector runs in MOCK mode: it validates its inputs and
returns a believable, clearly-fake response WITHOUT touching the network. The
shape of each `live_*` counterpart is shown in comments / stubs so the
real-integration pattern is obvious, but `config.LIVE_CONNECTORS` is False in
the demo so the mock path is always taken.

Each action is a function `(params: dict) -> dict`. Raising any exception marks
the step (and run) as failed — connectors validate required fields exactly as a
real one would, which is how the demo produces an honest "failed run" example.
"""
import hashlib

from . import config


def _hid(prefix: str, *parts: str) -> str:
    """Deterministic short id from inputs, e.g. CON-9f3a2c. Looks real, isn't."""
    seed = "|".join(str(p) for p in parts) or prefix
    digest = hashlib.sha1(seed.encode()).hexdigest()[:6]
    return f"{prefix}-{digest}"


def _require(params: dict, *fields: str) -> None:
    def _empty(v) -> bool:
        return v is None or (isinstance(v, str) and not v.strip()) or v == []

    missing = [f for f in fields if _empty(params.get(f))]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


# --- connector actions (all MOCK in the demo) --------------------------------

def http_request(params: dict) -> dict:
    _require(params, "url")
    method = (params.get("method") or "GET").upper()
    # MOCK: a canned, obviously-fictional API response. A live build would do
    #   resp = requests.request(method, params["url"], json=params.get("json"))
    # only when config.LIVE_CONNECTORS is True.
    return {
        "mock": True,
        "request": {"method": method, "url": params["url"]},
        "status_code": 200,
        "json": {
            "sales_total_usd": 4820.50,
            "orders": 37,
            "window": "last 24h (demo data)",
        },
    }


def email_send(params: dict) -> dict:
    _require(params, "to", "subject")
    # MOCK: pretend we handed this to an email provider.
    return {
        "mock": True,
        "provider": "MOCK email",
        "message_id": _hid("MSG", params["to"], params["subject"]),
        "accepted": [params["to"]],
        "subject": params["subject"],
    }


def slack_post_message(params: dict) -> dict:
    _require(params, "channel", "text")
    # MOCK: pretend we posted to a Slack-style workspace.
    return {
        "mock": True,
        "provider": "MOCK Slack",
        "ok": True,
        "channel": params["channel"],
        "ts": "1700000000.000100",
        "text": params["text"],
    }


def crm_upsert_contact(params: dict) -> dict:
    _require(params, "email")
    # MOCK: pretend we upserted a CRM contact.
    return {
        "mock": True,
        "provider": "MOCK CRM",
        "contact_id": _hid("CON", params["email"]),
        "created": True,
        "name": params.get("name", ""),
        "email": params["email"],
    }


def spreadsheet_append_row(params: dict) -> dict:
    _require(params, "sheet", "values")
    values = params["values"]
    if not isinstance(values, list):
        values = [values]
    # MOCK: pretend we appended a row to a spreadsheet.
    return {
        "mock": True,
        "provider": "MOCK Sheets",
        "sheet": params["sheet"],
        "row_index": 124,
        "appended": values,
    }


def delay_wait(params: dict) -> dict:
    seconds = float(params.get("seconds", 1))
    seconds = max(0.0, min(seconds, 1.0))  # capped so the demo stays snappy
    import time

    time.sleep(seconds)
    return {"mock": True, "provider": "local delay", "waited_seconds": seconds}


# --- registry -----------------------------------------------------------------

CONNECTORS: dict[str, dict] = {
    "http": {
        "label": "HTTP Request",
        "emoji": "🌐",
        "description": "Call an external HTTP/REST endpoint.",
        "actions": {"request": http_request},
    },
    "email": {
        "label": "Email",
        "emoji": "✉️",
        "description": "Send a transactional email.",
        "actions": {"send": email_send},
    },
    "slack": {
        "label": "Slack-style message",
        "emoji": "💬",
        "description": "Post a message to a team chat channel.",
        "actions": {"post_message": slack_post_message},
    },
    "crm": {
        "label": "CRM Contact",
        "emoji": "👤",
        "description": "Create or update a contact in a CRM.",
        "actions": {"upsert_contact": crm_upsert_contact},
    },
    "spreadsheet": {
        "label": "Spreadsheet Row",
        "emoji": "📊",
        "description": "Append a row to a spreadsheet.",
        "actions": {"append_row": spreadsheet_append_row},
    },
    "delay": {
        "label": "Delay",
        "emoji": "⏱️",
        "description": "Pause the flow for a number of seconds.",
        "actions": {"wait": delay_wait},
    },
}


def connector_label(connector: str) -> str:
    return CONNECTORS.get(connector, {}).get("label", connector)


def connector_emoji(connector: str) -> str:
    return CONNECTORS.get(connector, {}).get("emoji", "🔌")


def dispatch(connector: str, action: str, params: dict) -> dict:
    """Run a connector action. In the demo this is always the MOCK path."""
    spec = CONNECTORS.get(connector)
    if spec is None:
        raise ValueError(f"unknown connector: {connector}")
    fn = spec["actions"].get(action)
    if fn is None:
        raise ValueError(f"unknown action {action!r} for connector {connector!r}")
    # In a full build, a live connector would branch here on config.LIVE_CONNECTORS.
    _ = config.LIVE_CONNECTORS  # always False in the demo → mock path below
    return fn(params)
