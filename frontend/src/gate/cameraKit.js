/**
 * Perfect Corp JS Camera Kit wrapper (implementation.md Step 2c).
 *
 * `comprehensive` mode runs AI Skin Analysis AND AI Face Attributes from a
 * single capture, and validates face position, lighting, angle and distance
 * in-browser before it will fire. That kills the "strict photo requirements
 * will fail users" risk in one move: the SDK refuses a bad frame rather than
 * failing server-side after a twenty-second wait.
 *
 * Requires a secure context — the SDK will not initialise over plain HTTP.
 */

const SDK_GLOBAL = 'YMK'

/** HD needs >= 1080px on the short side, so ask for 1920 and check what we got. */
export const HD_MIN_SHORT_SIDE = 1080

export function isSdkReady() {
  return typeof window !== 'undefined' && typeof window[SDK_GLOBAL] !== 'undefined'
}

export async function waitForSdk(timeoutMs = 10000) {
  const started = Date.now()
  while (!isSdkReady()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error(
        'Perfect Corp Camera Kit failed to load. Check the script tag in index.html ' +
          'and confirm the page is served over HTTPS.'
      )
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  return window[SDK_GLOBAL]
}

/**
 * Open the camera at the highest resolution the device will give us.
 * Returns { stream, track, settings } so the caller can verify the HD floor.
 */
export async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('getUserMedia unavailable — this requires a secure context (HTTPS).')
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: 'user',
      width: { ideal: 1920 },
      height: { ideal: 1920 },
    },
    audio: false,
  })

  const track = stream.getVideoTracks()[0]
  const settings = track.getSettings()
  const shortSide = Math.min(settings.width ?? 0, settings.height ?? 0)

  return {
    stream,
    track,
    settings,
    /** Below this, HD skin analysis is not worth the 12-22 units. */
    meetsHdFloor: shortSide >= HD_MIN_SHORT_SIDE,
    shortSide,
  }
}

/** Grab one frame from a live video element as a JPEG blob. */
export async function captureFrame(videoEl, quality = 0.95) {
  const canvas = document.createElement('canvas')
  canvas.width = videoEl.videoWidth
  canvas.height = videoEl.videoHeight
  canvas.getContext('2d').drawImage(videoEl, 0, 0)

  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), 'image/jpeg', quality)
  })
}

/**
 * Capture a multi-frame sequence driven by the server's challenge spec.
 *
 * This is the raw material for check 1: each frame is taken under a colour the
 * server chose, so the verifier can assert that the volatile channels moved in
 * the physically correct direction between frames.
 *
 * The flash colours come from the server nonce and are never known to the
 * client in advance — that is what defeats a pre-recorded injected stream.
 *
 * `challenge` is the server's `client_view()`: frames are
 * `{index, colour, hex, pose, hd, hold_ms}`. `colour` is a name ("amber"); `hex`
 * is the value to actually paint. Each frame's own `hold_ms` is honoured
 * rather than a fixed delay, because check 1's timing signal fails any gap
 * shorter than the hold time it asked for.
 *
 * Every hold is floored at MIN_HOLD_MS as well. The server refuses to issue a
 * faster sequence, so this is redundant against a correct server — which is
 * the point. The failure it guards is a spec that arrives malformed, from a
 * proxy or a stale deployment, and the consequence of that reaching the screen
 * is a strobe in front of someone with photosensitive epilepsy. The check that
 * costs nothing goes on the side where the harm is physical.
 */
export const MIN_HOLD_MS = 500

export async function captureChallengeSequence(videoEl, flashEl, challenge, opts = {}) {
  const { settleMs = MIN_HOLD_MS, quality = 0.95, onFrame } = opts
  const frames = []
  // A steady challenge paints once and never repaints, so the screen makes one
  // transition for the whole capture instead of one per frame.
  const flashing = challenge.flashing !== false

  for (const f of challenge.frames) {
    // The prompt has to be shown *before* the hold, not after: the pose is
    // what the frame is evidence of, and a person told to move once the
    // shutter has closed has been asked for nothing.
    onFrame?.(f, frames.length, challenge.frames.length)

    if (flashing || frames.length === 0) {
      flashEl.style.backgroundColor = f.hex
      flashEl.style.opacity = '1'
    }

    // Let the display and the sensor's auto-exposure settle before sampling,
    // then stay on the colour for as long as the challenge demanded.
    const hold = Math.max(f.hold_ms ?? 0, settleMs, MIN_HOLD_MS)
    await new Promise((r) => setTimeout(r, hold))

    const blob = await captureFrame(videoEl, quality)
    frames.push({
      frameIndex: f.index,
      colour: f.colour,
      blob,
      capturedAt: Date.now(),
      pose: f.pose ?? null,
    })
  }

  flashEl.style.opacity = '0'
  return frames
}

export function stopCamera(stream) {
  stream?.getTracks().forEach((t) => t.stop())
}
