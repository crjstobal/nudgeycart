<?php
/**
 * Plugin Name: NudgeyCart
 * Description: AI checkout closer. When a shopper is about to leave, NudgeyCart finds out why and offers the cheapest thing that closes the sale, paid with PayPal.
 * Version: 0.1.0
 * Author: NudgeyCart
 * License: AGPL-3.0-or-later
 * Requires Plugins: woocommerce
 */

if (!defined('ABSPATH')) {
    exit;
}

const NUDGEYCART_COST_META = 'nudgey_cost';

/* ---------- settings ---------- */

add_action('admin_init', function () {
    register_setting('nudgeycart', 'nudgeycart_api_base', ['default' => 'http://localhost:8797', 'sanitize_callback' => 'esc_url_raw']);
    register_setting('nudgeycart', 'nudgeycart_public_key', ['sanitize_callback' => 'sanitize_text_field']);
    register_setting('nudgeycart', 'nudgeycart_test_mode', ['default' => '1', 'sanitize_callback' => 'sanitize_text_field']);
});

add_action('admin_menu', function () {
    add_submenu_page('woocommerce', 'NudgeyCart', 'NudgeyCart', 'manage_woocommerce', 'nudgeycart', function () {
        ?>
        <div class="wrap">
            <h1>NudgeyCart</h1>
            <p>Connect your store to NudgeyCart. Set a <strong>cost</strong> on each product (Product data &gt; General) so offers never go below your margin floor.</p>
            <form method="post" action="options.php">
                <?php settings_fields('nudgeycart'); ?>
                <table class="form-table">
                    <tr><th>API base URL</th><td><input class="regular-text" name="nudgeycart_api_base" value="<?php echo esc_attr(get_option('nudgeycart_api_base')); ?>"></td></tr>
                    <tr><th>Public key</th><td><input class="regular-text" name="nudgeycart_public_key" value="<?php echo esc_attr(get_option('nudgeycart_public_key')); ?>"></td></tr>
                    <tr><th>Test mode</th><td><label><input type="checkbox" name="nudgeycart_test_mode" value="1" <?php checked(get_option('nudgeycart_test_mode'), '1'); ?>> Show the "Simulate exit intent" button with <code>?nudgey_demo</code></label></td></tr>
                </table>
                <?php submit_button(); ?>
            </form>
        </div>
        <?php
    });
});

/* ---------- product cost field (never sent to the browser) ---------- */

add_action('woocommerce_product_options_pricing', function () {
    woocommerce_wp_text_input([
        'id' => NUDGEYCART_COST_META,
        'label' => __('Unit cost (NudgeyCart)', 'nudgeycart') . ' (' . get_woocommerce_currency_symbol() . ')',
        'desc_tip' => true,
        'description' => __('What this product costs you. NudgeyCart uses it to keep every offer above your margin floor.', 'nudgeycart'),
        'data_type' => 'price',
    ]);
});

add_action('woocommerce_admin_process_product_object', function ($product) {
    if (isset($_POST[NUDGEYCART_COST_META])) {
        $product->update_meta_data(NUDGEYCART_COST_META, wc_format_decimal(wp_unslash($_POST[NUDGEYCART_COST_META])));
    }
});

/* ---------- storefront widget ---------- */

function nudgeycart_page_type()
{
    if (function_exists('is_order_received_page') && is_order_received_page()) {
        return 'order_received';
    }
    if (is_cart()) {
        return 'cart';
    }
    if (is_checkout()) {
        return 'checkout';
    }
    if (is_product()) {
        return 'product';
    }
    return 'other';
}

add_action('wp_enqueue_scripts', function () {
    $key = get_option('nudgeycart_public_key');
    if (!$key || is_admin()) {
        return;
    }
    $api = untrailingslashit(get_option('nudgeycart_api_base', 'http://localhost:8797'));
    $cfg = [
        'apiBase' => $api,
        'key' => $key,
        'cartUrl' => rest_url('wc/store/v1/cart'),
        'page' => nudgeycart_page_type(),
        'storeName' => get_bloginfo('name'),
        'testMode' => get_option('nudgeycart_test_mode') === '1',
    ];
    if ($cfg['page'] === 'order_received') {
        global $wp;
        $order = wc_get_order(absint($wp->query_vars['order-received'] ?? 0));
        if ($order) {
            $cfg['orderReceived'] = [
                'id' => $order->get_id(),
                'total' => (float) $order->get_total(),
                'nudgey' => (bool) $order->get_meta('nudgey_nudge_id'),
            ];
        }
    }
    wp_enqueue_script('nudgeycart', $api . '/widget.js', [], null, ['in_footer' => true, 'strategy' => 'defer']);
    wp_add_inline_script('nudgeycart', 'window.NudgeyCartConfig = ' . wp_json_encode($cfg) . ';', 'before');
});

/* ---------- after a NudgeyCart payment ---------- */

// The order was created server-side, so the shopper's session cart is still full: empty it.
add_action('template_redirect', function () {
    if (isset($_GET['nudgey_done']) && function_exists('WC') && WC()->cart) {
        WC()->cart->empty_cart();
    }
});

// Orders created through the API belong to a guest session Woo does not know about; the
// order key in the URL is proof enough for the thank-you page.
add_filter('woocommerce_order_email_verification_required', function ($required, $order) {
    return ($order && $order->get_meta('nudgey_nudge_id')) ? false : $required;
}, 10, 2);
