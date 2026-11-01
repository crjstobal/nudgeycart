"""Headless walk through the local Playground store (scripts/dev.sh must be running).

Shopper: add a product, open the cart, simulate each kind of exit from the demo panel and
screenshot what the agent decided. Merchant: log into wp-admin and screenshot the dashboard.

    python scripts/e2e_playground.py [--store http://127.0.0.1:9411] [--out /tmp/nudgey-shots]
"""
import argparse
import json
import pathlib

from playwright.sync_api import sync_playwright

p = argparse.ArgumentParser()
p.add_argument("--store", default="http://127.0.0.1:9411")
p.add_argument("--out", default="/tmp/nudgey-shots")
p.add_argument("--scenarios", default="compare,shipping,coupon,trust,browse")
p.add_argument("--product", default="lodge-10-25-inch-cast-iron-skillet")
a = p.parse_args()
out = pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
    page = ctx.new_page()
    page.on("console", lambda m: m.type in ("error", "warning") and print("  console:", m.text[:200]))

    page.goto(f"{a.store}/product/{a.product}/")
    with page.expect_navigation():  # the add-to-cart form posts and reloads the page
        page.click("button[name=add-to-cart], .single_add_to_cart_button")
    page.goto(f"{a.store}/cart/")
    page.wait_for_selector(".ndg-panel button.ndg-sc", timeout=30000)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out / "cart.png"))

    results = {}
    for sc in a.scenarios.split(","):
        page.click(f".ndg-panel button[data-id={sc}]")
        page.wait_for_selector(".ndg-overlay, .ndg-toast", timeout=45000)
        page.wait_for_timeout(2500)  # let the PayPal buttons render
        page.screenshot(path=str(out / f"scenario-{sc}.png"))
        if page.query_selector(".ndg-overlay"):
            results[sc] = {"shown": True, "badge": page.inner_text(".ndg-badge"), "headline": page.inner_text(".ndg-h"),
                           "message": page.inner_text(".ndg-p"), "total": page.inner_text(".ndg-total"),
                           "paypal_buttons": len(page.query_selector_all(".ndg-btn iframe"))}
            page.click(".ndg-no")
        else:
            results[sc] = {"shown": False, "toast": page.inner_text(".ndg-toast")}
            page.evaluate("document.querySelectorAll('.ndg-toast').forEach(t => t.remove())")
        print(sc, json.dumps(results[sc], ensure_ascii=False))

    # Merchant side.
    page.goto(f"{a.store}/wp-login.php")
    page.fill("#user_login", "admin")
    page.fill("#user_pass", "password")
    page.click("#wp-submit")
    page.goto(f"{a.store}/wp-admin/admin.php?page=nudgeycart")
    page.wait_for_selector(".nudgey-kpi", timeout=30000)
    page.wait_for_timeout(3000)
    page.screenshot(path=str(out / "dashboard.png"), full_page=True)
    page.click(".ag-row[row-index='0']")
    page.wait_for_timeout(800)
    page.screenshot(path=str(out / "dashboard-drawer.png"))
    browser.close()
print("screenshots in", out)
