/**
 * The work an agent is allowed to do here, and the one moment it is not.
 *
 * This is a treasury desk. An agent holds the session, so it can already read
 * the ledger, price a payment, and stage it. What it cannot do is release the
 * money, because releasing money is the point at which the action stops being
 * reversible.
 *
 * The tiering below is the part that makes this a product rather than a
 * lecture. A twelve dollar renewal and a forty thousand dollar wire should not
 * cost the same friction: charge full friction for everything and people
 * switch it off, which is how a security control ends up protecting nothing.
 * So the depth of the human check is derived from what is actually at stake,
 * and derived here rather than accepted from the agent, because a caller that
 * can name its own risk tier has not been tiered at all.
 */

export const TIERS = {
  light: {
    id: 'light',
    label: 'Light',
    depth: 'A present human, in this tab, on a real input event.',
    why: 'Small, recurring, and already within a pattern this desk approves.',
  },
  standard: {
    id: 'standard',
    label: 'Standard',
    depth: 'A present human who answers a live challenge this page issues.',
    why: 'New counterparty, or enough money to be worth a script.',
  },
  critical: {
    id: 'critical',
    label: 'Critical',
    depth: 'Presence, authenticity, and binding to the enrolled signer.',
    why: 'Large, irreversible, or legally binding.',
  },
}

/**
 * Tier from the action itself.
 *
 * Deliberately boring and readable: a judge should be able to check the rule
 * against the demo in a few seconds, and an auditor should not have to run the
 * code to predict what it does. Note that the agent never supplies this. A
 * caller that can name its own risk tier has not been tiered at all.
 */
export function tierFor(action) {
  if (action.binding) return TIERS.critical
  if (action.value >= 10000) return TIERS.critical
  if (action.value >= 500) return TIERS.standard
  if (action.new_payee) return TIERS.standard
  return TIERS.light
}

export const LEDGER = [
  {
    id: 'inv-2291',
    kind: 'payment',
    payee: 'Kesho Logistics',
    memo: 'Freight, August',
    value: 184.5,
    currency: 'USD',
    new_payee: false,
    binding: false,
    note: 'Paid monthly for two years, always in this range.',
  },
  {
    id: 'inv-2304',
    kind: 'payment',
    payee: 'Nyati Fabrication',
    memo: 'Tooling deposit',
    value: 4200,
    currency: 'USD',
    new_payee: true,
    binding: false,
    note: 'First payment to this account. The bank details arrived by email.',
  },
  {
    id: 'inv-2310',
    kind: 'payment',
    payee: 'Halcyon Components',
    memo: 'Q4 supply, first tranche',
    value: 41800,
    currency: 'USD',
    new_payee: false,
    binding: false,
    note: 'Above the desk threshold. Cannot be recalled once released.',
  },
  {
    id: 'sub-0071',
    kind: 'renewal',
    payee: 'Meridian Analytics',
    memo: 'Seat licence, annual',
    value: 12,
    currency: 'USD',
    new_payee: false,
    binding: false,
    note: 'Auto-renewing. The kind of thing nobody reads.',
  },
  {
    id: 'con-0043',
    kind: 'signature',
    payee: 'Halcyon Components',
    memo: 'Master supply agreement, 24 months',
    value: 0,
    currency: 'USD',
    new_payee: false,
    binding: true,
    note: 'No money moves today. It commits the company for two years.',
  },
]

export function findInLedger(query) {
  if (!query) return LEDGER
  const q = String(query).toLowerCase()
  const hit = LEDGER.filter((a) =>
    [a.id, a.payee, a.memo, a.kind].join(' ').toLowerCase().includes(q))
  return hit.length ? hit : LEDGER
}

export function money(value, currency = 'USD') {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(value)
}

export function describe(action) {
  const tier = tierFor(action)
  return {
    id: action.id,
    kind: action.kind,
    payee: action.payee,
    memo: action.memo,
    amount: action.binding
      ? 'No money moves. A 24 month commitment.'
      : money(action.value, action.currency),
    value: action.value,
    new_payee: action.new_payee,
    risk_tier: tier.id,
    confirmation_depth: tier.depth,
    why_this_tier: tier.why,
    note: action.note,
  }
}
