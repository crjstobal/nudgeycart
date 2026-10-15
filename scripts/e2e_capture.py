"""Server-side money test, no browser: open a nudge, let the agent offer, pay it with a PayPal
sandbox test card, then check the WooCommerce order and the success fee.

Usage (stack running): python scripts/e2e_capture.py [--reason price] [--invoice]
The buyer-approval step that happens in the PayPal popup is replaced by a direct card
payment source, which only works in the sandbox.
"""
import argparse
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from nudgeycart import db, service  # noqa: E402
from nudgeycart.integrations import paypal  # noqa: E402

API = "http://localhost:8797"


def approve_as_sandbox_buyer(order_id: str) -> None:
    from playwright.sync_api import sync_playwright
    from nudgeycart.secrets import get_key

    email = get_key("paypal-sandbox.env", "PAYPAL_SANDBOX_BUYER_EMAIL")
    password = get_key("paypal-sandbox.env", "PAYPAL_SANDBOX_BUYER_PASSWORD")
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        for attempt in range(4):  # the sandbox often answers "we're having trouble" at first
            page.goto(f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}")
            try:
                page.wait_for_selector("#email", timeout=15000)
                break
            except Exception:
                page.wait_for_timeout(3000 * (attempt + 1))
        page.fill("#email", email)
        if page.locator("#btnNext").is_visible():
            page.click("#btnNext")
        page.fill("#password", password)
        page.click("#btnLogin")
        # PayPal pages poll forever, so "networkidle" never comes: wait for the pay button instead.
        try:
            page.wait_for_selector("#payment-submit-btn, button:has-text('Complete Purchase'), "
                                   "button:has-text('Continue'), button:has-text('Pay')", timeout=40000)
        except Exception:
            pass
        for sel in ("#payment-submit-btn", "button[data-id=payment-submit-btn]",
                    "button:has-text('Complete Purchase')", "button:has-text('Continue')",
                    "button:has-text('Pay')"):
            if page.locator(sel).first.is_visible():
                page.locator(sel).first.click()
                break
        else:
            page.screenshot(path="/tmp/nudgey-approve.png")
            raise SystemExit("approval button not found, see /tmp/nudgey-approve.png")
        page.wait_for_timeout(4000)
        b.close()
    status = paypal.get_order(order_id)["status"]
    print("order status after buyer approval:", status)

ap = argparse.ArgumentParser()
ap.add_argument("--reason", default="price")
ap.add_argument("--product", type=int, default=11)
ap.add_argument("--qty", type=int, default=1)
ap.add_argument("--invoice", action="store_true")
a = ap.parse_args()

with db.conn() as c:
    m = db.row(c.execute("SELECT * FROM merchants LIMIT 1").fetchone())
key = m["public_key"]
vid = f"e2e-{uuid.uuid4().hex[:8]}"

n = httpx.post(f"{API}/v1/nudges", json={
    "key": key, "visitor_id": vid, "session_id": vid, "trigger": "exit_intent", "force": "treatment",
    "signals": {"seconds_on_site": 120, "exit_intent": 1, "page": "checkout"},
    "items": [{"product_id": a.product, "qty": a.qty}], "shipping": 7.99}).json()
offer = httpx.post(f"{API}/v1/nudges/{n['nudge_id']}/reply", json={"key": key, "reason": a.reason},
                   timeout=60).json()
print(f"agent: [{offer['lever']}] {offer['message']}")
print(f"total {offer['original_total']} -> {offer['final_total']} (discount {offer['discount']})")

# Same order the widget creates. The PayPal popup approval is replaced by logging in as the
# sandbox buyer on the approval page, in a headless browser.
order = httpx.post(f"{API}/v1/nudges/{n['nudge_id']}/paypal-order", json={"key": key}, timeout=60).json()
print("paypal order:", order["id"])
approve_as_sandbox_buyer(order["id"])
nud = db.get("nudges", n["nudge_id"])
res = service.capture(m, nud["id"], order["id"])
print("capture:", res)
nud = db.get("nudges", nud["id"])
print(f"store order #{nud['store_order_id']} paid {nud['paid_total']} fee {nud['fee_amount']} error={nud['error']}")

if a.invoice:
    print("invoice:", service.invoice_fees(m))
