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

/**
 * A check that never ran is not a check that failed — say so distinctly.
 *
 * A check that ran against a stand-in is a third thing again. It has a
 * pass/fail internally, but that verdict is about a seeded fixture, so
 * rendering it as FAIL puts the console in direct contradiction with the
 * decision layer, which refuses to let such a result fail a gate. The
 * reviewer would be reading an accusation the record does not make.
 */
function checkVerdict(check) {
  if (!check.ran) return 'DID NOT RUN'
  if (check.synthetic) return 'NOT EVIDENCE'
  return check.verdict ?? (check.passed ? 'PASS' : 'FAIL')
}

/**
 * The certificate, offered once the gate has a verdict.
 *
 * Downloaded through fetch rather than a plain link so a refusal is readable.
 * A link handed the browser a JSON error body to render as a page, which turns
 * "the renderer is unreachable" into what looks like a broken console — the
 * reviewer needs to know the document was not produced, not to be shown a
 * stack of braces.
 *
 * The regime is chosen, never defaulted. A certificate that makes an EU claim
 * under US rules is worse than no certificate, so the selection is explicit and
 * the server refuses anything it was not given.
 */
function Certificate({ gateId, state }) {
  const [jurisdiction, setJurisdiction] = useState('UNSPECIFIED')
  const [tier, setTier] = useState('STANDARD')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [issued, setIssued] = useState(null)

  const sealable = ['PASS', 'REVIEW', 'FAIL', 'SIGNED', 'SEALED'].includes(state)

  async function download() {
    setBusy(true); setError(null)
    try {
      const q = `jurisdiction=${jurisdiction}&risk_tier=${tier}`
      const r = await fetch(`${API}/gates/${gateId}/attestation.pdf?${q}`)
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        throw new Error(body.detail ?? `the renderer returned ${r.status}`)
      }
      const digest = r.headers.get('X-Stratum-Document-SHA256')
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `stratum-${gateId.slice(0, 8)}-attestation.pdf`
      a.click()
      URL.revokeObjectURL(url)
      setIssued({ digest, bytes: blob.size })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!sealable) {
    return (
      <Card title="Certificate">
        <p className="dim small" style={{ margin: 0 }}>
          This gate has not reached a verdict. A certificate issued now would
          circulate as a finished record of an open question.
        </p>
      </Card>
    )
  }

  return (
    <Card title="Certificate">
      <p className="small" style={{ marginTop: 0 }}>
        A sealed PDF of the evidence, the limits of each check and the chain
        head — the part of this record that outlives the server.
      </p>

      <div className="wrap" style={{ marginBottom: 12 }}>
        <label className="small">
          Jurisdiction{' '}
          <select value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
            <option value="UNSPECIFIED">Not claimed</option>
            <option value="EU_AMLR">EU AMLR</option>
            <option value="US_CIP">US CIP</option>
          </select>
        </label>
        <label className="small">
          Risk tier{' '}
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="STANDARD">Standard</option>
            <option value="ENHANCED">Enhanced</option>
          </select>
        </label>
      </div>

      <button className="btn ghost" onClick={download} disabled={busy}>
        {busy ? <Spinner /> : null} Download certificate
      </button>

      {issued && (
        <p className="small" style={{ marginBottom: 0 }}>
          Issued, {issued.bytes.toLocaleString()} bytes. The audit chain now
          carries this digest, so the file can be matched against the record
          rather than taken on trust: <Hash value={issued.digest} />
        </p>
      )}
      {error && (
        <p className="small" style={{ color: 'var(--red)', marginBottom: 0 }}>
          No certificate was produced. {error}
        </p>
      )}
    </Card>
  )
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
      {gate.escalations > 0 && (
        <p className="small" style={{ margin: '8px 0 0', color: 'var(--red)' }}>
          ⚠ {gate.escalations === 1
            ? 'an agent tried to take a human-only step'
            : `${gate.escalations} agent attempts at a human-only step`}
        </p>
      )}
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
        <Badge value={checkVerdict(c)}
               title={c.synthetic
                 ? 'ran against a seeded fixture, not the sensor'
                 : undefined} />
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
function Integrity({ chain, timeline, gateId, onTampered }) {
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
      <Tamper chain={chain} timeline={timeline} gateId={gateId}
              onTampered={onTampered} />
    </>
  )
}

/**
 * Break the record on purpose, to show that breaking it shows.
 *
 * A chain that has only ever been intact demonstrates nothing — "✓ Intact" is
 * a claim about code the reviewer cannot see. This makes the property
 * falsifiable in front of them.
 *
 * It aims at a refusal block when there is one, because that is the edit an
 * attacker would actually want: not vandalism, but quietly erasing the record
 * that an agent was told no.
 */
function Tamper({ chain, timeline, gateId, onTampered }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  if (!chain.ok) return null

  const refusedAt = timeline?.findIndex((e) => e.type === 'transition.refused')
  const index = refusedAt >= 0 ? refusedAt : 0
  const target = timeline?.[index]

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${API}/demo/gates/${gateId}/tamper`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          index,
          payload: { note: 'nothing to see here' },
        }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(body.detail ?? `refused (${res.status})`)
      onTampered?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <hr className="rule" />
      <div className="between wrap" style={{ gap: 12 }}>
        <p className="dim small" style={{ margin: 0, maxWidth: '46ch' }}>
          Intact is only worth reading if broken were possible. Edit block{' '}
          <span className="mono">#{String(index).padStart(2, '0')}</span>
          {target?.type && <> (<span className="mono">{target.type}</span>)</>}
          {' '}underneath the API — drop the append-only trigger, rewrite the
          row, put the trigger back.
        </p>
        <button className="btn ghost" onClick={run} disabled={busy}>
          {busy ? 'Editing…' : 'Tamper with the record'}
        </button>
      </div>
      {error && (
        <p className="small" style={{ color: 'var(--amber)', margin: '10px 0 0' }}>
          {error}
        </p>
      )}
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

/**
 * A non-human reached for a step only a human may take.
 *
 * Above the fold and outside the timeline, because it changes what the ruling
 * means. Everything else on this screen is a question about evidence quality —
 * was the light good enough, is this a sibling. This is not that. It is the
 * system reporting that something tried to skip the person now being asked to
 * approve it, and a reviewer who scrolls past it is approving blind.
 */
function Escalations({ events }) {
  return (
    <div className="alert" style={{ marginBottom: 18 }}>
      <p style={{ margin: 0, fontWeight: 600, color: 'var(--red)' }}>
        {events.length === 1
          ? 'An agent tried to take a human-only step on this gate.'
          : `An agent made ${events.length} attempts at a human-only step on this gate.`}
      </p>
      <ul className="small" style={{ margin: '10px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>
        {events.map((e) => (
          <li key={e.hash}>
            <span className="mono">{e.actor}</span> reached for{' '}
            <span className="mono">{e.to}</span> from{' '}
            <span className="mono">{e.from}</span> — {e.reason}
            <br />
            <span className="dim">
              {e.at ? new Date(e.at).toLocaleString() : ''}{' '}
            </span>
            <Hash value={e.hash} chars={10} />
          </li>
        ))}
      </ul>
      <p className="small muted" style={{ margin: '10px 0 0' }}>
        It was refused, and the gate never moved. The attempt is recorded because a
        refusal nobody can read afterwards is indistinguishable from one that never
        happened.
      </p>
    </div>
  )
}

/**
 * Part of the evidence came from a seeded fixture rather than the sensor.
 *
 * Above the checks, because it changes how every number below it should be
 * read. A reviewer who scrolls straight to a score sees a measurement; what
 * is actually there is a measurement of a fabricated face. The gate can still
 * be signed — a person may have other grounds — but not on this evidence.
 */
function Stale({ reason }) {
  return (
    <div className="alert" style={{ marginBottom: 18, borderColor: 'var(--amber)' }}>
      <p style={{ margin: 0, fontWeight: 600, color: 'var(--amber)' }}>
        This packet may be out of date — {reason}.
      </p>
      <p className="small muted" style={{ margin: '8px 0 0' }}>
        What is on screen is the last copy that loaded successfully. The gate
        may have expired or been ruled on since. Reload before signing.
      </p>
    </div>
  )
}

function StandIn({ checks }) {
  const names = checks.filter((c) => c.synthetic).map((c) => c.name)
  if (!names.length) return null
  return (
    <div className="alert" style={{ marginBottom: 18, borderColor: 'var(--amber)' }}>
      <p style={{ margin: 0, fontWeight: 600, color: 'var(--amber)' }}>
        {names.length === 1
          ? `The ${names[0]} check ran against a stand-in, not the sensor.`
          : `${names.length} checks ran against a stand-in, not the sensor.`}
      </p>
      <p className="small muted" style={{ margin: '8px 0 0' }}>
        The numbers below are real, and they describe a seeded fixture rather
        than whoever was at the camera. Treat them as evidence that the
        pipeline ran, not as evidence about a person.
      </p>
    </div>
  )
}

const Detail = React.forwardRef(function Detail(
  { packet, onResolve, onTampered, busy, error, staleReason }, ref) {
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
  // A packet we could not re-confirm is treated the same way as a chain we
  // cannot trust: it blocks approval, never rejection. Refusing is the safe
  // response to not knowing; authorising on it is the dangerous one.
  const canApprove = named && !stale && !busy && !broken && !staleReason
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

      {staleReason && <Stale reason={staleReason} />}
      {packet.escalations?.length > 0 && <Escalations events={packet.escalations} />}
      {packet.checks?.length > 0 && <StandIn checks={packet.checks} />}

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
        <Integrity chain={packet.chain} timeline={packet.timeline}
                   gateId={packet.gate_id} onTampered={onTampered} />
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

      <Certificate gateId={packet.gate_id} state={packet.state} />

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
  const [stale, setStale] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const detailRef = useRef(null)
  // A ref, not the state value, so the polling interval does not have to be
  // torn down and rebuilt every time a ruling starts or finishes.
  const busyRef = useRef(false)
  useEffect(() => { busyRef.current = busy }, [busy])

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
    if (!selected) { setPacket(null); setStale(null); return }
    let live = true
    let loaded = false

    const load = () => {
      // A ruling in flight is about to change the thing being polled. Letting
      // a poll land in the middle overwrites the pane with a packet that is
      // already out of date by the time it arrives.
      if (busyRef.current) return
      fetch(`${API}/gates/${selected}/review`)
        .then((r) => (r.ok ? r.json()
          : Promise.reject(new Error(`could not load gate (${r.status})`))))
        .then((p) => {
          if (!live) return
          setPacket(p)
          setStale(null)
          loaded = true
        })
        .catch((e) => {
          if (!live) return
          // Blank the pane only if nothing ever loaded into it. A dropped
          // poll must not erase a packet a reviewer is part-way through
          // reading — and must not silently leave them reading it either.
          if (loaded) setStale(e.message)
          else { setError(e.message); setPacket(null) }
        })
    }

    setError(null)
    setStale(null)
    load()
    // The queue refreshes on its own timer while the open gate did not, so a
    // detail pane opened once sat unchanged indefinitely. On a screen whose
    // whole claim is "this is what was decided", showing a packet from some
    // minutes ago is the one thing it must not do: the gate can expire, or
    // another reviewer can rule on it, and this pane would never say so.
    const t = setInterval(load, 15000)
    return () => { live = false; clearInterval(t) }
  }, [selected, refresh])

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
        ? <Detail ref={detailRef} packet={packet} onResolve={resolve}
                  onTampered={() => setRefresh((n) => n + 1)}
                  busy={busy} error={error} staleReason={stale} />
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
