/**
 * The demo is one story told in three sittings, and this is the only place
 * that knows the order.
 *
 * It was previously known in two places: a `PATH` array that drew the home
 * page, and the individual pages, which each ended wherever their own work
 * finished. That is why the sequence only half existed. The home page said
 * "each step exists because the one before it failed to settle", the agent
 * page handed its gate to the camera, and then the trail went cold: the gate
 * page told the visitor a reviewer would look at their request and gave them
 * no way to go and watch it happen. To reach step three you had to go back to
 * the start and pick it off a list, which is precisely the menu the ordering
 * was introduced to avoid.
 *
 * So the steps live here, and the handoffs are derived from them rather than
 * written into each page. A step that cannot say what follows it cannot leave
 * a visitor stranded at the end of it.
 */

const KEY = 'stratum.gate'

export const STEPS = [
  {
    to: '/agent',
    n: '01',
    short: 'agent refused',
    name: 'An agent is refused',
    line: 'Valid credentials, correct request, still refused, and the refusal '
        + 'written to the chain as evidence.',
    outcome: 'A gate opens, and it needs a person.',
  },
  {
    to: '/gate/demo',
    n: '02',
    short: 'a person answers',
    name: 'A person answers',
    line: 'Three checks: someone is present, the capture is not generated, and '
        + 'it is the enrolled signer.',
    outcome: 'Two checks settle. The third cannot.',
  },
  {
    to: '/review',
    n: '03',
    short: 'a human rules',
    name: 'A reviewer settles it',
    line: 'A sibling and a bad photograph overlap measurably, so those gates '
        + 'reach a named human.',
    outcome: 'A ruling, and a sealed certificate.',
  },
]

/**
 * The gate the visitor is currently walking through.
 *
 * Session storage rather than a URL parameter or a context provider. The URL
 * is wrong because the visitor may arrive at step two directly from the home
 * page, with no gate to name yet. A provider is wrong because the pages are
 * separate route trees that already fetch their own state, and threading a
 * gate id through all of them to be read in two places would be a lot of
 * machinery for one string.
 *
 * Session rather than local: a gate is a demo run, and a run that resumed
 * silently in a window opened the next day would attach the visitor to a gate
 * whose reasoning they never saw.
 */
export function remember(gateId) {
  if (!gateId) return
  try { sessionStorage.setItem(KEY, gateId) } catch { /* private mode */ }
}

export function recall() {
  try { return sessionStorage.getItem(KEY) } catch { return null }
}

/**
 * Where step three should look.
 *
 * The reviewer console holds a queue, and a visitor arriving from the camera
 * has one gate in it they care about. Landing them on the queue and letting
 * them find it turns a handoff into a search.
 */
export function reviewLink(gateId) {
  const id = gateId ?? recall()
  return id ? `/review?gate=${id}` : '/review'
}
