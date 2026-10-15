/*!
 * NudgeyCart storefront widget. AGPL-3.0.
 * Detects when a shopper with a cart is about to leave, asks why, shows the offer the
 * NudgeyCart agent chose, and takes the payment with PayPal right inside the dialog.
 *
 * Expects window.NudgeyCartConfig = { apiBase, key, cartUrl, page, orderReceived? }.
 */
(function () {
  "use strict";
  var cfg = window.NudgeyCartConfig;
  if (!cfg || !cfg.apiBase || !cfg.key || window.NudgeyCart) return;

  // ---------- identity ----------
  function store(kind) {
    try { return window[kind]; } catch (e) { return null; }
  }
  function uid() {
    return (Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
  }
  function persisted(kind, k) {
    var s = store(kind), v = null;
    try { v = s && s.getItem(k); } catch (e) {}
    if (!v) { v = uid(); try { s && s.setItem(k, v); } catch (e) {} }
    return v;
  }
  var visitorId = persisted("localStorage", "nudgey_vid");
  var sessionId = persisted("sessionStorage", "nudgey_sid");
  document.cookie = "nudgey_vid=" + visitorId + ";path=/;max-age=31536000;SameSite=Lax";

  var params = new URLSearchParams(location.search);
  var force = params.get("nudgey_force");
  try {
    if (force) sessionStorage.setItem("nudgey_force", force);
    force = sessionStorage.getItem("nudgey_force");
  } catch (e) {}
  if (params.get("nudgey_reset")) {
    try { sessionStorage.removeItem("nudgey_sid"); } catch (e) {}
    sessionId = persisted("sessionStorage", "nudgey_sid");
  }

  var startedAt = Date.now();
  var state = { triggered: false, nudge: null, paypalReady: null, config: null, events: [] };

  // ---------- api ----------
  function api(path, body, method) {
    var opts = { method: method || "POST", headers: { "Content-Type": "application/json" }, keepalive: true };
    if (body) opts.body = JSON.stringify(Object.assign({ key: cfg.key }, body));
    return fetch(cfg.apiBase + path, opts).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
        return j;
      });
    });
  }
  function track(type, data) {
    state.events.push({ type: type, data: data || {} });
  }
  function flush() {
    if (!state.events.length) return;
    var evs = state.events.splice(0, state.events.length);
    api("/v1/events", { visitor_id: visitorId, session_id: sessionId, events: evs }).catch(function () {});
  }
  setInterval(flush, 10000);
  addEventListener("pagehide", flush);

  // ---------- conversions (control group measurement) ----------
  if (cfg.orderReceived && !cfg.orderReceived.nudgey) {
    api("/v1/conversions", { visitor_id: visitorId, order_ref: String(cfg.orderReceived.id),
                             total: cfg.orderReceived.total }).catch(function () {});
  }

  // ---------- cart ----------
  function readCart() {
    return fetch(cfg.cartUrl, { credentials: "same-origin" }).then(function (r) { return r.json(); })
      .then(function (c) {
        var minor = Math.pow(10, (c.totals && c.totals.currency_minor_unit) || 2);
        return {
          items: (c.items || []).map(function (i) { return { product_id: i.id, qty: i.quantity }; }),
          shipping: c.totals && c.totals.total_shipping != null ? Number(c.totals.total_shipping) / minor : null,
          total: c.totals ? Number(c.totals.total_price) / minor : 0
        };
      });
  }

  // ---------- triggers ----------
  var signals = { exit_intent: 0, idle_s: 0, tab_switches: 0, fast_scroll_up: 0, page: cfg.page };
  var onCheckoutish = cfg.page === "cart" || cfg.page === "checkout";
  var idleLimit = cfg.idleSeconds || 45;
  var lastActivity = Date.now(), hiddenAt = 0, lastY = scrollY, lastT = Date.now();

  function fire(trigger) {
    if (state.triggered || cfg.page === "order_received") return;
    state.triggered = true;
    signals.seconds_on_site = Math.round((Date.now() - startedAt) / 1000);
    signals.viewport = innerWidth < 768 ? "mobile" : "desktop";
    track("trigger", { trigger: trigger });
    readCart().then(function (cart) {
      if (!cart.items.length) { state.triggered = false; return; }
      return api("/v1/nudges", {
        visitor_id: visitorId, session_id: sessionId, trigger: trigger, signals: signals,
        items: cart.items, shipping: cart.shipping, locale: navigator.language, force: force
      }).then(function (res) {
        if (!res.show) { track("not_shown", { why: res.why }); return; }
        state.nudge = res;
        preloadPayPal();
        openDialog(res);
      });
    }).catch(function (e) { console.warn("[NudgeyCart]", e.message); });
  }

  // Desktop: pointer leaves through the top of the window (towards tabs / close button).
  document.addEventListener("mouseout", function (e) {
    if (!e.relatedTarget && e.clientY <= 0 && Date.now() - startedAt > 3000) {
      signals.exit_intent++;
      fire("exit_intent");
    }
  });
  ["mousemove", "keydown", "scroll", "touchstart"].forEach(function (t) {
    addEventListener(t, function () { lastActivity = Date.now(); }, { passive: true });
  });
  // Idle on cart / checkout.
  setInterval(function () {
    signals.idle_s = Math.round((Date.now() - lastActivity) / 1000);
    if (onCheckoutish && signals.idle_s >= idleLimit) fire("idle");
  }, 1000);
  // Mobile: coming back to the tab after comparing elsewhere.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { hiddenAt = Date.now(); signals.tab_switches++; return; }
    if (onCheckoutish && hiddenAt && Date.now() - hiddenAt > 8000) fire("tab_return");
  });
  // Mobile: a fast flick up towards the address bar on cart / checkout.
  addEventListener("scroll", function () {
    var now = Date.now(), dy = lastY - scrollY, dt = now - lastT;
    if (dt > 0 && dt < 250 && dy > innerHeight * 0.6 && scrollY < 200) {
      signals.fast_scroll_up++;
      if (onCheckoutish && innerWidth < 768) fire("scroll_up");
    }
    lastY = scrollY; lastT = now;
  }, { passive: true });

  // ---------- PayPal ----------
  function preloadPayPal() {
    if (state.paypalReady) return state.paypalReady;
    state.paypalReady = api("/v1/config?key=" + encodeURIComponent(cfg.key), null, "GET").then(function (c) {
      state.config = c;
      if (window.paypal) return window.paypal;
      return new Promise(function (resolve, reject) {
        var q = "client-id=" + encodeURIComponent(c.paypal_client_id) + "&currency=" + c.currency +
          "&intent=capture&components=buttons,messages&enable-funding=paylater,venmo";
        if (c.sandbox) q += "&buyer-country=US";
        var s = document.createElement("script");
        s.src = "https://www.paypal.com/sdk/js?" + q;
        s.setAttribute("data-namespace", "paypal");
        s.onload = function () { resolve(window.paypal); };
        s.onerror = function () { reject(new Error("PayPal SDK failed to load")); };
        document.head.appendChild(s);
      });
    });
    return state.paypalReady;
  }

  function renderPayPal(container, offer) {
    container.innerHTML = '<div class="ndg-muted">Loading PayPal…</div>';
    preloadPayPal().then(function (paypal) {
      container.innerHTML = "";
      var handlers = {
        style: { layout: "vertical", shape: "pill", height: 44 },
        createOrder: function () {
          track("accept", { lever: offer.lever });
          return api("/v1/nudges/" + offer.nudge_id + "/paypal-order", {}).then(function (o) { return o.id; });
        },
        onApprove: function (data, actions) {
          setStatus("Confirming your payment…");
          return api("/v1/nudges/" + offer.nudge_id + "/capture", { order_id: data.orderID })
            .then(function (res) {
              track("paid", { lever: offer.lever });
              flush();
              setStatus("Paid. Thank you! Taking you to your order…");
              setTimeout(function () { location.href = res.redirect; }, 900);
            })
            .catch(function (e) {
              if (/INSTRUMENT_DECLINED/.test(e.message)) return actions.restart();
              setStatus("Payment could not be completed: " + e.message, true);
            });
        },
        onCancel: function () { track("paypal_cancel", {}); },
        onError: function (err) { setStatus("PayPal error. Please try again.", true); console.warn(err); }
      };
      var sources = offer.lever === "pay_later"
        ? [paypal.FUNDING.PAYLATER, paypal.FUNDING.PAYPAL]
        : [paypal.FUNDING.PAYPAL, paypal.FUNDING.PAYLATER, paypal.FUNDING.VENMO, paypal.FUNDING.CARD];
      sources.forEach(function (src) {
        var b = paypal.Buttons(Object.assign({ fundingSource: src }, handlers));
        if (b.isEligible()) {
          var div = document.createElement("div");
          div.className = "ndg-btn";
          container.appendChild(div);
          b.render(div);
        }
      });
      if (offer.lever === "pay_later" && paypal.Messages) {
        var msg = document.createElement("div");
        container.appendChild(msg);
        paypal.Messages({ amount: Number(offer.final_total), placement: "cart",
                          style: { layout: "text", text: { size: 12 } } }).render(msg);
      }
    }).catch(function (e) { container.innerHTML = '<div class="ndg-error">' + esc(e.message) + "</div>"; });
  }

  // ---------- UI ----------
  var root, body, statusEl;
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function money(v, cur) {
    try { return new Intl.NumberFormat(navigator.language, { style: "currency", currency: cur }).format(Number(v)); }
    catch (e) { return v + " " + cur; }
  }
  function setStatus(t, err) {
    if (!statusEl) return;
    statusEl.textContent = t;
    statusEl.className = err ? "ndg-error" : "ndg-muted";
  }

  var CSS = "" +
    ".ndg-overlay{position:fixed;inset:0;background:rgba(17,20,24,.45);z-index:2147483646;display:flex;" +
    "align-items:flex-end;justify-content:center;font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}" +
    "@media(min-width:640px){.ndg-overlay{align-items:center}}" +
    ".ndg-card{background:#fff;color:#1d2125;width:100%;max-width:420px;max-height:92vh;overflow:auto;" +
    "border-radius:18px 18px 0 0;box-shadow:0 20px 60px rgba(0,0,0,.25);padding:20px 20px 16px;animation:ndg-in .22s ease-out}" +
    "@media(min-width:640px){.ndg-card{border-radius:18px}}" +
    "@keyframes ndg-in{from{transform:translateY(16px);opacity:.4}to{transform:none;opacity:1}}" +
    ".ndg-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}" +
    ".ndg-avatar{width:34px;height:34px;border-radius:50%;background:#0070e0;color:#fff;display:grid;place-items:center;font-weight:700}" +
    ".ndg-title{font-weight:650}.ndg-sub{font-size:12px;color:#6b7280}" +
    ".ndg-x{margin-left:auto;background:none;border:0;font-size:22px;color:#6b7280;cursor:pointer;padding:4px 8px}" +
    ".ndg-bubble{background:#f2f4f7;border-radius:14px 14px 14px 4px;padding:10px 12px;margin:8px 0;max-width:92%}" +
    ".ndg-me{background:#0070e0;color:#fff;border-radius:14px 14px 4px 14px;margin-left:auto}" +
    ".ndg-chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}" +
    ".ndg-chip{border:1px solid #d0d5dd;background:#fff;border-radius:999px;padding:7px 12px;cursor:pointer;font:inherit;font-size:14px;color:#1d2125}" +
    ".ndg-chip:hover{border-color:#0070e0;color:#0070e0}" +
    ".ndg-row{display:flex;gap:8px;margin-top:8px}" +
    ".ndg-input{flex:1;border:1px solid #d0d5dd;border-radius:10px;padding:9px 11px;font:inherit;color:#1d2125;background:#fff}" +
    ".ndg-send{border:0;background:#1d2125;color:#fff;border-radius:10px;padding:0 14px;font:inherit;cursor:pointer}" +
    ".ndg-offer{border:1px solid #e4e7ec;border-radius:14px;padding:12px 14px;margin:10px 0}" +
    ".ndg-line{display:flex;justify-content:space-between;font-size:14px;color:#475467;margin:2px 0}" +
    ".ndg-total{display:flex;justify-content:space-between;font-weight:700;font-size:17px;margin-top:6px;color:#1d2125}" +
    ".ndg-strike{text-decoration:line-through;color:#98a2b3;font-weight:400;margin-right:6px}" +
    ".ndg-badge{display:inline-block;background:#e8f2ff;color:#0057b8;border-radius:6px;padding:2px 8px;font-size:12px;font-weight:600;margin-bottom:6px}" +
    ".ndg-btn{margin-top:8px}.ndg-muted{color:#6b7280;font-size:13px;margin:6px 0}.ndg-error{color:#b42318;font-size:13px;margin:6px 0}" +
    ".ndg-disclosure{font-size:11px;color:#98a2b3;margin-top:10px}" +
    ".ndg-link{background:none;border:0;color:#6b7280;text-decoration:underline;cursor:pointer;font:inherit;font-size:13px;padding:6px 0}" +
    ".ndg-typing span{display:inline-block;width:6px;height:6px;margin:0 2px;background:#98a2b3;border-radius:50%;animation:ndg-b 1s infinite}" +
    ".ndg-typing span:nth-child(2){animation-delay:.15s}.ndg-typing span:nth-child(3){animation-delay:.3s}" +
    "@keyframes ndg-b{0%,80%,100%{opacity:.3}40%{opacity:1}}" +
    ".ndg-sim{position:fixed;right:16px;bottom:16px;z-index:2147483645;background:#1d2125;color:#fff;border:0;border-radius:999px;" +
    "padding:10px 16px;font:600 13px -apple-system,sans-serif;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.2)}";

  function ensureStyle() {
    if (document.getElementById("ndg-style")) return;
    var st = document.createElement("style");
    st.id = "ndg-style";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function openDialog(res) {
    ensureStyle();
    root = document.createElement("div");
    root.className = "ndg-overlay";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.innerHTML =
      '<div class="ndg-card"><div class="ndg-head"><div class="ndg-avatar">N</div><div>' +
      '<div class="ndg-title">' + esc(cfg.storeName || "Checkout assistant") + '</div>' +
      '<div class="ndg-sub">Checkout help · payments by PayPal</div></div>' +
      '<button class="ndg-x" aria-label="Close">×</button></div><div class="ndg-body"></div>' +
      '<div class="ndg-status"></div></div>';
    document.body.appendChild(root);
    body = root.querySelector(".ndg-body");
    statusEl = root.querySelector(".ndg-status");
    root.querySelector(".ndg-x").onclick = close;
    root.addEventListener("click", function (e) { if (e.target === root) close(); });
    addEventListener("keydown", escClose);
    track("shown", { trigger: res.trigger });
    say(res.message);
    var chips = document.createElement("div");
    chips.className = "ndg-chips";
    res.chips.forEach(function (c) {
      var b = document.createElement("button");
      b.className = "ndg-chip";
      b.textContent = c.label;
      b.onclick = function () { chips.remove(); send({ reason: c.reason }, c.label); };
      chips.appendChild(b);
    });
    body.appendChild(chips);
    freeText("Or tell me in your own words…", function (txt) { chips.remove(); send({ text: txt }, txt); });
  }

  function escClose(e) { if (e.key === "Escape") close(); }

  function close() {
    if (!root) return;
    if (state.nudge) api("/v1/nudges/" + state.nudge.nudge_id + "/dismiss", {}).catch(function () {});
    track("dismiss", {});
    root.remove();
    root = null;
    removeEventListener("keydown", escClose);
  }

  function say(text, mine) {
    var d = document.createElement("div");
    d.className = "ndg-bubble" + (mine ? " ndg-me" : "");
    d.textContent = text;
    body.appendChild(d);
    d.scrollIntoView({ block: "nearest" });
    return d;
  }

  function freeText(placeholder, onSend) {
    var row = document.createElement("form");
    row.className = "ndg-row";
    row.innerHTML = '<input class="ndg-input" maxlength="300" placeholder="' + esc(placeholder) + '">' +
      '<button class="ndg-send" type="submit">Send</button>';
    row.onsubmit = function (e) {
      e.preventDefault();
      var v = row.querySelector("input").value.trim();
      if (!v) return;
      row.remove();
      onSend(v);
    };
    body.appendChild(row);
    return row;
  }

  function send(payload, shown) {
    say(shown, true);
    body.querySelectorAll(".ndg-offer,.ndg-row,.ndg-link").forEach(function (n) { n.remove(); });
    var typing = say("");
    typing.innerHTML = '<span class="ndg-typing"><span></span><span></span><span></span></span>';
    setStatus("");
    payload.locale = navigator.language;
    api("/v1/nudges/" + state.nudge.nudge_id + "/reply", payload).then(function (offer) {
      typing.remove();
      say(offer.message);
      showOffer(offer);
    }).catch(function (e) {
      typing.remove();
      setStatus("Sorry, something went wrong: " + e.message, true);
    });
  }

  var LEVER_LABEL = {
    discount: "Special offer", free_shipping: "Free shipping", pay_later: "Pay in 4, interest-free",
    express_checkout: "One-click checkout", reassurance: "Protected by PayPal", none: ""
  };

  function showOffer(o) {
    if (o.lever === "none") {
      var bye = document.createElement("button");
      bye.className = "ndg-link";
      bye.textContent = "Close";
      bye.onclick = close;
      body.appendChild(bye);
      return;
    }
    var card = document.createElement("div");
    card.className = "ndg-offer";
    var cur = o.currency, changed = o.final_total !== o.original_total;
    card.innerHTML =
      (LEVER_LABEL[o.lever] ? '<div class="ndg-badge">' + esc(LEVER_LABEL[o.lever]) + "</div>" : "") +
      '<div class="ndg-line"><span>Items</span><span>' + money(o.item_total, cur) + "</span></div>" +
      '<div class="ndg-line"><span>Shipping</span><span>' +
      (Number(o.shipping) === 0 ? "Free" : money(o.shipping, cur)) + "</span></div>" +
      (Number(o.discount) > 0 ? '<div class="ndg-line"><span>Your offer</span><span>−' +
        money(o.discount, cur) + "</span></div>" : "") +
      '<div class="ndg-total"><span>Total</span><span>' +
      (changed ? '<span class="ndg-strike">' + money(o.original_total, cur) + "</span>" : "") +
      money(o.final_total, cur) + "</span></div>" +
      (o.installment ? '<div class="ndg-muted">or 4 payments of ' + money(o.installment, cur) + "</div>" : "") +
      '<div class="ndg-pp"></div>' +
      (o.disclosure ? '<div class="ndg-disclosure">' + esc(o.disclosure) + "</div>" : "");
    body.appendChild(card);
    renderPayPal(card.querySelector(".ndg-pp"), o);
    if (o.turns_left > 0) {
      var more = document.createElement("button");
      more.className = "ndg-link";
      more.textContent = "Still not sure? Tell me why";
      more.onclick = function () {
        more.remove();
        freeText("What's still holding you back?", function (t) { send({ text: t }, t); });
      };
      body.appendChild(more);
    }
  }

  // ---------- public API & demo helper ----------
  window.NudgeyCart = { trigger: function () { state.triggered = false; fire("manual"); }, visitorId: visitorId };
  if (cfg.testMode && params.get("nudgey_demo") !== null) {
    ensureStyle();
    var sim = document.createElement("button");
    sim.className = "ndg-sim";
    sim.textContent = "Simulate exit intent";
    sim.onclick = function () { state.triggered = false; signals.exit_intent++; fire("exit_intent"); };
    document.addEventListener("DOMContentLoaded", function () { document.body.appendChild(sim); });
    if (document.body) document.body.appendChild(sim);
  }
  track("page_view", { page: cfg.page });
})();
