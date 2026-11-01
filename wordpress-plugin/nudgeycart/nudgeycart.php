<?php
/**
 * Plugin Name: NudgeyCart
 * Description: AI checkout closer for WooCommerce. When a shopper is about to leave, NudgeyCart reads why from their behaviour and offers the cheapest thing that closes the sale (a real competitor price match, free shipping, a discount inside your margin floor), paid with PayPal right in the dialog.
 * Version: 0.2.0
 * Author: NudgeyCart
 * License: AGPL-3.0-or-later
 * Requires at least: 6.5
 * Requires PHP: 8.1
 * Requires Plugins: woocommerce
 * WC requires at least: 8.0
 */

if (!defined('ABSPATH')) {
    exit;
}

define('NUDGEYCART_VERSION', '0.2.0');
define('NUDGEYCART_FILE', __FILE__);
define('NUDGEYCART_DIR', plugin_dir_path(__FILE__));
define('NUDGEYCART_URL', plugin_dir_url(__FILE__));
const NUDGEYCART_COST_META = 'nudgey_cost';
const NUDGEYCART_DEFAULT_API = 'http://localhost:8797';

require_once NUDGEYCART_DIR . 'includes/api.php';
require_once NUDGEYCART_DIR . 'includes/store.php';
require_once NUDGEYCART_DIR . 'includes/rest.php';
require_once NUDGEYCART_DIR . 'includes/admin.php';
require_once NUDGEYCART_DIR . 'includes/demo-store.php';

// Orders are created through the CRUD API, so High-Performance Order Storage is fine.
add_action('before_woocommerce_init', function () {
    if (class_exists(\Automattic\WooCommerce\Utilities\FeaturesUtil::class)) {
        \Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility('custom_order_tables', NUDGEYCART_FILE, true);
    }
});

// A new store connects itself to NudgeyCart on activation (demo merchant in test mode).
register_activation_hook(__FILE__, function () {
    if (!get_option('nudgeycart_api_base')) {
        update_option('nudgeycart_api_base', NUDGEYCART_DEFAULT_API);
    }
    if (!get_option('nudgeycart_test_mode')) {
        update_option('nudgeycart_test_mode', '1');
    }
});
