/**
 * Shopify Storefront, wired so the agent can shop and cannot pay.
 *
 * This is the same boundary as the treasury desk, drawn on somebody else's
 * infrastructure, and it is worth saying why that matters. On the treasury
 * side we enforce the line ourselves. Here the line is also structural: the
 * Storefront Cart API has no payment capability in it at all. A cart can be
 * created, added to and priced, and none of those operations can charge a
 * card. The only route to an actual payment is `cart.checkoutUrl`, a hosted
 * page on Shopify's own domain where the buyer enters their card details.
 *
 * So the rule this module keeps is small and total: `checkoutUrl` never enters
 * a tool return value. It is held in a module-private Map that no registered
 * `execute` function reads from, keyed by cart id, and it is handed to the
 * browser only after a human has settled the gate. The agent can fill the
 * cart, price it, and ask for a human. It cannot reach the pay button, and it
 * cannot read the address of the pay button either.
 *
 * The Storefront access token is public by design, which is why prompting for
 * one in the page is not a leak. It is scoped to reading a catalogue and
 * building carts. That is precisely the half of commerce that is safe to
 * delegate.
 */

const API_VERSION = '2026-01'

/* --------------------------------------------------------------- private */

/**
 * Checkout URLs, keyed by cart id.
 *
 * Deliberately module scoped and never exported. `build_cart` writes here and
 * returns the cart without it. Only `releaseCheckout`, called from the human
 * settlement path, reads it back out.
 */
const CHECKOUTS = new Map()

const listeners = new Set()

export const shop = {
  connected: false,
  mode: 'demo',
  store: 'stratum-demo',
  products: [],
  cart: null,
  error: null,
}

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribeShop(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/* ------------------------------------------------------------ demo stock */

/**
 * A small catalogue so the flow runs with no network and no store setup.
 *
 * A live Storefront token swaps this out entirely. It exists so a judge on a
 * conference network sees the boundary rather than a fetch error, which is the
 * thing actually being demonstrated.
 */
const DEMO_PRODUCTS = [
  {
    id: 'gid://shopify/Product/1',
    title: 'Field Notes, hardback, pack of three',
    price: { amount: '24.00', currencyCode: 'USD' },
    variantId: 'gid://shopify/ProductVariant/101',
    image: null,
    tags: ['stationery', 'notebook', 'paper'],
  },
  {
    id: 'gid://shopify/Product/2',
    title: 'Mechanical keyboard, 65 percent, tactile',
    price: { amount: '148.00', currencyCode: 'USD' },
    variantId: 'gid://shopify/ProductVariant/102',
    image: null,
    tags: ['keyboard', 'desk', 'input'],
  },
  {
    id: 'gid://shopify/Product/3',
    title: 'Desk lamp, warm, dimmable',
    price: { amount: '89.00', currencyCode: 'USD' },
    variantId: 'gid://shopify/ProductVariant/103',
    image: null,
    tags: ['lamp', 'light', 'desk'],
  },
  {
    id: 'gid://shopify/Product/4',
    title: 'Cable set, braided, one metre, pack of four',
    price: { amount: '32.00', currencyCode: 'USD' },
    variantId: 'gid://shopify/ProductVariant/104',
    image: null,
    tags: ['cable', 'usb', 'desk'],
  },
  {
    id: 'gid://shopify/Product/5',
    title: 'Monitor arm, single, clamp mount',
    price: { amount: '210.00', currencyCode: 'USD' },
    variantId: 'gid://shopify/ProductVariant/105',
    image: null,
    tags: ['monitor', 'arm', 'desk', 'mount'],
  },
]

/* ------------------------------------------------------------- transport */

async function storefront(query, variables, ms = 8000) {
  const stop = new AbortController()
  const timer = setTimeout(() => stop.abort(), ms)
  try {
    const res = await fetch(
      `https://${shop.store}.myshopify.com/api/${API_VERSION}/graphql.json`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Shopify-Storefront-Access-Token': shop.token,
        },
        body: JSON.stringify({ query, variables }),
        signal: stop.signal,
      },
    )
    const json = await res.json().catch(() => null)
    if (!res.ok || json?.errors) {
      return { ok: false, errors: json?.errors || [{ message: `HTTP ${res.status}` }] }
    }
    return { ok: true, data: json?.data }
  } catch (e) {
    return { ok: false, errors: [{ message: String(e?.message || e) }] }
  } finally {
    clearTimeout(timer)
  }
}

const PRODUCT_QUERY = `
  query search($q: String!, $n: Int!) {
    products(query: $q, first: $n) {
      edges {
        node {
          id
          title
          featuredImage { url altText }
          variants(first: 1) {
            edges { node { id price { amount currencyCode } } }
          }
        }
      }
    }
  }
`

const CART_CREATE = `
  mutation cartCreate {
    cartCreate {
      cart { id checkoutUrl }
      userErrors { field message }
    }
  }
`

const CART_LINES_ADD = `
  mutation cartLinesAdd($cartId: ID!, $lines: [CartLineInput!]!) {
    cartLinesAdd(cartId: $cartId, lines: $lines) {
      cart {
        id
        checkoutUrl
        cost { subtotalAmount { amount currencyCode } totalAmount { amount currencyCode } }
        lines(first: 50) {
          edges {
            node {
              id
              quantity
              merchandise {
                ... on ProductVariant {
                  id
                  title
                  price { amount currencyCode }
                  product { title }
                }
              }
            }
          }
        }
      }
      userErrors { field message }
    }
  }
`

/* --------------------------------------------------------------- connect */

export function connect({ store, token }) {
  const s = (store || '').trim().replace(/^https?:\/\//, '').replace(/\.myshopify\.com.*$/, '')
  const t = (token || '').trim()

  if (!s || !t) {
    shop.connected = true
    shop.mode = 'demo'
    shop.store = 'stratum-demo'
    shop.token = null
    shop.error = null
    shop.products = DEMO_PRODUCTS
    emit()
    return { mode: 'demo', store: shop.store }
  }

  shop.connected = true
  shop.mode = 'live'
  shop.store = s
  shop.token = t
  shop.error = null
  emit()
  return { mode: 'live', store: s }
}

export function disconnect() {
  shop.connected = false
  shop.mode = 'demo'
  shop.store = 'stratum-demo'
  shop.token = null
  shop.products = []
  shop.cart = null
  shop.error = null
  CHECKOUTS.clear()
  emit()
}

/* ----------------------------------------------------------------- reads */

function matches(p, q) {
  const needle = q.trim().toLowerCase()
  if (!needle) return true
  const hay = `${p.title} ${(p.tags || []).join(' ')}`.toLowerCase()
  return needle.split(/\s+/).some((w) => hay.includes(w))
}

export async function searchProducts(query, limit = 5) {
  if (shop.mode === 'live' && shop.token) {
    const r = await storefront(PRODUCT_QUERY, { q: query, n: limit })
    if (r.ok) {
      const items = (r.data?.products?.edges || []).map(({ node }) => {
        const v = node.variants?.edges?.[0]?.node
        return {
          id: node.id,
          title: node.title,
          price: v?.price || null,
          variantId: v?.id || null,
          image: node.featuredImage?.url || null,
        }
      })
      shop.products = items
      shop.error = null
      emit()
      return { source: 'storefront_api', store: shop.store, count: items.length, products: items }
    }
    shop.error = r.errors?.[0]?.message || 'Storefront API unreachable'
    emit()
  }

  const items = DEMO_PRODUCTS.filter((p) => matches(p, query)).slice(0, limit)
  const use = items.length ? items : DEMO_PRODUCTS.slice(0, limit)
  shop.products = use
  emit()
  return {
    source: 'demo_catalogue',
    store: shop.store,
    count: use.length,
    products: use.map(({ tags, ...rest }) => rest),
    note: shop.error
      ? `Live store unreachable (${shop.error}), served the demo catalogue instead.`
      : 'No live store connected, serving the demo catalogue.',
  }
}

/* ----------------------------------------------------------------- carts */

function money(n, currency = 'USD') {
  return { amount: n.toFixed(2), currencyCode: currency }
}

export async function buildCart(lines) {
  if (!Array.isArray(lines) || !lines.length) {
    return { error: 'NO_LINES', message: 'Pass at least one { variantId, quantity }.' }
  }

  if (shop.mode === 'live' && shop.token) {
    const made = await storefront(CART_CREATE, {})
    const cartId = made.data?.cartCreate?.cart?.id
    if (made.ok && cartId) {
      const added = await storefront(CART_LINES_ADD, {
        cartId,
        lines: lines.map((l) => ({ merchandiseId: l.variantId, quantity: l.quantity || 1 })),
      })
      const cart = added.data?.cartLinesAdd?.cart
      if (added.ok && cart) {
        // The one line this whole module exists to make true.
        CHECKOUTS.set(cart.id, cart.checkoutUrl)
        const out = {
          cart_id: cart.id,
          source: 'storefront_api',
          store: shop.store,
          lines: (cart.lines?.edges || []).map(({ node }) => ({
            title: node.merchandise?.product?.title || node.merchandise?.title,
            variantId: node.merchandise?.id,
            quantity: node.quantity,
            price: node.merchandise?.price,
          })),
          subtotal: cart.cost?.subtotalAmount,
        }
        shop.cart = out
        shop.error = null
        emit()
        return withBoundary(out)
      }
    }
    shop.error = made.errors?.[0]?.message || 'Storefront cart failed'
    emit()
  }

  const resolved = lines.map((l) => {
    const p = DEMO_PRODUCTS.find((d) => d.variantId === l.variantId)
      || shop.products.find((d) => d.variantId === l.variantId)
    return p ? { p, quantity: Math.max(1, l.quantity || 1) } : null
  }).filter(Boolean)

  if (!resolved.length) {
    return {
      error: 'NO_SUCH_VARIANT',
      message: 'None of those variant ids are in the catalogue.',
      known_variant_ids: DEMO_PRODUCTS.map((d) => d.variantId),
    }
  }

  const subtotal = resolved.reduce(
    (sum, { p, quantity }) => sum + Number(p.price.amount) * quantity, 0,
  )
  const cartId = `gid://shopify/Cart/demo-${Date.now().toString(36)}`

  CHECKOUTS.set(
    cartId,
    `https://${shop.store}.myshopify.com/cart/c/${cartId.split('/').pop()}`,
  )

  const out = {
    cart_id: cartId,
    source: 'demo_catalogue',
    store: shop.store,
    lines: resolved.map(({ p, quantity }) => ({
      title: p.title,
      variantId: p.variantId,
      quantity,
      price: p.price,
    })),
    subtotal: money(subtotal, resolved[0].p.price.currencyCode),
  }
  shop.cart = out
  emit()
  return withBoundary(out)
}

function withBoundary(cart) {
  return {
    ...cart,
    checkout_url: null,
    checkout_url_withheld: true,
    message:
      'Cart built. The checkout URL is deliberately not in this response. It is '
      + 'held in the page keyed by cart_id and released to the browser only '
      + 'after a human at this desk settles the gate. Call '
      + 'request_human_confirmation with this cart_id.',
    next_tool: 'request_human_confirmation',
    next_tool_input: { cart_id: cart.cart_id },
  }
}

export function cartSubtotal(cartId) {
  if (shop.cart?.cart_id !== cartId) return null
  return Number(shop.cart.subtotal?.amount || 0)
}

export function knownCart(cartId) {
  return CHECKOUTS.has(cartId)
}

/**
 * Read the withheld checkout URL back out.
 *
 * Called from the human settlement path only. Nothing on the tool surface
 * imports this, which is what makes the withholding real rather than a naming
 * convention.
 */
export function releaseCheckout(cartId) {
  return CHECKOUTS.get(cartId) || null
}

export { DEMO_PRODUCTS }
