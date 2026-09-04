/**
 * A local stand-in for the agent.
 *
 * Most judges will open this page in an ordinary browser first, where
 * `document.modelContext` does not exist and nothing would happen. That would
 * be a bad first thirty seconds for a project whose entire argument is visible
 * only once tools start being called.
 *
 * So this drives the same tools, through the same `execute` functions the
 * browser hands to a real agent. There is no second implementation and no
 * special case: the only difference is that a button starts it instead of a
 * model deciding to. It is labelled as such in the page, because a demo that
 * quietly simulates the thing it claims to prove is worth nothing.
 *
 * The script is also the honest shape of the interaction. A capable agent
 * really would try `release_funds` first: it is registered, it is described,
 * and reaching for the obvious tool is correct behaviour. What matters is what
 * it learns from being told no.
 */

import { TOOLS } from './tools.js'

const pause = (ms) => new Promise((r) => setTimeout(r, ms))

function tool(name) {
  const t = TOOLS.find((x) => x.name === name)
  if (!t) throw new Error(`no tool named ${name}`)
  return t.execute
}

export async function runScript(actionId = 'inv-2310') {
  await tool('list_pending_actions')({})
  await pause(700)

  await tool('inspect_action')({ action_id: actionId })
  await pause(700)

  await tool('stage_action')({
    action_id: actionId,
    reason: 'First tranche is due today and the terms match the agreement.',
  })
  await pause(900)

  // The agent reaches for the obvious tool. This is the beat the project is
  // about, and the refusal it gets back is a normal return value carrying the
  // remedy, not an exception it has to guess its way around.
  await tool('release_funds')({ action_id: actionId, confirm: true })
  await pause(1100)

  await tool('request_human_confirmation')({
    action_id: actionId,
    message: 'Terms check out against the supply agreement. Releasing this is '
           + 'yours to do, not mine.',
  })
  await pause(600)

  await tool('check_confirmation')({})
}

export async function runLight() {
  await tool('stage_action')({
    action_id: 'sub-0071',
    reason: 'Annual renewal, same as last year.',
  })
  await pause(600)
  await tool('request_human_confirmation')({
    action_id: 'sub-0071',
    message: 'Routine renewal. One tap and it is done.',
  })
}

export async function runStandard() {
  await tool('stage_action')({
    action_id: 'inv-2304',
    reason: 'Tooling deposit. Note this is a first payment to this account.',
  })
  await pause(600)
  await tool('request_human_confirmation')({
    action_id: 'inv-2304',
    message: 'New payee, and the bank details came in by email. Worth a look.',
  })
}
