/**
 * STRATUM's WebMCP tool surface.
 *
 * Agents are being handed real sessions on real accounts. Searching a
 * catalogue, filling a cart and booking a table are exactly what a tool
 * surface is for, and they are far more reliable through registered tools than
 * through a model guessing at a UI.
 *
 * This page is about the step after those: the moment an action stops being
 * reversible. There a web app has two options and both cost something real. It
 * can leave the capability in the page, in which case anything holding the
 * session can fire it. Or it can hide the capability, in which case the agent
 * cannot see the boundary, treats the failure as a bug, and starts looking for
 * a way around it. Hidden boundaries are what produce retries, scraped forms
 * and synthetic clicks.
 *
 * So `release_funds` below is registered. It is fully described, it appears in
 * the agent's tool list, and it always refuses. The refusal is a normal return
 * value rather than a thrown error, it says why, and it names the tool to call
 * instead. That is the whole idea: a tool surface should be able to teach an
 * agent where its authority ends, in the same structured channel it uses to
 * teach it everything else.
 *
 * What is left is a genuine collaboration. The agent reads the ledger, prices
 * the payment, stages it and asks for a human. The human, in this tab, is the
 * only party who can close it. Neither could finish the task alone.
 */

import { describe, findInLedger, LEDGER, tierFor } from './actions.js'
import { buildCart, cartSubtotal, knownCart, releaseCheckout, searchProducts, shop } from './shopify.js'

const API = import.meta.env.VITE_XANO_API_BASE || '/api'

/* ------------------------------------------------------------------ store */

/**
 * One place that knows what the agent has done, so the page can show it.
 *
 * A tool call the human cannot see is indistinguishable from no tool call at
 * all, and the entire premise here is that the two parties are working on the
 * same thing at the same time. So every call lands in this log, refusals
 * included, and the console renders it live.
 */
const listeners = new Set()

export const state = {
  calls: [],
  staged: null,
  gateId: null,
  status: 'idle',
  tier: null,
  receipt: null,
  bridge: 'checking',
  checkoutCartId: null,
  checkoutUrl: null,
}

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function log(name, input, output, verdict) {
  state.calls = [...state.calls, {
    n: state.calls.length + 1,
    name,
    input,
    output,
    verdict,
    at: new Date().toISOString().slice(11, 19),
  }]
  emit()
}

/* ---------------------------------------------------------------- backend */

/**
 * The verifier holds the audit chain and the real gate state machine.
 *
 * It is on a free instance that sleeps, so every call here is best effort and
 * nothing in the page waits on it. The boundary itself is enforced locally as
 * well as remotely, which means a cold backend degrades the evidence trail and
 * never the refusal.
 */
async function api(path, options = {}, ms = 6000) {
  const stop = new AbortController()
  const timer = setTimeout(() => stop.abort(), ms)
  try {
    const res = await fetch(`${API}${path}`, { ...options, signal: stop.signal })
    const json = await res.json().catch(() => null)
    return { ok: res.ok, status: res.status, json }
  } catch {
    return { ok: false, status: 0, json: null }
  } finally {
    clearTimeout(timer)
  }
}

export async function wake() {
  const r = await api('/health', {}, 4000)
  state.bridge = r.ok ? 'live' : 'waking'
  emit()
  if (!r.ok) {
    const again = await api('/health', {}, 60000)
    state.bridge = again.ok ? 'live' : 'offline'
    emit()
  }
}

function audit(event, detail) {
  if (!state.gateId) return
  api(`/gates/${state.gateId}/transition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ to: event, actor: 'agent', detail }),
  }).catch(() => {})
}

/* ------------------------------------------------------------------ tools */

export const TOOLS = [
  {
    name: 'list_pending_actions',
    description:
      'List everything on the treasury desk waiting to be actioned: vendor '
      + 'payments, renewals and agreements. Returns each item with the risk '
      + 'tier this site assigns it and the depth of human confirmation that '
      + 'tier requires. Read only.',
    annotations: { readOnlyHint: true },
    inputSchema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Optional filter over payee, memo, id or kind.',
        },
      },
    },
    execute: async ({ query } = {}) => {
      const items = findInLedger(query).map(describe)
      const out = {
        count: items.length,
        items,
        note: 'risk_tier is computed by this site from the action itself. It '
            + 'is not an input and cannot be supplied by a caller.',
      }
      log('list_pending_actions', { query: query ?? null }, out, 'ok')
      return out
    },
  },

  {
    name: 'inspect_action',
    description:
      'Read one pending action in full, including why it was placed in its '
      + 'risk tier and what would be required to confirm it. Read only.',
    annotations: { readOnlyHint: true },
    inputSchema: {
      type: 'object',
      properties: {
        action_id: { type: 'string', description: 'For example inv-2310.' },
      },
      required: ['action_id'],
    },
    execute: async ({ action_id }) => {
      const found = LEDGER.find((a) => a.id === action_id)
      if (!found) {
        const miss = {
          error: 'NO_SUCH_ACTION',
          message: `Nothing on the desk with id ${action_id}.`,
          known_ids: LEDGER.map((a) => a.id),
        }
        log('inspect_action', { action_id }, miss, 'error')
        return miss
      }
      const out = describe(found)
      log('inspect_action', { action_id }, out, 'ok')
      return out
    },
  },

  {
    name: 'stage_action',
    description:
      'Prepare a pending action for release: validate it, price it, and put it '
      + 'in front of the human at this desk. This is the whole of the work the '
      + 'agent can complete on its own. Staging moves no money and signs '
      + 'nothing, and can be undone by staging something else.',
    inputSchema: {
      type: 'object',
      properties: {
        action_id: { type: 'string', description: 'The action to stage.' },
        reason: {
          type: 'string',
          description: 'Why this is being staged now, shown to the human.',
        },
      },
      required: ['action_id'],
    },
    execute: async ({ action_id, reason }) => {
      const found = LEDGER.find((a) => a.id === action_id)
      if (!found) {
        const miss = {
          error: 'NO_SUCH_ACTION',
          known_ids: LEDGER.map((a) => a.id),
        }
        log('stage_action', { action_id }, miss, 'error')
        return miss
      }

      const tier = tierFor(found)
      state.staged = { ...found, reason: reason || null }
      state.tier = tier
      state.status = 'staged'
      state.receipt = null
      state.gateId = null
      emit()

      const gate = await api('/demo/gate', { method: 'POST' })
      if (gate.json?.id) {
        state.gateId = gate.json.id
        emit()
      }

      const out = {
        staged: describe(found),
        reason: reason || null,
        gate_id: state.gateId,
        risk_tier: tier.id,
        required_confirmation: tier.depth,
        next_tool: 'request_human_confirmation',
        note: 'Staged only. Nothing has moved. This action can only be '
            + 'completed by a human present in this browser tab.',
      }
      log('stage_action', { action_id, reason: reason ?? null }, out, 'ok')
      return out
    },
  },

  {
    name: 'release_funds',
    description:
      'Release a staged payment, or execute a staged signature. This tool is '
      + 'published so that its boundary is visible rather than discovered: it '
      + 'is real, it is understood, and it always refuses a non-human caller. '
      + 'Call request_human_confirmation instead.',
    inputSchema: {
      type: 'object',
      properties: {
        action_id: { type: 'string' },
        confirm: { type: 'boolean', description: 'Has no effect.' },
      },
      required: ['action_id'],
    },
    execute: async ({ action_id }) => {
      audit('agent.attempted_release', { action_id })

      const remote = await api('/agent/tools/sign_document', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gate_id: state.gateId, actor: 'agent' }),
      })

      const out = {
        refused: true,
        error: 'HUMAN_REQUIRED',
        tool: 'release_funds',
        action_id,
        message:
          'Refused because the caller is an agent. This is not a permissions '
          + 'misconfiguration and retrying will not change it. Releasing funds '
          + 'is irreversible, so it is reserved to a human who is present in '
          + 'this browser tab at the moment it happens.',
        next_tool: 'request_human_confirmation',
        next_tool_input: { action_id },
        server_said: remote.status
          ? `${remote.status} from the verifier`
          : 'verifier unreachable, refused locally',
        recorded: Boolean(state.gateId),
        note: 'The attempt was written to the audit chain. A refusal that '
            + 'leaves no trace looks identical to an agent that never tried.',
      }
      log('release_funds', { action_id }, out, 'refused')
      return out
    },
  },

  {
    name: 'search_products',
    description:
      'Search the connected Shopify store and return matching products with '
      + 'title, price, image and variant id. Read only. Runs against the '
      + 'Storefront API when a store is connected, and a demo catalogue '
      + 'otherwise.',
    annotations: { readOnlyHint: true },
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'What to search the catalogue for.' },
        limit: { type: 'number', description: 'Maximum results. Defaults to 5.' },
      },
      required: ['query'],
    },
    execute: async ({ query, limit }) => {
      const out = await searchProducts(query, limit || 5)
      log('search_products', { query, limit: limit || 5 }, out, 'ok')
      return out
    },
  },

  {
    name: 'build_cart',
    description:
      'Create a Shopify cart and add lines to it, then return the line items '
      + 'and subtotal. The cart checkout URL is deliberately withheld from '
      + 'this response: the Storefront Cart API cannot take a payment, and the '
      + 'checkout URL is the only route to one, so it is held in the page and '
      + 'released to the browser only after a human settles the gate. Call '
      + 'request_human_confirmation with the returned cart_id.',
    inputSchema: {
      type: 'object',
      properties: {
        lines: {
          type: 'array',
          description: 'The variants to add.',
          items: {
            type: 'object',
            properties: {
              variantId: { type: 'string' },
              quantity: { type: 'number' },
            },
            required: ['variantId'],
          },
        },
      },
      required: ['lines'],
    },
    execute: async ({ lines }) => {
      const out = await buildCart(lines)
      log('build_cart', { lines }, out, out.error ? 'error' : 'handoff')
      return out
    },
  },

  {
    name: 'request_human_confirmation',
    description:
      'Hand the staged action, or a built cart, to the human at this desk and '
      + 'open the confirmation gate in the page, at the depth the risk tier '
      + 'requires. Returns immediately with PENDING_HUMAN. The calling agent '
      + 'cannot resolve this itself. Poll check_confirmation to see the '
      + 'outcome. Pass action_id for a treasury action, or cart_id for a '
      + 'Shopify cart.',
    inputSchema: {
      type: 'object',
      properties: {
        action_id: { type: 'string' },
        cart_id: {
          type: 'string',
          description: 'A cart id returned by build_cart.',
        },
        message: {
          type: 'string',
          description: 'A short note to show the human, in your own words.',
        },
      },
    },
    execute: async ({ action_id, cart_id, message }) => {
      if (cart_id) {
        if (!knownCart(cart_id)) {
          const miss = {
            error: 'NO_SUCH_CART',
            message: 'No cart with that id was built in this page.',
            next_tool: 'build_cart',
          }
          log('request_human_confirmation', { cart_id }, miss, 'error')
          return miss
        }

        const subtotal = cartSubtotal(cart_id) ?? 0
        const asAction = {
          id: cart_id,
          kind: 'checkout',
          payee: `${shop.store} (Shopify)`,
          amount: subtotal,
          currency: 'USD',
          newPayee: true,
          note: 'Shopify checkout. Payment is taken by Shopify, never by this page.',
        }
        state.staged = asAction
        state.tier = tierFor(asAction)
        state.checkoutCartId = cart_id

        if (!state.gateId) {
          const gate = await api('/demo/gate', { method: 'POST' })
          if (gate.json?.id) state.gateId = gate.json.id
        }

        state.status = 'pending_human'
        state.agentMessage = message || null
        emit()
        audit('agent.requested_human', { cart_id })

        const out = {
          status: 'PENDING_HUMAN',
          cart_id,
          risk_tier: state.tier.id,
          awaiting: state.tier.depth,
          gate_id: state.gateId,
          resolvable_by_caller: false,
          message:
            'The gate is open in the page. Once a human here settles it, the '
            + 'browser is sent to Shopify\'s hosted checkout, where the card '
            + 'details are entered on Shopify\'s own domain. You will not '
            + 'receive that URL.',
          poll: 'check_confirmation',
        }
        log('request_human_confirmation', { cart_id, message: message ?? null }, out, 'handoff')
        return out
      }

      if (!action_id) {
        const miss = {
          error: 'NOTHING_TO_CONFIRM',
          message: 'Pass action_id for a treasury action, or cart_id for a cart.',
        }
        log('request_human_confirmation', {}, miss, 'error')
        return miss
      }

      if (!state.staged || state.staged.id !== action_id) {
        const found = LEDGER.find((a) => a.id === action_id)
        if (!found) {
          const miss = { error: 'NO_SUCH_ACTION', known_ids: LEDGER.map((a) => a.id) }
          log('request_human_confirmation', { action_id }, miss, 'error')
          return miss
        }
        state.staged = found
        state.tier = tierFor(found)
        if (!state.gateId) {
          const gate = await api('/demo/gate', { method: 'POST' })
          if (gate.json?.id) state.gateId = gate.json.id
        }
      }

      state.status = 'pending_human'
      state.agentMessage = message || null
      emit()
      audit('agent.requested_human', { action_id })

      const out = {
        status: 'PENDING_HUMAN',
        action_id,
        risk_tier: state.tier.id,
        awaiting: state.tier.depth,
        gate_id: state.gateId,
        resolvable_by_caller: false,
        message:
          'The gate is open in the page. A human present in this tab must '
          + 'complete it. You cannot complete it and no retry will change that.',
        poll: 'check_confirmation',
      }
      log('request_human_confirmation', { action_id, message: message ?? null }, out, 'handoff')
      return out
    },
  },

  {
    name: 'check_confirmation',
    description:
      'Check whether the human has settled the open confirmation. Returns '
      + 'PENDING_HUMAN, CONFIRMED or DECLINED. Read only, and safe to poll.',
    annotations: { readOnlyHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: async () => {
      const map = {
        idle: 'NOTHING_STAGED',
        staged: 'STAGED_NOT_YET_SENT',
        pending_human: 'PENDING_HUMAN',
        confirmed: 'CONFIRMED',
        declined: 'DECLINED',
      }
      const out = {
        status: map[state.status] || 'UNKNOWN',
        action_id: state.staged?.id ?? null,
        risk_tier: state.tier?.id ?? null,
        settled_by: state.receipt?.settled_by ?? null,
        next_tool: state.status === 'confirmed' ? 'get_receipt' : null,
      }
      log('check_confirmation', {}, out, state.status === 'confirmed' ? 'ok' : 'waiting')
      return out
    },
  },

  {
    name: 'get_receipt',
    description:
      'Retrieve the sealed record of a confirmed action: who confirmed it, at '
      + 'what depth, when, and the audit chain position it was written to. '
      + 'Read only.',
    annotations: { readOnlyHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: async () => {
      if (state.status !== 'confirmed' || !state.receipt) {
        const out = {
          error: 'NOT_CONFIRMED',
          status: state.status,
          message: 'There is no receipt because no human has confirmed anything yet.',
        }
        log('get_receipt', {}, out, 'error')
        return out
      }
      log('get_receipt', {}, state.receipt, 'ok')
      return state.receipt
    },
  },

  {
    name: 'verify_record',
    description:
      'Recompute the audit chain for this session and report whether the '
      + 'record has been altered since it was written. Read only.',
    annotations: { readOnlyHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: async () => {
      if (!state.gateId) {
        const out = { error: 'NO_GATE', message: 'Nothing has been staged in this session.' }
        log('verify_record', {}, out, 'error')
        return out
      }
      const r = await api(`/gates/${state.gateId}/verify_chain`, {}, 10000)
      const out = r.json || {
        error: 'VERIFIER_UNREACHABLE',
        message: 'The audit service did not answer in time.',
      }
      log('verify_record', {}, out, r.ok ? 'ok' : 'error')
      return out
    },
  },
]

/* --------------------------------------------------------------- register */

export const TOOL_NAMES = TOOLS.map((t) => t.name)

export function supported() {
  return typeof document !== 'undefined'
    && typeof document.modelContext?.registerTool === 'function'
}

/**
 * Register every tool, and unregister them when the console unmounts.
 *
 * Tools are scoped to the page that owns them. Leaving them registered after
 * the visitor has navigated away would offer an agent a treasury desk that is
 * no longer on screen, which is the same class of mistake as leaving the
 * capability in the page in the first place.
 */
export function registerAll() {
  if (!supported()) return () => {}
  const handles = TOOLS.map((tool) => {
    try {
      return document.modelContext.registerTool(tool)
    } catch {
      return null
    }
  })
  return () => {
    handles.forEach((h, i) => {
      try {
        if (h && typeof h.unregister === 'function') h.unregister()
        else document.modelContext.unregisterTool?.(TOOLS[i].name)
      } catch { /* the page is going away regardless */ }
    })
  }
}

/* ------------------------------------------- what only the human can do */

/**
 * Settle the open gate.
 *
 * Called from the console's own UI and never from a tool, which is the entire
 * point. `trusted` carries `event.isTrusted`, which the browser sets to false
 * for any click a script dispatches. It is not a complete defence on its own,
 * and it is not claimed as one, but it is a real property that a synthetic
 * click cannot forge from inside the page.
 */
export function settle({ approved, by, trusted, depth }) {
  if (!trusted) {
    log('human.confirm', { by }, {
      refused: true,
      error: 'UNTRUSTED_EVENT',
      message: 'This confirmation did not come from a real input event.',
    }, 'refused')
    return false
  }

  state.status = approved ? 'confirmed' : 'declined'
  if (approved) {
    state.receipt = {
      action: describe(state.staged),
      settled_by: by,
      decision: 'CONFIRMED',
      confirmation_depth: depth,
      at: new Date().toISOString(),
      gate_id: state.gateId,
      chain: state.gateId
        ? `written to the audit chain for gate ${state.gateId}`
        : 'recorded locally, verifier was unreachable',
    }

    // A settled cart is the only thing that can unseal the checkout URL, and
    // it goes straight to the browser rather than into any tool result.
    if (state.checkoutCartId) {
      const url = releaseCheckout(state.checkoutCartId)
      if (url) {
        state.checkoutUrl = url
        state.receipt.checkout = 'released to the browser, not to the agent'
      }
    }
  }
  emit()

  if (state.gateId) {
    api(`/gates/${state.gateId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        decision: approved ? 'approve' : 'reject',
        reviewer: by,
        note: `WebMCP confirmation at ${depth} depth`,
      }),
    }).catch(() => {})
  }
  return true
}

export function reset() {
  state.calls = []
  state.staged = null
  state.gateId = null
  state.status = 'idle'
  state.tier = null
  state.receipt = null
  state.checkoutCartId = null
  state.checkoutUrl = null
  emit()
}
