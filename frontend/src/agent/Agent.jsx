/**
 * The agent console.
 *
 * Every other screen in this project shows a human being verified. This one
 * shows the thing that verification is *for*: an agent, holding valid
 * credentials, reaching for the one step it may not take — and the refusal
 * being written down.
 *
 * Nothing here is simulated. Each row is a real request to the real API, and
 * the 409s are the server's answer, not a script's. That matters, because the
 * whole claim rests on the refusal being structural rather than performed.
 */

import React, { useCallback, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { Hash, Mark, Spinner } from '../ui.jsx'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

const S = {
  page: { maxWidth: 760, margin: '0 auto', padding: '48px 24px 96px' },
  call: { borderLeft: '2px solid var(--border)', paddingLeft: 16 },
  refused: { borderLeft: '2px solid var(--red)' },
  allowed: { borderLeft: '2px solid var(--indigo-bright)' },
  body: { color: 'var(--ink-3)', fontSize: 12, paddingLeft: 18, marginTop: 10 },
}

/**
 * One step in the run.
 *
 * `expect` is not decoration. Stating the expected status before the call is
 * made is what turns the run from a narration into an assertion: if the server
 * ever answered 200 to step 2, the page would say so in amber rather than
 * quietly reporting a success.
 */
const SCRIPT = [
  {
    key: 'open',
    title: 'The agent opens a gate',
    line: 'A payment run needs authorising. The agent asks for a human — which is the one thing it is supposed to do.',
    method: 'POST', path: () => '/demo/gate', body: null, expect: 201,
  },
  {
    key: 'sign',
    title: 'The agent tries to sign it itself',
    line: 'Same credentials, same session, one field different. In an ordinary audit log this is indistinguishable from the human, because the log records the principal and the agent is holding it.',
    method: 'POST', path: (id) => `/gates/${id}/transition`,
    body: { to: 'SIGNED', actor: 'agent' }, expect: 409,
  },
  {
    key: 'challenge',
    title: 'The agent does the part that is its to do',
    line: 'The boundary is around the signature, not around the agent. It may summon the challenge. It may not answer it.',
    method: 'POST', path: (id) => `/gates/${id}/transition`,
    body: { to: 'CHALLENGED', actor: 'agent' }, expect: 200,
  },
  {
    key: 'capture',
    title: 'The agent tries to manufacture the capture',
    line: 'Injection, refused at the state machine rather than left to the sensor to catch. Frames the agent generated itself are never scored, because it cannot reach the state that would hold them.',
    method: 'POST', path: (id) => `/gates/${id}/transition`,
    body: { to: 'CAPTURED', actor: 'agent' }, expect: 409,
  },
  {
    key: 'audit',
    title: 'What is left behind',
    line: 'Both attempts are in the append-only chain, each naming the actor and the move it reached for.',
    method: 'GET', path: (id) => `/gates/${id}/audit`, body: null, expect: 200,
  },
]

function Call({ step, result, pending }) {
  const unexpected = result && result.status !== step.expect
  const style = {
    ...S.call,
    ...(result ? (result.status === 409 ? S.refused : S.allowed) : {}),
  }

  return (
    <div style={style}>
      <p className="mono small" style={{ margin: 0 }}>
        <span className="muted">{step.method}</span> {result?.path ?? step.path('…')}
        {step.body && <span className="muted"> {JSON.stringify(step.body)}</span>}
      </p>
      {result ? (
        <p className="mono small" style={{ margin: '4px 0 0' }}>
          <strong style={{ color: result.status === 409 ? 'var(--red)' : 'var(--ink)' }}>
            {result.status}
          </strong>{' '}
          <span className="muted">
            {result.status === 409 ? 'refused' : 'ok'} · {result.ms}ms
          </span>
          {unexpected && (
            <span style={{ color: 'var(--amber)' }}> · expected {step.expect}</span>
          )}
        </p>
      ) : pending ? (
        // The prediction is shown before the call, not after. Stated up front it
        // is a claim the run can falsify; revealed afterwards it would only ever
        // agree with whatever came back.
        <p className="mono small muted" style={{ margin: '4px 0 0' }}>
          expects {step.expect}
        </p>
      ) : (
        <p className="mono small muted" style={{ margin: '4px 0 0' }}><Spinner /></p>
      )}
    </div>
  )
}

/** The server's own words, never this page's paraphrase of them. */
function Verdict({ step, result }) {
  if (!result) return null
  const d = result.json ?? {}

  if (result.status === 409) {
    const detail = d.detail ?? {}
    return (
      <div style={S.body}>
        <p style={{ margin: 0, color: 'var(--red)' }}><strong>{detail.reason}</strong></p>
        <p style={{ margin: '6px 0 0' }}>
          Refused at <span className="mono">{detail.from} → {detail.to}</span>, and written to
          the chain as <span className="mono">transition.refused</span> before the error was
          raised — so the attempt survives whether or not anyone was watching for it.
        </p>
      </div>
    )
  }

  if (step.key === 'open') {
    return (
      <p style={S.body}>
        Gate <Hash value={d.id} chars={8} /> created, state <strong>{d.state}</strong>.
      </p>
    )
  }

  if (step.key === 'challenge') {
    return (
      <p style={S.body}>
        Allowed. State is now <strong>{d.state}</strong>. Nothing about that move requires a
        person, so nothing about it is refused.
      </p>
    )
  }

  if (step.key === 'audit') {
    const refused = (d.events ?? []).filter((e) => e.type === 'transition.refused')
    return (
      <div style={S.body}>
        <p style={{ margin: '0 0 8px' }}>
          {d.events?.length} events recorded, {refused.length} of them refusals:
        </p>
        {refused.map((e) => {
          let p = {}
          try { p = JSON.parse(e.payload ?? '{}') } catch { /* shown as-is below */ }
          return (
            <p key={e.hash} className="mono small" style={{ margin: '0 0 4px' }}>
              <span style={{ color: 'var(--red)' }}>{p.actor}</span>
              {' reached for '}<strong>{p.to}</strong>{'  '}
              <Hash value={e.hash} chars={10} />
            </p>
          )
        })}
      </div>
    )
  }

  return null
}

export default function Agent() {
  const [results, setResults] = useState({})
  const [step, setStep] = useState(-1)
  const [running, setRunning] = useState(false)
  const [gateId, setGateId] = useState(null)
  const [chain, setChain] = useState(null)
  const [error, setError] = useState(null)
  const idRef = useRef(null)

  const run = useCallback(async () => {
    setResults({}); setGateId(null); setChain(null); setError(null)
    setRunning(true)
    idRef.current = null

    try {
      for (const [i, s] of SCRIPT.entries()) {
        setStep(i)
        const path = s.path(idRef.current)
        const started = performance.now()
        const res = await fetch(`${API}${path}`, {
          method: s.method,
          headers: s.body ? { 'Content-Type': 'application/json' } : undefined,
          body: s.body ? JSON.stringify(s.body) : undefined,
        })
        const json = await res.json().catch(() => null)
        const ms = Math.round(performance.now() - started)

        if (s.key === 'open') {
          if (!json?.id) throw new Error(`could not open a gate (${res.status})`)
          idRef.current = json.id
          setGateId(json.id)
        }

        setResults((prev) => ({ ...prev, [s.key]: { status: res.status, json, ms, path } }))
        await new Promise((r) => setTimeout(r, 600))
      }

      const v = await fetch(`${API}/gates/${idRef.current}/verify_chain`)
      setChain(await v.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }, [])

  return (
    <>
      <div className="topbar">
        <Link to="/" style={{ textDecoration: 'none' }}><Mark /></Link>
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>agent console</span>
      </div>
      <main style={S.page}>
      <h1 style={{ fontSize: 28, margin: '10px 0 12px' }}>An agent reaching for a signature</h1>
      <p className="muted" style={{ maxWidth: 620 }}>
        An agent can be handed the credentials to move money, sign contracts and close
        tickets. What it cannot be handed is a face in front of a camera. Below, an agent
        with a valid session tries to authorise its own work — twice — against the live API.
        The refusals are the server's, not this page's.
      </p>

      <div style={{ margin: '28px 0' }}>
        <button className="btn btn-primary" onClick={run} disabled={running}>
          {running ? <><Spinner /> running</> : chain ? 'Run it again' : 'Run the agent'}
        </button>
      </div>

      {error && <p className="alert">{error}</p>}

      <div className="stack" style={{ gap: 30 }}>
        {SCRIPT.map((s, i) => {
          const pending = i > step
          return (
            <section key={s.key} className={pending ? 'step-pending' : undefined}>
              <p className="eyebrow" style={{ marginBottom: 6 }}>step {i + 1}</p>
              <h3 style={{ fontSize: 16, margin: '0 0 6px' }}>{s.title}</h3>
              <p className="muted small" style={{ margin: '0 0 12px', maxWidth: 620 }}>{s.line}</p>
              <Call step={s} result={results[s.key]} pending={pending} />
              <Verdict step={s} result={results[s.key]} />
            </section>
          )
        })}
      </div>

      {chain && (
        <div className="card" style={{ marginTop: 36 }}>
          <p className="eyebrow" style={{ marginBottom: 8 }}>chain</p>
          <p style={{ margin: '0 0 8px' }}>
            {chain.ok ? (
              <>Verified over {chain.length} events. Head <Hash value={chain.head} chars={12} />.
                {' '}A refusal is evidence, not damage.</>
            ) : (
              <span style={{ color: 'var(--red)' }}>
                Broken at event {chain.broken_at}: {chain.reason}
              </span>
            )}
          </p>
          <p className="muted small" style={{ margin: 0 }}>
            Nothing above moved the gate any closer to being signed. It is still waiting on a
            person — which is the only way it can ever be signed at all.
          </p>
          <div style={{ marginTop: 18, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Link className="btn btn-primary" to={`/gate/${gateId}`}>Hand it to a human →</Link>
            <Link className="btn" to="/review">Reviewer console</Link>
          </div>
        </div>
      )}
      </main>
    </>
  )
}
