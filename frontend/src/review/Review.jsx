/**
 * Reviewer console (implementation.md Step 9).
 *
 * This screen exists because the system is honest about a measured fact: the
 * worst honest capture and the closest sibling produce overlapping distances,
 * so there is a band where no threshold is correct (see checks/binding.py).
 * Rather than pick a threshold and be quietly wrong, those gates come here.
 *
 * Two rules shape everything below.
 *
 * 1. The reviewer sees the triggering signal, never raw biometric data. No
 *    capture, no landmark coordinates, no face. Someone resolving "bad
 *    photograph or close relative?" does not need the biometrics to do it —
 *    they need the signal that objected and its measured limits. Shipping the
 *    face here would create a second copy of the most sensitive artefact in
 *    the system, in the least controlled place (context.md §11.8).
 *
 * 2. The reviewer is told what the machine could not decide, and why — never
 *    given a score and asked to rubber-stamp it. A console that displays
 *    "0.55 — approve?" manufactures consent for a decision nobody made.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Card, Chain, Hash, Mark, Spinner } from '../ui.jsx'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

/** A check that never ran is not a check that failed — say so distinctly. */
function checkVerdict(check) {
  if (!check.ran) return 'DID NOT RUN'
  return check.verdict ?? (check.passed ? 'PASS' : 'FAIL')
}

function timeLeft(expiresAt) {
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (Number.isNaN(ms)) return null
  if (ms <= 0) return 'expired'
  const m = Math.floor(ms / 60000)
  return m >= 1 ? `${m}m left` : `${Math.floor(ms / 1000)}s left`
}

function QueueItem({ gate, selected, onSelect }) {
  const left = timeLeft(gate.expires_at)
  return (
    <button
      className="queue-item"
      aria-current={selected}
      onClick={() => onSelect(gate.gate_id)}
    >
      <div className="between">
        <code className="mono dim">{gate.gate_id.slice(0, 8)}</code>
        <span
          className="mono"
          style={{ color: gate.expired ? 'var(--red)' : 'var(--ink-3)' }}
        >
          {left}
        </span>
      </div>
      <p style={{ margin: '6px 0 0', fontSize: 13, lineHeight: 1.45 }}>
        {gate.triggering_signal}
      </p>
    </button>
  )
}

function Checks({ checks }) {
  if (!checks?.length) {
    return <p className="muted small">No check evidence was recorded for this gate.</p>
  }
  return checks.map((c) => (
    <div key={c.check_no} className="check-row">
      <div>
        <div style={{ fontWeight: 600 }}>{c.name}</div>
        <div className="mono dim">check {c.check_no}</div>
      </div>
      <div>
        <Badge value={checkVerdict(c)} />
        {c.reason && (
          <p className="muted" style={{ margin: '7px 0 0', fontSize: 13 }}>{c.reason}</p>
        )}
        {c.limitations?.length > 0 && (
          <ul className="limits">
            {c.limitations.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        )}
      </div>
    </div>
  ))
}

/**
 * The chain result is rendered loudly when broken and quietly when intact.
 * A tamper-evident log whose failure state looks like its success state is
 * decorative, so a break gets colour, words and a visibly severed link.
 */
function Integrity({ chain, timeline }) {
  if (!chain) return null
  return (
    <>
      {chain.ok ? (
        <p className="muted small" style={{ margin: '0 0 12px' }}>
          <span style={{ color: 'var(--green)' }}>✓ Intact</span> — {chain.length} blocks,
          each hash covering the one before it. Nothing was edited after the fact.
        </p>
      ) : (
        <div className="alert" style={{ marginBottom: 14 }}>
          <strong style={{ color: 'var(--red)' }}>⚠ Audit chain is broken</strong>
          <p className="small" style={{ margin: '6px 0 0' }}>
            {chain.reason
              ? `${chain.reason.replace(/\.?$/, '.')} `
              : 'A hash does not match the record it covers. '}
            Do not authorise this gate. The history you are being shown cannot
            be trusted, which is a more serious finding than anything in it.
          </p>
        </div>
      )}
      <Chain events={timeline} brokenAt={chain.ok ? null : chain.broken_at} />
      <p className="dim small" style={{ margin: '4px 0 0' }}>
        Each block shows its own digest. The link between two blocks is the
        prev_hash pointer.
      </p>
    </>
  )
}

function Timeline({ events }) {
  if (!events?.length) return null
  return (
    <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
      {events.map((e, i) => {
        const refused = e.type === 'transition.refused'
        return (
          <li key={i} className="check-row" style={{ gridTemplateColumns: '76px 1fr' }}>
            <span className="mono dim">
              {e.at ? new Date(e.at).toLocaleTimeString() : ''}
            </span>
            <span style={{ fontSize: 13 }}>
              <span style={{ color: refused ? 'var(--red)' : 'var(--ink)' }}>
                {e.type}
              </span>
              {/* Three consecutive rows reading "evidence" tell a reviewer
                  nothing. The score is deliberately not shown: this screen
                  asks for a judgement, not agreement with a number. */}
              {e.type === 'evidence' && e.payload?.check_no != null && (
                <span className="muted"> check {e.payload.check_no}</span>
              )}
              {e.type === 'review' && e.payload?.reviewer_id && (
                <span className="muted">
                  {' '}{e.payload.decision} by {e.payload.reviewer_id}
                </span>
              )}
              {e.payload?.from && (
                <span className="muted"> {e.payload.from} → {e.payload.to}</span>
              )}
              {e.payload?.actor && (
                <span
                  className="mono"
                  style={{ marginLeft: 6, color: e.payload.actor === 'human' ? 'var(--indigo-bright)' : 'var(--ink-3)' }}
                >
                  {e.payload.actor}
                </span>
              )}
              {refused && e.payload?.reason && (
                <div className="small" style={{ color: 'var(--red)', marginTop: 3 }}>
                  refused: {e.payload.reason}
                </div>
              )}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

const Detail = React.forwardRef(function Detail({ packet, onResolve, busy, error }, ref) {
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem('stratum.reviewer') ?? ''
  )
  const [notes, setNotes] = useState('')

  useEffect(() => { setNotes('') }, [packet.gate_id])

  const submit = (decision) => {
    localStorage.setItem('stratum.reviewer', reviewer)
    onResolve(packet.gate_id, decision, reviewer, notes)
  }

  const named = reviewer.trim().length > 0
  const stale = packet.expired || !packet.decidable
  // A broken chain blocks approval but not rejection. The two are not
  // symmetric: authorising on a history that cannot be trusted is the
  // dangerous act, while refusing is the safe response to not knowing.
  const broken = packet.chain && packet.chain.ok === false
  const canApprove = named && !stale && !busy && !broken
  const canReject = named && !stale && !busy

  return (
    <section ref={ref} className="pane" style={{ padding: '24px 28px 72px' }}>
      <div className="between" style={{ marginBottom: 18 }}>
        <div>
          <h2 style={{ fontSize: 19 }}>
            Gate <code className="mono" style={{ fontSize: 15 }}>{packet.gate_id.slice(0, 18)}…</code>
          </h2>
          <div className="wrap" style={{ marginTop: 8, alignItems: 'center' }}>
            <Badge value={packet.state} />
            <span className="mono dim">{packet.mode}</span>
            {packet.expired && <Badge value="FAIL" title="expired" />}
          </div>
        </div>
        <Hash value={packet.chain?.head} chars={10} />
      </div>

      <Card title="Why this needs a person">
        {packet.reasons?.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.65 }}>
            {packet.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        ) : (
          <p style={{ margin: 0 }}>{packet.triggering_signal}</p>
        )}
        <hr className="rule" />
        <p className="dim small" style={{ margin: 0 }}>
          These are the words the decision layer recorded at the time, read back
          out of the audit chain — not recomputed now. You are seeing what was
          decided and on what basis.
        </p>
      </Card>

      <Card title="Checks">
        <Checks checks={packet.checks} />
      </Card>

      <Card title="Integrity">
        <Integrity chain={packet.chain} timeline={packet.timeline} />
      </Card>

      <Card title="History">
        <Timeline events={packet.timeline} />
      </Card>

      {packet.reviews?.length > 0 && (
        <Card title="Earlier rulings">
          {packet.reviews.map((r) => (
            <div key={r.id} className="row" style={{ paddingTop: 6 }}>
              <Badge value={r.decision === 'approve' ? 'PASS' : 'FAIL'} />
              <span style={{ fontSize: 13 }}>
                <strong>{r.reviewer_id}</strong>
                {r.notes && <span className="muted"> — {r.notes}</span>}
              </span>
            </div>
          ))}
        </Card>
      )}

      <Card title="Your ruling">
        <p className="muted small" style={{ marginTop: 0 }}>
          You are not confirming a score. You are deciding a question the
          evidence did not settle, and your name is recorded against it in the
          same tamper-evident chain as everything above.
        </p>
        <div className="stack" style={{ gap: 10, marginBottom: 12 }}>
          <input
            className="input"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="Your reviewer ID — required"
            aria-label="Reviewer ID"
          />
          <input
            className="input"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Why (optional, but it is the only record of your reasoning)"
            aria-label="Notes"
          />
        </div>

        {stale && (
          <div className="alert alert-amber" style={{ marginBottom: 12 }}>
            <p className="small" style={{ margin: 0 }}>
              {packet.expired
                ? 'This gate has expired. It can no longer be authorised — the person it belongs to must start again.'
                : `This gate is ${packet.state}; it is no longer awaiting a review.`}
            </p>
          </div>
        )}
        {broken && !stale && (
          <div className="alert" style={{ marginBottom: 12 }}>
            <p className="small" style={{ margin: 0 }}>
              Approval is disabled because the audit chain is broken. You can
              still reject: refusing is the safe response to a history that
              cannot be trusted.
            </p>
          </div>
        )}

        <div className="wrap">
          <button className="btn btn-primary" disabled={!canApprove}
                  onClick={() => submit('approve')}>
            {busy ? <Spinner /> : null} Approve — authorise
          </button>
          <button className="btn btn-danger" disabled={!canReject}
                  onClick={() => submit('reject')}>
            Reject
          </button>
        </div>

        {!named && (
          <p className="dim small" style={{ marginBottom: 0 }}>
            Enter your reviewer ID to rule. An anonymous review is not a review.
          </p>
        )}
        {error && (
          <p className="small" style={{ color: 'var(--red)', marginBottom: 0 }}>{error}</p>
        )}
      </Card>

      <p className="dim small">
        No capture, landmark or biometric value appears on this screen, by
        design. If resolving this gate seems to require seeing the person's
        face, the correct outcome is to reject and ask them to re-verify.
      </p>
    </section>
  )
})

export default function Review() {
  const [gates, setGates] = useState(null)
  const [selected, setSelected] = useState(null)
  const [packet, setPacket] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [queueError, setQueueError] = useState(null)
  const detailRef = useRef(null)

  const loadQueue = useCallback(async () => {
    try {
      const res = await fetch(`${API}/gates?state=REVIEW`)
      if (!res.ok) throw new Error(`queue unavailable (${res.status})`)
      const data = await res.json()
      setGates(data.gates)
      setQueueError(null)
      return data.gates
    } catch (e) {
      setQueueError(e.message)
      setGates([])
      return []
    }
  }, [])

  useEffect(() => {
    loadQueue()
    // Poll, because a gate can expire while the reviewer is reading it and a
    // console showing a stale queue invites rulings that will be refused.
    const t = setInterval(loadQueue, 15000)
    return () => clearInterval(t)
  }, [loadQueue])

  useEffect(() => {
    if (!selected) { setPacket(null); return }
    let live = true
    setError(null)
    fetch(`${API}/gates/${selected}/review`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`could not load gate (${r.status})`))))
      .then((p) => { if (live) setPacket(p) })
      .catch((e) => { if (live) { setError(e.message); setPacket(null) } })
    return () => { live = false }
  }, [selected])

  // On a narrow screen the panes stack, so the detail opens below the fold and
  // a tap looks like it did nothing. Bring it into view.
  useEffect(() => {
    if (packet && window.matchMedia('(max-width: 900px)').matches) {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [packet?.gate_id])

  const resolve = useCallback(async (gateId, decision, reviewerId, notes) => {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${API}/gates/${gateId}/review`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ reviewer_id: reviewerId, decision, notes }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        // A 409 is not a bug to hide: the state machine refused a move, and
        // the refusal is itself recorded. Show the reason it gave.
        throw new Error(body.detail ?? `ruling refused (${res.status})`)
      }
      const remaining = await loadQueue()
      setSelected(remaining.find((g) => g.gate_id !== gateId)?.gate_id ?? null)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }, [loadQueue])

  return (
    <main className="shell">
      <aside className="pane" style={{ borderRight: '1px solid var(--border)' }}>
        <div className="topbar">
          <Link to="/" style={{ color: 'inherit' }}><Mark /></Link>
          <span className="eyebrow" style={{ marginLeft: 'auto' }}>reviewer</span>
        </div>

        <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid var(--border)' }}>
          <h1 style={{ fontSize: 16 }}>Awaiting a human</h1>
          <p className="muted small" style={{ margin: '6px 0 0' }}>
            {gates === null
              ? 'Loading…'
              : `${gates.length} gate${gates.length === 1 ? '' : 's'} the checks could not settle. Oldest first.`}
          </p>
        </div>

        {queueError && (
          <div style={{ padding: 20 }}>
            <div className="alert"><p className="small" style={{ margin: 0 }}>{queueError}</p></div>
          </div>
        )}

        {gates?.length === 0 && !queueError && (
          <p className="muted" style={{ padding: '32px 20px', lineHeight: 1.6 }}>
            Nothing waiting. Every gate either passed all three checks or was
            refused outright — no judgement call is outstanding.
          </p>
        )}

        {gates?.map((g) => (
          <QueueItem
            key={g.gate_id} gate={g}
            selected={g.gate_id === selected} onSelect={setSelected}
          />
        ))}
      </aside>

      {packet
        ? <Detail ref={detailRef} packet={packet} onResolve={resolve} busy={busy} error={error} />
        : (
          <section className="pane" style={{ display: 'grid', placeItems: 'center', padding: 40 }}>
            <p className="muted" style={{ maxWidth: 340, textAlign: 'center' }}>
              {error ?? 'Select a gate to see the signal that referred it.'}
            </p>
          </section>
        )}
    </main>
  )
}
