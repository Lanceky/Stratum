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
 * This is the raw material for check 1: each frame is tagged with the colour
 * that was on screen when it was taken, so the verifier can assert that the
 * volatile channels moved in the physically correct direction.
 *
 * The flash colours come from the server nonce and are never known to the
 * client in advance — that is what defeats a pre-recorded injected stream.
 */
export async function captureChallengeSequence(videoEl, flashEl, challenge, opts = {}) {
  const { settleMs = 220, quality = 0.95 } = opts
  const frames = []

  for (const [index, colour] of challenge.colours.entries()) {
    flashEl.style.backgroundColor = colour
    flashEl.style.opacity = '1'

    // Let the display and the sensor's auto-exposure settle before sampling.
    await new Promise((r) => setTimeout(r, settleMs))

    const blob = await captureFrame(videoEl, quality)
    frames.push({
      index,
      colour,
      blob,
      capturedAt: Date.now(),
      pose: challenge.poses?.[index] ?? null,
    })
  }

  flashEl.style.opacity = '0'
  return frames
}

export function stopCamera(stream) {
  stream?.getTracks().forEach((t) => t.stop())
}
