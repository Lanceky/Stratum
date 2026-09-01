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
    // Ran, but against a stand-in. Styled with the absent badge rather than
    // pass or fail because what it shares with "did not run" is the thing
    // that matters: no evidence about the person was produced.
    'NOT EVIDENCE': 'badge-absent',
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

/**
 * A credential, drawn as an object rather than as a panel.
 *
 * Every other card here is a container for part of a console — something you
 * work in. This one is something you look at: the visual form of an identity
 * the system has issued. So it gets its own stage, its own light, and a
 * deliberately small amount of information.
 *
 * It is presentational and holds no view of what it is showing, because it is
 * used for two different things — the deployment's own signing identity, and
 * an issued claim — and a component that knew the difference would end up
 * carrying both stories. Every value it renders is passed in from the server;
 * there is no specimen mode, because a card that could show an invented
 * identity would be indistinguishable at a glance from one showing a real one.
 *
 * The sections are optional and stack in a fixed order: identity, then live
 * counts, then fixed facts, then a caveat, then the deployment stamp. Fixed,
 * because the order is the argument — who this is, what it has done, how to
 * check it, and what it does not cover.
 */
export function IdentityCard({
  glyph, eyebrow, title, status, tone = 'indigo', stats = [], rows = [],
  note, footer, children,
}) {
  return (
    <article className={`idcard idcard-${tone}`}>
      <header className="idcard-head">
        <span className="idcard-glyph">{glyph ?? <ShieldGlyph />}</span>
        {status && (
          <span className="idcard-status"><i className="idcard-dot" />{status}</span>
        )}
      </header>

      {eyebrow && <p className="idcard-eyebrow">{eyebrow}</p>}
      <h3 className="idcard-title">{title}</h3>

      {stats.length > 0 && (
        <div className="idcard-stats">
          {stats.map((s) => (
            <div key={s.k}>
              <div className="idcard-stat-n" style={s.tone ? { color: s.tone } : undefined}>
                {s.v}
              </div>
              <div className="idcard-stat-k">{s.k}</div>
            </div>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <dl className="idcard-rows">
          {rows.map((r) => (
            <React.Fragment key={r.k}>
              <dt>{r.k}</dt>
              <dd className={r.mono ? 'mono' : undefined} title={r.title}>{r.v}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}

      {children}
      {note && <p className="idcard-note">{note}</p>}
      {footer && <p className="idcard-foot mono">{footer}</p>}
    </article>
  )
}

export function ShieldGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2.5 4.5 5.6v6.2c0 4.6 3.1 8.4 7.5 9.7 4.4-1.3 7.5-5.1 7.5-9.7V5.6Z" />
      <path d="m8.9 12.1 2.2 2.2 4.1-4.5" />
    </svg>
  )
}

export function KeyGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="8.4" cy="12" r="3.5" />
      <path d="M11.9 12H21" />
      <path d="M17.6 12v3" />
      <path d="M20.4 12v2.1" />
    </svg>
  )
}

/**
 * Truncated in the middle, not at the end. Hashes and addresses from the same
 * origin often share a prefix, so an end-truncated pair can look identical
 * while differing; keeping both ends is what someone comparing against a block
 * explorer actually reads.
 */
export function middle(value, keep = 6) {
  if (!value) return '—'
  const s = String(value)
  return s.length <= keep * 2 + 1 ? s : `${s.slice(0, keep)}…${s.slice(-keep)}`
}
