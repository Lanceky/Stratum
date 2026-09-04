/**
 * The Shopify connect panel.
 *
 * It sits under the agent's call log because it is the second half of the same
 * argument. On the treasury side we enforce the boundary ourselves. Here
 * Shopify enforces most of it for us: their Cart API has no payment capability
 * in it, so an agent holding a cart is structurally unable to charge anything.
 * The only route to a payment is the checkout URL, and this page withholds
 * that from every tool response.
 *
 * The token prompt is safe to put in a page. A Storefront access token is
 * public by design and scoped to reading a catalogue and building carts, which
 * is exactly the half of commerce that is safe to hand an agent.
 */

import React, { useEffect, useState } from 'react'

import { connect, disconnect, shop, subscribeShop } from './shopify.js'

function useShop() {
  const [, bump] = useState(0)
  useEffect(() => subscribeShop(() => bump((n) => n + 1)), [])
  return shop
}

export default function ShopifyPanel() {
  const s = useShop()
  const [open, setOpen] = useState(false)
  const [store, setStore] = useState('')
  const [token, setToken] = useState('')

  function submit(e) {
    e.preventDefault()
    connect({ store, token })
    setOpen(false)
    setToken('')
  }

  if (!s.connected) {
    return (
      <div className="wm-shop">
        {!open ? (
          <div className="wm-shop-cta">
            <button type="button" className="btn btn-connect" onClick={() => setOpen(true)}>
              Connect to Shopify
            </button>
            <span className="wm-shop-badge">cart-safe, checkout-gated</span>
            <p className="muted small wm-shop-why">
              The Storefront Cart API has no payment capability in it, so an
              agent can fill a cart and cannot charge it. The checkout URL is
              the only route to a payment, and it never appears in a tool
              response.
            </p>
          </div>
        ) : (
          <form className="wm-shop-form" onSubmit={submit}>
            <label className="wm-field">
              <span className="muted small">Store domain</span>
              <input
                value={store}
                onChange={(e) => setStore(e.target.value)}
                placeholder="your-store (leave blank for the demo store)"
                autoComplete="off"
              />
            </label>
            <label className="wm-field">
              <span className="muted small">Storefront public access token</span>
              <input
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="leave blank to use the demo catalogue"
                autoComplete="off"
              />
            </label>
            <p className="muted small">
              A Storefront token is public by design. Leave both blank and the
              desk serves a five item demo catalogue, so the flow runs with no
              store and no network.
            </p>
            <div className="wm-shop-actions">
              <button type="submit" className="btn btn-primary">
                {store && token ? 'Connect store' : 'Use demo store'}
              </button>
              <button type="button" className="btn" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    )
  }

  return (
    <div className="wm-shop">
      <div className="wm-shop-live">
        <div>
          <span className="wm-pill wm-ok">
            {s.mode === 'live' ? `connected: ${s.store}` : 'demo catalogue'}
          </span>
          <span className="wm-shop-badge">cart-safe, checkout-gated</span>
        </div>
        <button type="button" className="btn" onClick={disconnect}>
          Disconnect
        </button>
      </div>

      {s.error && (
        <p className="muted small">
          Storefront API said: {s.error}. Falling back to the demo catalogue.
        </p>
      )}

      {!!s.products.length && (
        <ul className="wm-shop-list">
          {s.products.map((p) => (
            <li key={p.variantId || p.id}>
              <span>{p.title}</span>
              <code>
                {p.price ? `${p.price.currencyCode} ${p.price.amount}` : ''}
              </code>
            </li>
          ))}
        </ul>
      )}

      {s.cart && (
        <div className="wm-shop-cart">
          <p className="muted small">
            Cart {s.cart.cart_id.split('/').pop()} ·{' '}
            {s.cart.lines.reduce((n, l) => n + l.quantity, 0)} items ·{' '}
            subtotal {s.cart.subtotal.currencyCode} {s.cart.subtotal.amount}
          </p>
          <p className="muted small">
            The checkout URL for this cart is held in the page. It was not
            returned to the agent, and it is released to the browser only after
            the gate is settled.
          </p>
        </div>
      )}
    </div>
  )
}
