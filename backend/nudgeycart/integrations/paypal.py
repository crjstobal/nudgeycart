"""PayPal REST: Orders v2 (the negotiated sale) and Invoicing v2 (NudgeyCart's success fee)."""
from __future__ import annotations

import threading
import time
import uuid
from decimal import Decimal

import httpx

from .. import settings
from ..core.policy import Cart, Offer, money

_token: dict = {"value": None, "exp": 0.0}
_lock = threading.Lock()


class PayPalError(Exception):
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body
        super().__init__(f"PayPal {status}: {body.get('name')} {body.get('details') or body.get('message')}")


def _access_token() -> str:
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 60:
            return _token["value"]
        r = httpx.post(
            f"{settings.paypal_base()}/v1/oauth2/token",
            auth=(settings.paypal_client_id(), settings.paypal_client_secret()),
            data={"grant_type": "client_credentials"}, timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], exp=time.time() + j["expires_in"])
        return _token["value"]


def _call(method: str, path: str, json: dict | None = None, idem: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json",
               "Prefer": "return=representation"}
    if idem:
        headers["PayPal-Request-Id"] = idem
    try:
        r = httpx.request(method, f"{settings.paypal_base()}{path}", json=json, headers=headers, timeout=30)
    except httpx.HTTPError as e:
        raise PayPalError(503, {"name": "NETWORK_ERROR", "message": str(e)}) from e
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        raise PayPalError(r.status_code, body)
    return body


def _amt(currency: str, v: Decimal) -> dict:
    return {"currency_code": currency, "value": f"{money(v):.2f}"}


def order_payload(cart: Cart, offer: Offer, reference: str, store_name: str, payee_email: str | None) -> dict:
    cur = cart.currency
    unit = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reference,
            "custom_id": reference,
            "description": f"{store_name} order (NudgeyCart)",
            "soft_descriptor": store_name[:22],
            "amount": {
                **_amt(cur, offer.final_total),
                "breakdown": {
                    "item_total": _amt(cur, offer.item_total),
                    "shipping": _amt(cur, offer.shipping),
                    "discount": _amt(cur, offer.discount_amount),
                },
            },
            "items": [
                {"name": l.name[:127], "quantity": str(l.qty), "unit_amount": _amt(cur, l.unit_price),
                 "sku": (l.sku or str(l.product_id))[:127], "category": "PHYSICAL_GOODS"}
                for l in cart.lines
            ],
        }],
    }
    if payee_email:
        unit["purchase_units"][0]["payee"] = {"email_address": payee_email}
    return unit


def create_order(cart: Cart, offer: Offer, reference: str, store_name: str, payee_email: str | None) -> dict:
    return _call("POST", "/v2/checkout/orders",
                 order_payload(cart, offer, reference, store_name, payee_email), idem=f"create-{reference}")


def capture_order(order_id: str) -> dict:
    return _call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, idem=f"capture-{order_id}")


def get_order(order_id: str) -> dict:
    return _call("GET", f"/v2/checkout/orders/{order_id}")


def capture_summary(cap: dict) -> dict:
    """Flatten the bits of a capture response we need downstream."""
    pu = cap["purchase_units"][0]
    c = pu["payments"]["captures"][0]
    payer = cap.get("payer", {})
    ship = pu.get("shipping", {})
    addr = ship.get("address", {})
    return {
        "capture_id": c["id"],
        "status": c["status"],
        "amount": Decimal(c["amount"]["value"]),
        "currency": c["amount"]["currency_code"],
        "email": payer.get("email_address"),
        "first_name": payer.get("name", {}).get("given_name", ""),
        "last_name": payer.get("name", {}).get("surname", ""),
        "ship_name": ship.get("name", {}).get("full_name", ""),
        "address": {
            "address_1": addr.get("address_line_1", ""), "address_2": addr.get("address_line_2", ""),
            "city": addr.get("admin_area_2", ""), "state": addr.get("admin_area_1", ""),
            "postcode": addr.get("postal_code", ""), "country": addr.get("country_code", ""),
        },
    }


# ---------- Invoicing: how NudgeyCart bills its success fee ----------

def create_and_send_invoice(billing_email: str, currency: str, lines: list[tuple[str, Decimal]],
                            note: str) -> dict:
    number = _call("POST", "/v2/invoicing/generate-next-invoice-number")["invoice_number"]
    inv = _call("POST", "/v2/invoicing/invoices", {
        "detail": {
            "invoice_number": number,
            "currency_code": currency,
            "note": note,
            "payment_term": {"term_type": "NET_10"},
        },
        "invoicer": {"business_name": "NudgeyCart"},
        "primary_recipients": [{"billing_info": {"email_address": billing_email}}],
        "items": [
            {"name": name[:200], "quantity": "1", "unit_amount": _amt(currency, amount)}
            for name, amount in lines
        ],
    }, idem=f"inv-{uuid.uuid4().hex}")
    inv_id = inv.get("id") or inv["href"].rsplit("/", 1)[-1]
    sent = _call("POST", f"/v2/invoicing/invoices/{inv_id}/send",
                 {"send_to_invoicer": True, "send_to_recipient": True})
    detail = _call("GET", f"/v2/invoicing/invoices/{inv_id}")
    url = detail.get("detail", {}).get("metadata", {}).get("recipient_view_url") or sent.get("href")
    return {"id": inv_id, "number": number, "status": detail.get("status"), "url": url}


def verify_webhook(headers: dict, event: dict, webhook_id: str) -> bool:
    body = _call("POST", "/v1/notifications/verify-webhook-signature", {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": webhook_id,
        "webhook_event": event,
    })
    return body.get("verification_status") == "SUCCESS"
