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

import { openCamera, stopCamera, captureChallengeSequence } from './cameraKit.js'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

const S = {
  page: {
    minHeight: '100vh', background: '#0b0d12', color: '#e8eaf0',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: 24, textAlign: 'center',
  },
  video: {
    width: '100%', maxWidth: 420, borderRadius: 16, background: '#000',
    transform: 'scaleX(-1)', // mirror, so it feels like a mirror
  },
  flash: {
    position: 'fixed', inset: 0, opacity: 0, pointerEvents: 'none',
    transition: 'background-color 60ms linear', zIndex: 10,
  },
  btn: {
    marginTop: 20, padding: '14px 28px', fontSize: 16, borderRadius: 10,
    border: 0, background: '#4f7cff', color: '#fff', cursor: 'pointer',
  },
  note: { opacity: 0.6, fontSize: 13, maxWidth: 420, lineHeight: 1.5 },
  warn: { color: '#ffb020', fontSize: 13, maxWidth: 420 },
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
        <h1>Verify you're here</h1>
        <p style={S.note}>
          We'll flash a few colours on screen and take several photos. We check
          that a real person is present and that it's you.
        </p>
        <p style={S.note}>
          <strong>No photo is ever stored.</strong> Images exist in memory only
          for the length of this check. We keep a derived, non-reversible score —
          never anything that could reconstruct your face.
        </p>
        {error && <p style={S.warn}>{error}</p>}
        <button style={S.btn} onClick={begin}>I understand — open camera</button>
      </main>
    )
  }

  return (
    <main style={S.page}>
      <div ref={flashRef} style={S.flash} />
      <video ref={videoRef} playsInline muted style={S.video} />

      {camera && !camera.meetsHdFloor && (
        <p style={S.warn}>
          Camera is {camera.shortSide}px on the short side; HD analysis wants
          1080px. Continuing at reduced fidelity.
        </p>
      )}

      {phase === 'ready' && (
        <>
          <p style={S.note}>Hold still. Turn screen brightness up if you can.</p>
          <button style={S.btn} onClick={runCapture}>Start check</button>
        </>
      )}
      {phase === 'capturing' && <p style={S.note}>Hold still…</p>}
      {phase === 'scoring' && <p style={S.note}>Checking…</p>}
      {phase === 'done' && result && (
        <>
          <h2>{result.decision}</h2>
          <p style={S.note}>{result.reason}</p>
        </>
      )}
      {error && <p style={S.warn}>{error}</p>}
    </main>
  )
}
