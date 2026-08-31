/**
 * Shared UI primitives.
 *
 * Kept deliberately small. The interesting one is <Chain>, which draws the
 * audit trail as linked blocks: each block shows the first bytes of its hash,
 * and the connector between two blocks *is* the prev_hash pointer. When the
 * chain breaks, the connector after the broken block becomes a dashed red line
 * — the picture of the data structure fails in the same place the data
 * structure does.
 */

import React from 'react'

/** A verdict, or the absence of one. Each state gets its own colour and word. */
export function Badge({ value, title }) {
  const key = String(value ?? '').toUpperCase()
  const cls = {
    PASS: 'badge-pass',
    REVIEW: 'badge-review',
    FAIL: 'badge-fail',
    SIGNED: 'badge-signed',
    SEALED: 'badge-signed',
    'DID NOT RUN': 'badge-absent',
  }[key] ?? 'badge-idle'
  return <span className={`badge ${cls}`} title={title}>{key || '—'}</span>
}

export function Card({ title, right, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-head">
          <h3 className="eyebrow">{title}</h3>
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

export function Mark() {
  return <span className="mark">STRAT<span>U</span>M</span>
}

/** Hashes are shown truncated. A full 64-hex string is noise to a human. */
export function Hash({ value, chars = 12 }) {
  if (!value) return null
  return (
    <code className="mono" style={{ color: 'var(--indigo-bright)' }} title={value}>
      {String(value).slice(0, chars)}…
    </code>
  )
}

const EVENT_LABEL = {
  'gate.created': 'created',
  'transition': 'transition',
  'transition.refused': 'refused',
  'evidence': 'evidence',
  'review': 'ruling',
  'capture': 'capture',
}

function blockClass(event, index, brokenAt) {
  if (brokenAt != null && index >= brokenAt) return 'block block-broken'
  if (event.type === 'transition.refused') return 'block block-refuse'
  if (event.payload?.actor === 'human') return 'block block-human'
  return 'block block-ok'
}

/**
 * The audit trail, drawn as what it is.
 *
 * `brokenAt` comes from the verifier's own chain check rather than being
 * recomputed here — the client is not in a position to adjudicate its own
 * integrity, and pretending otherwise would be theatre.
 */
export function Chain({ events = [], brokenAt = null }) {
  if (!events.length) return <p className="muted small">No events recorded.</p>
  return (
    <div className="chain-scroll"><div className="chain">
      {events.map((e, i) => {
        const p = e.payload ?? {}
        const label = EVENT_LABEL[e.type] ?? e.type
        return (
          <div key={i} className={blockClass(e, i, brokenAt)}>
            <div className="block-index">#{String(i).padStart(2, '0')}</div>
            <div className="block-type">{label}</div>
            {e.type === 'evidence' && p.check_no != null && (
              <div className="mono dim" style={{ fontSize: 10 }}>check {p.check_no}</div>
            )}
            {p.from && (
              <div className="mono dim" style={{ fontSize: 10 }}>
                {p.from} → {p.to}
              </div>
            )}
            {p.actor && (
              <div className="mono" style={{ fontSize: 10, color: p.actor === 'human' ? 'var(--indigo-bright)' : 'var(--ink-3)' }}>
                {p.actor}
              </div>
            )}
            <div className="block-hash" style={{ marginTop: 6 }}>
              {e.hash ? String(e.hash).slice(0, 8) : '········'}
            </div>
          </div>
        )
      })}
    </div></div>
  )
}

export function Spinner() {
  return <span className="spin" aria-label="working" />
}

/**
 * A screen that has not been built yet, said plainly.
 *
 * A blank page or a half-working mock both invite the reader to guess. Naming
 * the step and what blocks it is more useful than either.
 */
export function Pending({ title, step, line }) {
  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div style={{ maxWidth: 440, textAlign: 'center' }}>
        <p className="eyebrow">{step}</p>
        <h1 style={{ fontSize: 24, margin: '10px 0 12px' }}>{title}</h1>
        <p className="muted">{line}</p>
        <p style={{ marginTop: 24 }}>
          <a href="/">← back</a>
        </p>
      </div>
    </main>
  )
}
