"""Configuration for SyncBridge.

Everything has a safe default so the demo runs with ZERO config. The only
setting that changes behaviour is SYNCBRIDGE_LIVE_CONNECTORS: it stays `false`
in the demo, which forces every connector to run in MOCK mode (no real network
calls, no keys). The real-connector code path is shown but never used unless you
explicitly opt in for a full deployment.
"""
import os
from pathlib import Path

try:  # pragma: no cover - trivial
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite location. The demo seeds a throwaway file under the project.
DB_PATH = os.getenv("SYNCBRIDGE_DB_PATH", str(BASE_DIR / "syncbridge_demo.db"))

# Master switch. OFF by default → all connectors are MOCK, no live network.
LIVE_CONNECTORS = os.getenv("SYNCBRIDGE_LIVE_CONNECTORS", "false").lower() == "true"

# Whether to start APScheduler with the app (on by default so the dashboard
# shows fresh scheduled runs). Disabled automatically by the smoke test.
ENABLE_SCHEDULER = os.getenv("SYNCBRIDGE_ENABLE_SCHEDULER", "true").lower() == "true"
