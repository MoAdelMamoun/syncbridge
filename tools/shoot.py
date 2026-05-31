"""Capture real screenshots of the running SyncBridge app via Playwright.

The FastAPI app must already be running on the given base URL:
    uvicorn app:app --port 8775
    python tools/shoot.py http://localhost:8775
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def main(base: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = base.rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000},
                                device_scale_factor=2)

        # 1) Flows list (home) — KPIs, flows table, recent runs, connectors.
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_selector("table")
        time.sleep(0.8)
        page.screenshot(path=str(OUT / "flows.png"), full_page=True)
        print("saved flows.png")

        # 2) Flow detail — the webhook signup flow with its steps + field mapping.
        page.goto(base + "/flows/1", wait_until="networkidle")
        page.wait_for_selector(".steps")
        time.sleep(0.6)
        page.screenshot(path=str(OUT / "flow_detail.png"), full_page=True)
        print("saved flow_detail.png")

        # 3) Run logs — open the most recent successful run from the runs list.
        page.goto(base + "/runs", wait_until="networkidle")
        page.wait_for_selector("table tbody tr")
        # Click the first run whose status pill says "success".
        rows = page.locator("table tbody tr")
        clicked = False
        for i in range(rows.count()):
            if "success" in rows.nth(i).inner_text().lower():
                rows.nth(i).click()
                clicked = True
                break
        if not clicked:
            rows.first.click()
        page.wait_for_selector(".log")
        time.sleep(0.6)
        page.screenshot(path=str(OUT / "run_logs.png"), full_page=True)
        print("saved run_logs.png")

        browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8775")
