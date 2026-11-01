/*!
 * NudgeyCart merchant dashboard (wp-admin > WooCommerce > NudgeyCart). AGPL-3.0.
 * AG Grid Community for the decision log, AG Charts Community for the trends.
 */
(function () {
  "use strict";
  var A = window.NudgeyCartAdmin;
  if (!A) return;
  var cur = A.currency || "USD";

  var LEVER = {
    price_match: { label: "Price match", color: "#00a32a" },
    free_shipping: { label: "Free shipping", color: "#2271b1" },
    discount: { label: "Discount", color: "#dba617" },
    reassurance: { label: "Reassurance", color: "#6d4bd6" },
    none: { label: "Did not interrupt", color: "#a7aaad" }
  };
  var REASON = {
    comparison_shopping: "Comparing prices", shipping_cost: "Shipping cost", price: "Price",
    trust: "Trust", checkout_friction: "Checkout friction", just_browsing: "Just browsing", unclear: "Unclear"
  };
  var STATE = {
    paid: "Saved sale", offered: "Offered", accepted: "Opened PayPal", dismissed: "Dismissed",
    skipped: "Not interrupted", control: "Holdout", refunded: "Refunded", deciding: "Deciding"
  };
  var PALETTE = ["#2271b1", "#00a32a", "#dba617", "#6d4bd6", "#d63638", "#3582c4", "#a7aaad"];

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(v, digits) {
    if (v == null || v === "") return "–";
    return new Intl.NumberFormat(undefined, { style: "currency", currency: cur,
      maximumFractionDigits: digits == null ? 2 : digits }).format(v);
  }
  function pct(r) { return r == null ? "–" : (r * 100).toFixed(1) + "%"; }
  function rest(path, method, body) {
    return fetch(A.rest + path, {
      method: method || "GET", credentials: "same-origin",
      headers: { "X-WP-Nonce": A.nonce, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || j.message || r.status); return j; });
    });
  }
  function notice(html, type) {
    var n = el("nudgey-notice");
    n.hidden = false;
    n.className = "notice inline notice-" + (type || "info");
    n.querySelector("p").innerHTML = html;
  }

  var tryBtn = el("nudgey-try");
  if (tryBtn) tryBtn.href = A.shopUrl;

  // ================= Settings tab =================
  var form = el("nudgey-settings-form");
  if (form) {
    rest("admin/summary").then(function (s) {
      var g = s.config.guardrails;
      form.min_margin_pct.value = Math.round(g.min_margin_pct * 100);
      form.max_discount_pct.value = Math.round(g.max_discount_pct * 100);
      form.control_pct.value = g.control_pct;
      form.billing_email.value = s.config.billing_email || "";
      form.enabled.checked = A.enabled;
      el("nudgey-conn").innerHTML = "Connected as <strong>" + esc(s.config.merchant) + "</strong> (" +
        esc(s.config.merchant_id) + ")" + (s.config.test_mode ? ", <strong>test mode</strong> with the PayPal sandbox" : "") +
        ". Price match with live competitor prices: " + (s.config.price_match ? "on (Channel3)" : "off") +
        ". Fee: " + (g.fee_pct * 100) + "% of saved sales only.";
    }).catch(function (e) { el("nudgey-conn").textContent = "Not connected: " + e.message; });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      rest("admin/settings", "POST", {
        min_margin_pct: Number(form.min_margin_pct.value) / 100,
        max_discount_pct: Number(form.max_discount_pct.value) / 100,
        control_pct: Number(form.control_pct.value),
        billing_email: form.billing_email.value,
        enabled: form.enabled.checked
      }).then(function () {
        el("nudgey-saved").hidden = false;
        setTimeout(function () { el("nudgey-saved").hidden = true; }, 2500);
      }).catch(function (e) { notice("Could not save: " + esc(e.message), "error"); });
    });
    return;
  }

  // ================= Dashboard tab =================
  if (!el("nudgey-dashboard")) return;

  // ---------- KPIs ----------
  function kpis(s) {
    var tiles = [
      { l: "Revenue saved", v: fmt(s.saved_revenue, 0), h: s.saved_orders + " orders the agent closed", c: "#00a32a" },
      { l: "Conversion uplift vs holdout", v: s.uplift_pts == null ? "–" : (s.uplift_pts > 0 ? "+" : "") + s.uplift_pts + " pts",
        h: "nudged " + pct(s.treatment.rate) + " vs holdout " + pct(s.control.rate), c: "#2271b1", pos: s.uplift_pts > 0 },
      { l: "Conceded to close", v: fmt(s.discount_given, 0),
        h: s.saved_revenue ? (100 * s.discount_given / (s.saved_revenue + s.discount_given)).toFixed(1) + "% of saved carts, never below margin" : "never below your margin floor",
        c: "#dba617" },
      { l: "Chose not to interrupt", v: String(s.skipped), h: "shoppers left alone: no reason to nudge", c: "#6d4bd6" },
      { l: "NudgeyCart fees", v: fmt(s.fees_total, 0), h: fmt(s.fees_uninvoiced) + " not invoiced yet", c: "#a7aaad" }
    ];
    el("nudgey-kpis").innerHTML = tiles.map(function (t) {
      return '<div class="nudgey-kpi" style="--kpi:' + t.c + '"><div class="l">' + esc(t.l) + '</div><div class="v' +
        (t.pos ? " pos" : "") + '">' + esc(t.v) + '</div><div class="h">' + esc(t.h) + "</div></div>";
    }).join("");
    el("nudgey-invoice").disabled = !(s.fees_uninvoiced > 0);
    el("nudgey-sub").textContent = s.merchant.name + " · fee " + (s.merchant.fee_pct * 100) + "% of saved sales · margin floor " +
      Math.round(s.merchant.min_margin_pct * 100) + "% · holdout " + s.merchant.control_pct + "%" +
      (s.merchant.test_mode ? " · TEST MODE (PayPal sandbox)" : "");
  }

  // ---------- charts ----------
  var charts = {};
  var theme = {
    baseTheme: "ag-default",
    palette: { fills: PALETTE, strokes: PALETTE },
    params: { fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", fontSize: 12 },
    overrides: { common: { background: { visible: false } } }
  };
  function chart(id, options) {
    options.container = el(id);
    options.theme = theme;
    if (charts[id]) charts[id].update(options); else charts[id] = agCharts.AgCharts.create(options);
  }
  function moneyLabel(p) { return fmt(p.value, 0); }

  function renderCharts(s) {
    var daily = s.daily.map(function (d) {
      return { day: d.day.slice(5), revenue: d.saved_revenue, orders: d.saved_orders, nudges: d.nudges };
    });
    chart("chart-daily", {
      data: daily,
      series: [
        { type: "bar", xKey: "day", yKey: "revenue", yName: "Saved revenue", cornerRadius: 3,
          tooltip: { renderer: function (p) { return { content: fmt(p.datum.revenue) + " · " + p.datum.orders + " orders" }; } } },
        { type: "line", xKey: "day", yKey: "nudges", yName: "Shoppers about to leave", yKeyAxis: "y2",
          marker: { enabled: false }, stroke: "#6d4bd6", strokeWidth: 2 }
      ],
      axes: {
        x: { type: "category", position: "bottom", label: { autoRotate: false } },
        y: { type: "number", position: "left", label: { formatter: moneyLabel } },
        y2: { type: "number", position: "right", gridLine: { enabled: false } }
      },
      legend: { position: "bottom" }
    });
    chart("chart-ab", {
      data: [
        { group: "Nudged", rate: (s.treatment.rate || 0) * 100, n: s.treatment.converted + " / " + s.treatment.visitors },
        { group: "Holdout", rate: (s.control.rate || 0) * 100, n: s.control.converted + " / " + s.control.visitors }
      ],
      series: [{ type: "bar", xKey: "group", yKey: "rate", yName: "Conversion", cornerRadius: 4,
        itemStyler: function (p) { return { fill: p.datum.group === "Nudged" ? "#2271b1" : "#c3c4c7" }; },
        label: { formatter: function (p) { return p.value.toFixed(1) + "%"; } },
        tooltip: { renderer: function (p) { return { content: p.datum.rate.toFixed(1) + "% converted (" + p.datum.n + " visitors)" }; } } }],
      axes: { x: { type: "category", position: "bottom" },
              y: { type: "number", position: "left", label: { formatter: function (p) { return p.value + "%"; } } } },
      legend: { enabled: false }
    });
    chart("chart-reasons", {
      data: s.reasons.map(function (r) { return { reason: r.label, count: r.count }; }),
      series: [{ type: "donut", angleKey: "count", legendItemKey: "reason", calloutLabelKey: "reason",
        innerRadiusRatio: 0.62, calloutLabel: { enabled: false } }],
      // Right-hand legend in one column, wide enough for "Checkout friction" without truncation.
      legend: { position: "right", orientation: "vertical", maxWidth: 150, item: { maxWidth: 150, label: { maxLength: 24 } } }
    });
    var levers = Object.keys(s.levers).map(function (k) {
      var l = s.levers[k];
      return { lever: (LEVER[k] || { label: k }).label, key: k, revenue: l.revenue,
               close: l.offered ? Math.round(100 * l.paid / l.offered) : 0, paid: l.paid, offered: l.offered };
    }).sort(function (a, b) { return b.revenue - a.revenue; });
    chart("chart-levers", {
      data: levers,
      series: [
        { type: "bar", direction: "horizontal", xKey: "lever", yKey: "revenue", yName: "Saved revenue", cornerRadius: 3,
          itemStyler: function (p) { return { fill: (LEVER[p.datum.key] || {}).color || "#2271b1" }; },
          label: { formatter: function (p) { return fmt(p.value, 0); }, placement: "outside-end" },
          tooltip: { renderer: function (p) { var d = p.datum;
            return { content: fmt(d.revenue) + " · closed " + d.paid + " of " + d.offered + " (" + d.close + "%)" }; } } }
      ],
      axes: { x: { type: "category", position: "left" }, y: { type: "number", position: "bottom", label: { formatter: moneyLabel } } },
      legend: { enabled: false }
    });
  }

  // ---------- grid ----------
  var filter = "all";
  var gridTheme = agGrid.themeQuartz.withParams({
    accentColor: "#2271b1", fontFamily: "inherit", fontSize: 13, headerFontWeight: 600,
    headerBackgroundColor: "#f6f7f7", borderColor: "#dcdcde", rowHoverColor: "#f0f6fc",
    wrapperBorderRadius: 8, spacing: 6
  });
  var grid = agGrid.createGrid(el("nudgey-grid"), {
    theme: gridTheme,
    rowData: [],
    getRowId: function (p) { return p.data.id; },
    defaultColDef: { sortable: true, filter: true, resizable: true },
    pagination: true, paginationPageSize: 25, paginationPageSizeSelector: [25, 50, 100],
    animateRows: true,
    rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true },
    isExternalFilterPresent: function () { return filter !== "all"; },
    doesExternalFilterPass: function (n) { return n.data.state === filter; },
    onRowClicked: function (e) { openDrawer(e.data); },
    overlayNoRowsTemplate: '<span class="nudgey-empty">No decisions yet. Open your store, add something to the cart and try to leave.</span>',
    columnDefs: [
      { headerName: "When", field: "created_at", width: 145, sort: "desc", filter: "agDateColumnFilter",
        valueGetter: function (p) { return new Date(p.data.created_at * 1000); },
        valueFormatter: function (p) { return p.value.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); } },
      { headerName: "Outcome", field: "state", width: 140,
        cellRenderer: function (p) { return '<span class="ng-pill ng-' + esc(p.value) + '">' + esc(STATE[p.value] || p.value) + "</span>"; } },
      { headerName: "Why leaving", field: "reason_category", width: 175,
        valueFormatter: function (p) { return REASON[p.value] || (p.data.group_name === "control" ? "Not analysed" : ""); },
        cellRenderer: function (p) {
          if (!p.value) return '<span class="ng-why">' + (p.data.group_name === "control" ? "Holdout" : "") + "</span>";
          var c = Math.round((p.data.confidence || 0) * 100);
          return '<span class="ng-conf" title="confidence ' + c + '%"><span class="bar"><b style="width:' + c + '%"></b></span>' +
            esc(REASON[p.value] || p.value) + "</span>";
        } },
      { headerName: "Offer", field: "lever", width: 160,
        valueFormatter: function (p) { return (LEVER[p.value] || {}).label || ""; },
        cellRenderer: function (p) {
          var l = LEVER[p.value];
          return l ? '<span class="ng-lever"><i style="background:' + l.color + '"></i>' + esc(l.label) + "</span>" : "";
        } },
      { headerName: "Cart", field: "cart_total", width: 100, type: "rightAligned", filter: "agNumberColumnFilter",
        valueFormatter: function (p) { return fmt(p.value); } },
      { headerName: "Conceded", width: 105, type: "rightAligned", filter: "agNumberColumnFilter",
        valueGetter: function (p) { var d = p.data; return d.state === "paid" ? Math.round(((d.cart_total || 0) - (d.paid_total || 0)) * 100) / 100 : null; },
        valueFormatter: function (p) { return p.value ? fmt(p.value) : ""; } },
      { headerName: "Paid", field: "paid_total", width: 100, type: "rightAligned", filter: "agNumberColumnFilter",
        valueFormatter: function (p) { return p.value ? fmt(p.value) : ""; } },
      { headerName: "Order", field: "store_order_id", width: 90,
        cellRenderer: function (p) {
          return p.data.order_url ? '<a href="' + esc(p.data.order_url) + '" onclick="event.stopPropagation()">#' + esc(p.value) + "</a>" : esc(p.value || "");
        } },
      { headerName: "Agent reasoning", field: "rationale", flex: 1, minWidth: 280, tooltipField: "rationale" },
      { headerName: "Trigger", field: "trigger", width: 110, hide: true },
      { headerName: "Model", field: "model", width: 140, hide: true },
      { headerName: "Decision ms", field: "decision_ms", width: 110, type: "rightAligned", hide: true }
    ]
  });

  document.querySelectorAll(".nudgey-chip").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll(".nudgey-chip").forEach(function (x) { x.classList.remove("is-on"); });
      b.classList.add("is-on");
      filter = b.getAttribute("data-filter");
      grid.onFilterChanged();
    });
  });

  // ---------- drawer: one decision, explained ----------
  var drawer = el("nudgey-drawer");
  function openDrawer(n) {
    var offer = n.offer || {};
    var evidence = offer.evidence || [];
    var signals = n.signals || {};
    var lines = (n.cart && n.cart.lines) || [];
    var sigRows = Object.keys(signals).filter(function (k) { return signals[k] !== 0 && signals[k] !== "" && signals[k] != null; })
      .map(function (k) { return "<tr><td><code>" + esc(k) + "</code></td><td>" + esc(signals[k]) + "</td></tr>"; }).join("");
    drawer.innerHTML = '<button class="x" aria-label="Close">×</button>' +
      "<h3>" + esc(STATE[n.state] || n.state) + (n.lever && LEVER[n.lever] ? " · " + esc(LEVER[n.lever].label) : "") + "</h3>" +
      '<div class="description">' + new Date(n.created_at * 1000).toLocaleString() + " · trigger " + esc(n.trigger || "–") +
      " · " + esc(n.model || "") + (n.decision_ms ? " in " + n.decision_ms + " ms" : "") + "</div>" +
      (n.reason_category ? '<div class="sec"><h4>What the agent read</h4><div class="quote"><b>' + esc(REASON[n.reason_category] || n.reason_category) +
        " (" + Math.round((n.confidence || 0) * 100) + "% confident)</b>" +
        (evidence.length ? '<ul class="ev">' + evidence.map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("") + "</ul>" : "") + "</div></div>" : "") +
      (n.rationale ? '<div class="sec"><h4>Why this offer</h4><p>' + esc(n.rationale) + "</p></div>" : "") +
      (offer.headline ? '<div class="sec"><h4>What the shopper saw</h4><p><strong>' + esc(offer.headline) + "</strong><br>" + esc(offer.message) + "</p>" +
        ((offer.matches || []).length ? '<p class="description">Matched ' + esc(offer.matches[0].competitor) + " at " + fmt(offer.matches[0].competitor_price) + " (Channel3)</p>" : "") + "</div>" : "") +
      '<div class="sec"><h4>Cart</h4><table>' + lines.map(function (l) {
        return "<tr><td>" + esc(l.qty) + " × " + esc(l.name) + "</td><td>" + fmt(l.unit_price * l.qty) + "</td></tr>";
      }).join("") + "<tr><td>Cart total</td><td>" + fmt(n.cart_total) + "</td></tr>" +
      (n.paid_total ? "<tr><td><strong>Paid with PayPal</strong></td><td><strong>" + fmt(n.paid_total) + "</strong></td></tr>" +
        "<tr><td>NudgeyCart fee</td><td>" + fmt(n.fee_amount) + "</td></tr>" : "") + "</table>" +
      (n.order_url ? '<p><a class="button" href="' + esc(n.order_url) + '">Open order #' + esc(n.store_order_id) + "</a></p>" : "") + "</div>" +
      (sigRows ? '<div class="sec"><h4>Behaviour signals</h4><table>' + sigRows + "</table></div>" : "") +
      (n.error ? '<div class="sec"><h4>Issue</h4><p style="color:#d63638">' + esc(n.error) + "</p></div>" : "");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    drawer.querySelector(".x").onclick = closeDrawer;
  }
  function closeDrawer() { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(); });

  // ---------- billing ----------
  el("nudgey-invoice").addEventListener("click", function () {
    var b = this;
    b.disabled = true;
    b.textContent = "Creating PayPal invoice…";
    rest("admin/invoice", "POST").then(function (inv) {
      notice("PayPal invoice <strong>" + esc(inv.paypal_invoice_id) + "</strong> sent for " + fmt(Number(inv.amount)) + " (" +
        inv.orders + " saved orders). " + (inv.url ? '<a target="_blank" rel="noopener" href="' + esc(inv.url) + '">Open invoice</a>' : ""), "success");
      load();
    }).catch(function (e) { notice("Invoice failed: " + esc(e.message), "error"); b.disabled = false; })
      .finally(function () { b.textContent = "Invoice fees via PayPal"; });
  });

  // ---------- load & refresh ----------
  function load() {
    return rest("admin/summary").then(function (s) {
      cur = s.merchant.currency || cur;
      kpis(s);
      renderCharts(s);
      grid.setGridOption("rowData", s.nudges);
    }).catch(function (e) { notice("Could not reach NudgeyCart: " + esc(e.message), "error"); });
  }
  load();
  setInterval(function () { if (!document.hidden) load(); }, 15000);
})();
