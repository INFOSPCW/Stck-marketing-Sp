/** @odoo-module **/
/**
 * Trading AI Advisor — Dashboard JS
 * Adds RSI colour coding and live price refresh hints.
 */

import { registry } from "@web/core/registry";
import { Component, onMounted } from "@odoo/owl";

// Colour-code RSI field values after form renders
function applyRsiColour() {
    document.querySelectorAll('.o_field_float[name="rsi"] span').forEach(el => {
        const val = parseFloat(el.textContent);
        if (!isNaN(val)) {
            el.classList.remove('rsi-overbought', 'rsi-oversold', 'rsi-neutral');
            if (val >= 70) el.classList.add('rsi-overbought');
            else if (val <= 30) el.classList.add('rsi-oversold');
            else el.classList.add('rsi-neutral');
        }
    });
}

// Run after each form render
document.addEventListener('DOMContentLoaded', () => {
    const observer = new MutationObserver(applyRsiColour);
    observer.observe(document.body, { childList: true, subtree: true });
    applyRsiColour();
});
