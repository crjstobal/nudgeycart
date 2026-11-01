/*!
 * NudgeyCart storefront widget. AGPL-3.0.
 *
 * Never asks the shopper why they are leaving. It notices behaviour that usually comes before
 * an abandoned cart (hunting for a coupon, lingering on shipping, copying the product name and
 * switching tabs, reading the returns policy...) and, when the shopper moves to leave, sends
 * those signals to the store. The NudgeyCart agent picks one offer, or decides not to interrupt,
 * and the shopper can pay with PayPal right inside the dialog.
 *
 * Talks only to its own store (/wp-json/nudgeycart/v1); the store talks to NudgeyCart.
 */
(function () {
  "use strict";
  var cfg = window.NudgeyCartConfig;
  if (!cfg || !cfg.rest || window.NudgeyCart) return;

  // ---------- identity ----------
  function storage(kind) {
    try { return window[kind]; } catch (e) { return null; }
  }
  function get(kind, k) { try { return storage(kind).getItem(k); } catch (e) { return null; } }
  function set(kind, k, v) { try { storage(kind).setItem(k, v); } catch (e) {} }
  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 10); }
  function persisted(kind, k) {
    var v = get(kind, k);
    if (!v) { v = uid(); set(kind, k, v); }
    return v;
  }
  var visitorId = persisted("localStorage", "nudgey_vid");
  var sessionId = persisted("sessionStorage", "nudgey_sid");
  // The store reads this cookie to attribute natural (non-nudged) orders to the holdout test.
  document.cookie = "nudgey_vid=" + visitorId + ";path=/;max-age=31536000;SameSite=Lax";

  var params = new URLSearchParams(location.search);
  if (params.get("nudgey_reset")) {
    try { sessionStorage.removeItem("nudgey_sid"); sessionStorage.removeItem("nudgey_signals"); } catch (e) {}
    sessionId = persisted("sessionStorage", "nudgey_sid");
  }
  var demoPanel = cfg.demoPanel || (cfg.testMode && params.get("nudgey_demo") !== null);

  // ---------- api (same origin, WordPress REST) ----------
  function api(path, body) {
    return fetch(cfg.rest + path, {
      method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json", "X-WP-Nonce": cfg.nonce },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error((j && (j.error || j.message)) || ("HTTP " + r.status));
        return j;
      });
    });
  }

  // ---------- behaviour signals ----------
  // Session-level counters survive navigation (product page -> cart), page-level ones do not.
  var S = {};
  try { S = JSON.parse(get("sessionStorage", "nudgey_signals") || "{}") || {}; } catch (e) { S = {}; }
  function save() { set("sessionStorage", "nudgey_signals", JSON.stringify(S)); renderPanel(); }
  function bump(k, by) { S[k] = Math.round(((S[k] || 0) + (by || 1)) * 10) / 10; save(); }

  var startedAt = Date.now();
  var visits = Number(get("localStorage", "nudgey_visits") || 0);
  if (!get("sessionStorage", "nudgey_counted")) {
    visits += 1; set("localStorage", "nudgey_visits", String(visits)); set("sessionStorage", "nudgey_counted", "1");
  }
  if (cfg.product) {
    var seen = {};
    try { seen = JSON.parse(get("localStorage", "nudgey_seen") || "{}") || {}; } catch (e) {}
    seen[cfg.product.id] = (seen[cfg.product.id] || 0) + 1;
    set("localStorage", "nudgey_seen", JSON.stringify(seen));
    if (seen[cfg.product.id] > 1) S.product_revisits = seen[cfg.product.id] - 1;
  }

  var cartNames = [];
  function nameWords(name) {
    return String(name || "").toLowerCase().split(/[^a-z0-9]+/).filter(function (w) { return w.length > 2; });
  }
  function looksLikeProductName(text) {
    var t = String(text || "").toLowerCase();
    var names = cartNames.slice();
    if (cfg.product) names.push(cfg.product.name);
    return names.some(function (n) {
      var ws = nameWords(n);
      if (!ws.length) return false;
      var hit = ws.filter(function (w) { return t.indexOf(w) !== -1; }).length;
      return hit >= Math.min(2, ws.length);
    });
  }

  // Copying the product name right before leaving is the strongest "comparing prices" tell.
  document.addEventListener("copy", function () {
    var sel = String(window.getSelection ? window.getSelection() : "");
    if (looksLikeProductName(sel)) bump("copied_product_name"); else if (sel.trim()) bump("copied_text");
  });

  var hiddenAt = 0;
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { hiddenAt = Date.now(); bump("tab_switches"); return; }
    if (hiddenAt) {
      var away = Math.round((Date.now() - hiddenAt) / 1000);
      bump("away_s", away);
      S.last_away_s = away;
      if (away >= 8) S.returned_after_away = 1;
      save();
    }
  });

  function matches(el, sel) { return el && el.closest && el.closest(sel); }
  var COUPON = "[id*=coupon],[name*=coupon],[class*=coupon],.wc-block-components-totals-coupon";
  var SHIPPING = ".wc-block-components-totals-shipping,.woocommerce-shipping-totals,tr.shipping,[class*=shipping-rates]";
  var PRICE = ".price,.wc-block-components-product-price,.wc-block-components-totals-footer-item,.order-total,.cart-subtotal";
  var POSTCODE = "[name*=postcode],[id*=postcode]";
  var POLICY = /(return|refund|shipping|delivery|privacy|terms|contact|faq|warranty|guarantee)/i;

  document.addEventListener("focusin", function (e) {
    if (matches(e.target, COUPON)) bump("coupon_focus");
  });
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (matches(t, COUPON)) bump("coupon_focus");
    if (matches(t, ".wc-block-components-quantity-selector__button--minus,.minus")) bump("qty_decreases");
    if (matches(t, ".wc-block-cart-item__remove-link,a.remove,.remove_from_cart_button")) bump("item_removals");
    if (matches(t, "#tab-title-reviews,a[href='#reviews'],.woocommerce-review-link")) bump("reviews_views");
    var a = matches(t, "a");
    if (a && POLICY.test((a.textContent || "") + " " + (a.getAttribute("href") || ""))) bump("policy_views");
  }, true);
  document.addEventListener("change", function (e) {
    if (matches(e.target, POSTCODE)) bump("postcode_edits");
    if (e.target && e.target.classList && e.target.classList.contains("qty")) {
      var prev = Number(e.target.getAttribute("data-nudgey-prev") || e.target.defaultValue || 0);
      if (Number(e.target.value) < prev) bump("qty_decreases");
      e.target.setAttribute("data-nudgey-prev", e.target.value);
    }
  }, true);

  // Dwell: time the pointer rests on the shipping line or on prices.
  function dwell(sel, key) {
    var since = 0;
    document.addEventListener("mouseover", function (e) { if (!since && matches(e.target, sel)) since = Date.now(); });
    document.addEventListener("mouseout", function (e) {
      if (since && !matches(e.relatedTarget, sel)) { bump(key, (Date.now() - since) / 1000); since = 0; }
    });
  }
  dwell(SHIPPING, "shipping_dwell_s");
  dwell(PRICE, "price_dwell_s");
  // On touch screens there is no hover: count time the shipping line is on screen instead.
  if ("IntersectionObserver" in window) {
    var shipSeenAt = 0;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { shipSeenAt = Date.now(); bump("shipping_views"); }
        else if (shipSeenAt) { bump("shipping_dwell_s", (Date.now() - shipSeenAt) / 1000 / 3); shipSeenAt = 0; }
      });
    }, { threshold: 0.9 });
    var watchShipping = function () {
      var el = document.querySelector(SHIPPING);
      if (el && !el.getAttribute("data-nudgey-io")) { el.setAttribute("data-nudgey-io", "1"); io.observe(el); }
    };
    setInterval(watchShipping, 1500);
  }

  function snapshot() {
    var page = {
      page: cfg.page, trigger_page_seconds: Math.round((Date.now() - startedAt) / 1000),
      seconds_on_site: Math.round((Date.now() - startedAt) / 1000) + (S.seconds_before || 0),
      device: innerWidth < 768 ? "mobile" : "desktop", visits: visits, idle_s: idleSeconds(),
      scroll_depth: Math.round(100 * (scrollY + innerHeight) / Math.max(document.body.scrollHeight, 1))
    };
    if (cfg.page === "checkout") {
      page.checkout_fields_filled = Array.prototype.filter.call(
        document.querySelectorAll("form input:not([type=hidden]):not([type=checkbox]):not([type=radio])"),
        function (i) { return i.value && i.value.trim(); }).length;
      page.form_errors = document.querySelectorAll(".woocommerce-error li,.wc-block-components-validation-error").length;
    }
    return Object.assign({}, S, page);
  }
  addEventListener("pagehide", function () {
    S.seconds_before = (S.seconds_before || 0) + Math.round((Date.now() - startedAt) / 1000);
    save();
  });

  // ---------- cart (Store API, for names and to warm price lookups) ----------
  var cartItems = 0;
  function readCart() {
    return fetch(cfg.storeCartUrl, { credentials: "same-origin" }).then(function (r) { return r.json(); })
      .then(function (c) {
        cartItems = (c.items || []).length;
        cartNames = (c.items || []).map(function (i) { return i.name; });
        return c;
      }).catch(function () { return { items: [] }; });
  }
  readCart().then(function (c) {
    // Look up competitor prices now, so the decision at exit time never waits on a search.
    var sig = (c.items || []).map(function (i) { return i.id + "x" + i.quantity; }).join(",");
    if (cfg.priceMatch && sig && get("sessionStorage", "nudgey_warm") !== sig) {
      set("sessionStorage", "nudgey_warm", sig);
      api("warm", {}).catch(function () {});
    }
    renderPanel();
  });
  // The block cart updates in place: refresh what we know after cart changes.
  document.addEventListener("click", function (e) {
    if (matches(e.target, ".add_to_cart_button,.single_add_to_cart_button,.wc-block-components-product-button__button")) {
      setTimeout(function () { set("sessionStorage", "nudgey_warm", ""); readCart().then(function (c) {
        if (cfg.priceMatch && (c.items || []).length) api("warm", {}).catch(function () {});
      }); }, 1500);
    }
  });

  // ---------- triggers ----------
  var state = { busy: false, done: false, nudge: null, paypal: null };
  var onCheckoutish = cfg.page === "cart" || cfg.page === "checkout";
  var lastActivity = Date.now(), lastY = scrollY, lastT = Date.now();
  function idleSeconds() { return Math.round((Date.now() - lastActivity) / 1000); }

  function fire(trigger, extra) {
    if (state.busy || cfg.page === "order_received") return;
    if (state.done && !extra) return;
    state.busy = true;
    var signals = Object.assign(snapshot(), (extra && extra.signals) || {});
    showThinking();
    api("nudge", {
      visitor_id: visitorId, session_id: sessionId, trigger: trigger, signals: signals,
      locale: navigator.language, force: extra ? "treatment" : null
    }).then(function (res) {
      state.done = true;
      if (!res.show) { hideThinking(); explainSkip(res); return; }
      state.nudge = res;
      loadPayPal();
      openDialog(res);
    }).catch(function (e) {
      hideThinking();
      if (demoPanel) toast("NudgeyCart: " + e.message, true);
      console.warn("[NudgeyCart]", e.message);
    }).finally(function () { state.busy = false; });
  }

  // Desktop: the pointer leaves through the top of the window, towards the tabs or close button.
  document.addEventListener("mouseout", function (e) {
    if (!e.relatedTarget && e.clientY <= 0 && Date.now() - startedAt > 3000 && cartItems) fire("exit_intent");
  });
  ["mousemove", "keydown", "scroll", "touchstart"].forEach(function (t) {
    addEventListener(t, function () { lastActivity = Date.now(); }, { passive: true });
  });
  setInterval(function () {
    if (onCheckoutish && cartItems && idleSeconds() >= 45) fire("idle");
  }, 1000);
  // Coming back from another tab after a while: they were probably comparing.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && onCheckoutish && cartItems && S.last_away_s >= 8) setTimeout(function () { fire("tab_return"); }, 400);
  });
  // Mobile: a fast flick up towards the address bar on cart or checkout.
  addEventListener("scroll", function () {
    var now = Date.now(), dy = lastY - scrollY, dt = now - lastT;
    if (dt > 0 && dt < 250 && dy > innerHeight * 0.6 && scrollY < 200 && onCheckoutish && innerWidth < 768 && cartItems) {
      fire("scroll_up");
    }
    lastY = scrollY; lastT = now;
  }, { passive: true });

  // ---------- PayPal ----------
  function loadPayPal() {
    if (state.paypal) return state.paypal;
    state.paypal = new Promise(function (resolve, reject) {
      if (window.nudgeyPaypal) return resolve(window.nudgeyPaypal);
      var q = "client-id=" + encodeURIComponent(cfg.paypalClientId) + "&currency=" + encodeURIComponent(cfg.currency) +
        "&intent=capture&components=buttons,messages&enable-funding=paylater,venmo";
      if (cfg.sandbox) q += "&buyer-country=US";
      var s = document.createElement("script");
      s.src = "https://www.paypal.com/sdk/js?" + q;
      // Own namespace, so we never clash with another PayPal plugin on the page.
      s.setAttribute("data-namespace", "nudgeyPaypal");
      s.onload = function () { resolve(window.nudgeyPaypal); };
      s.onerror = function () { reject(new Error("PayPal could not load")); };
      document.head.appendChild(s);
    });
    return state.paypal;
  }

  function renderPayPal(container, offer) {
    container.innerHTML = '<div class="ndg-muted">Loading PayPal…</div>';
    loadPayPal().then(function (paypal) {
      container.innerHTML = "";
      var handlers = {
        style: { layout: "vertical", shape: "pill", height: 44, label: "pay" },
        createOrder: function () {
          return api("nudges/" + offer.nudge_id + "/paypal-order").then(function (o) { return o.id; });
        },
        onApprove: function (data, actions) {
          setStatus("Confirming your payment…");
          return api("nudges/" + offer.nudge_id + "/capture", { order_id: data.orderID }).then(function (res) {
            if (res.status !== "COMPLETED") { setStatus("Payment status: " + res.status, true); return; }
            setStatus("Paid. Thank you! Taking you to your order…");
            setTimeout(function () { location.href = res.redirect; }, 700);
          }).catch(function (e) {
            if (/INSTRUMENT_DECLINED/.test(e.message)) return actions.restart();
            setStatus("Payment could not be completed: " + e.message, true);
          });
        },
        onError: function (err) { setStatus("PayPal error. Please try again.", true); console.warn(err); }
      };
      [paypal.FUNDING.PAYPAL, paypal.FUNDING.PAYLATER, paypal.FUNDING.VENMO, paypal.FUNDING.CARD].forEach(function (src) {
        var b = paypal.Buttons(Object.assign({ fundingSource: src }, handlers));
        if (b.isEligible()) {
          var div = document.createElement("div");
          div.className = "ndg-btn";
          container.appendChild(div);
          b.render(div);
        }
      });
      if (paypal.Messages && offer.installment) {
        var msg = document.createElement("div");
        msg.className = "ndg-ppmsg";
        container.appendChild(msg);
        paypal.Messages({ amount: Number(offer.final_total), placement: "cart",
                          style: { layout: "text", text: { size: 12 } } }).render(msg);
      }
    }).catch(function (e) { container.innerHTML = '<div class="ndg-error">' + esc(e.message) + "</div>"; });
  }

  // ---------- UI ----------
  var root, statusEl, thinkingEl;
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
    statusEl.className = "ndg-status " + (err ? "ndg-error" : "ndg-muted");
  }

  var CSS = "" +
    ".ndg-overlay{position:fixed;inset:0;background:rgba(17,20,24,.45);z-index:2147483646;display:flex;align-items:flex-end;" +
    "justify-content:center;font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;animation:ndg-fade .18s ease-out}" +
    "@media(min-width:640px){.ndg-overlay{align-items:center}}" +
    "@keyframes ndg-fade{from{opacity:0}to{opacity:1}}" +
    ".ndg-card{background:#fff;color:#1d2125;width:100%;max-width:420px;max-height:94vh;overflow:auto;border-radius:20px 20px 0 0;" +
    "box-shadow:0 20px 60px rgba(0,0,0,.25);padding:22px 20px 16px;animation:ndg-in .24s cubic-bezier(.2,.9,.3,1.2)}" +
    "@media(min-width:640px){.ndg-card{border-radius:20px}}" +
    "@keyframes ndg-in{from{transform:translateY(20px) scale(.98);opacity:.3}to{transform:none;opacity:1}}" +
    ".ndg-x{position:absolute;right:10px;top:8px;background:none;border:0;font-size:24px;color:#6b7280;cursor:pointer;padding:6px 10px;line-height:1}" +
    ".ndg-rel{position:relative}" +
    ".ndg-badge{display:inline-flex;align-items:center;gap:6px;background:#e8f2ff;color:#0057b8;border-radius:999px;padding:3px 10px;font-size:12px;font-weight:650}" +
    ".ndg-h{font-size:21px;font-weight:750;letter-spacing:-.01em;margin:10px 0 4px}" +
    ".ndg-p{color:#475467;margin:0 0 12px}" +
    ".ndg-match{display:flex;align-items:center;gap:10px;background:#f6faf7;border:1px solid #d7eedf;border-radius:12px;padding:10px 12px;margin:0 0 10px;font-size:14px}" +
    ".ndg-match b{color:#067647}" +
    ".ndg-offer{border:1px solid #e4e7ec;border-radius:14px;padding:12px 14px;margin:6px 0 10px}" +
    ".ndg-line{display:flex;justify-content:space-between;font-size:14px;color:#475467;margin:2px 0}" +
    ".ndg-save{color:#067647;font-weight:600}" +
    ".ndg-total{display:flex;justify-content:space-between;font-weight:750;font-size:18px;margin-top:6px;color:#1d2125}" +
    ".ndg-strike{text-decoration:line-through;color:#98a2b3;font-weight:400;margin-right:6px;font-size:15px}" +
    ".ndg-btn{margin-top:8px}.ndg-ppmsg{margin-top:6px}" +
    ".ndg-muted{color:#6b7280;font-size:13px;margin:6px 0}.ndg-error{color:#b42318;font-size:13px;margin:6px 0}" +
    ".ndg-disclosure{font-size:11px;color:#98a2b3;margin-top:10px}" +
    ".ndg-link{display:block;margin:6px auto 0;background:none;border:0;color:#6b7280;text-decoration:underline;cursor:pointer;font:inherit;font-size:13px;padding:6px}" +
    ".ndg-thinking{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:2147483646;background:#1d2125;color:#fff;" +
    "border-radius:999px;padding:9px 16px;font:600 13px -apple-system,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25);display:flex;gap:8px;align-items:center}" +
    ".ndg-dots span{display:inline-block;width:6px;height:6px;margin:0 2px;background:#fff;border-radius:50%;animation:ndg-b 1s infinite}" +
    ".ndg-dots span:nth-child(2){animation-delay:.15s}.ndg-dots span:nth-child(3){animation-delay:.3s}" +
    "@keyframes ndg-b{0%,80%,100%{opacity:.25}40%{opacity:1}}" +
    ".ndg-toast{position:fixed;left:16px;bottom:16px;max-width:380px;z-index:2147483646;background:#fff;color:#1d2125;border:1px solid #e4e7ec;" +
    "border-radius:14px;padding:12px 14px;font:13px/1.45 -apple-system,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.15)}" +
    ".ndg-toast b{display:block;margin-bottom:2px}.ndg-toast.err{border-color:#fda29b}" +
    ".ndg-panel{position:fixed;right:16px;bottom:16px;width:290px;z-index:2147483645;background:#101418;color:#e8eaed;border-radius:16px;" +
    "font:12.5px/1.4 -apple-system,sans-serif;box-shadow:0 12px 36px rgba(0,0,0,.3);overflow:hidden}" +
    ".ndg-panel header{display:flex;align-items:center;gap:8px;padding:10px 12px;background:#1a2027;cursor:pointer;font-weight:700}" +
    ".ndg-panel header span{margin-left:auto;font-weight:400;color:#98a2b3}" +
    ".ndg-panel .ndg-pb{padding:10px 12px}" +
    ".ndg-panel button.ndg-sc{display:block;width:100%;text-align:left;margin:0 0 6px;background:#1f2731;color:#e8eaed;border:1px solid #2b3540;" +
    "border-radius:10px;padding:8px 10px;cursor:pointer;font:inherit}" +
    ".ndg-panel button.ndg-sc:hover{border-color:#6ea8fe}" +
    ".ndg-panel button.ndg-sc small{display:block;color:#98a2b3}" +
    ".ndg-panel .ndg-live{margin-top:8px;border-top:1px solid #2b3540;padding-top:8px;color:#98a2b3}" +
    ".ndg-panel .ndg-live code{color:#9ad1ff;font:11px ui-monospace,Menlo,monospace}" +
    ".ndg-panel.min .ndg-pb{display:none}";

  function ensureStyle() {
    if (document.getElementById("ndg-style")) return;
    var st = document.createElement("style");
    st.id = "ndg-style";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function showThinking() {
    if (!demoPanel) return; // shoppers only see the result, never a spinner
    ensureStyle();
    hideThinking();
    thinkingEl = document.createElement("div");
    thinkingEl.className = "ndg-thinking";
    thinkingEl.innerHTML = '<span class="ndg-dots"><span></span><span></span><span></span></span> NudgeyCart is reading the signals';
    document.body.appendChild(thinkingEl);
  }
  function hideThinking() { if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; } }

  function toast(html, err, ms) {
    ensureStyle();
    var t = document.createElement("div");
    t.className = "ndg-toast" + (err ? " err" : "");
    t.innerHTML = html;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, ms || 9000);
  }

  var REASON = {
    price: "price", shipping_cost: "shipping cost", comparison_shopping: "comparing prices", trust: "trust",
    checkout_friction: "checkout friction", just_browsing: "just browsing", unclear: "unclear signals"
  };
  function explainSkip(res) {
    if (!demoPanel) return;
    if (res.group === "control") {
      toast("<b>Holdout group</b>This visitor is measured but never nudged, so the dashboard can prove the uplift.");
    } else if (res.reason) {
      toast("<b>Agent chose not to interrupt</b>Read as " + esc(REASON[res.reason] || res.reason) + ". " + esc(res.rationale || ""), false, 12000);
    } else {
      toast("<b>No nudge</b>" + esc(res.why || ""));
    }
  }

  var LEVER = {
    price_match: "Price matched", free_shipping: "Free shipping", discount: "Special offer",
    reassurance: "Protected by PayPal"
  };

  function openDialog(o) {
    hideThinking();
    ensureStyle();
    var cur = o.currency, changed = o.final_total !== o.original_total;
    var match = (o.matches || [])[0];
    root = document.createElement("div");
    root.className = "ndg-overlay";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "ndg-h");
    root.innerHTML =
      '<div class="ndg-card ndg-rel"><button class="ndg-x" aria-label="Close">×</button>' +
      '<span class="ndg-badge">' + esc(LEVER[o.lever] || "Before you go") + "</span>" +
      '<div class="ndg-h" id="ndg-h">' + esc(o.headline) + "</div>" +
      '<p class="ndg-p">' + esc(o.message) + "</p>" +
      (match ? '<div class="ndg-match">✓ <span>' + esc(match.competitor) + " lists it at <b>" +
        money(match.competitor_price, cur) + "</b>. Same price here.</span></div>" : "") +
      '<div class="ndg-offer">' +
      '<div class="ndg-line"><span>Items</span><span>' + money(o.item_total, cur) + "</span></div>" +
      '<div class="ndg-line"><span>Shipping</span><span' + (Number(o.shipping) === 0 ? ' class="ndg-save">Free' : ">" +
        money(o.shipping, cur)) + "</span></div>" +
      (Number(o.discount) > 0 ? '<div class="ndg-line ndg-save"><span>' + (o.lever === "price_match" ? "Price match" : "Your offer") +
        "</span><span>−" + money(o.discount, cur) + "</span></div>" : "") +
      '<div class="ndg-total"><span>Total</span><span>' +
      (changed ? '<span class="ndg-strike">' + money(o.original_total, cur) + "</span>" : "") + money(o.final_total, cur) + "</span></div>" +
      "</div>" +
      '<div class="ndg-pp"></div><div class="ndg-status"></div>' +
      (o.disclosure ? '<div class="ndg-disclosure">' + esc(o.disclosure) + "</div>" : "") +
      '<button class="ndg-link ndg-no">No thanks, keep shopping</button></div>';
    document.body.appendChild(root);
    statusEl = root.querySelector(".ndg-status");
    root.querySelector(".ndg-x").onclick = close;
    root.querySelector(".ndg-no").onclick = close;
    root.addEventListener("click", function (e) { if (e.target === root) close(); });
    addEventListener("keydown", escClose);
    renderPayPal(root.querySelector(".ndg-pp"), o);
    root.querySelector(".ndg-x").focus();
  }

  function escClose(e) { if (e.key === "Escape") close(); }

  function close() {
    if (!root) return;
    if (state.nudge) api("nudges/" + state.nudge.nudge_id + "/dismiss").catch(function () {});
    root.remove();
    root = null;
    removeEventListener("keydown", escClose);
  }

  // ---------- demo panel (Playground stores) ----------
  var SCENARIOS = [
    { id: "compare", label: "Comparing prices", hint: "copied the product name, 40s in another tab",
      signals: { copied_product_name: 1, tab_switches: 2, away_s: 41, last_away_s: 41, returned_after_away: 1 }, trigger: "tab_return" },
    { id: "shipping", label: "Shipping shock", hint: "lingered on shipping, re-entered postcode",
      signals: { shipping_dwell_s: 7.5, shipping_views: 3, postcode_edits: 1 }, trigger: "exit_intent" },
    { id: "coupon", label: "Coupon hunter", hint: "focused the coupon box twice, lowered quantity",
      signals: { coupon_focus: 2, qty_decreases: 1, price_dwell_s: 4 }, trigger: "exit_intent" },
    { id: "trust", label: "Trust doubts", hint: "read the returns policy and reviews",
      signals: { policy_views: 2, reviews_views: 1 }, trigger: "exit_intent" },
    { id: "browse", label: "Just browsing", hint: "12s visit, nothing else",
      signals: { seconds_on_site: 12, visits: 1 }, trigger: "exit_intent" }
  ];
  var panel;
  function renderPanel() {
    if (!demoPanel || !document.body) return;
    ensureStyle();
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "ndg-panel" + (get("sessionStorage", "nudgey_panel_min") ? " min" : "");
      document.body.appendChild(panel);
      panel.addEventListener("click", function (e) {
        if (matches(e.target, "header")) {
          panel.classList.toggle("min");
          set("sessionStorage", "nudgey_panel_min", panel.classList.contains("min") ? "1" : "");
          return;
        }
        var b = matches(e.target, "button.ndg-sc");
        if (!b) return;
        // The cart count arrives asynchronously: re-check before telling anyone it is empty.
        (cartItems ? Promise.resolve() : readCart()).then(function () {
          if (!cartItems) { toast("<b>Add something to the cart first</b>NudgeyCart only steps in when there is a sale to save."); return; }
          if (b.getAttribute("data-id") === "live") { fire("manual", { signals: {} }); return; }
          var sc = SCENARIOS.filter(function (s) { return s.id === b.getAttribute("data-id"); })[0];
          fire(sc.trigger, { signals: sc.signals });
        });
      });
    }
    var live = Object.keys(S).filter(function (k) { return k !== "seconds_before" && S[k]; })
      .map(function (k) { return "<code>" + esc(k) + "=" + esc(S[k]) + "</code>"; }).join(" ");
    panel.innerHTML = "<header>NudgeyCart demo <span>" + (cartItems ? cartItems + " in cart" : "cart empty") + "</span></header>" +
      '<div class="ndg-pb"><div style="margin-bottom:8px;color:#98a2b3">Simulate why a shopper is leaving:</div>' +
      SCENARIOS.map(function (s) {
        return '<button class="ndg-sc" data-id="' + s.id + '">' + esc(s.label) + "<small>" + esc(s.hint) + "</small></button>";
      }).join("") +
      '<button class="ndg-sc" data-id="live">Use my real behaviour<small>copy the product name, switch tabs, open the coupon box…</small></button>' +
      '<div class="ndg-live">Signals this visit: ' + (live || "none yet") + "</div></div>";
  }
  if (document.body) renderPanel(); else document.addEventListener("DOMContentLoaded", renderPanel);

  window.NudgeyCart = { trigger: function (signals) { state.done = false; fire("manual", { signals: signals || {} }); },
                        signals: snapshot, visitorId: visitorId,
                        offer: function () { return state.nudge; } };
})();
