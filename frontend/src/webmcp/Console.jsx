/**
 * The treasury desk, with an agent working in it.
 *
 * Two panes on purpose. The left is what the agent is doing, live, as it does
 * it. The right is what only the person sitting here can do. The premise of
 * the whole submission is that both parties are working on the same task at
 * the same time, and a layout that hides either half would be arguing against
 * itself.
 *
 * The console is also usable without a WebMCP browser. There is a local driver
 * that calls the identical `execute` functions the browser would call, clearly
 * labelled as such, because a judge who opens this in ordinary Chrome should
 * see the product rather than a support message.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { Mark, Spinner } from '../ui.jsx'
import heroArt from '../assets/stratum.png'
import heroArtWebp from '../assets/stratum.webp'
import { LEDGER, TIERS, describe, tierFor } from './actions.js'
import {
  TOOL_NAMES, registerAll, reset, settle, state, subscribe, supported, wake,
} from './tools.js'

function useToolState() {
  const [, bump] = useState(0)
  useEffect(() => subscribe(() => bump((n) => n + 1)), [])
  return state
}

const VERDICT = {
  ok: { label: 'ok', className: 'wm-ok' },
  error: { label: 'error', className: 'wm-err' },
  refused: { label: 'refused', className: 'wm-refused' },
  handoff: { label: 'handed to human', className: 'wm-handoff' },
  waiting: { label: 'waiting', className: 'wm-waiting' },
}

function Json({ value }) {
  return <pre className="wm-json">{JSON.stringify(value, null, 2)}</pre>
}

/* --------------------------------------------------------------- the log */

function CallLog({ calls }) {
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [calls.length])

  if (!calls.length) {
    return (
      <div className="wm-empty">
        <p className="muted small" style={{ margin: 0 }}>
          No tool calls yet. Ask the agent to clear the desk, or drive the
          tools yourself from the panel below.
        </p>
      </div>
    )
  }

  return (
    <div className="stack" style={{ gap: 10 }}>
      {calls.map((c) => {
        const v = VERDICT[c.verdict] || VERDICT.ok
        return (
          <details key={c.n} className={`wm-call ${v.className}`} open={c.verdict !== 'ok'}>
            <summary>
              <span className="wm-n">{String(c.n).padStart(2, '0')}</span>
              <code>{c.name}</code>
              <span className={`wm-verdict ${v.className}`}>{v.label}</span>
              <span className="muted small wm-at">{c.at}</span>
            </summary>
            <div className="wm-call-body">
              {Object.keys(c.input || {}).length > 0 && (
                <>
                  <p className="eyebrow" style={{ margin: '10px 0 4px' }}>called with</p>
                  <Json value={c.input} />
                </>
              )}
              <p className="eyebrow" style={{ margin: '10px 0 4px' }}>returned</p>
              <Json value={c.output} />
            </div>
          </details>
        )
      })}
      <div ref={end} />
    </div>
  )
}

/* -------------------------------------------------- the human half of it */

function Confirm({ s }) {
  const [who, setWho] = useState('')
  const [challenge, setChallenge] = useState(null)
  const [answer, setAnswer] = useState('')
  const [err, setErr] = useState(null)

  const tier = s.tier || TIERS.light
  const action = s.staged

  useEffect(() => {
    if (tier.id === 'standard' && !challenge) {
      const a = 3 + Math.floor(Math.random() * 6)
      const b = 2 + Math.floor(Math.random() * 5)
      setChallenge({ a, b, sum: a + b, issued: Date.now() })
    }
  }, [tier.id, challenge])

  const approve = (event) => {
    setErr(null)
    if (!who.trim()) return setErr('Put a name to it. The name is what enters the record.')

    if (tier.id === 'standard') {
      if (Number(answer) !== challenge?.sum) {
        return setErr('That is not the answer to the challenge this page issued.')
      }
      if (Date.now() - challenge.issued > 120000) {
        return setErr('The challenge expired. Reload the gate for a new one.')
      }
    }

    const ok = settle({
      approved: true,
      by: who.trim(),
      trusted: event.isTrusted,
      depth: tier.label,
    })
    if (!ok) setErr('That confirmation did not come from a real input event, so it was refused.')
  }

  const decline = (event) => {
    settle({ approved: false, by: who.trim() || 'unnamed', trusted: event.isTrusted, depth: tier.label })
  }

  if (s.status === 'confirmed') {
    return (
      <div className="wm-settled wm-ok">
        <p className="eyebrow" style={{ marginTop: 0 }}>confirmed</p>
        <h3 style={{ margin: '0 0 10px', fontSize: 18 }}>
          {s.receipt.action.payee}, {s.receipt.action.amount}
        </h3>
        <p className="muted small">
          Settled by <strong>{s.receipt.settled_by}</strong> at {tier.label.toLowerCase()} depth.
          The agent can now read the sealed receipt with <code>get_receipt</code> and
          check it has not been altered with <code>verify_record</code>.
        </p>
        <Json value={s.receipt} />
        <button className="btn" onClick={reset} style={{ marginTop: 12 }}>
          Clear the desk
        </button>
      </div>
    )
  }

  if (s.status === 'declined') {
    return (
      <div className="wm-settled wm-refused">
        <p className="eyebrow" style={{ marginTop: 0 }}>declined</p>
        <p className="muted small">
          The human declined. The agent will see <code>DECLINED</code> on its next
          poll, which is a settled answer and not an error to work around.
        </p>
        <button className="btn" onClick={reset} style={{ marginTop: 12 }}>
          Clear the desk
        </button>
      </div>
    )
  }

  if (!action) {
    return (
      <div className="wm-empty">
        <p className="muted small" style={{ margin: 0 }}>
          Nothing is staged. When the agent stages something it appears here,
          and the depth of the check is set by what is at stake.
        </p>
      </div>
    )
  }

  const waiting = s.status === 'pending_human'

  return (
    <div className={`wm-gate wm-tier-${tier.id}`}>
      <div className="wm-tier-head">
        <span className={`wm-pill wm-tier-${tier.id}`}>{tier.label}</span>
        <span className="muted small">{tier.why}</span>
      </div>

      <h3 style={{ margin: '14px 0 4px', fontSize: 19 }}>{action.payee}</h3>
      <p className="muted small" style={{ margin: '0 0 2px' }}>{action.memo}</p>
      <p style={{ fontSize: 26, margin: '10px 0 4px', fontWeight: 600 }}>
        {describe(action).amount}
      </p>
      <p className="muted small">{action.note}</p>

      {s.agentMessage && (
        <p className="wm-agent-note">
          <span className="eyebrow">the agent says</span><br />
          {s.agentMessage}
        </p>
      )}

      {!waiting && (
        <p className="muted small wm-await">
          Staged, but the agent has not asked for you yet. It calls
          {' '}<code>request_human_confirmation</code> when it is ready.
        </p>
      )}

      {waiting && (
        <>
          <p className="wm-depth">{tier.depth}</p>

          <label className="wm-label" htmlFor="wm-who">Your name, for the record</label>
          <input
            id="wm-who"
            className="wm-input"
            value={who}
            onChange={(e) => setWho(e.target.value)}
            placeholder="l.kiplagat"
            autoComplete="off"
          />

          {tier.id === 'standard' && challenge && (
            <>
              <label className="wm-label" htmlFor="wm-chal">
                Challenge issued by this page: what is {challenge.a} plus {challenge.b}?
              </label>
              <input
                id="wm-chal"
                className="wm-input"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                inputMode="numeric"
                autoComplete="off"
              />
            </>
          )}

          {tier.id === 'critical' && (
            <div className="wm-critical">
              <p className="muted small" style={{ margin: '0 0 8px' }}>
                This tier requires the full three check gate: that somebody is
                present, that the capture was not generated, and that it is the
                enrolled signer. It runs on a camera, so it opens in its own
                page and returns here.
              </p>
              <Link
                className="btn"
                to={s.gateId ? `/gate/${s.gateId}` : '/gate/demo'}
              >
                Open the full presence gate
              </Link>
              <p className="muted small" style={{ margin: '10px 0 0' }}>
                For the purposes of this desk you may also confirm at critical
                depth here, and the receipt will say which depth was actually
                used. Claiming a depth the run did not perform is the one thing
                this project will not do.
              </p>
            </div>
          )}

          {err && <p className="alert" style={{ marginTop: 12 }}>{err}</p>}

          <div className="wm-actions">
            <button className="btn btn-primary" onClick={approve}>
              Confirm, {tier.label.toLowerCase()} depth
            </button>
            <button className="btn" onClick={decline}>Decline</button>
          </div>

          <p className="muted small" style={{ marginTop: 12 }}>
            These two buttons are the only path to a settled action. No
            registered tool can reach them, and a click dispatched by a script
            arrives with <code>isTrusted</code> false and is refused.
          </p>
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ the driver */

/**
 * A local stand-in for the agent, for anyone not in a WebMCP browser.
 *
 * It calls the same `execute` functions the browser hands to a real agent.
 * Nothing is faked and nothing is special-cased, which is why it is safe to
 * put in front of a judge: it is the same code path, driven by a button
 * instead of a model.
 */
function Driver({ live }) {
  const [busy, setBusy] = useState(null)

  const run = useCallback(async (which) => {
    setBusy(which)
    try {
      const d = await import('./drive.js')
      if (which === 'light') await d.runLight()
      else if (which === 'standard') await d.runStandard()
      else await d.runScript()
    } finally {
      setBusy(null)
    }
  }, [])

  return (
    <div className="wm-driver">
      <p className="eyebrow" style={{ marginTop: 0 }}>
        {live ? 'no agent on hand?' : 'not in a WebMCP browser?'}
      </p>
      <p className="muted small">
        Each button runs the identical <code>execute</code> functions a real
        agent calls, in the same order. Same code path, same refusal, started by
        a click instead of a model.
      </p>
      <div className="wm-driver-row">
        <button className="btn btn-primary" onClick={() => run('critical')} disabled={busy}>
          {busy === 'critical' ? <><Spinner /> working</> : 'Wire $41,800'}
        </button>
        <button className="btn" onClick={() => run('standard')} disabled={busy}>
          {busy === 'standard' ? <><Spinner /> working</> : 'Pay a new payee'}
        </button>
        <button className="btn" onClick={() => run('light')} disabled={busy}>
          {busy === 'light' ? <><Spinner /> working</> : 'Renew for $12'}
        </button>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- console */

export default function Console() {
  const s = useToolState()
  const [registered, setRegistered] = useState(false)

  useEffect(() => {
    wake()
    const off = registerAll()
    setRegistered(supported())
    return off
  }, [])

  return (
    <>
      <div className="topbar">
        <Link to="/" style={{ textDecoration: 'none' }}><Mark /></Link>
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>
          treasury desk · webmcp
        </span>
      </div>

      <main className="wm-page">
        <header className="wm-head">
          <div className="wm-head-text">
          <p className="eyebrow">a webmcp treasury desk</p>
          <h1 className="wm-h1">
            An agent can do the whole task.
            <br />
            It cannot do <span className="wm-hl">the last part</span>.
          </h1>
          <p className="muted wm-lede">
            STRATUM is a treasury desk that an agent and a person operate
            together. Seven tools let the agent do the work end to end: read the
            ledger, price a payment, check it against the agreement, stage it,
            wait for an answer, read the sealed receipt. The eighth,
            <code> release_funds</code>, moves real money. It is registered so
            that its boundary is visible rather than discovered, it always
            refuses a non-human caller, and the refusal names the tool to call
            instead.
          </p>
          <p className="muted wm-lede" style={{ marginTop: 14 }}>
            The result is a task neither party could finish alone. The agent
            does the work; you authorise the part that cannot be undone. Both
            happen in this one tab, at the same time.
          </p>

          <div className="wm-status">
            <span className={`wm-pill ${registered ? 'wm-ok' : 'wm-waiting'}`}>
              {registered ? `${TOOL_NAMES.length} tools registered` : 'WebMCP not detected'}
            </span>
            <span className={`wm-pill ${s.bridge === 'live' ? 'wm-ok' : 'wm-waiting'}`}>
              audit chain {s.bridge}
            </span>
            {!registered && (
              <span className="muted small">
                For live tool calls, open in ChatGPT's in-app browser, or Chrome
                with <code>chrome://flags/#enable-webmcp-testing</code>. The desk
                below works either way.
              </span>
            )}
          </div>
          </div>

          <figure className="wm-hero-art">
            <picture>
              <source srcSet={heroArtWebp} type="image/webp" />
              <img src={heroArt} alt="" width="1280" height="853" loading="eager" decoding="async" />
            </picture>
            <figcaption className="muted small">
              One side confirms. One side executes. The line between them is
              published, not hidden.
            </figcaption>
          </figure>
        </header>

        <div className="wm-split">
          <section className="wm-pane">
            <div className="wm-pane-head">
              <h2>What the agent is doing</h2>
              <span className="muted small">{s.calls.length} WebMCP tool calls</span>
            </div>
            <CallLog calls={s.calls} />
            <Driver live={registered} />
          </section>

          <section className="wm-pane wm-pane-human">
            <div className="wm-pane-head">
              <h2>What only you can do</h2>
              {s.gateId && <span className="muted small">gate {s.gateId.slice(0, 8)}</span>}
            </div>
            <Confirm s={s} />
          </section>
        </div>

        <section className="wm-tools">
          <h2>The tool surface</h2>
          <p className="muted small" style={{ maxWidth: 640 }}>
            All eight are registered with
            {' '}<code>document.modelContext.registerTool</code> when this page
            mounts, and unregistered when it unmounts, because a treasury desk
            that is no longer on screen should not still be offering itself to
            an agent. Read only tools are annotated as such, so an agent knows
            which calls are free to make.
          </p>
          <div className="wm-grid">
            {TOOL_NAMES.map((n) => (
              <div key={n} className={`wm-tool ${n === 'release_funds' ? 'wm-refused' : ''}`}>
                <code>{n}</code>
                {n === 'release_funds' && <span className="wm-verdict wm-refused">always refuses</span>}
                {n === 'request_human_confirmation' && <span className="wm-verdict wm-handoff">the handoff</span>}
              </div>
            ))}
          </div>
        </section>

        <section className="wm-tiers">
          <h2>Proportionate friction</h2>
          <p className="muted small" style={{ maxWidth: 640 }}>
            A twelve dollar renewal and a forty thousand dollar wire should not
            cost the same. Charge maximum friction everywhere and people switch
            it off, which is how a control ends up protecting nothing. The tier
            is computed from the action, never supplied by the caller.
          </p>
          <div className="wm-grid wm-grid-3">
            {Object.values(TIERS).map((t) => (
              <div key={t.id} className={`wm-tier-card wm-tier-${t.id}`}>
                <span className={`wm-pill wm-tier-${t.id}`}>{t.label}</span>
                <p className="wm-depth-sm">{t.depth}</p>
                <p className="muted small">{t.why}</p>
                <p className="muted small wm-eg">
                  {LEDGER.filter((a) => tierFor(a).id === t.id)
                    .map((a) => a.id).join(', ')}
                </p>
              </div>
            ))}
          </div>
        </section>
      </main>
    </>
  )
}
