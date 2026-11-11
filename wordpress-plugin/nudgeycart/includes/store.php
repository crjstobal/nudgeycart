<?php
/**
 * Store side: product unit cost, the trusted cart, paid orders, and the storefront widget.
 */

if (!defined('ABSPATH')) {
    exit;
}

/* ---------- product cost field (never sent to the browser) ---------- */

add_action('woocommerce_product_options_pricing', function () {
    woocommerce_wp_text_input([
        'id' => NUDGEYCART_COST_META,
        'label' => __('Unit cost (NudgeyCart)', 'nudgeycart') . ' (' . get_woocommerce_currency_symbol() . ')',
        'desc_tip' => true,
        'description' => __('What this product costs you. NudgeyCart uses it to keep every offer above your margin floor. Products without a cost never get a money offer.', 'nudgeycart'),
        'data_type' => 'price',
    ]);
});

add_action('woocommerce_admin_process_product_object', function ($product) {
    if (isset($_POST[NUDGEYCART_COST_META])) {
        $product->update_meta_data(NUDGEYCART_COST_META, wc_format_decimal(wp_unslash($_POST[NUDGEYCART_COST_META])));
    }
});

/* ---------- the cart, priced by the store ---------- */

/** Make the shopper's WooCommerce session and cart available inside REST requests. */
function nudgeycart_load_cart(): bool
{
    if (!function_exists('WC')) {
        return false;
    }
    if (null === WC()->cart && function_exists('wc_load_cart')) {
        wc_load_cart();
    }
    return null !== WC()->cart;
}

/**
 * The cart as NudgeyCart sees it: prices and unit costs straight from the catalogue,
 * shipping from WooCommerce's own calculation. The browser only ever sends signals.
 */
function nudgeycart_build_cart(): array
{
    $lines = [];
    if (!nudgeycart_load_cart()) {
        return ['currency' => get_woocommerce_currency(), 'shipping' => '0', 'lines' => []];
    }
    WC()->cart->calculate_totals();
    foreach (WC()->cart->get_cart() as $item) {
        $p = $item['data'];
        if (!$p instanceof WC_Product) {
            continue;
        }
        // Variations carry their own price; the cost may sit on the parent.
        $cost = $p->get_meta(NUDGEYCART_COST_META);
        if ($cost === '' && $p->get_parent_id()) {
            $cost = get_post_meta($p->get_parent_id(), NUDGEYCART_COST_META, true);
        }
        $lines[] = [
            'product_id' => $p->get_id(),
            'name' => $p->get_name(),
            'sku' => $p->get_sku() ?: null,
            'qty' => (int) $item['quantity'],
            'unit_price' => wc_format_decimal($p->get_price(), 2),
            'unit_cost' => $cost !== '' ? wc_format_decimal($cost, 2) : null,
        ];
    }
    $cart = [
        'currency' => get_woocommerce_currency(),
        'shipping' => wc_format_decimal(WC()->cart->get_shipping_total(), 2),
        'lines' => $lines,
    ];
    // Under the store's free-shipping minimum: tell the agent which products could close the gap.
    $min = nudgeycart_free_shipping_min();
    $subtotal = (float) WC()->cart->get_subtotal();
    if ($min > 0 && $subtotal < $min && (float) $cart['shipping'] > 0) {
        $cart['free_shipping_min'] = wc_format_decimal($min, 2);
        $cart['addons'] = nudgeycart_addon_candidates($min - $subtotal, $subtotal, array_column($lines, 'product_id'));
    }
    return $cart;
}

/** The store's own free-shipping minimum: the lowest "minimum order amount" of its free shipping methods. */
function nudgeycart_free_shipping_min(): float
{
    $forced = get_option('nudgeycart_free_shipping_min', '');
    if ($forced !== '') {
        return (float) $forced;
    }
    $min = 0.0;
    $zones = array_map(fn($z) => new WC_Shipping_Zone($z['id']), WC_Shipping_Zones::get_zones());
    $zones[] = new WC_Shipping_Zone(0);
    foreach ($zones as $zone) {
        foreach ($zone->get_shipping_methods(true) as $method) {
            if ($method->id === 'free_shipping' && in_array($method->get_option('requires'), ['min_amount', 'either'], true)) {
                $amount = (float) $method->get_option('min_amount');
                $min = ($min === 0.0 || $amount < $min) ? $amount : $min;
            }
        }
    }
    return $min;
}

/**
 * Catalogue products that would lift the cart over the free-shipping minimum on their own:
 * in stock, with a known unit cost, not in the cart, and not dearer than the cart itself.
 */
function nudgeycart_addon_candidates(float $gap, float $subtotal, array $in_cart): array
{
    $out = [];
    $products = wc_get_products(['status' => 'publish', 'type' => 'simple', 'stock_status' => 'instock',
        'limit' => 60, 'orderby' => 'date', 'order' => 'DESC', 'exclude' => array_map('intval', $in_cart)]);
    foreach ($products as $p) {
        $price = (float) $p->get_price();
        $cost = $p->get_meta(NUDGEYCART_COST_META);
        if ($cost === '' || $price < $gap || $price > $subtotal || !$p->is_purchasable()) {
            continue;
        }
        $out[] = [
            'product_id' => $p->get_id(), 'name' => $p->get_name(), 'sku' => $p->get_sku() ?: null,
            'unit_price' => wc_format_decimal($price, 2), 'unit_cost' => wc_format_decimal($cost, 2),
            'image' => $p->get_image_id() ? wp_get_attachment_url($p->get_image_id()) : null,
        ];
    }
    usort($out, fn($a, $b) => (float) $a['unit_price'] <=> (float) $b['unit_price']);
    return array_slice($out, 0, 8);
}

/* ---------- paid order from a NudgeyCart capture ---------- */

/**
 * Create the WooCommerce order for a payment PayPal already captured.
 *
 * @param array $res Response of POST /v1/nudges/{id}/capture.
 * @return WC_Order|WP_Error
 */
function nudgeycart_create_paid_order(string $nudge_id, array $res)
{
    $cap = $res['capture'];
    $offer = $res['offer'];
    try {
        $order = wc_create_order(['customer_id' => get_current_user_id(), 'created_via' => 'nudgeycart']);
        if (is_wp_error($order)) {
            return $order;
        }
        foreach ($res['lines'] as $l) {
            $product = wc_get_product((int) $l['product_id']);
            if (!$product) {
                throw new Exception('product ' . (int) $l['product_id'] . ' no longer exists');
            }
            $line_total = (float) $l['unit_price'] * (int) $l['qty'];
            $order->add_product($product, (int) $l['qty'], ['subtotal' => $line_total, 'total' => $line_total]);
        }
        $ship = new WC_Order_Item_Shipping();
        $ship->set_method_title((float) $offer['shipping'] > 0 ? __('Shipping', 'nudgeycart') : __('Free shipping (NudgeyCart)', 'nudgeycart'));
        $ship->set_method_id('flat_rate');
        $ship->set_total(wc_format_decimal($offer['shipping'], 2));
        $order->add_item($ship);
        if ((float) $offer['discount'] > 0) {
            $fee = new WC_Order_Item_Fee();
            $fee->set_name($offer['lever'] === 'price_match' ? __('Price match (NudgeyCart)', 'nudgeycart') : __('NudgeyCart offer', 'nudgeycart'));
            $fee->set_amount('-' . wc_format_decimal($offer['discount'], 2));
            $fee->set_total('-' . wc_format_decimal($offer['discount'], 2));
            $fee->set_tax_status('none');
            $order->add_item($fee);
        }
        $addr = [
            'first_name' => $cap['first_name'] ?? '', 'last_name' => $cap['last_name'] ?? '',
            'address_1' => $cap['address']['address_1'] ?? '', 'address_2' => $cap['address']['address_2'] ?? '',
            'city' => $cap['address']['city'] ?? '', 'state' => $cap['address']['state'] ?? '',
            'postcode' => $cap['address']['postcode'] ?? '', 'country' => $cap['address']['country'] ?? '',
        ];
        $order->set_address($addr + ['email' => $cap['email'] ?? ''], 'billing');
        $order->set_address($addr, 'shipping');
        $order->set_payment_method('paypal');
        $order->set_payment_method_title('PayPal (NudgeyCart)');
        $order->update_meta_data('_nudgey_nudge_id', $nudge_id);
        $order->update_meta_data('_nudgey_lever', $offer['lever']);
        $order->update_meta_data('_nudgey_fee', $res['fee'] ?? '');
        $order->calculate_totals(false);
        $order->payment_complete($cap['capture_id']);
        $order->add_order_note(sprintf(
            /* translators: 1: lever, 2: PayPal capture id */
            __('Sale closed by NudgeyCart (%1$s). Paid with PayPal, capture %2$s.', 'nudgeycart'),
            $offer['lever'], $cap['capture_id']
        ));
        $order->save();
        return $order;
    } catch (Throwable $e) {
        return new WP_Error('nudgeycart_order', $e->getMessage());
    }
}

// Orders created by NudgeyCart belong to a guest session: the order key in the URL is enough
// proof for the thank-you page.
add_filter('woocommerce_order_email_verification_required', function ($required, $order) {
    return ($order && $order->get_meta('_nudgey_nudge_id')) ? false : $required;
}, 10, 2);

/* ---------- holdout measurement: natural conversions ---------- */

add_action('woocommerce_thankyou', function ($order_id) {
    $order = wc_get_order($order_id);
    if (!$order || $order->get_meta('_nudgey_nudge_id') || $order->get_meta('_nudgey_conversion_sent')) {
        return;
    }
    $visitor = isset($_COOKIE['nudgey_vid']) ? sanitize_text_field(wp_unslash($_COOKIE['nudgey_vid'])) : '';
    if ($visitor && get_option('nudgeycart_secret_key')) {
        nudgeycart_api('POST', '/v1/conversions', [
            'visitor_id' => $visitor, 'order_ref' => (string) $order_id, 'total' => (float) $order->get_total(),
        ], 10);
        $order->update_meta_data('_nudgey_conversion_sent', '1');
        $order->save();
    }
});

/* ---------- storefront widget ---------- */

function nudgeycart_page_type(): string
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
    if (is_admin() || get_option('nudgeycart_enabled', '1') !== '1') {
        return;
    }
    $cfg = nudgeycart_config();
    if (is_wp_error($cfg)) {
        return; // Never break the storefront because NudgeyCart is unreachable.
    }
    $page = nudgeycart_page_type();
    $product = null;
    if ($page === 'product' && ($p = wc_get_product(get_queried_object_id()))) {
        $product = ['id' => $p->get_id(), 'name' => $p->get_name()];
    }
    wp_enqueue_script('nudgeycart', NUDGEYCART_URL . 'assets/widget.js', [], NUDGEYCART_VERSION, ['in_footer' => true, 'strategy' => 'defer']);
    wp_add_inline_script('nudgeycart', 'window.NudgeyCartConfig = ' . wp_json_encode([
        'rest' => esc_url_raw(rest_url('nudgeycart/v1/')),
        'nonce' => wp_create_nonce('wp_rest'),
        'storeCartUrl' => esc_url_raw(rest_url('wc/store/v1/cart')),
        'page' => $page,
        'product' => $product,
        'storeName' => wp_specialchars_decode(get_bloginfo('name'), ENT_QUOTES),
        'paypalClientId' => $cfg['paypal_client_id'],
        'currency' => $cfg['currency'],
        'sandbox' => (bool) $cfg['sandbox'],
        'testMode' => get_option('nudgeycart_test_mode') === '1' && !empty($cfg['test_mode']),
        // Playground demo stores show a scenario panel so judges can try every kind of exit.
        'demoPanel' => get_option('nudgeycart_demo_panel') === '1' && !empty($cfg['test_mode']),
        'priceMatch' => !empty($cfg['price_match']),
    ]) . ';', 'before');
});
