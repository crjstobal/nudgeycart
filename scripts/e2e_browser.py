"""Headless storefront test: add to cart, trigger the nudge, talk to the agent, see PayPal buttons.

Usage: python scripts/e2e_browser.py [--store http://localhost:8090] [--out /tmp/nudgey-e2e]
Requires the stack from scripts/dev.sh to be running.
"""
import argparse
import pathlib

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--store", default="http://localhost:8090")
ap.add_argument("--out", default="/tmp/nudgey-e2e")
ap.add_argument("--reason", default="I'd rather pay later")
ap.add_argument("--mobile", action="store_true")
a = ap.parse_args()
out = pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(**(p.devices["iPhone 13"] if a.mobile else {"viewport": {"width": 1280, "height": 860}}))
    page = ctx.new_page()
    page.on("console", lambda m: print("  [console]", m.text) if "NudgeyCart" in m.text or m.type == "error" else None)

    page.goto(f"{a.store}/product/aurora-wireless-headphones/")
    page.click("button[name=add-to-cart]")
    page.wait_for_load_state("networkidle")
    page.goto(f"{a.store}/cart/?nudgey_force=treatment&nudgey_reset=1")
    page.wait_for_function("window.NudgeyCart !== undefined")
    page.screenshot(path=out / "1-cart.png")

    page.evaluate("window.NudgeyCart.trigger()")
    page.wait_for_selector(".ndg-overlay .ndg-chip", timeout=20000)
    page.screenshot(path=out / "2-nudge.png")
    print("nudge shown:", page.inner_text(".ndg-bubble"))

    page.click(f".ndg-chip:has-text(\"{a.reason}\")")
    page.wait_for_selector(".ndg-offer", timeout=60000)
    print("agent said:", page.locator(".ndg-bubble").last.inner_text())
    print("offer:", " | ".join(page.locator(".ndg-offer").inner_text().split("\n")[:8]))
    page.wait_for_selector(".ndg-pp iframe", timeout=30000)
    page.wait_for_timeout(2500)
    page.screenshot(path=out / "3-offer.png")
    print("paypal buttons:", page.locator(".ndg-pp iframe").count())
    browser.close()
print("screenshots in", out)
