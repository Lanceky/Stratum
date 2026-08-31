/**
 * Gate capture UI (implementation.md Step 2c).
 *
 * The flow a human sees: consent → camera → challenge sequence → result.
 * The consent screen is not decoration — under the EU AI Act a system doing
 * biometric verification carries transparency duties, so we say what we do
 * in plain language before the camera opens (context.md §11.8).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { Badge, Mark } from '../ui.jsx'
import { openCamera, stopCamera, captureChallengeSequence } from './cameraKit.js'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

const S = {
  page: {
    minHeight: '100vh',
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: 24, textAlign: 'center', gap: 4,
  },
  video: {
    width: '100%', maxWidth: 420, borderRadius: 'var(--r-lg)', background: '#000',
    border: '1px solid var(--border-indigo)',
    boxShadow: '0 0 0 1px rgba(99,102,241,0.12), 0 12px 48px rgba(99,102,241,0.18)',
    transform: 'scaleX(-1)', // mirror, so it feels like a mirror
  },
  flash: {
    position: 'fixed', inset: 0, opacity: 0, pointerEvents: 'none',
    transition: 'background-color 60ms linear', zIndex: 10,
  },
  note: { maxWidth: 420 },
}

export default function Gate() {
  const { gateId } = useParams()
  const videoRef = useRef(null)
  const flashRef = useRef(null)
  const streamRef = useRef(null)

  const [phase, setPhase] = useState('consent')
  const [challenge, setChallenge] = useState(null)
  const [camera, setCamera] = useState(null)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  useEffect(() => () => stopCamera(streamRef.current), [])

  const begin = useCallback(async () => {
    setError(null)
    try {
      // The challenge is fetched only now, and is never known to the client
      // in advance. A pre-recorded stream cannot anticipate it.
      const res = await fetch(`${API}/gates/${gateId}/challenge`, { method: 'POST' })
      if (!res.ok) throw new Error(`challenge request failed (${res.status})`)
      const spec = await res.json()
      setChallenge(spec)

      const cam = await openCamera()
      streamRef.current = cam.stream
      setCamera(cam)
      videoRef.current.srcObject = cam.stream
      await videoRef.current.play()

      setPhase('ready')
    } catch (e) {
      setError(e.message)
      setPhase('consent')
    }
  }, [gateId])

  const runCapture = useCallback(async () => {
    setPhase('capturing')
    try {
      const frames = await captureChallengeSequence(
        videoRef.current, flashRef.current, challenge
      )

      const form = new FormData()
      form.append('nonce', challenge.nonce)
      frames.forEach((f, i) => {
        form.append(`frame_${i}`, f.blob, `frame_${i}.jpg`)
        form.append(`meta_${i}`, JSON.stringify({
          colour: f.colour, pose: f.pose, capturedAt: f.capturedAt,
        }))
      })

      setPhase('scoring')
      const res = await fetch(`${API}/gates/${gateId}/capture`, {
        method: 'POST', body: form,
      })
      if (!res.ok) throw new Error(`capture rejected (${res.status})`)

      setResult(await res.json())
      setPhase('done')
    } catch (e) {
      setError(e.message)
      setPhase('ready')
    } finally {
      stopCamera(streamRef.current)
    }
  }, [challenge, gateId])

  if (phase === 'consent') {
    return (
      <main style={S.page}>
        <Mark />
        <h1 style={{ fontSize: 28, margin: '18px 0 4px' }}>Verify you're here</h1>
        <p className="muted" style={S.note}>
          We'll flash a few colours on screen and take several photos. We check
          that a real person is present and that it's you.
        </p>
        <div className="card" style={{ maxWidth: 420, marginTop: 14, textAlign: 'left' }}>
          <p className="eyebrow" style={{ marginBottom: 8 }}>what we keep</p>
          <p className="small" style={{ margin: 0 }}>
            <strong>No photo is ever stored.</strong> Images exist in memory only
            for the length of this check. We keep a derived, non-reversible
            score — never anything that could reconstruct your face.
          </p>
        </div>
        {error && (
          <div className="alert" style={{ maxWidth: 420, marginTop: 14 }}>
            <p className="small" style={{ margin: 0 }}>{error}</p>
          </div>
        )}
        <button className="btn btn-primary" style={{ marginTop: 22 }} onClick={begin}>
          I understand — open camera
        </button>
      </main>
    )
  }

  return (
    <main style={S.page}>
      <div ref={flashRef} style={S.flash} />
      <video ref={videoRef} playsInline muted style={S.video} />

      {camera && !camera.meetsHdFloor && (
        <div className="alert alert-amber" style={{ maxWidth: 420, marginTop: 14 }}>
          <p className="small" style={{ margin: 0 }}>
            Camera is {camera.shortSide}px on the short side; HD analysis wants
            1080px. Continuing at reduced fidelity.
          </p>
        </div>
      )}

      {phase === 'ready' && (
        <>
          <p className="muted" style={{ ...S.note, marginTop: 18 }}>
            Hold still. Turn screen brightness up if you can.
          </p>
          <button className="btn btn-primary" onClick={runCapture}>Start check</button>
        </>
      )}
      {phase === 'capturing' && (
        <p className="muted" style={{ marginTop: 18 }}>Hold still…</p>
      )}
      {phase === 'scoring' && (
        <p className="muted" style={{ marginTop: 18 }}>Checking…</p>
      )}
      {phase === 'done' && result && (
        <div style={{ marginTop: 20 }}>
          <Badge value={result.decision} />
          <p className="muted" style={{ ...S.note, marginTop: 10 }}>{result.reason}</p>
        </div>
      )}
      {error && (
        <div className="alert" style={{ maxWidth: 420, marginTop: 14 }}>
          <p className="small" style={{ margin: 0 }}>{error}</p>
        </div>
      )}
    </main>
  )
}
