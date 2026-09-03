/**
 * Gate capture UI (implementation.md Step 2c).
 *
 * The flow a human sees: consent → camera → challenge sequence → result.
 * The consent screen is not decoration — under the EU AI Act a system doing
 * biometric verification carries transparency duties, so we say what we do
 * in plain language before the camera opens (context.md §11.8).
 *
 * Two disclosures, not one, and deliberately not merged. The biometric notice
 * is about what happens to the data; the photosensitivity warning is about what
 * happens to the person. Flashing content can trigger a seizure, and a single
 * "I agree" covering both would bury a physical-safety warning inside a privacy
 * notice — where nobody reads it, and where agreeing to it is not really a
 * choice because there is no alternative on offer. So the warning carries its
 * own way out: a capture that never flashes, which costs the light-response
 * evidence and therefore sends the gate to a reviewer rather than passing it.
 *
 * The camera is opened *before* the challenge is fetched. The button says
 * "open camera", so that is what it must do first; fetching first meant a
 * server error swallowed the permission prompt entirely and the person was
 * told the challenge failed when they had never been asked for anything.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { Badge, Mark, Next, Rail, Spinner } from '../ui.jsx'
import { remember, reviewLink } from '../journey.js'
import { openCamera, stopCamera, captureChallengeSequence } from './cameraKit.js'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

const S = {
  page: {
    // The topbar is 57px and only present on the consent and verdict screens.
    // Subtracting it keeps a screen that fits from acquiring a scrollbar, which
    // on the verdict screen would push the next step below the fold.
    minHeight: 'calc(100vh - 57px)',
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

/** What the person should do about it, per failure reason. */
const CAMERA_HELP = {
  NotAllowedError:
    'Camera access was blocked. This check cannot run without it — allow the ' +
    'camera from your browser’s address bar, then try again.',
  NotFoundError: 'No camera was found on this device.',
  NotReadableError:
    'The camera is in use by another application. Close it and try again.',
  OverconstrainedError:
    'This camera cannot supply a usable resolution for the check.',
}

/**
 * The server names a pose ("left", "up"); a person needs an instruction.
 *
 * Mirrored, deliberately. The video is flipped so it behaves like a mirror, so
 * "turn left" has to mean the direction the person sees themselves move, not
 * the direction in the raw frame. Check 1 only measures that the face moved
 * off-axis, so either direction satisfies it — but an instruction that fights
 * the picture makes people hesitate, and hesitation lands mid-pose.
 */
const POSE_COPY = {
  neutral: 'Hold still, look straight ahead',
  left: 'Now turn your head slightly left',
  right: 'Now turn your head slightly right',
  up: 'Now tilt your chin up slightly',
}

export default function Gate() {
  const { gateId: routeId } = useParams()
  const videoRef = useRef(null)
  const flashRef = useRef(null)
  const streamRef = useRef(null)

  const [gateId, setGateId] = useState(routeId === 'demo' ? null : routeId)
  const [phase, setPhase] = useState('consent')
  const [challenge, setChallenge] = useState(null)
  const [camera, setCamera] = useState(null)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [prompt, setPrompt] = useState(null)
  // Null until the person chooses. Not defaulted to true: the point of the
  // warning is that the flashing path is opted into, not opted out of.
  const [flashing, setFlashing] = useState(null)

  useEffect(() => () => stopCamera(streamRef.current), [])

  const begin = useCallback(async (wantsFlash) => {
    setError(null)
    setFlashing(wantsFlash)
    setPhase('opening')

    // 1. Camera first. A denied permission is a different problem from a
    //    server that is down, and the person needs to be told which one.
    let cam
    try {
      cam = await openCamera()
    } catch (e) {
      setError(CAMERA_HELP[e.name] ?? `Could not open the camera: ${e.message}`)
      setPhase('consent')
      return
    }
    streamRef.current = cam.stream
    setCamera(cam)

    // 2. Then the challenge, which is fetched only now and is never known to
    //    the client in advance — a pre-recorded stream cannot anticipate it.
    //    The browser names the gate, not the nonce, so there is nothing here
    //    for a client to substitute.
    try {
      let id = gateId
      if (id == null) {
        const made = await fetch(`${API}/demo/gate`, { method: 'POST' })
        if (!made.ok) throw new Error(`could not start a demo gate (${made.status})`)
        id = (await made.json()).id
        setGateId(id)
      }
      // Whether the agent opened this gate or the visitor entered at step two,
      // this is the gate the reviewer console should land on.
      remember(id)

      const res = await fetch(`${API}/challenge`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ gate_id: id, flashing: wantsFlash }),
      })
      if (!res.ok) throw new Error(await describe(res))
      setChallenge(await res.json())
      setPhase('ready')
    } catch (e) {
      stopCamera(streamRef.current)
      setError(e.message)
      setPhase('consent')
    }
  }, [gateId])

  // The stream is attached once the element exists, not inside begin(): at the
  // moment the camera opens the page is still on the consent screen and the
  // <video> has not been rendered, so srcObject there would land on null.
  useEffect(() => {
    const el = videoRef.current
    if (!el || !camera || el.srcObject) return
    el.srcObject = camera.stream
    el.play().catch(() => {})
  }, [camera, phase])

  const runCapture = useCallback(async () => {
    setPhase('capturing')
    try {
      const frames = await captureChallengeSequence(
        videoRef.current, flashRef.current, challenge,
        {
          onFrame: (f, i, n) => setPrompt({
            text: POSE_COPY[f.pose] ?? 'Hold still',
            at: i + 1,
            of: n,
          }),
        }
      )

      const form = new FormData()
      for (const f of frames) {
        form.append('frames', f.blob, `frame_${f.frameIndex}.jpg`)
        // Repeated field, one per frame, in the same order as the blobs. The
        // server pairs them by position and refuses a length mismatch rather
        // than guessing. Milliseconds — the server converts.
        form.append('captured_at', String(f.capturedAt))
      }

      setPhase('scoring')
      const res = await fetch(`${API}/gates/${gateId}/capture`, {
        method: 'POST', body: form,
      })
      if (!res.ok) throw new Error(await describe(res))

      setResult(await res.json())
      setPhase('done')
    } catch (e) {
      setError(e.message)
      setPhase('ready')
    } finally {
      stopCamera(streamRef.current)
    }
  }, [challenge, gateId])

  if (phase === 'consent' || phase === 'opening') {
    const busy = phase === 'opening'
    return (
      <>
      <div className="topbar">
        <Link to="/" style={{ textDecoration: 'none' }}><Mark /></Link>
        <Rail at={1} />
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>the gate</span>
      </div>
      <main style={S.page}>
        <h1 style={{ fontSize: 28, margin: '18px 0 4px' }}>Verify you're here</h1>
        <p className="muted" style={S.note}>
          A few photos over a few seconds. We check that a real person is
          present, and that it's you.
        </p>

        {/* Physical safety, stated before the data notice and before either
            button. A person who cannot safely watch this needs to know that
            first — everything else on this screen can wait. */}
        <div className="alert alert-amber" style={{ maxWidth: 420, marginTop: 16, textAlign: 'left' }}>
          <p className="eyebrow" style={{ marginBottom: 8 }}>
            flashing lights
          </p>
          <p className="small" style={{ margin: 0 }}>
            The standard check <strong>flashes colours on screen</strong>. This
            can trigger a seizure in people with photosensitive epilepsy. The
            flashes are kept below three per second and avoid saturated red, but
            if you are photosensitive — or simply would rather not — there is a
            version that never flashes.
          </p>
          <button
            className="btn"
            style={{ marginTop: 12, width: '100%' }}
            onClick={() => begin(false)}
            disabled={busy}
          >
            {busy && flashing === false
              ? <><Spinner /> Opening camera…</>
              : 'Use the no-flash check'}
          </button>
          <p className="small dim" style={{ margin: '8px 0 0' }}>
            It measures less, so a person reviews the result instead of the
            system deciding on its own.
          </p>
        </div>

        <div className="card" style={{ maxWidth: 420, marginTop: 12, textAlign: 'left' }}>
          <p className="eyebrow" style={{ marginBottom: 8 }}>what we keep</p>
          <p className="small" style={{ margin: 0 }}>
            <strong>No photo is ever stored.</strong> Images exist in memory only
            for the length of this check. We keep a derived, non-reversible
            score — never anything that could reconstruct your face.
          </p>
          <p className="small dim" style={{ margin: '8px 0 0' }}>
            This is biometric processing.{' '}
            <a href="/terms" target="_blank" rel="noreferrer">Terms and your rights</a>.
          </p>
        </div>

        {error && (
          <div className="alert" style={{ maxWidth: 420, marginTop: 14 }}>
            <p className="small" style={{ margin: 0 }}>{error}</p>
          </div>
        )}
        <button
          className="btn btn-primary"
          style={{ marginTop: 18 }}
          onClick={() => begin(true)}
          disabled={busy}
        >
          {busy && flashing !== false
            ? <><Spinner /> Opening camera…</>
            : error ? 'Try again' : 'I understand — open camera'}
        </button>
      </main>
      </>
    )
  }

  return (
    <>
    {/* No chrome while the camera is running: the person is meant to be
        looking at the lens, and a navigation bar at that moment is an
        invitation to leave mid-capture. It returns with the verdict. */}
    {phase === 'done' && (
      <div className="topbar">
        <Link to="/" style={{ textDecoration: 'none' }}><Mark /></Link>
        <Rail at={1} />
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>the gate</span>
      </div>
    )}
    <main style={S.page}>
      <div ref={flashRef} style={S.flash} />
      {/* Removed once the camera is stopped: leaving the element mounted put a
          dead black rectangle over the result, which reads as a failure. */}
      {phase !== 'done' && <video ref={videoRef} playsInline muted style={S.video} />}

      {camera && !camera.meetsHdFloor && phase !== 'done' && (
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
            {challenge?.frames?.length} photos over a few seconds.{' '}
            {challenge?.flashing === false
              ? 'The screen stays one steady colour.'
              : 'The screen will flash colours.'}{' '}
            Hold still and look straight ahead — near the end you'll be asked to
            turn your head once.
          </p>
          {challenge?.flashing === false && (
            <p className="small dim" style={{ ...S.note, marginTop: 0 }}>
              No-flash check — a person will review the result.
            </p>
          )}
          <button className="btn btn-primary" onClick={runCapture}>Start check</button>
        </>
      )}
      {phase === 'capturing' && (
        <div style={{ marginTop: 18 }}>
          <p style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            {prompt?.text ?? 'Hold still'}
          </p>
          <p className="eyebrow" style={{ marginTop: 6 }}>
            {prompt ? `${prompt.at} of ${prompt.of}` : ''}
          </p>
        </div>
      )}
      {phase === 'scoring' && (
        <p className="muted" style={{ marginTop: 18 }}><Spinner /> Checking…</p>
      )}
      {phase === 'done' && result && <Outcome result={result} gateId={gateId} />}
      {error && phase !== 'done' && (
        <div className="alert" style={{ maxWidth: 420, marginTop: 14 }}>
          <p className="small" style={{ margin: 0 }}>{error}</p>
        </div>
      )}
    </main>
    </>
  )
}

/**
 * What the decision means to the person who just stood in front of the camera.
 *
 * REVIEW is the interesting case and the one the demo turns on: it is not a
 * failure and must not read like one. The gate is now in a reviewer's queue,
 * and saying so is the difference between a system that referred a decision to
 * a human and one that appears to have broken.
 */
const OUTCOME_COPY = {
  pass: 'Verified. The action you authorised can proceed.',
  review: 'This needs a person to look at it. Your request is now with a ' +
          'reviewer — nothing is signed until they decide.',
  fail: 'This did not verify, so nothing was authorised.',
}

/**
 * Where each verdict leads.
 *
 * The gate page used to stop at the verdict, which stranded the visitor at
 * exactly the moment the demo becomes interesting: they were told a reviewer
 * now holds their request, and given no way to go and see that happen. Every
 * outcome opens onto something, so every outcome says what.
 */
const OUTCOME_NEXT = {
  review: {
    eyebrow: 'step 03',
    line: 'Your gate is in the queue now. The console is the other side of '
        + 'this screen: the same evidence, read by the person who has to put '
        + 'their name on the decision.',
    cta: 'Watch a human settle it →',
  },
  pass: {
    eyebrow: 'step 03',
    line: 'This one settled without a person. The reviewer console holds the '
        + 'gates that did not, which is where the harder half of the problem '
        + 'is: a sibling and a bad photograph overlap measurably.',
    cta: 'See the gates that could not settle →',
  },
  fail: {
    eyebrow: 'step 03',
    line: 'Nothing was authorised, and that is the correct outcome for a check '
        + 'that did not verify. The console shows the cases where refusing '
        + 'outright would have been the wrong answer.',
    cta: 'See what needs a person →',
  },
}

function Outcome({ result, gateId }) {
  const verdict = String(result.verdict ?? '').toLowerCase()
  const next = OUTCOME_NEXT[verdict]
  return (
    <div style={{ marginTop: 20, maxWidth: 420 }}>
      <Badge value={result.state ?? result.verdict} />
      <p className="muted" style={{ marginTop: 10 }}>
        {OUTCOME_COPY[verdict] ?? 'The check finished.'}
      </p>
      {result.reasons?.length > 0 && (
        // Folded away by default. The findings are written for the reviewer
        // who has to act on them, and they name internals — a person who has
        // just been told a human will look at their request is owed that
        // sentence, not a stack of check names they cannot do anything about.
        // Available rather than hidden: refusing to show the reasoning at all
        // would make this the kind of opaque system it exists to replace.
        <details style={{ marginTop: 14, textAlign: 'left' }}>
          <summary className="small muted" style={{ cursor: 'pointer' }}>
            What the system checked
          </summary>
          <ul className="small muted" style={{ paddingLeft: 18, marginTop: 8 }}>
            {result.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </details>
      )}
      {next && (
        <div style={{ textAlign: 'left' }}>
          <Next {...next} to={reviewLink(gateId)} />
        </div>
      )}
    </div>
  )
}

/** FastAPI puts the useful part in `detail`; the status code alone says little. */
async function describe(res) {
  try {
    const body = await res.json()
    const d = body.detail
    if (typeof d === 'string') return d
    if (d) return d.reason ?? JSON.stringify(d)
  } catch {
    // fall through to the status code
  }
  return `request failed (${res.status})`
}
