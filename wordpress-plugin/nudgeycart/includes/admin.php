<?php
/**
 * wp-admin: the merchant dashboard (WooCommerce > NudgeyCart) and the orders list column.
 */

if (!defined('ABSPATH')) {
    exit;
}

const NUDGEYCART_AG_GRID = 'https://cdn.jsdelivr.net/npm/ag-grid-community@36.2.0/dist/ag-grid-community.min.js';
const NUDGEYCART_AG_CHARTS = 'https://cdn.jsdelivr.net/npm/ag-charts-community@14.2.0/dist/umd/ag-charts-community.js';

add_action('admin_menu', function () {
    $hook = add_submenu_page('woocommerce', 'NudgeyCart', 'NudgeyCart', 'manage_woocommerce', 'nudgeycart', 'nudgeycart_render_dashboard', 3);
    add_action("load-$hook", function () {
        // Developer setting: which NudgeyCart API this store talks to.
        if (isset($_POST['nudgeycart_api_base']) && check_admin_referer('nudgeycart_connection')) {
            update_option('nudgeycart_api_base', esc_url_raw(wp_unslash($_POST['nudgeycart_api_base'])));
            delete_option('nudgeycart_secret_key');
            delete_transient('nudgeycart_config');
            nudgeycart_connect();
            wp_safe_redirect(admin_url('admin.php?page=nudgeycart&tab=settings'));
            exit;
        }
    });
});

add_action('admin_enqueue_scripts', function ($hook) {
    if ($hook !== 'woocommerce_page_nudgeycart') {
        return;
    }
    wp_enqueue_style('nudgeycart-admin', NUDGEYCART_URL . 'assets/admin.css', [], NUDGEYCART_VERSION);
    wp_enqueue_script('ag-grid-community', NUDGEYCART_AG_GRID, [], null, true);
    wp_enqueue_script('ag-charts-community', NUDGEYCART_AG_CHARTS, [], null, true);
    wp_enqueue_script('nudgeycart-admin', NUDGEYCART_URL . 'assets/admin.js', ['ag-grid-community', 'ag-charts-community'], NUDGEYCART_VERSION, true);
    wp_add_inline_script('nudgeycart-admin', 'window.NudgeyCartAdmin = ' . wp_json_encode([
        'rest' => esc_url_raw(rest_url('nudgeycart/v1/')),
        'nonce' => wp_create_nonce('wp_rest'),
        'currency' => get_woocommerce_currency(),
        'shopUrl' => esc_url_raw(add_query_arg('nudgey_demo', '1', wc_get_page_permalink('shop'))),
        'enabled' => get_option('nudgeycart_enabled', '1') === '1',
    ]) . ';', 'before');
});

function nudgeycart_render_dashboard()
{
    $tab = isset($_GET['tab']) && $_GET['tab'] === 'settings' ? 'settings' : 'dashboard';
    ?>
    <div class="wrap nudgey-wrap">
        <div class="nudgey-head">
            <div class="nudgey-logo" aria-hidden="true">N</div>
            <div>
                <h1>NudgeyCart</h1>
                <p class="nudgey-sub" id="nudgey-sub">AI checkout closer, paid with PayPal</p>
            </div>
            <div class="nudgey-head-actions">
                <a class="button" id="nudgey-try" target="_blank" rel="noopener" href="#">Try it on your store</a>
                <button class="button button-primary" id="nudgey-invoice" disabled>Invoice fees via PayPal</button>
            </div>
        </div>
        <nav class="nav-tab-wrapper">
            <a href="<?php echo esc_url(admin_url('admin.php?page=nudgeycart')); ?>" class="nav-tab <?php echo $tab === 'dashboard' ? 'nav-tab-active' : ''; ?>">Dashboard</a>
            <a href="<?php echo esc_url(admin_url('admin.php?page=nudgeycart&tab=settings')); ?>" class="nav-tab <?php echo $tab === 'settings' ? 'nav-tab-active' : ''; ?>">Guardrails &amp; settings</a>
        </nav>
        <div id="nudgey-notice" class="notice inline" hidden><p></p></div>

        <?php if ($tab === 'dashboard') : ?>
        <div id="nudgey-dashboard" class="nudgey-dash">
            <section class="nudgey-kpis" id="nudgey-kpis" aria-label="Key figures"></section>
            <section class="nudgey-row">
                <div class="nudgey-card nudgey-span2"><h2>Revenue saved per day</h2><div class="nudgey-chart" id="chart-daily"></div></div>
                <div class="nudgey-card"><h2>Nudged vs holdout conversion</h2><div class="nudgey-chart" id="chart-ab"></div></div>
            </section>
            <section class="nudgey-row">
                <div class="nudgey-card"><h2>Why shoppers were leaving</h2><div class="nudgey-chart" id="chart-reasons"></div></div>
                <div class="nudgey-card nudgey-span2"><h2>What closed the sale</h2><div class="nudgey-chart" id="chart-levers"></div></div>
            </section>
            <section class="nudgey-card nudgey-grid-card">
                <div class="nudgey-grid-head">
                    <h2>Every decision the agent made</h2>
                    <div class="nudgey-filters" role="group" aria-label="Filter decisions">
                        <button class="button nudgey-chip is-on" data-filter="all">All</button>
                        <button class="button nudgey-chip" data-filter="paid">Saved sales</button>
                        <button class="button nudgey-chip" data-filter="skipped">Chose not to interrupt</button>
                        <button class="button nudgey-chip" data-filter="control">Holdout</button>
                    </div>
                </div>
                <div id="nudgey-grid" class="nudgey-grid"></div>
            </section>
        </div>
        <aside class="nudgey-drawer" id="nudgey-drawer" aria-hidden="true"></aside>
        <?php else : ?>
        <div id="nudgey-settings" class="nudgey-settings">
            <form id="nudgey-settings-form" class="nudgey-card">
                <h2>Guardrails</h2>
                <p class="description">The AI proposes, these rules decide. No offer ever goes below your margin floor, NudgeyCart's fee included. Products need a unit cost (Product data &gt; General) to get money offers.</p>
                <table class="form-table" role="presentation">
                    <tr><th scope="row"><label for="ng-enabled">Show offers to shoppers</label></th>
                        <td><input type="checkbox" id="ng-enabled" name="enabled"></td></tr>
                    <tr><th scope="row"><label for="ng-margin">Margin floor</label></th>
                        <td><input type="number" id="ng-margin" name="min_margin_pct" min="0" max="90" step="1" class="small-text"> %
                        <p class="description">Minimum gross margin on any order NudgeyCart closes.</p></td></tr>
                    <tr><th scope="row"><label for="ng-discount">Max discount</label></th>
                        <td><input type="number" id="ng-discount" name="max_discount_pct" min="0" max="90" step="1" class="small-text"> %
                        <p class="description">Cap for plain discounts. Price matches and free shipping only need to respect the margin floor. Under your free-shipping minimum, the agent first suggests an add-on that reaches it.</p></td></tr>
                    <tr><th scope="row"><label for="ng-holdout">Holdout group</label></th>
                        <td><input type="number" id="ng-holdout" name="control_pct" min="0" max="50" step="1" class="small-text"> %
                        <p class="description">Shoppers who are measured but never nudged. This is how the dashboard proves the uplift you pay for.</p></td></tr>
                    <tr><th scope="row"><label for="ng-billing">Billing email</label></th>
                        <td><input type="email" id="ng-billing" name="billing_email" class="regular-text">
                        <p class="description">NudgeyCart sends its success-fee invoices here through PayPal Invoicing.</p></td></tr>
                </table>
                <p><button class="button button-primary" type="submit">Save guardrails</button> <span class="nudgey-saved" id="nudgey-saved" hidden>Saved</span></p>
            </form>
            <div class="nudgey-card">
                <h2>Connection</h2>
                <p id="nudgey-conn" class="description">Checking…</p>
                <details>
                    <summary>Developer settings</summary>
                    <form method="post">
                        <?php wp_nonce_field('nudgeycart_connection'); ?>
                        <p><label>NudgeyCart API <input class="regular-text code" name="nudgeycart_api_base" value="<?php echo esc_attr(nudgeycart_api_base()); ?>"></label>
                        <button class="button">Save and reconnect</button></p>
                    </form>
                </details>
            </div>
        </div>
        <?php endif; ?>
    </div>
    <?php
}

/* ---------- orders list: which sales NudgeyCart closed ---------- */

function nudgeycart_order_column_label(): string
{
    return 'NudgeyCart';
}

function nudgeycart_order_column_value($order): void
{
    $order = $order instanceof WC_Order ? $order : wc_get_order($order);
    $lever = $order ? $order->get_meta('_nudgey_lever') : '';
    if ($lever) {
        $labels = ['price_match' => 'Price match', 'top_up' => 'Cart top-up', 'free_shipping' => 'Free shipping', 'discount' => 'Discount', 'reassurance' => 'Reassured'];
        printf('<mark class="order-status status-processing"><span>%s</span></mark>', esc_html('Saved: ' . ($labels[$lever] ?? $lever)));
    }
}

// HPOS orders screen.
add_filter('manage_woocommerce_page_wc-orders_columns', function ($cols) {
    $cols['nudgeycart'] = nudgeycart_order_column_label();
    return $cols;
});
add_action('manage_woocommerce_page_wc-orders_custom_column', function ($col, $order) {
    if ($col === 'nudgeycart') {
        nudgeycart_order_column_value($order);
    }
}, 10, 2);
// Legacy posts screen.
add_filter('manage_edit-shop_order_columns', function ($cols) {
    $cols['nudgeycart'] = nudgeycart_order_column_label();
    return $cols;
});
add_action('manage_shop_order_posts_custom_column', function ($col, $post_id) {
    if ($col === 'nudgeycart') {
        nudgeycart_order_column_value($post_id);
    }
}, 10, 2);
