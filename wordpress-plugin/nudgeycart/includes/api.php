<?php
/**
 * Server-to-server client for the NudgeyCart API.
 *
 * The merchant secret key lives in this site's options and is only ever sent from PHP.
 * In WordPress Playground, PHP runs in the visitor's browser and wp_remote_* becomes a
 * fetch() call, which is why the API accepts cross-origin requests with a bearer key.
 */

if (!defined('ABSPATH')) {
    exit;
}

function nudgeycart_api_base(): string
{
    return untrailingslashit(get_option('nudgeycart_api_base') ?: NUDGEYCART_DEFAULT_API);
}

/**
 * @return array|WP_Error Decoded JSON body, or an error carrying the API message and status.
 */
function nudgeycart_api(string $method, string $path, ?array $body = null, int $timeout = 25)
{
    $secret = get_option('nudgeycart_secret_key');
    $args = [
        'method' => $method,
        'timeout' => $timeout,
        'headers' => ['Content-Type' => 'application/json', 'Accept' => 'application/json'],
    ];
    if ($secret) {
        $args['headers']['Authorization'] = 'Bearer ' . $secret;
    }
    if ($body !== null) {
        $args['body'] = wp_json_encode($body);
    }
    // Not wp_safe_remote_request: during development the API runs on localhost.
    $res = wp_remote_request(nudgeycart_api_base() . $path, $args);
    if (is_wp_error($res)) {
        return new WP_Error('nudgeycart_unreachable', 'NudgeyCart API unreachable: ' . $res->get_error_message(), ['status' => 502]);
    }
    $code = (int) wp_remote_retrieve_response_code($res);
    $data = json_decode(wp_remote_retrieve_body($res), true);
    if ($code >= 400) {
        $msg = is_array($data) ? ($data['error'] ?? $data['detail'] ?? 'error') : 'error';
        return new WP_Error('nudgeycart_api', is_string($msg) ? $msg : wp_json_encode($msg), ['status' => $code]);
    }
    return is_array($data) ? $data : [];
}

/** Catalogue snapshot (prices and unit costs) used to seed a demo merchant's history. */
function nudgeycart_catalog_snapshot(int $limit = 30): array
{
    $out = [];
    foreach (wc_get_products(['status' => 'publish', 'limit' => $limit, 'type' => 'simple']) as $p) {
        $out[] = [
            'product_id' => $p->get_id(),
            'name' => $p->get_name(),
            'price' => wc_format_decimal($p->get_price(), 2),
            'cost' => $p->get_meta(NUDGEYCART_COST_META) !== '' ? wc_format_decimal($p->get_meta(NUDGEYCART_COST_META), 2) : null,
        ];
    }
    return $out;
}

/**
 * Make sure this store has a NudgeyCart merchant account. Stores without a key register a
 * demo merchant (test mode, sandbox PayPal) so the plugin works the moment it is activated.
 *
 * @return true|WP_Error
 */
function nudgeycart_connect(bool $force = false)
{
    if (get_option('nudgeycart_secret_key') && !$force) {
        return true;
    }
    delete_option('nudgeycart_secret_key');
    $res = nudgeycart_api('POST', '/v1/demo-merchants', [
        'store_name' => wp_specialchars_decode(get_bloginfo('name'), ENT_QUOTES),
        'currency' => get_woocommerce_currency(),
        'store_url' => home_url(),
        'catalog' => nudgeycart_catalog_snapshot(),
    ], 40);
    if (is_wp_error($res)) {
        return $res;
    }
    update_option('nudgeycart_secret_key', $res['secret_key'], false);
    update_option('nudgeycart_merchant_id', $res['merchant_id'], false);
    delete_transient('nudgeycart_config');
    return true;
}

/**
 * Merchant configuration from the API (PayPal client id, currency, guardrails), cached briefly
 * so storefront pages do not wait on the network.
 *
 * @return array|WP_Error
 */
function nudgeycart_config(bool $fresh = false)
{
    if (!$fresh && is_array($cached = get_transient('nudgeycart_config'))) {
        return $cached;
    }
    if (!get_option('nudgeycart_secret_key')) {
        $ok = nudgeycart_connect();
        if (is_wp_error($ok)) {
            return $ok;
        }
    }
    $cfg = nudgeycart_api('GET', '/v1/config', null, 15);
    if (is_wp_error($cfg) && ($cfg->get_error_data()['status'] ?? 0) === 401) {
        // The API forgot this demo store (e.g. redeployed with a fresh database): reconnect.
        $ok = nudgeycart_connect(true);
        $cfg = is_wp_error($ok) ? $ok : nudgeycart_api('GET', '/v1/config', null, 15);
    }
    if (!is_wp_error($cfg)) {
        set_transient('nudgeycart_config', $cfg, 10 * MINUTE_IN_SECONDS);
    }
    return $cfg;
}
