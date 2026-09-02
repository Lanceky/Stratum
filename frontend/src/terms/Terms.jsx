/**
 * Terms, disclaimers and the notices this system owes the person in front of
 * the camera.
 *
 * Two of these are not boilerplate, and are the reason this is a route rather
 * than a paragraph in a README.
 *
 * The photosensitivity notice is a physical-safety warning. Content flashing
 * between roughly 3 and 30 Hz triggers seizures in people with photosensitive
 * epilepsy; WCAG 2.3.1 makes it a Level A criterion, which is to say a floor.
 * The capture path enforces the limit in `challenge.py` rather than disclaiming
 * it here — a consent screen does not stop a seizure — and this page states
 * both the limit and the way to avoid the flashes altogether.
 *
 * The biometric notice is the EU AI Act / GDPR Article 9 transparency duty
 * (context.md §11.8). Facial data is special-category, and the honest position
 * is that this is a prototype which should not be handed a real identity.
 *
 * Written in the second person and in plain words on purpose. A notice nobody
 * can read is not a notice.
 */

import React from 'react'
import { Link } from 'react-router-dom'

import { Mark } from '../ui.jsx'

export default function Terms() {
  return (
    <main className="home" style={{ maxWidth: 760, gap: 32 }}>
      <header>
        <Link to="/" style={{ textDecoration: 'none' }}><Mark /></Link>
        <h1 className="home-h1" style={{ fontSize: 34, marginTop: 20 }}>
          Terms &amp; safety notices
        </h1>
        <p className="muted" style={{ marginTop: 12, fontSize: 15 }}>
          What STRATUM does to you and with your data, and where it stops. Read
          the first two sections before you use the camera.
        </p>
      </header>

      {/* First, because it is the only one that can hurt someone. */}
      <section>
        <div className="alert alert-amber" style={{ textAlign: 'left' }}>
          <p className="eyebrow" style={{ marginTop: 0, marginBottom: 8 }}>
            1 · photosensitive epilepsy — read before verifying
          </p>
          <p className="small" style={{ margin: 0 }}>
            <strong>
              The standard check flashes colours across the screen and can
              trigger a seizure in people with photosensitive epilepsy.
            </strong>{' '}
            If you have ever had a seizure, blackout, or unexplained loss of
            awareness triggered by flashing lights, screens or patterns — or if
            you have never been tested and are unsure — use the no-flash check.
          </p>
          <p className="small" style={{ marginBottom: 0 }}>
            The no-flash option is offered on every capture screen, before the
            camera opens. You do not have to explain why you are choosing it,
            and you are never asked to disclose a medical condition.
          </p>
        </div>

        <div className="card" style={{ marginTop: 14, textAlign: 'left' }}>
          <p className="eyebrow" style={{ marginTop: 0, marginBottom: 10 }}>
            what we do to reduce the risk
          </p>
          <ul className="small muted" style={{ paddingLeft: 18, margin: 0, lineHeight: 1.7 }}>
            <li>
              <strong>Under three flashes a second.</strong> Each colour is held
              for at least 500&nbsp;ms, so the screen changes at most twice a
              second. WCAG 2.3.1 sets the limit at three; the server refuses to
              issue a faster sequence and the browser refuses to paint one.
            </li>
            <li>
              <strong>No saturated red.</strong> Saturated red provokes seizures
              at lower intensities than any other hue, so no colour used in the
              sequence crosses the threshold the standard sets for it.
            </li>
            <li>
              <strong>A path with no flashes at all.</strong> One steady colour
              held for the whole capture — a single transition, which is not a
              flash.
            </li>
          </ul>
          <p className="small dim" style={{ marginBottom: 0 }}>
            These are limits, not a guarantee. No screen-based check can be
            certified safe for every individual, and reducing a risk is not the
            same as removing it. If you are in any doubt, do not use the
            flashing check.
          </p>
        </div>

        <div className="card" style={{ marginTop: 14, textAlign: 'left' }}>
          <p className="eyebrow" style={{ marginTop: 0, marginBottom: 10 }}>
            what the no-flash check costs you
          </p>
          <p className="small muted" style={{ margin: 0 }}>
            Nothing in access terms — it is not a lesser queue and it is not
            slower. But it genuinely measures less. The flashing sequence exists
            to test whether your skin responds to changing light the way skin
            does, and a steady screen cannot ask that question. So the system
            does not pretend to have an answer: it records that the light test
            never ran, and sends your gate to a human reviewer instead of
            deciding on its own.
          </p>
          <p className="small dim" style={{ marginBottom: 0 }}>
            Review rather than refusal, deliberately. Refusing would turn an
            accessibility need into a wall; passing anyway would turn it into
            the cheapest way past the check.
          </p>
        </div>
      </section>

      <section>
        <p className="eyebrow" style={{ marginTop: 0 }}>2 · biometric data</p>
        <div className="card" style={{ textAlign: 'left' }}>
          <p className="small" style={{ marginTop: 0 }}>
            <strong>This is biometric processing.</strong> Images of your face
            are analysed to decide whether a live human is present, and whether
            that human is you. Under the GDPR this is special-category data
            (Article&nbsp;9); under Illinois BIPA and comparable US state laws it
            is a biometric identifier; under the EU AI Act you are entitled to be
            told in advance that you are interacting with such a system. This
            page and the screen before the camera are that notice.
          </p>
          <ul className="small muted" style={{ paddingLeft: 18, lineHeight: 1.7 }}>
            <li>
              <strong>No image is stored.</strong> Frames exist in memory for
              the length of the check and are then discarded. There is no photo
              archive and nothing is written to disk.
            </li>
            <li>
              <strong>What is kept is derived and non-reversible</strong> —
              scores and distances, from which a face cannot be reconstructed.
            </li>
            <li>
              <strong>Claim gates keep a nullifier, not an identity</strong>: a
              keyed one-way tag that reveals only whether this person has already
              claimed in this context. It cannot be reversed to a face, and the
              same person in a different context produces an unlinkable tag.
            </li>
            <li>
              <strong>Every event is written to an append-only chain</strong>,
              refusals included. It records what was decided and by whom — not
              what you look like.
            </li>
          </ul>
          <p className="small" style={{ marginBottom: 0 }}>
            <strong>Your consent is the basis, and you may decline.</strong>{' '}
            Nothing is captured until you press the button. Leaving before
            capture leaves no record of you beyond the gate the agent had
            already opened.
          </p>
        </div>
      </section>

      <section>
        <p className="eyebrow" style={{ marginTop: 0 }}>
          3 · a human decides, and you can ask for one
        </p>
        <div className="card" style={{ textAlign: 'left' }}>
          <p className="small muted" style={{ margin: 0 }}>
            STRATUM never signs anything on your behalf. A verdict of{' '}
            <strong>REVIEW</strong> means a named person must look at your gate
            before anything happens, and the system is built to reach that
            outcome whenever the evidence does not settle the question — which is
            the point of it. GDPR Article&nbsp;22 gives you a right not to be
            subject to a purely automated decision with legal or similarly
            significant effects. Here that right is the default path, not an
            appeal you have to go looking for.
          </p>
        </div>
      </section>

      <section>
        <p className="eyebrow" style={{ marginTop: 0 }}>4 · what this is not</p>
        <div className="card" style={{ textAlign: 'left' }}>
          <p className="small" style={{ marginTop: 0 }}>
            <strong>
              This is a prototype built for a hackathon. Do not use it to
              protect anything real, and do not give it a real identity
              document.
            </strong>
          </p>
          <ul className="small muted" style={{ paddingLeft: 18, lineHeight: 1.7, marginBottom: 0 }}>
            <li>
              Its published figures come from a physical simulation, not from
              cameras in a room. No physical presentation attack has been run
              against it.
            </li>
            <li>
              A printed photograph responds to coloured light much as skin does.
              Only the depth signal separates them, and it does so partially.
            </li>
            <li>
              The demo signing key is not held in custody hardware. A signature
              it produces shows the chain is intact — not that a regulated
              institution stands behind it.
            </li>
            <li>
              It is not KYC, not identity proofing against a document, and not
              evidence of anyone's legal identity.
            </li>
          </ul>
        </div>
      </section>

      <p className="small dim" style={{ margin: 0 }}>
        <Link to="/">← back to the demo</Link>
      </p>
    </main>
  )
}
