# NudgeyCart

AI checkout closer for WooCommerce, powered by PayPal.

When a shopper with a full cart is about to leave, NudgeyCart does **not** ask them why. It reads
what they did (opened the coupon box, lingered on the shipping cost, copied the product name and
switched tabs, read the returns policy...), lets Claude infer the most likely reason, and shows
**one** offer, or none at all:

| What the agent reads | What it offers | Cost to the merchant |
|---|---|---|
| Comparing prices elsewhere | **Price match** against the real, in-stock competitor price found by [Channel3](https://trychannel3.com) | the gap, only if the margin floor holds |
| Shipping cost shock, cart under the free-shipping minimum | **Cart top-up**: one matching add-on that reaches the minimum | nothing: the order grows |
| Shipping cost shock, otherwise | **Free shipping** | the shipping fee |
| Price sensitive | **Discount**, the smallest that plausibly closes | capped by policy and margin |
| Trust doubts, checkout friction | **Reassurance**: PayPal Buyer Protection, pay in one tap, Pay in 4 | nothing |
| Just browsing, unclear | **Nothing.** Not interrupting is a valid decision | nothing |

The shopper pays with PayPal (PayPal, Pay Later, Venmo, card) right inside the dialog, and the
order lands in WooCommerce as paid. Merchants pay nothing up front: NudgeyCart charges a success
fee on the sales it closed, billed through **PayPal Invoicing**, and proves its value against a
built-in holdout group.

> Built for the PayPal AI Hackathon (2026). Everything runs against the PayPal sandbox: no real money moves.

## Try it

**Live demo: https://nudgeycart-api.onrender.com/demo**

One click boots a WooCommerce store in your browser with WordPress Playground, NudgeyCart
installed and 30 days of illustrative history. Nothing to install; the store is yours alone and
disappears when you close the tab.

1. Add a product to the cart and open the cart.
2. Use the **NudgeyCart demo** panel to simulate why a shopper leaves (or behave like one: copy
   the product name, switch tabs, open the coupon box, then move the mouse to the tabs).
3. Pay the offer with the PayPal sandbox test buyer below (click **Pay with PayPal** and log in).
4. In wp-admin (admin / password): **WooCommerce > NudgeyCart** for the dashboard, and
   **WooCommerce > Orders** for the saved sale.

### Test account

A PayPal **sandbox** buyer for judges and testers. It only exists inside PayPal's test environment
and holds play money:

| | |
|---|---|
| Email | `sb-boqne53858588@personal.example.com` |
| Password | `k297?Du9` |

The same login opens the success-fee invoice that the dashboard's **Invoice fees via PayPal**
button sends. wp-admin of the demo store: `admin` / `password`.

## How it works

```
Shopper's browser               WooCommerce store (plugin)              NudgeyCart API            Services
-----------------               --------------------------              --------------            --------
widget.js collects behaviour -> /wp-json/nudgeycart/v1/nudge:
signals, detects exit intent    prices + unit costs from the catalogue -> /v1/nudges:            Channel3 (cached
                                                                          holdout assignment,      competitor prices)
                                                                          Claude decides,       -> Claude
                                                                          policy.clamp() caps
dialog + PayPal buttons      <- offer, bound to the shopper's session  <-
createOrder / onApprove      -> paypal-order / capture                 -> Orders v2            -> PayPal
                                creates the paid WooCommerce order
wp-admin dashboard (AG Grid, <- /admin/summary                         <- /v1/merchant/*       -> PayPal Invoicing
AG Charts)                                                                success-fee invoices
```

Design rules:

- **The model proposes, the policy disposes.** `backend/nudgeycart/core/policy.py` is pure Python
  with no vendor SDKs. Whatever Claude says, every concession is clamped to the merchant's margin
  floor (NudgeyCart's fee included). A price match is all or nothing: never a partial "match".
- **Prices and costs come from the store**, never from the browser. The widget only talks to its
  own store; the store plugin calls the API server-side with the merchant's secret key. Unit cost
  lives in a WooCommerce product field and never reaches the storefront.
- **No questionnaire, and silence is allowed.** Claude must state its confidence; below 0.45 it
  does not interrupt. Every decision, including the ones not shown, is logged with its evidence.
- **Honest by construction.** No fake urgency or scarcity, no hinting at the tracking, and money
  offers carry a personalised-pricing disclosure (NY Algorithmic Pricing Disclosure Act, EU Omnibus).
- **Anti-abuse.** A visitor (or IP) who already got a money offer in the last 30 days only gets
  free levers, so faking an exit does not farm discounts.
- **Fast enough for an exit.** Claude Haiku 5.5 decides in a few seconds; competitor prices
  are looked up when the item lands in the cart, not when the shopper leaves.

## How PayPal, AI and the sponsor tools are used

| Tool | Where | What it does here |
|---|---|---|
| **PayPal Orders v2** + JavaScript SDK | `backend/nudgeycart/integrations/paypal.py`, `assets/widget.js` | The offer dialog renders the PayPal, Pay Later, Venmo and card buttons. The order is created server side with the agent's price breakdown and captured server side; the plugin then creates the paid WooCommerce order. |
| **PayPal Pay Later** | `assets/widget.js` | Pay Later button and "Pay in 4" messaging inside every offer. |
| **PayPal Invoicing v2** | `backend/nudgeycart/integrations/paypal.py`, `service.py` | The 5% success fee on closed sales is sent to the merchant as a PayPal invoice from the dashboard. |
| **PayPal webhooks** | `backend/nudgeycart/app.py` | Verified refund events adjust the saved-sales figures. |
| **Claude Haiku 5.5** (Anthropic API) | `backend/nudgeycart/agent.py` | The decision agent: reads the cart and behaviour signals, diagnoses why the shopper is leaving, picks one lever among those the policy allows (or none) and writes the offer and its rationale. Structured output, with a rules fallback. |
| **Channel3** | `backend/nudgeycart/integrations/channel3.py` | Product search for real, in-stock competitor prices of the exact product. Powers the price match lever. |
| **AG Grid + AG Charts** | `wordpress-plugin/nudgeycart/assets/admin.js` | Merchant dashboard in wp-admin: KPIs, charts, decision log with detail drawer. |
| **Render** | `render.yaml` | Hosts the API, which also serves the plugin zip and the Playground blueprint. |
| **WordPress Playground** | `playground/blueprint.json` | Boots the demo store in the visitor's browser. |

In production the success fee would be captured inside the shopper's payment with PayPal multiparty
`platform_fees`; that needs partner approval, so this prototype bills it with Invoicing.

## Repository layout

| Path | What |
|---|---|
| `wordpress-plugin/nudgeycart/` | WooCommerce plugin: widget, REST routes, paid orders, cost field, wp-admin dashboard |
| `wordpress-plugin/nudgeycart/assets/widget.js` | Storefront widget: behaviour signals, exit triggers, offer dialog, PayPal buttons |
| `wordpress-plugin/nudgeycart/assets/admin.js` | Merchant dashboard (AG Grid Community + AG Charts Community) |
| `backend/nudgeycart/core/policy.py` | Guardrails: margin floor, levers, price match, holdout, clamping |
| `backend/nudgeycart/agent.py` | Claude decision (structured output) with a rules fallback |
| `backend/nudgeycart/service.py` | Use cases: nudge, pay, capture, dashboard, invoicing |
| `backend/nudgeycart/integrations/` | PayPal (Orders v2, Invoicing v2, webhooks) and Channel3 |
| `backend/nudgeycart/demo.py` | Self-service demo merchants for Playground stores |
| `playground/blueprint.json` | WordPress Playground recipe: WooCommerce, the plugin, demo products |

## Run it locally (macOS)

Requirements: Python 3.11+, Node 22+ (the script fetches Node 24 for the Playground CLI via npx),
a PayPal sandbox app and an Anthropic API key.

Secrets are read at runtime from AWS SSM (`/servikaki/secrets/paypal-sandbox.env`,
`claude-servikaki.env`, `channel3.env`), or from environment variables with the same names:
`PAYPAL_API_BASE`, `PAYPAL_ENV`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`,
`PAYPAL_SANDBOX_BUYER_EMAIL`, `ANTHROPIC_API_KEY`, `CHANNEL3_API_KEY` (optional: without it the
price match lever is never offered). Nothing is written to disk.

```bash
pip install -r backend/requirements.txt
./scripts/dev.sh      # API on :8797, Playground store on :9411 with the plugin mounted live
```

Tests:

```bash
python -m pytest -q backend/tests          # guardrail math, price match, PayPal breakdown
python scripts/e2e_playground.py           # headless: every demo scenario + the dashboard
python scripts/e2e_capture.py --invoice    # real sandbox payment, WooCommerce order, fee invoice
```

Configuration: `NUDGEY_MODEL` (default `claude-haiku-5-5`), `NUDGEY_EFFORT` (default `low`),
`NUDGEY_PUBLIC_URL` (the API's public URL, used in the Playground blueprint),
`NUDGEY_PAYPAL_WEBHOOK_ID` (enables verified refund webhooks).

## License

AGPL-3.0, see [LICENSE](LICENSE).
