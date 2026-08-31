import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'

import './theme.css'
import { Card, Mark } from './ui.jsx'
import Gate from './gate/Gate.jsx'
import Agent from './agent/Agent.jsx'
import Review from './review/Review.jsx'
import Verify from './verify/Verify.jsx'
import Dashboard from './dashboard/Dashboard.jsx'

const ROUTES = [
  {
    to: '/agent',
    name: 'Agent console',
    line: 'Watch an agent with valid credentials try to sign its own work, and be refused on the record.',
    state: 'live',
  },
  {
    to: '/gate/demo',
    name: 'Gate',
    line: 'Capture, then three checks: a live human, an unforged capture, the right person.',
    state: 'live',
  },
  {
    to: '/review',
    name: 'Reviewer console',
    line: 'Resolve the gates the checks could not settle — with the signal, never the face.',
    state: 'live',
  },
  {
    to: '/verify',
    name: 'Public verifier',
    line: 'Resolve an attestation hash against its DNS record.',
    state: 'step 10a',
  },
  {
    to: '/dashboard',
    name: 'Tenant dashboard',
    line: 'Gates, attestations and the typosquat sweep.',
    state: 'step 10',
  },
]

function Home() {
  return (
    <main style={{ minHeight: '100vh' }}>
      <div className="topbar">
        <Mark />
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>
          human authorisation for agentic workflows
        </span>
      </div>

      <div style={{ maxWidth: 860, margin: '0 auto', padding: '64px 24px 80px' }}>
        <p className="eyebrow">the boundary</p>
        <h1 style={{ fontSize: 38, lineHeight: 1.16, margin: '10px 0 0', maxWidth: 720 }}>
          An AI agent can do the work.
          <br />
          Only a verified human can{' '}
          <span style={{ color: 'var(--indigo-bright)' }}>sign for it</span>.
        </h1>
        <p className="muted" style={{ maxWidth: 560, marginTop: 18, fontSize: 15 }}>
          Every authorisation is written to a hash chain, one block per event.
          An agent reaching for a signature is refused — and the refusal is
          recorded, because that is exactly what an auditor wants to see.
        </p>

        <div style={{ display: 'grid', gap: 14, marginTop: 44 }}>
          {ROUTES.map((r) => (
            <Link key={r.to} to={r.to} style={{ color: 'inherit', textDecoration: 'none' }}>
              <Card className="hover-lift">
                <div className="between">
                  <div>
                    <h3 style={{ fontSize: 15 }}>{r.name}</h3>
                    <p className="muted small" style={{ margin: '5px 0 0' }}>{r.line}</p>
                  </div>
                  <span className={`badge ${r.state === 'live' ? 'badge-signed' : 'badge-idle'}`}>
                    {r.state}
                  </span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
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
      <Route path="/dashboard" element={<Dashboard />} />
    </Routes>
  </BrowserRouter>
)
