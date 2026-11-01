import io
import json
import logging
import os
import zipfile

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import db, demo, service, settings
from .integrations import paypal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="NudgeyCart", version="0.2.0")
# WordPress Playground runs PHP inside the visitor's browser, so the store plugin's
# server-side calls arrive as browser fetches from playground.wordpress.net. Auth is the
# merchant secret key in the Authorization header, never cookies, so open CORS is safe.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PLUGIN_DIR = settings.ROOT / "wordpress-plugin" / "nudgeycart"
BLUEPRINT = settings.ROOT / "playground" / "blueprint.json"


@app.on_event("startup")
def _startup() -> None:
    db.init()


@app.exception_handler(service.NudgeError)
async def _nudge_error(_: Request, e: service.NudgeError):
    return JSONResponse({"error": str(e)}, status_code=e.status)


def _ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else None)


async def _body(req: Request) -> dict:
    try:
        b = await req.json()
    except ValueError:
        raise service.NudgeError(400, "JSON body required")
    return b if isinstance(b, dict) else {}


@app.get("/health")
def health():
    return {"ok": True}


# ---------- distribution: plugin zip and Playground demo ----------

@app.get("/plugin/nudgeycart.zip")
def plugin_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PLUGIN_DIR.rglob("*")):
            if p.is_file() and p.name != ".DS_Store":
                z.write(p, f"nudgeycart/{p.relative_to(PLUGIN_DIR)}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Cache-Control": "no-cache",
                             "Content-Disposition": "attachment; filename=nudgeycart.zip"})


@app.get("/playground/blueprint.json")
def blueprint(dev: int = 0):
    """The demo store recipe, pointed at this API. dev=1 skips the plugin install (mounted locally)."""
    bp = json.loads(BLUEPRINT.read_text().replace("{{API_BASE}}", settings.PUBLIC_URL))
    if dev:
        bp["steps"] = [s for s in bp["steps"] if s.get("pluginData", {}).get("resource") != "url"]
    return JSONResponse(bp, headers={"Cache-Control": "no-cache"})


@app.get("/demo")
def demo_redirect():
    return RedirectResponse("https://playground.wordpress.net/?blueprint-url="
                            f"{settings.PUBLIC_URL}/playground/blueprint.json")


@app.post("/v1/demo-merchants")
async def demo_merchant(req: Request):
    return demo.create_demo_merchant(await _body(req), _ip(req))


# ---------- store plugin (merchant secret key) ----------

@app.get("/v1/config")
def config(authorization: str = Header("")):
    return service.config(service.merchant_by_secret(authorization))


@app.put("/v1/settings")
async def update_settings(req: Request, authorization: str = Header("")):
    return service.update_settings(service.merchant_by_secret(authorization), await _body(req))


@app.post("/v1/market/warm")
async def warm(req: Request, authorization: str = Header("")):
    return service.warm_market(service.merchant_by_secret(authorization), await _body(req))


@app.post("/v1/nudges")
async def open_nudge(req: Request, authorization: str = Header("")):
    return service.open_nudge(service.merchant_by_secret(authorization), await _body(req))


@app.post("/v1/nudges/{nid}/dismiss")
def dismiss(nid: str, authorization: str = Header("")):
    return service.dismiss(service.merchant_by_secret(authorization), nid)


@app.post("/v1/nudges/{nid}/paypal-order")
def paypal_order(nid: str, authorization: str = Header("")):
    return service.create_paypal_order(service.merchant_by_secret(authorization), nid)


@app.post("/v1/nudges/{nid}/capture")
async def capture(nid: str, req: Request, authorization: str = Header("")):
    body = await _body(req)
    return service.capture(service.merchant_by_secret(authorization), nid, str(body.get("order_id")))


@app.post("/v1/nudges/{nid}/store-order")
async def store_order(nid: str, req: Request, authorization: str = Header("")):
    return service.record_store_order(service.merchant_by_secret(authorization), nid, await _body(req))


@app.post("/v1/conversions")
async def conversions(req: Request, authorization: str = Header("")):
    return service.record_conversion(service.merchant_by_secret(authorization), await _body(req))


@app.post("/v1/events")
async def events(req: Request, authorization: str = Header("")):
    return service.record_events(service.merchant_by_secret(authorization), await _body(req))


@app.get("/v1/merchant/summary")
def summary(authorization: str = Header("")):
    return service.summary(service.merchant_by_secret(authorization))


@app.get("/v1/merchant/invoices")
def invoices(authorization: str = Header("")):
    return service.list_invoices(service.merchant_by_secret(authorization))


@app.post("/v1/merchant/invoices")
def invoice(authorization: str = Header("")):
    return service.invoice_fees(service.merchant_by_secret(authorization))


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
