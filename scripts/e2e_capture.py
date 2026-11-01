"""Real money path on the local Playground store, PayPal sandbox (scripts/dev.sh must be running).

A headless shopper adds a product, triggers a demo exit scenario and gets an offer. The PayPal
order is created through the store plugin exactly as the PayPal button would; the popup approval
is replaced by logging in as the sandbox buyer on PayPal's approval page. Then the store captures,
creates the paid WooCommerce order, and the script checks it in wp-admin.

    python scripts/e2e_capture.py [--scenario shipping] [--invoice]
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from nudgeycart.secrets import get_key  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--store", default="http://127.0.0.1:9411")
ap.add_argument("--scenario", default="shipping")
ap.add_argument("--product", default="lodge-10-25-inch-cast-iron-skillet")
ap.add_argument("--invoice", action="store_true")
ap.add_argument("--out", default="/tmp/nudgey-shots")
a = ap.parse_args()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)


def approve_as_sandbox_buyer(browser, order_id: str) -> None:
    email = get_key("paypal-sandbox.env", "PAYPAL_SANDBOX_BUYER_EMAIL")
    password = get_key("paypal-sandbox.env", "PAYPAL_SANDBOX_BUYER_PASSWORD")
    page = browser.new_page()
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
                "button:has-text('Complete Purchase')", "button:has-text('Continue')", "button:has-text('Pay')"):
        if page.locator(sel).first.is_visible():
            page.locator(sel).first.click()
            break
    else:
        page.screenshot(path=str(out / "approve-failed.png"))
        raise SystemExit(f"approval button not found, see {out / 'approve-failed.png'}")
    page.wait_for_timeout(4000)
    page.close()


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
    shop = ctx.new_page()
    shop.goto(f"{a.store}/product/{a.product}/")
    with shop.expect_navigation():  # the add-to-cart form posts and reloads the page
        shop.click(".single_add_to_cart_button")
    shop.goto(f"{a.store}/cart/")
    shop.wait_for_selector(".ndg-panel button.ndg-sc", timeout=30000)
    shop.click(f".ndg-panel button[data-id={a.scenario}]")
    shop.wait_for_selector(".ndg-overlay, .ndg-toast", timeout=45000)
    if not shop.query_selector(".ndg-overlay"):
        raise SystemExit("no offer shown: " + shop.inner_text(".ndg-toast"))
    offer = shop.evaluate("window.NudgeyCart.offer()")
    print(f"agent: [{offer['lever']}] {offer['headline']}: {offer['message']}")
    print(f"total {offer['original_total']} -> {offer['final_total']}")

    # What the PayPal button's createOrder does: same endpoint, same shopper session.
    rest = "window.NudgeyCartConfig.rest"
    call = ("async ([path, body]) => { const r = await fetch(" + rest + " + path, {method: 'POST', "
            "credentials: 'same-origin', headers: {'Content-Type': 'application/json', "
            "'X-WP-Nonce': window.NudgeyCartConfig.nonce}, body: JSON.stringify(body || {})}); "
            "return {status: r.status, body: await r.json()}; }")
    order = shop.evaluate(call, [f"nudges/{offer['nudge_id']}/paypal-order", {}])
    assert order["status"] == 200, order
    print("paypal order:", order["body"]["id"])
    approve_as_sandbox_buyer(browser, order["body"]["id"])

    # What onApprove does.
    cap = shop.evaluate(call, [f"nudges/{offer['nudge_id']}/capture", {"order_id": order["body"]["id"]}])
    print("capture:", cap)
    assert cap["status"] == 200 and cap["body"]["status"] == "COMPLETED", cap
    shop.goto(cap["body"]["redirect"])
    shop.wait_for_timeout(2000)
    shop.screenshot(path=str(out / "order-received.png"), full_page=True)

    # Merchant side: the order exists, is paid and carries the NudgeyCart badge.
    shop.goto(f"{a.store}/wp-login.php")
    shop.fill("#user_login", "admin")
    shop.fill("#user_pass", "password")
    shop.click("#wp-submit")
    shop.goto(f"{a.store}/wp-admin/admin.php?page=wc-orders")
    shop.wait_for_timeout(2000)
    shop.screenshot(path=str(out / "orders.png"))
    first = shop.locator("table.wp-list-table tbody tr").first
    print("orders list, newest:", " | ".join(first.inner_text().split("\n")[:6]))
    if a.invoice:
        shop.goto(f"{a.store}/wp-admin/admin.php?page=nudgeycart")
        shop.wait_for_selector(".nudgey-kpi", timeout=30000)
        inv = shop.evaluate("async () => { const r = await fetch(window.NudgeyCartAdmin.rest + 'admin/invoice', "
                            "{method: 'POST', credentials: 'same-origin', headers: {'X-WP-Nonce': "
                            "window.NudgeyCartAdmin.nonce}}); return {status: r.status, body: await r.json()}; }")
        print("invoice:", inv)
    browser.close()
print("screenshots in", out)
