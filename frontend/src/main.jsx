import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'

import './theme.css'
import { Mark, IdentityCard, KeyGlyph, middle } from './ui.jsx'
import Gate from './gate/Gate.jsx'
import Agent from './agent/Agent.jsx'
import Review from './review/Review.jsx'
import Verify from './verify/Verify.jsx'
import Dashboard from './dashboard/Dashboard.jsx'
import Claim from './claim/Claim.jsx'
import Terms from './terms/Terms.jsx'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

/**
 * The demo is a single story told in three sittings, so it is presented as an
 * order rather than a menu.
 *
 * A list of five equal cards makes every door look alike and leaves the visitor
 * to guess which one to open first. These three are not alternatives — each one
 * only means anything because of the one before it: the agent is refused, so a
 * human has to appear; the human's evidence is inconclusive, so a reviewer has
 * to rule; the ruling is worth nothing unless it leaves the building, so it is
 * sealed into a document.
 */
const PATH = [
  {
    to: '/agent',
    n: '01',
    name: 'An agent is refused',
    line: 'Valid credentials, correct request, still refused — and the refusal written to the chain as evidence.',
    outcome: 'A gate opens, and it needs a person.',
  },
  {
    to: '/gate/demo',
    n: '02',
    name: 'A person answers',
    line: 'Three checks: someone is present, the capture is not generated, and it is the enrolled signer.',
    outcome: 'Two checks settle. The third cannot.',
  },
  {
    to: '/review',
    n: '03',
    name: 'A reviewer settles it',
    line: 'A sibling and a bad photograph overlap measurably, so those gates reach a named human.',
    outcome: 'A ruling — and a sealed certificate.',
  },
]

// Routes that exist but are not built. Named plainly rather than hidden: a
// door that opens onto nothing costs more trust than an absent one.
const UNBUILT = [
  { to: '/verify', name: 'Public verifier', line: 'resolve an attestation against its DNS record' },
  { to: '/dashboard', name: 'Tenant dashboard', line: 'gates, attestations, typosquat sweep' },
]

function useSystem() {
  const [sys, setSys] = useState(null)
  useEffect(() => {
    let alive = true
    // One request, not three. The counts come from /health because the server
    // is the only thing that can count gates it has not listed — the earlier
    // version called /gates with no state, got the REVIEW page back, and
    // labelled it "gates opened", which was two names for the same number.
    const load = async () => {
      try {
        const r = await fetch(`${API}/health`)
        if (!r.ok) throw new Error(`health ${r.status}`)
        const h = await r.json()
        if (alive) setSys(h)
      } catch {
        if (alive) setSys({ down: true })
      }
    }
    load()
    const t = setInterval(load, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  return sys
}

/**
 * The deployment, drawn as its own credential.
 *
 * Two claims on this page are about the same object — that authorisations are
 * really being recorded, and that something signs them — so they are one card
 * rather than two panels. Stating either in prose and showing nothing asks to
 * be taken on trust, which is the one thing this system is built not to
 * require.
 *
 * The limits are chips, not sentences. `can_sign` is derived from a key that
 * loads rather than from a variable that is set: a build advertising a
 * signature it cannot produce is the worst thing this card could say.
 */
function SystemCard({ sys }) {
  if (!sys) {
    return <IdentityCard glyph={<KeyGlyph />} eyebrow="issuer" tone="amber"
      title="reading system state…" status="waiting" />
  }

  if (sys.down) {
    return <IdentityCard glyph={<KeyGlyph />} eyebrow="issuer" tone="red"
      title="verifier not answering" status="offline"
      chips={[{ k: 'run make verifier', title: 'Nothing here can be demonstrated until it is running.' }]} />
  }

  const iss = sys.issuer ?? {}
  const led = iss.ledger ?? {}
  const waiting = sys.gates?.awaiting_review ?? 0

  return (
    <IdentityCard
      glyph={<KeyGlyph />}
      eyebrow="issuer"
      title={iss.can_sign ? middle(iss.address, 10) : 'no signing key'}
      tone={iss.can_sign ? 'indigo' : 'amber'}
      status={iss.can_sign ? 'signing' : 'unsigned'}
      stats={[
        { k: 'gates opened', v: sys.gates?.total ?? 0 },
        { k: 'awaiting a human', v: waiting,
          tone: waiting ? 'var(--amber-bright)' : undefined },
      ]}
      rows={[
        { k: 'scheme', v: 'EIP-191' },
        { k: 'blocks', v: led.events ?? 0, mono: true },
        { k: 'head', v: middle(led.latest, 7), mono: true, title: led.latest },
        { k: 'sensor units',
          v: `${sys.units?.remaining ?? '—'} / ${sys.units?.ceiling ?? '—'}`, mono: true },
      ]}
      chips={[
        { k: sys.api_mode,
          title: 'The sensor grant is metered and cannot be topped up, so its '
               + 'calls run from recordings. Everything else is live.' },
        { k: iss.can_sign ? 'demo key' : 'cannot sign',
          title: iss.can_sign
            ? "Held in the server's environment. Production belongs in an HSM, "
              + 'and the certificate says so.'
            : 'Nothing this build produces can be verified on chain.' },
      ]}
    />
  )
}

function Home() {
  const sys = useSystem()
  return (
    <main style={{ minHeight: '100vh' }}>
      <div className="topbar">
        <Mark />
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>
          human authorisation for agentic workflows
        </span>
      </div>

      <div className="home">
        <section className="home-hero">
          <div>
            <p className="eyebrow">the boundary</p>
            <h1 className="home-h1">
              An AI agent can do the work.
              <br />
              Only a verified human can{' '}
              <span style={{ color: 'var(--indigo-bright)' }}>sign for it</span>.
            </h1>
            <p className="muted" style={{ maxWidth: 520, marginTop: 18, fontSize: 15 }}>
              Every authorisation is written to a hash chain, one block per
              event. An agent reaching for a signature is refused — and the
              refusal is recorded, because that is what an auditor came for.
            </p>
          </div>
          <SystemCard sys={sys} />
        </section>

        <section>
          <div className="between" style={{ marginBottom: 16 }}>
            <p className="eyebrow" style={{ margin: 0 }}>the demo, in order</p>
            <p className="dim small" style={{ margin: 0 }}>each step exists because the one before it failed to settle</p>
          </div>

          <div className="path">
            {PATH.map((s) => (
              <Link key={s.to} to={s.to} className="path-step">
                <span className="path-n">{s.n}</span>
                <h3 className="path-name">{s.name}</h3>
                <p className="muted small path-line">{s.line}</p>
                <p className="path-outcome">{s.outcome}</p>
              </Link>
            ))}
          </div>
        </section>

        <section className="second">
          <div>
            <p className="eyebrow" style={{ marginTop: 0 }}>the same layer, a different problem</p>
            <h3 style={{ fontSize: 18, margin: '0 0 10px' }}>
              One human, one claim
            </h3>
            <p className="muted small" style={{ maxWidth: 560, marginTop: 0 }}>
              A wallet costs nothing to create, so anything allocated per wallet
              is allocated per script. The same checks run, but the question
              changes from <em>is this the enrolled signer?</em> to{' '}
              <em>is this anyone we have already seen?</em> — and the error that
              matters flips: not a stranger let through, but an allocation taken
              from someone entitled to it.
            </p>
            <p className="muted small" style={{ maxWidth: 560 }}>
              So a close relative is referred to a person, not refused, with the
              odds of coincidence printed beside the finding.
            </p>
            <Link to="/claim" className="btn">try to claim twice →</Link>
          </div>
          <ul className="second-points">
            <li><strong>No hardware.</strong> A phone, not a $50,000 orb.</li>
            <li><strong>Per-campaign nullifiers.</strong> Unlinkable between
              campaigns; not zero-knowledge, and says so.</li>
            <li><strong>EIP-191.</strong> Solidity{' '}
              <code className="mono">ecrecover</code> verifies it unchanged.</li>
            <li><strong>The odds travel with it.</strong> False-match risk grows
              with the roster, so it is stated per sweep.</li>
          </ul>
        </section>

        <section className="artefact">
          <div>
            <p className="eyebrow" style={{ marginTop: 0 }}>what comes out</p>
            <h3 style={{ fontSize: 16, margin: '0 0 8px' }}>
              A sealed certificate that outlives this server
            </h3>
            <p className="muted small" style={{ margin: 0, maxWidth: 560 }}>
              The evidence, the measured limit of every check, and the chain head
              — rendered to PDF/A and signed. It states what was <em>not</em>{' '}
              established as plainly as what was, because whoever relies on it
              inherits both.
            </p>
          </div>
          <div className="artefact-doc" aria-hidden="true">
            <div className="doc-line doc-line-title" />
            <div className="doc-line" style={{ width: '78%' }} />
            <div className="doc-line" style={{ width: '52%' }} />
            <div className="doc-box" />
            <div className="doc-line" style={{ width: '66%' }} />
            <div className="doc-line" style={{ width: '84%' }} />
            <div className="doc-seal">SIGNED</div>
          </div>
        </section>

        <section className="unbuilt">
          <p className="eyebrow" style={{ marginTop: 0 }}>not built</p>
          <p className="dim small" style={{ margin: '0 0 10px' }}>
            Scoped and routed, with nothing behind them yet. Listed so the gap is
            visible rather than discovered by clicking.
          </p>
          <div className="wrap">
            {UNBUILT.map((u) => (
              <Link key={u.to} to={u.to} className="unbuilt-item">
                <span>{u.name}</span>
                <span className="dim"> — {u.line}</span>
              </Link>
            ))}
          </div>
        </section>

        <footer className="between" style={{ borderTop: '1px solid var(--border)', paddingTop: 20 }}>
          <p className="dim small" style={{ margin: 0 }}>
            Prototype. Simulated figures, demo key, no real identities.
          </p>
          <Link to="/terms" className="small">Terms &amp; safety notices</Link>
        </footer>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/agent" element={<Agent />} />
      <Route path="/gate/:gateId" element={<Gate />} />
      <Route path="/review" element={<Review />} />
      <Route path="/verify" element={<Verify />} />
      <Route path="/claim" element={<Claim />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/terms" element={<Terms />} />
    </Routes>
  </BrowserRouter>
)
