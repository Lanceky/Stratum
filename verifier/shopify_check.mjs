import assert from 'node:assert'
import {
  buildCart, connect, knownCart, releaseCheckout, searchProducts, shop,
} from '../frontend/src/webmcp/shopify.js'

let pass = 0
const ok = (name, cond) => {
  assert.ok(cond, name)
  pass += 1
  console.log('  ok  ' + name)
}

connect({})
ok('connect with no credentials falls back to demo', shop.mode === 'demo')

const found = await searchProducts('desk', 5)
ok('search_products returns products', found.products.length > 0)
ok('search_products reports its source', found.source === 'demo_catalogue')
ok('every product carries a variantId', found.products.every((p) => p.variantId))
ok('every product carries a price', found.products.every((p) => p.price?.amount))
ok('search does not leak internal tags', found.products.every((p) => !('tags' in p)))

const picks = found.products.slice(0, 2).map((p) => ({ variantId: p.variantId, quantity: 1 }))
const cart = await buildCart(picks)

ok('build_cart returns a cart_id', typeof cart.cart_id === 'string')
ok('build_cart returns line items', cart.lines.length === 2)
ok('build_cart returns a subtotal', Number(cart.subtotal.amount) > 0)

const expected = found.products.slice(0, 2)
  .reduce((n, p) => n + Number(p.price.amount), 0)
ok('subtotal is the sum of the lines', Number(cart.subtotal.amount) === expected)

// The invariant the whole module exists for.
const blob = JSON.stringify(cart)
ok('checkout_url is null in the tool response', cart.checkout_url === null)
ok('response is marked as withholding it', cart.checkout_url_withheld === true)
ok('no myshopify checkout link anywhere in the payload', !blob.includes('/cart/c/'))
ok('no key named checkoutUrl in the payload', !blob.includes('checkoutUrl'))
ok('the refusal names the next tool', cart.next_tool === 'request_human_confirmation')
ok('the next tool input is supplied', cart.next_tool_input.cart_id === cart.cart_id)

// But the page itself can still get it, after a human settles.
ok('the page knows the cart', knownCart(cart.cart_id) === true)
const url = releaseCheckout(cart.cart_id)
ok('the page can release the checkout url', typeof url === 'string' && url.includes('myshopify.com'))
ok('an unknown cart releases nothing', releaseCheckout('gid://shopify/Cart/nope') === null)

const bad = await buildCart([{ variantId: 'gid://shopify/ProductVariant/999' }])
ok('unknown variants are rejected', bad.error === 'NO_SUCH_VARIANT')
const empty = await buildCart([])
ok('an empty cart is rejected', empty.error === 'NO_LINES')

console.log(`\n${pass} checks passed`)
