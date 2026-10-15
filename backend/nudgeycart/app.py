import logging
import os

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import db, service, settings
from .integrations import paypal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="NudgeyCart", version="0.1.0")
# The widget runs on merchant storefronts; it only ever sends the public merchant key.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

WIDGET = settings.ROOT / "widget" / "nudgeycart.js"
DASHBOARD = settings.ROOT / "dashboard" / "index.html"


@app.on_event("startup")
def _startup() -> None:
    db.init()


@app.exception_handler(service.NudgeError)
async def _nudge_error(_: Request, e: service.NudgeError):
    return JSONResponse({"error": str(e)}, status_code=e.status)


def _ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else None)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/widget.js")
def widget():
    return FileResponse(WIDGET, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/dashboard")
def dashboard():
    return FileResponse(DASHBOARD, media_type="text/html")


# ---------- storefront (public key) ----------

@app.get("/v1/config")
def config(key: str):
    m = service.merchant_by_key(key)
    return {"paypal_client_id": settings.paypal_client_id(), "currency": m["currency"],
            "sandbox": settings.paypal_env() != "live", "merchant": m["name"],
            "test_mode": bool(m["test_mode"])}


@app.post("/v1/events")
async def events(req: Request):
    body = await req.json()
    return service.record_events(service.merchant_by_key(body.get("key", "")), body)


@app.post("/v1/nudges")
async def open_nudge(req: Request):
    body = await req.json()
    return service.open_nudge(service.merchant_by_key(body.get("key", "")), body, _ip(req))


@app.post("/v1/nudges/{nid}/reply")
async def reply(nid: str, req: Request):
    body = await req.json()
    return service.reply(service.merchant_by_key(body.get("key", "")), nid, body)


@app.post("/v1/nudges/{nid}/dismiss")
async def dismiss(nid: str, req: Request):
    body = await req.json()
    return service.dismiss(service.merchant_by_key(body.get("key", "")), nid)


@app.post("/v1/nudges/{nid}/paypal-order")
async def paypal_order(nid: str, req: Request):
    body = await req.json()
    return service.create_paypal_order(service.merchant_by_key(body.get("key", "")), nid)


@app.post("/v1/nudges/{nid}/capture")
async def capture(nid: str, req: Request):
    body = await req.json()
    return service.capture(service.merchant_by_key(body.get("key", "")), nid, str(body.get("order_id")))


@app.post("/v1/conversions")
async def conversions(req: Request):
    body = await req.json()
    return service.record_conversion(service.merchant_by_key(body.get("key", "")), body)


# ---------- merchant (admin token) ----------

@app.get("/v1/merchant/summary")
def summary(x_admin_token: str = Header("")):
    return service.summary(service.merchant_by_admin(x_admin_token))


@app.get("/v1/merchant/invoices")
def invoices(x_admin_token: str = Header("")):
    return service.list_invoices(service.merchant_by_admin(x_admin_token))


@app.post("/v1/merchant/invoices")
def invoice(x_admin_token: str = Header("")):
    return service.invoice_fees(service.merchant_by_admin(x_admin_token))


# ---------- PayPal webhooks ----------

@app.post("/v1/webhooks/paypal")
async def paypal_webhook(req: Request):
    event = await req.json()
    webhook_id = os.environ.get("NUDGEY_PAYPAL_WEBHOOK_ID")
    if not webhook_id or not paypal.verify_webhook({k.lower(): v for k, v in req.headers.items()},
                                                   event, webhook_id):
        return JSONResponse({"error": "signature not verified"}, status_code=400)
    if event.get("event_type") == "PAYMENT.CAPTURE.REFUNDED":
        up = next((l["href"] for l in event["resource"].get("links", []) if l.get("rel") == "up"), "")
        service.handle_refund(up.rstrip("/").rsplit("/", 1)[-1])
    return {"ok": True}
