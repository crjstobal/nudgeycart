<?php
/**
 * One-call demo store for WordPress Playground: real, widely sold products (so the price match
 * lever finds genuine competitor prices), unit costs, flat-rate shipping and sane store options.
 *
 * Called from the Playground blueprint: nudgeycart_seed_demo_store();
 */

if (!defined('ABSPATH')) {
    exit;
}

/** name, price, unit cost, short description, image (assets/products/<image>.jpg) */
const NUDGEYCART_DEMO_PRODUCTS = [
    ['Lodge 10.25 Inch Cast Iron Skillet', '39.95', '14.00', 'Pre-seasoned cast iron that goes from stovetop to oven to campfire.', 'skillet'],
    ['Hydro Flask 32 oz Wide Mouth Water Bottle', '49.95', '22.00', 'Double-wall vacuum insulation: cold for 24 hours, hot for 12.', 'water-bottle'],
    ['Stanley Quencher H2.0 FlowState Tumbler 40 oz', '49.00', '21.00', 'Fits most cup holders, with a handle and a three-position lid.', 'tumbler-40oz'],
    ['Ember Mug 2 10 oz', '149.95', '82.00', 'Keeps your coffee at the exact temperature you choose.', 'smart-mug'],
    ['Fellow Stagg EKG Electric Pour-Over Kettle', '175.00', '98.00', 'Precision pour spout and variable temperature control.', 'kettle'],
    ['YETI Rambler 20 oz Tumbler', '38.00', '16.00', 'Stainless steel, dishwasher safe, MagSlider lid.', 'tumbler-20oz'],
    ['Bodum Chambord French Press 8 Cup', '45.00', '18.00', 'The classic French press, borosilicate glass and stainless steel.', 'french-press'],
    ['Moleskine Classic Notebook Large Ruled', '24.95', '9.00', 'Hard cover, 240 ruled pages, elastic closure.', 'notebook'],
];

/**
 * Copies a bundled product photo into the media library. No thumbnails are generated: resizing
 * eight images in a browser-hosted PHP (Playground) would slow the store's first boot.
 */
function nudgeycart_demo_image(string $slug, string $title): int
{
    $src = NUDGEYCART_DIR . "assets/products/{$slug}.jpg";
    if (!is_readable($src)) {
        return 0;
    }
    $upload = wp_upload_bits("nudgeycart-{$slug}.jpg", null, file_get_contents($src));
    if (!empty($upload['error'])) {
        return 0;
    }
    $id = wp_insert_attachment([
        'post_mime_type' => 'image/jpeg', 'post_title' => $title, 'post_status' => 'inherit',
    ], $upload['file']);
    if (is_wp_error($id) || !$id) {
        return 0;
    }
    $size = getimagesize($upload['file']);
    wp_update_attachment_metadata($id, [
        'width' => $size ? $size[0] : 800, 'height' => $size ? $size[1] : 800,
        'file' => _wp_relative_upload_path($upload['file']), 'sizes' => [],
    ]);
    update_post_meta($id, '_wp_attachment_image_alt', $title);
    return (int) $id;
}

function nudgeycart_seed_demo_store(): array
{
    update_option('blogname', 'Kettle & Co.');
    update_option('blogdescription', 'Kitchen and coffee gear we actually use');
    update_option('woocommerce_currency', 'USD');
    update_option('woocommerce_default_country', 'US:CA');
    update_option('woocommerce_store_address', '1 Market St');
    update_option('woocommerce_store_city', 'San Francisco');
    update_option('woocommerce_store_postcode', '94105');
    update_option('woocommerce_calc_taxes', 'no');
    update_option('woocommerce_default_customer_address', 'base');
    update_option('woocommerce_coming_soon', 'no');
    update_option('woocommerce_onboarding_profile', ['skipped' => true]);
    update_option('woocommerce_task_list_hidden', 'yes');
    update_option('woocommerce_show_marketplace_suggestions', 'no');
    update_option('woocommerce_allow_tracking', 'no');

    // Flat-rate shipping everywhere, so the shipping lever has something to waive.
    $zone = new WC_Shipping_Zone(0);
    if (!$zone->get_shipping_methods()) {
        $instance = $zone->add_shipping_method('flat_rate');
        update_option("woocommerce_flat_rate_{$instance}_settings", [
            'enabled' => 'yes', 'title' => 'Flat rate', 'tax_status' => 'none', 'cost' => '7.99',
        ]);
    }
    // Shoppers in the holdout group still need a normal way to pay.
    $cod = get_option('woocommerce_cod_settings', []);
    update_option('woocommerce_cod_settings', array_merge(is_array($cod) ? $cod : [], [
        'enabled' => 'yes', 'title' => 'Pay on delivery (demo)',
    ]));

    $created = 0;
    if (!wc_get_products(['limit' => 1, 'return' => 'ids'])) {
        foreach (NUDGEYCART_DEMO_PRODUCTS as [$name, $price, $cost, $desc, $image]) {
            $p = new WC_Product_Simple();
            $p->set_name($name);
            $p->set_regular_price($price);
            $p->set_short_description($desc);
            $p->set_description($desc);
            $p->set_sku('KC-' . strtoupper(substr(md5($name), 0, 6)));
            $p->update_meta_data(NUDGEYCART_COST_META, $cost);
            $p->set_status('publish');
            $image_id = nudgeycart_demo_image($image, $name);
            if ($image_id) {
                $p->set_image_id($image_id);
            }
            $p->save();
            $created++;
        }
    }
    update_option('nudgeycart_test_mode', '1');
    update_option('nudgeycart_demo_panel', '1');
    delete_option('nudgeycart_secret_key');
    $connected = nudgeycart_connect();
    return ['products' => $created, 'connected' => is_wp_error($connected) ? $connected->get_error_message() : true];
}
