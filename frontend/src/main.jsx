import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'

import Gate from './gate/Gate.jsx'
import Review from './review/Review.jsx'
import Verify from './verify/Verify.jsx'
import Dashboard from './dashboard/Dashboard.jsx'

function Home() {
  return (
    <main style={{ padding: 24, fontFamily: 'system-ui', color: '#e8eaf0', background: '#0b0d12', minHeight: '100vh' }}>
      <h1>STRATUM</h1>
      <p style={{ opacity: 0.7 }}>AI agents can do the work. Only a verified human can sign for it.</p>
      <ul>
        <li><Link to="/gate/demo">Gate</Link> — capture + three checks</li>
        <li><Link to="/review">Reviewer console</Link> — Nutrient DWS Viewer</li>
        <li><Link to="/verify">Public verifier</Link> — check an attestation</li>
        <li><Link to="/dashboard">Tenant dashboard</Link></li>
      </ul>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/gate/:gateId" element={<Gate />} />
      <Route path="/review" element={<Review />} />
      <Route path="/verify" element={<Verify />} />
      <Route path="/dashboard" element={<Dashboard />} />
    </Routes>
  </BrowserRouter>
)
