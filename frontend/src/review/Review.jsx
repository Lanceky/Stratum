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

import React, { useCallback, useEffect, useState } from 'react'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

const INK = '#e8eaf0'
const MUTED = 'rgba(232,234,240,0.62)'
const LINE = 'rgba(232,234,240,0.12)'

const S = {
  page: {
    minHeight: '100vh', background: '#0b0d12', color: INK,
    fontFamily: 'system-ui, -apple-system, sans-serif',
    display: 'grid', gridTemplateColumns: 'minmax(300px, 380px) 1fr',
    alignItems: 'stretch',
  },
  queue: { borderRight: `1px solid ${LINE}`, overflowY: 'auto', maxHeight: '100vh' },
  queueHead: {
    padding: '20px 20px 12px', borderBottom: `1px solid ${LINE}`,
    position: 'sticky', top: 0, background: '#0b0d12', zIndex: 1,
  },
  h1: { margin: 0, fontSize: 17, letterSpacing: 0.2 },
  sub: { margin: '6px 0 0', fontSize: 12.5, color: MUTED, lineHeight: 1.5 },
  item: {
    display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
    padding: '14px 20px', border: 0, borderBottom: `1px solid ${LINE}`,
    background: 'transparent', color: INK, font: 'inherit',
  },
  itemOn: { background: 'rgba(79,124,255,0.13)', boxShadow: 'inset 3px 0 0 #4f7cff' },
  mono: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11.5 },
  signal: { margin: '6px 0 0', fontSize: 13, lineHeight: 1.45 },
  detail: { padding: '24px 28px 64px', overflowY: 'auto', maxHeight: '100vh' },
  card: {
    border: `1px solid ${LINE}`, borderRadius: 12, padding: 18,
    marginBottom: 18, background: 'rgba(255,255,255,0.02)',
  },
  cardHead: { margin: '0 0 10px', fontSize: 12, letterSpacing: 1.1, color: MUTED, textTransform: 'uppercase' },
  row: { display: 'flex', gap: 12, alignItems: 'baseline', padding: '9px 0', borderTop: `1px solid ${LINE}` },
  btn: {
    padding: '11px 20px', fontSize: 14, borderRadius: 9, border: 0,
    cursor: 'pointer', fontWeight: 600,
  },
  approve: { background: '#2f9e5e', color: '#fff' },
  reject: { background: '#c2452e', color: '#fff' },
  input: {
    width: '100%', padding: '10px 12px', borderRadius: 8, marginBottom: 12,
    border: `1px solid ${LINE}`, background: '#11141c', color: INK,
    font: 'inherit', fontSize: 14, boxSizing: 'border-box',
  },
  warn: { color: '#ffb020', fontSize: 13, lineHeight: 1.5 },
  note: { color: MUTED, fontSize: 12.5, lineHeight: 1.55 },
  empty: { padding: 40, color: MUTED, fontSize: 14, lineHeight: 1.6 },
}

const badge = (bg, fg = '#fff') => ({
  display: 'inline-block', padding: '2px 8px', borderRadius: 999,
  fontSize: 11, fontWeight: 700, letterSpacing: 0.4, background: bg, color: fg,
})

const VERDICT = {
  PASS: badge('rgba(47,158,94,0.22)', '#6ee7a0'),
  REVIEW: badge('rgba(255,176,32,0.20)', '#ffc861'),
  FAIL: badge('rgba(194,69,46,0.22)', '#ff9b85'),
}

/** A check that never ran is not a check that failed — say so distinctly. */
function checkVerdict(check) {
  if (!check.ran) return 'DID NOT RUN'
  return check.verdict ?? (check.passed ? 'PASS' : 'FAIL')
}

function Verdict({ value }) {
  const style = VERDICT[value] ?? badge('rgba(232,234,240,0.14)', MUTED)
  return <span style={style}>{value}</span>
}

function timeLeft(expiresAt) {
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (Number.isNaN(ms)) return null
  if (ms <= 0) return 'expired'
  const m = Math.floor(ms / 60000)
  return m >= 1 ? `${m} min left` : `${Math.floor(ms / 1000)}s left`
}

function QueueItem({ gate, selected, onSelect }) {
  const left = timeLeft(gate.expires_at)
  return (
    <button
      style={{ ...S.item, ...(selected ? S.itemOn : null) }}
      onClick={() => onSelect(gate.gate_id)}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ ...S.mono, color: MUTED }}>{gate.gate_id.slice(0, 8)}</span>
        <span style={{ ...S.mono, color: gate.expired ? '#ff9b85' : MUTED }}>{left}</span>
      </div>
      <p style={S.signal}>{gate.triggering_signal}</p>
    </button>
  )
}

function Checks({ checks }) {
  if (!checks?.length) {
    return <p style={S.note}>No check evidence was recorded for this gate.</p>
  }
  return checks.map((c) => {
    const verdict = checkVerdict(c)
    return (
      <div key={c.check_no} style={S.row}>
        <div style={{ width: 116, flexShrink: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{c.name}</div>
          <div style={{ ...S.mono, color: MUTED }}>check {c.check_no}</div>
        </div>
        <div style={{ flex: 1 }}>
          <Verdict value={verdict} />
          {c.reason && <p style={{ ...S.signal, color: MUTED }}>{c.reason}</p>}
          {c.limitations?.length > 0 && (
            <ul style={{ ...S.note, margin: '6px 0 0', paddingLeft: 18 }}>
              {c.limitations.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          )}
        </div>
      </div>
    )
  })
}

/**
 * The chain result is rendered loudly when broken and quietly when intact.
 * A tamper-evident log whose failure state looks like its success state is
 * decorative, so a break gets colour, an icon and words — not a red dot.
 */
function Chain({ chain }) {
  if (!chain) return null
  if (chain.ok) {
    return (
      <p style={S.note}>
        ✓ Audit chain intact — {chain.length ?? chain.events ?? '?'} events, each
        hash covering the one before it. Nothing has been edited after the fact.
      </p>
    )
  }
  return (
    <div style={{ border: '1px solid #c2452e', background: 'rgba(194,69,46,0.14)', borderRadius: 10, padding: 14 }}>
      <strong style={{ color: '#ff9b85' }}>⚠ Audit chain is broken.</strong>
      <p style={{ ...S.warn, margin: '6px 0 0' }}>
        {chain.reason ?? 'A hash does not match the record it covers.'} Do not
        authorise this gate. The history you are being shown cannot be trusted,
        which is a more serious finding than anything in it.
      </p>
    </div>
  )
}

function Timeline({ events }) {
  if (!events?.length) return null
  return (
    <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
      {events.map((e, i) => {
        const refused = e.type === 'transition.refused'
        return (
          <li key={i} style={{ ...S.row, borderTop: i ? `1px solid ${LINE}` : 0 }}>
            <span style={{ ...S.mono, color: MUTED, width: 62, flexShrink: 0 }}>
              {e.at ? new Date(e.at).toLocaleTimeString() : ''}
            </span>
            <span style={{ flex: 1, fontSize: 13 }}>
              <span style={{ color: refused ? '#ff9b85' : INK }}>{e.type}</span>
              {e.payload?.from && (
                <span style={{ color: MUTED }}> {e.payload.from} → {e.payload.to}</span>
              )}
              {e.payload?.actor && (
                <span style={{ ...S.mono, color: MUTED }}> ({e.payload.actor})</span>
              )}
              {refused && e.payload?.reason && (
                <div style={{ ...S.warn, marginTop: 3 }}>refused: {e.payload.reason}</div>
              )}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

function Detail({ packet, onResolve, busy, error }) {
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
    <section style={S.detail}>
      <h2 style={{ margin: '0 0 4px', fontSize: 20 }}>
        Gate <span style={S.mono}>{packet.gate_id}</span>
      </h2>
      <p style={S.sub}>
        {packet.mode} · <Verdict value={packet.state} />
        {packet.expired && <span style={{ color: '#ff9b85' }}> · expired</span>}
      </p>

      <div style={S.card}>
        <h3 style={S.cardHead}>Why this needs a person</h3>
        {packet.reasons?.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6, fontSize: 14 }}>
            {packet.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        ) : (
          <p style={{ margin: 0, fontSize: 14 }}>{packet.triggering_signal}</p>
        )}
        <p style={{ ...S.note, marginTop: 12, marginBottom: 0 }}>
          These are the words the decision layer recorded at the time, read back
          out of the audit chain — not recomputed now. You are seeing what was
          decided and on what basis.
        </p>
      </div>

      <div style={S.card}>
        <h3 style={S.cardHead}>Checks</h3>
        <Checks checks={packet.checks} />
      </div>

      <div style={S.card}>
        <h3 style={S.cardHead}>Integrity</h3>
        <Chain chain={packet.chain} />
      </div>

      <div style={S.card}>
        <h3 style={S.cardHead}>History</h3>
        <Timeline events={packet.timeline} />
      </div>

      {packet.reviews?.length > 0 && (
        <div style={S.card}>
          <h3 style={S.cardHead}>Earlier rulings</h3>
          {packet.reviews.map((r) => (
            <div key={r.id} style={S.row}>
              <span style={{ fontSize: 13 }}>
                <strong>{r.reviewer_id}</strong> — {r.decision}
                {r.notes && <span style={{ color: MUTED }}> · {r.notes}</span>}
              </span>
            </div>
          ))}
        </div>
      )}

      <div style={S.card}>
        <h3 style={S.cardHead}>Your ruling</h3>
        <p style={{ ...S.note, marginTop: 0 }}>
          You are not confirming a score. You are deciding a question the
          evidence did not settle, and your name is recorded against it in the
          same tamper-evident chain as everything above.
        </p>
        <input
          style={S.input}
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="Your reviewer ID — required"
          aria-label="Reviewer ID"
        />
        <input
          style={S.input}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Why (optional, but it is the only record of your reasoning)"
          aria-label="Notes"
        />
        {stale && (
          <p style={S.warn}>
            {packet.expired
              ? 'This gate has expired. It can no longer be authorised — the person it belongs to must start again.'
              : `This gate is ${packet.state}; it is no longer awaiting a review.`}
          </p>
        )}
        {broken && !stale && (
          <p style={S.warn}>
            Approval is disabled because the audit chain is broken. You can
            still reject: refusing is the safe response to a history that
            cannot be trusted.
          </p>
        )}
        <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
          <button
            style={{ ...S.btn, ...S.approve, opacity: canApprove ? 1 : 0.45 }}
            disabled={!canApprove}
            onClick={() => submit('approve')}
          >
            Approve — authorise
          </button>
          <button
            style={{ ...S.btn, ...S.reject, opacity: canReject ? 1 : 0.45 }}
            disabled={!canReject}
            onClick={() => submit('reject')}
          >
            Reject
          </button>
        </div>
        {!named && <p style={{ ...S.note, marginTop: 10 }}>Enter your reviewer ID to rule. An anonymous review is not a review.</p>}
        {error && <p style={{ ...S.warn, marginTop: 10 }}>{error}</p>}
      </div>

      <p style={S.note}>
        No capture, landmark or biometric value appears on this screen, by
        design. If resolving this gate seems to require seeing the person's
        face, the correct outcome is to reject and ask them to re-verify.
      </p>
    </section>
  )
}

export default function Review() {
  const [gates, setGates] = useState(null)
  const [selected, setSelected] = useState(null)
  const [packet, setPacket] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [queueError, setQueueError] = useState(null)

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
    <main style={S.page}>
      <aside style={S.queue}>
        <div style={S.queueHead}>
          <h1 style={S.h1}>Awaiting a human</h1>
          <p style={S.sub}>
            {gates === null ? 'Loading…'
              : `${gates.length} gate${gates.length === 1 ? '' : 's'} the checks could not settle. Oldest first.`}
          </p>
        </div>
        {queueError && <p style={{ ...S.warn, padding: 20 }}>{queueError}</p>}
        {gates?.length === 0 && !queueError && (
          <p style={S.empty}>
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
        ? <Detail packet={packet} onResolve={resolve} busy={busy} error={error} />
        : (
          <section style={S.detail}>
            <p style={S.empty}>
              {error ?? 'Select a gate to see the signal that referred it.'}
            </p>
          </section>
        )}
    </main>
  )
}
