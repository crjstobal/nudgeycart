<?php
/**
 * REST routes under /wp-json/nudgeycart/v1/.
 *
 * Storefront routes are same-origin calls from the widget. Each nudge is bound to the
 * shopper's WooCommerce session, so one shopper can never pay or capture another's offer.
 * Admin routes proxy the merchant dashboard to the NudgeyCart API with the secret key.
 */

if (!defined('ABSPATH')) {
    exit;
}

function nudgeycart_rest_result($res)
{
    if (is_wp_error($res)) {
        $status = $res->get_error_data()['status'] ?? 500;
        return new WP_REST_Response(['error' => $res->get_error_message()], $status >= 400 ? $status : 500);
    }
    return rest_ensure_response($res);
}

function nudgeycart_session_nudges(): array
{
    return (nudgeycart_load_cart() && WC()->session) ? (array) WC()->session->get('nudgey_nudges', []) : [];
}

function nudgeycart_owns_nudge(string $id): bool
{
    return in_array($id, nudgeycart_session_nudges(), true);
}

function nudgeycart_str($v, int $max = 64): string
{
    return substr(sanitize_text_field((string) $v), 0, $max);
}

add_action('rest_api_init', function () {
    $ns = 'nudgeycart/v1';
    $public = ['permission_callback' => '__return_true'];
    $nid = '(?P<id>ndg_[a-f0-9]{16})';

    register_rest_route($ns, '/nudge', $public + ['methods' => 'POST', 'callback' => function (WP_REST_Request $r) {
        $cart = nudgeycart_build_cart();
        if (!$cart['lines']) {
            return ['show' => false, 'why' => 'empty cart'];
        }
        $signals = $r->get_param('signals');
        $res = nudgeycart_api('POST', '/v1/nudges', [
            'visitor_id' => nudgeycart_str($r->get_param('visitor_id')),
            'session_id' => nudgeycart_str($r->get_param('session_id')),
            'trigger' => nudgeycart_str($r->get_param('trigger'), 32),
            'signals' => is_array($signals) ? array_slice($signals, 0, 60, true) : [],
            'locale' => nudgeycart_str($r->get_param('locale'), 16),
            'force' => get_option('nudgeycart_test_mode') === '1' ? nudgeycart_str($r->get_param('force'), 16) : null,
            'ip' => isset($_SERVER['REMOTE_ADDR']) ? sanitize_text_field(wp_unslash($_SERVER['REMOTE_ADDR'])) : null,
            'cart' => $cart,
        ], 30);
        if (!is_wp_error($res) && !empty($res['nudge_id']) && WC()->session) {
            $mine = nudgeycart_session_nudges();
            $mine[] = $res['nudge_id'];
            WC()->session->set('nudgey_nudges', array_slice($mine, -20));
        }
        return nudgeycart_rest_result($res);
    }]);

    register_rest_route($ns, "/nudges/$nid/dismiss", $public + ['methods' => 'POST', 'callback' => function (WP_REST_Request $r) {
        $id = $r['id'];
        return nudgeycart_owns_nudge($id) ? nudgeycart_rest_result(nudgeycart_api('POST', "/v1/nudges/$id/dismiss", [])) : ['ok' => true];
    }]);

    register_rest_route($ns, "/nudges/$nid/paypal-order", $public + ['methods' => 'POST', 'callback' => function (WP_REST_Request $r) {
        $id = $r['id'];
        if (!nudgeycart_owns_nudge($id)) {
            return new WP_REST_Response(['error' => 'offer not found in this session'], 404);
        }
        return nudgeycart_rest_result(nudgeycart_api('POST', "/v1/nudges/$id/paypal-order", []));
    }]);

    register_rest_route($ns, "/nudges/$nid/capture", $public + ['methods' => 'POST', 'callback' => function (WP_REST_Request $r) {
        $id = $r['id'];
        if (!nudgeycart_owns_nudge($id)) {
            return new WP_REST_Response(['error' => 'offer not found in this session'], 404);
        }
        $res = nudgeycart_api('POST', "/v1/nudges/$id/capture", ['order_id' => nudgeycart_str($r->get_param('order_id'), 40)], 40);
        if (is_wp_error($res)) {
            return nudgeycart_rest_result($res);
        }
        if (($res['status'] ?? '') !== 'COMPLETED') {
            return ['status' => $res['status'] ?? 'UNKNOWN'];
        }
        if (empty($res['capture'])) {
            // Already captured earlier (double click, retry): send the shopper to that order.
            $orders = wc_get_orders(['meta_key' => '_nudgey_nudge_id', 'meta_value' => $id, 'limit' => 1]);
            return ['status' => 'COMPLETED', 'redirect' => $orders ? $orders[0]->get_checkout_order_received_url() : wc_get_page_permalink('shop')];
        }
        $order = nudgeycart_create_paid_order($id, $res);
        if (is_wp_error($order)) {
            // The money is captured: never fail the shopper here. Flag it for the merchant.
            nudgeycart_api('POST', "/v1/nudges/$id/store-order", ['error' => $order->get_error_message()]);
            return ['status' => 'COMPLETED', 'redirect' => wc_get_page_permalink('shop'), 'warning' => 'paid, order pending'];
        }
        nudgeycart_api('POST', "/v1/nudges/$id/store-order", [
            'store_order_id' => (string) $order->get_id(),
            'total' => wc_format_decimal($order->get_total(), 2),
            'url' => $order->get_edit_order_url(),
        ]);
        WC()->cart->empty_cart();
        return ['status' => 'COMPLETED', 'redirect' => $order->get_checkout_order_received_url()];
    }]);

    register_rest_route($ns, '/warm', $public + ['methods' => 'POST', 'callback' => function () {
        $cart = nudgeycart_build_cart();
        if (!$cart['lines']) {
            return ['lookups' => 0];
        }
        $lines = array_map(fn($l) => ['product_id' => $l['product_id'], 'name' => $l['name']], $cart['lines']);
        return nudgeycart_rest_result(nudgeycart_api('POST', '/v1/market/warm', ['lines' => $lines], 60));
    }]);

    register_rest_route($ns, '/events', $public + ['methods' => 'POST', 'callback' => function (WP_REST_Request $r) {
        $events = $r->get_param('events');
        return nudgeycart_rest_result(nudgeycart_api('POST', '/v1/events', [
            'visitor_id' => nudgeycart_str($r->get_param('visitor_id')),
            'session_id' => nudgeycart_str($r->get_param('session_id')),
            'events' => is_array($events) ? array_slice($events, 0, 50) : [],
        ], 10));
    }]);

    /* ---------- merchant dashboard ---------- */

    $admin = ['permission_callback' => fn() => current_user_can('manage_woocommerce')];

    register_rest_route($ns, '/admin/summary', $admin + ['methods' => 'GET', 'callback' => function () {
        $cfg = nudgeycart_config();
        if (is_wp_error($cfg)) {
            return nudgeycart_rest_result($cfg);
        }
        $sum = nudgeycart_api('GET', '/v1/merchant/summary');
        if (is_wp_error($sum)) {
            return nudgeycart_rest_result($sum);
        }
        // Link saved sales to the WooCommerce orders they created.
        foreach ($sum['nudges'] as &$n) {
            $oid = $n['store_order_id'] ?? '';
            $n['order_url'] = ctype_digit((string) $oid) && ($o = wc_get_order((int) $oid)) ? $o->get_edit_order_url() : null;
        }
        $sum['invoices'] = nudgeycart_api('GET', '/v1/merchant/invoices');
        $sum['config'] = $cfg;
        return $sum;
    }]);

    register_rest_route($ns, '/admin/invoice', $admin + ['methods' => 'POST', 'callback' => function () {
        return nudgeycart_rest_result(nudgeycart_api('POST', '/v1/merchant/invoices', [], 60));
    }]);

    register_rest_route($ns, '/admin/settings', $admin + ['methods' => 'POST', 'callback' => function (WP_REST_Request $r) {
        $body = [];
        foreach (['min_margin_pct', 'max_discount_pct', 'control_pct'] as $k) {
            if ($r->get_param($k) !== null) {
                $body[$k] = (float) $r->get_param($k);
            }
        }
        if ($r->get_param('billing_email') !== null) {
            $body['billing_email'] = sanitize_email($r->get_param('billing_email'));
        }
        if ($r->get_param('enabled') !== null) {
            update_option('nudgeycart_enabled', $r->get_param('enabled') ? '1' : '0');
        }
        $res = nudgeycart_api('PUT', '/v1/settings', $body);
        delete_transient('nudgeycart_config');
        return nudgeycart_rest_result($res);
    }]);
});
