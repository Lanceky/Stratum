/**
 * The claim console: one human, one claim.
 *
 * Everywhere else in this project the question is "is this the enrolled
 * signer?" — a comparison against one person on file. Here it is "is this
 * anyone we have already seen?", swept across a whole campaign, and the
 * arithmetic is not the same. The dangerous error inverts: a false accept in
 * binding lets a stranger move money, but a false *match* here takes an
 * allocation away from someone who was entitled to it.
 *
 * So the console is built to be attacked rather than admired. You choose who
 * walks up to the camera — the same person again, their sibling, a stranger —
 * and the machine has to answer in front of you. A demo that could only show
 * the face that passes would be proving nothing.
 *
 * The captures are synthetic and the page says so. Every distance here
 * measures the matcher, not real skin.
 */

import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Card, Hash, Mark, Spinner } from '../ui.jsx'

const API = import.meta.env.VITE_XANO_API_BASE ?? '/api'

const COHORT = ['ada', 'brix', 'cyrus', 'dana']

// Three people who could present themselves, and what each one is testing.
// Named by the mistake they would cause, because that is the reason to try
// them: "a close relative" is not a variation, it is the case where refusing
// is the expensive answer.
const WHO = [
  {
    key: 'self',
    label: 'the same person, again',
    line: 'A second wallet, a different angle. The attack this check exists for.',
    want: 'should be caught',
  },
  {
    key: 'sibling',
    label: 'a close relative',
    line: 'Similar face, different person. Turning them away costs an honest person their allocation.',
    want: 'must not be auto-refused',
  },
  {
    key: 'stranger',
    label: 'somebody new',
    line: 'Nobody on the roster. The ordinary case, broken quietly by a wrong threshold.',
    want: 'should be allowed',
  },
]

const VERDICT_LINE = {
  UNIQUE: 'Nobody on this roster is this person. They may claim.',
  DUPLICATE: 'This person is already on the roster. A second claim will be refused.',
  REVIEW: 'The distance falls in the band where a relative and a badly captured photograph overlap. The machine will not decide this one.',
}

const S = {
  page: { maxWidth: 1180, margin: '0 auto', padding: '40px 24px 96px' },
  split: { display: 'grid', gap: 20, gridTemplateColumns: 'minmax(0,1fr) 360px', alignItems: 'start' },
  rail: { position: 'sticky', top: 24, display: 'grid', gap: 14 },
  pick: { display: 'grid', gap: 8 },
  dist: { display: 'grid', gap: 6, marginTop: 14 },
  bar: { position: 'relative', height: 10, borderRadius: 5, background: 'var(--surface-2)' },
}

const pct = (x) => `${(Number(x) * 100).toFixed(2)}%`

async function detailOf(res, fallback) {
  try { return (await res.json()).detail ?? fallback } catch { return fallback }
}

/**
 * Where this capture landed, against the two thresholds that decide it.
 *
 * Drawn rather than tabulated because the whole argument is about a band. The
 * verdict is not a number crossing a line, it is a number falling into one of
 * three regions, and the middle region is what the product is for. Three
 * floats in a table hide that; a bar with the band marked on it does not.
 */
function Distance({ nearest, thresholds }) {
  if (!nearest || !thresholds || nearest.distance == null) return null
  const max = Math.max(thresholds.unique_above * 1.4, nearest.distance * 1.1)
  const at = (v) => `${Math.min(100, Math.max(0, (v / max) * 100))}%`
  return (
    <div style={S.dist}>
      <div className="wrap" style={{ justifyContent: 'space-between' }}>
        <span className="dim small">distance to the closest person on the roster</span>
        <code className="mono small">{Number(nearest.distance).toFixed(2)}</code>
      </div>
      <div style={S.bar}>
        <div style={{
          position: 'absolute', left: 0, width: at(thresholds.duplicate_below),
          top: 0, bottom: 0, borderRadius: '5px 0 0 5px', background: 'rgba(239,68,68,.30)',
        }} />
        <div style={{
          position: 'absolute', left: at(thresholds.duplicate_below),
          right: `calc(100% - ${at(thresholds.unique_above)})`,
          top: 0, bottom: 0, background: 'rgba(245,158,11,.28)',
        }} />
        <div style={{
          position: 'absolute', left: at(thresholds.unique_above), right: 0,
          top: 0, bottom: 0, borderRadius: '0 5px 5px 0', background: 'rgba(34,197,94,.22)',
        }} />
        <div style={{
          position: 'absolute', left: at(nearest.distance), top: -5, bottom: -5,
          width: 3, marginLeft: -1, borderRadius: 2, background: '#fff',
          boxShadow: '0 0 0 1px rgba(0,0,0,.55)',
        }} title={`distance ${nearest.distance}`} />
      </div>
      <div className="wrap small dim" style={{ justifyContent: 'space-between' }}>
        <span>same person</span>
        <span>could be either</span>
        <span>different people</span>
      </div>
    </div>
  )
}

export default function Claim() {
  // A fresh campaign per visit. Two people demoing at once on the same server
  // would otherwise land in each other's roster, and the second would see a
  // duplicate finding that had nothing to do with what they did.
  const [context] = useState(() => `airdrop-${Math.random().toString(36).slice(2, 7)}`)
  const [roster, setRoster] = useState(null)
  const [enrolments, setEnrolments] = useState({})
  const [person, setPerson] = useState('ada')
  const [variant, setVariant] = useState('self')
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState(null)
  const [signed, setSigned] = useState(null)
  const [error, setError] = useState('')
  const [pose, setPose] = useState(1)

  const loadRoster = useCallback(async () => {
    try {
      const r = await fetch(`${API}/claims/roster/${context}`)
      if (r.ok) setRoster(await r.json())
    } catch { /* the panel stays empty; the page still works */ }
  }, [context])

  useEffect(() => { loadRoster() }, [loadRoster])

  const capture = async (who, kind, at) => {
    const r = await fetch(`${API}/demo/claimant`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ person: who, variant: kind, pose: at }),
    })
    if (!r.ok) throw new Error(await detailOf(r, `claimant ${r.status}`))
    return (await r.json()).capture
  }

  const addToRoster = async (who, kind, at) => {
    const r = await fetch(`${API}/claims/enrol`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        context, subject_ref: who, capture: await capture(who, kind, at),
      }),
    })
    if (!r.ok) throw new Error(await detailOf(r, `enrol ${r.status}`))
    return (await r.json()).enrolment_id
  }

  const enrol = async (who) => {
    setBusy(`enrol:${who}`); setError('')
    try {
      const id = await addToRoster(who, 'self', 0)
      setEnrolments((m) => ({ ...m, [who]: id }))
      await loadRoster()
    } catch (e) { setError(String(e.message ?? e)) } finally { setBusy('') }
  }

  /**
   * The claim, in the order the real flow runs it.
   *
   * A gate is opened first so the sweep has somewhere to be recorded. Writing
   * the evidence to a gate rather than returning it loose is what makes the
   * finding reviewable later — a verdict nobody can look up afterwards is an
   * opinion, not a record.
   */
  const claim = async () => {
    setBusy('claim'); setError(''); setResult(null); setSigned(null)
    try {
      const at = (pose % 2) + 1
      setPose(pose + 1)
      const face = await capture(person, variant, at)

      const g = await fetch(`${API}/demo/gate?mode=one_human_one_claim&ttl_s=900`,
        { method: 'POST' })
      if (!g.ok) throw new Error(`gate ${g.status}`)
      const gate = await g.json()

      const wallet = `0x${Array.from({ length: 40 },
        () => '0123456789abcdef'[Math.floor(Math.random() * 16)]).join('')}`

      const r = await fetch(`${API}/claims/verify`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ context, address: wallet, gate_id: gate.id, capture: face }),
      })
      if (!r.ok) throw new Error(await detailOf(r, `verify ${r.status}`))
      const body = await r.json()
      setResult({ ...body, gate: gate.id, wallet, who: person, as: variant, pose: at })
    } catch (e) { setError(String(e.message ?? e)) } finally { setBusy('') }
  }

  /**
   * Settle the gate, then issue the claim.
   *
   * The gate is walked to the verdict the sweep actually produced, not to
   * PASS. Signing a duplicate as a pass was the first thing this console did
   * wrong, and it is the exact dishonesty the rest of the system is built to
   * prevent: a contract would have received a signed approval for a claim the
   * machine had just refused.
   *
   * Which enrolment identifies this claimant is the other half, because the
   * nullifier derives from it. A returning face is identified by the enrolment
   * it matched — which is what makes their second claim collide with their
   * first. A newcomer has none yet, so they are enrolled here: joining the
   * roster is part of claiming, not a step someone can skip to stay
   * uncheckable next time.
   *
   * REVIEW is not settled here at all. A gate in that band is waiting for a
   * named person, and this page has no name to give.
   */
  const sign = async () => {
    if (!result) return
    const verdict = result.uniqueness.verdict
    const settleAs = { UNIQUE: 'PASS', DUPLICATE: 'FAIL' }[verdict]
    if (!settleAs) return

    setBusy('sign'); setError('')
    try {
      const enrolmentId = verdict === 'UNIQUE'
        ? await addToRoster(result.who, result.as, result.pose)
        : result.uniqueness.nearest?.enrolment_id

      const steps = [['CHALLENGED', 'system'], ['CAPTURED', 'human'],
        ['SCORED', 'system'], [settleAs, 'system']]
      // Only a pass goes on to SIGNED, and only a human may take it there.
      // A refusal is already final — there is nothing for a person to sign.
      if (settleAs === 'PASS') steps.push(['SIGNED', 'human'])

      for (const [to, actor] of steps) {
        const r = await fetch(`${API}/gates/${result.gate}/transition`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ to, actor }),
        })
        if (!r.ok) throw new Error(await detailOf(r, `transition ${to}: ${r.status}`))
      }

      const r = await fetch(`${API}/claims/${result.gate}/signature`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ context, address: result.wallet, enrolment_id: enrolmentId }),
      })
      setSigned({ ok: r.ok, status: r.status, settled: settleAs, body: await r.json() })
      await loadRoster()
    } catch (e) { setError(String(e.message ?? e)) } finally { setBusy('') }
  }

  const u = result?.uniqueness
  const enrolled = roster?.roster_size ?? 0

  return (
    <main style={S.page}>
      <div className="wrap" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
        <Link to="/" className="dim small"><Mark /> ← back</Link>
        <span className="dim small mono">{context}</span>
      </div>

      <h1 style={{ margin: '0 0 6px', fontSize: 30 }}>One human, one claim</h1>
      <p className="muted" style={{ maxWidth: 640, marginTop: 0 }}>
        A wallet is free to create; a person is not. Airdrops, votes, faucets —
        one share each. This checks the claimant against everyone already on the
        roster, and refuses to guess when the answer is close.
      </p>
      <p className="small dim" style={{ maxWidth: 640 }}>
        Faces are synthetic: these distances measure the matcher, not real skin.
        That caveat travels onto the certificate.
      </p>

      <div style={S.split} className="review-split">
        <div style={{ display: 'grid', gap: 20 }}>
          <Card title="the campaign roster"
            right={<span className="dim small">{enrolled} enrolled</span>}>
            <p className="small muted" style={{ marginTop: 0 }}>
              Enrolment is separate from claiming: a roster that grew as a side
              effect of claiming could never answer <em>was this person already
              here?</em> — the claimant would have just added themselves.
            </p>
            <div className="wrap" style={{ gap: 8 }}>
              {COHORT.map((who) => (
                <button key={who} className="btn btn-ghost" onClick={() => enrol(who)}
                  disabled={!!busy}>
                  {busy === `enrol:${who}` ? <Spinner /> : `enrol ${who}`}
                  {enrolments[who] ? ' ✓' : ''}
                </button>
              ))}
            </div>
            {enrolled > 0 && (
              <p className="small dim" style={{ marginBottom: 0, marginTop: 14 }}>
                A full sweep carries a{' '}
                <strong>{pct(roster.false_match_across_a_full_sweep)}</strong>{' '}
                chance a match is coincidence. It grows with every enrolment,
                so it sits beside the verdict, not in a footnote.
              </p>
            )}
          </Card>

          {u && (
            <Card title="the sweep"
              right={<Badge value={u.ran ? u.verdict : 'DID NOT RUN'} />}>
              <p style={{ marginTop: 0 }}>{VERDICT_LINE[u.verdict] ?? u.reason}</p>
              <p className="small dim">{u.reason}</p>
              <Distance nearest={u.nearest} thresholds={u.thresholds} />
              <dl className="kv small" style={{ marginTop: 18 }}>
                <dt>compared against</dt>
                <dd>
                  {u.comparisons_run} of {u.roster_size} enrolments
                  {u.comparisons_skipped > 0 && (
                    <span style={{ color: 'var(--amber)' }}>
                      {' '}— {u.comparisons_skipped} could not be compared, and
                      nothing is asserted about them
                    </span>
                  )}
                </dd>
                <dt>chance of coincidence</dt>
                <dd>
                  {pct(u.false_match.across_this_sweep)}{' '}
                  <span className="dim">
                    (decided automatically only below{' '}
                    {pct(u.false_match.auto_decide_ceiling)})
                  </span>
                </dd>
                <dt>gate</dt>
                <dd><code className="mono">{result.gate.slice(0, 8)}…</code></dd>
              </dl>
              {u.verdict === 'REVIEW' && (
                <p className="small" style={{
                  borderLeft: '2px solid var(--amber)', paddingLeft: 12,
                  marginBottom: 0,
                }}>
                  The case the system is built around. The measurement is real
                  and not good enough to act on, so it stops instead of picking
                  the answer that looks decisive. A named person rules.
                </p>
              )}
            </Card>
          )}

          {result && (
            <Card title="what the gate decided"
              right={<Badge value={result.decision.verdict} />}>
              <p className="small muted" style={{ marginTop: 0 }}>
                The sweep is one check of several. A clean sweep that never
                established a live person is not a claim, so the gate applies
                the whole requirement — not the part that was submitted.
              </p>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {result.decision.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </Card>
          )}

          {signed && (
            <Card
              title={!signed.ok ? 'refused'
                : signed.settled === 'FAIL' ? 'refusal, signed' : 'signed for a contract'}
              right={<Badge value={signed.ok ? (signed.settled === 'FAIL' ? 'FAIL' : 'SIGNED') : 'FAIL'} />}>
              {signed.ok ? (
                <>
                  <p className="small" style={{ marginTop: 0 }}>
                    {signed.settled === 'FAIL'
                      ? <>A signed refusal. The verdict reads{' '}
                        <code className="mono">FAIL</code> — a contract handed
                        an approval here would be acting on a claim the machine
                        had just rejected.</>
                      : <>What a verifying contract receives. It runs{' '}
                        <code className="mono">ecrecover</code> over the message
                        and compares the result against the issuer.</>}
                  </p>
                  <dl className="kv small">
                    <dt>verdict</dt>
                    <dd><Badge value={signed.body.claim.verdict} /></dd>
                    <dt>nullifier</dt>
                    <dd><Hash value={signed.body.claim.nullifier} chars={22} /></dd>
                    <dt>authorised by</dt>
                    <dd>{signed.body.claim.decided_by}</dd>
                    <dt>issuer</dt>
                    <dd><code className="mono">{signed.body.issuer}</code></dd>
                    <dt>scheme</dt>
                    <dd>{signed.body.scheme}</dd>
                  </dl>
                  <p className="small dim">
                    The nullifier stops a second wallet. Derived from enrolment
                    and campaign together, so it differs per campaign and cannot
                    track anyone between them. Not zero-knowledge — we hold the
                    secret and can link it back.
                  </p>
                  <p style={{ marginBottom: 0 }}>
                    <a className="btn btn-ghost"
                      href={`${API}/gates/${result.gate}/attestation.pdf?jurisdiction=EU_AMLR`}>
                      the sealed certificate →
                    </a>
                  </p>
                </>
              ) : (
                <>
                  <p style={{ marginTop: 0 }}>{signed.body.detail}</p>
                  <p className="small dim" style={{ marginBottom: 0 }}>
                    Refused by a unique index on (campaign, nullifier), not a
                    lookup. Under load a lookup is a suggestion; the index is
                    the guarantee.
                  </p>
                </>
              )}
            </Card>
          )}

          {error && (
            <Card title="error">
              <p className="small" style={{ margin: 0, color: 'var(--red)' }}>{error}</p>
            </Card>
          )}
        </div>

        <aside style={S.rail} className="review-rail">
          <Card title="who walks up">
            <p className="small muted" style={{ marginTop: 0 }}>
              Enrol somebody, then choose who presents themselves. The useful
              result is the one you did not expect.
            </p>
            <div className="wrap" style={{ gap: 6, marginBottom: 14 }}>
              {COHORT.map((who) => (
                <button key={who}
                  className={`btn ${person === who ? '' : 'btn-ghost'}`}
                  onClick={() => setPerson(who)} disabled={!!busy}>{who}</button>
              ))}
            </div>
            <div style={S.pick}>
              {WHO.map((w) => (
                <button key={w.key} className="tile" onClick={() => setVariant(w.key)}
                  disabled={!!busy} aria-pressed={variant === w.key}>
                  <span className="tile-name">{w.label}</span>
                  <span className="tile-line">{w.line}</span>
                  <span className="tile-want">{w.want}</span>
                </button>
              ))}
            </div>
            <button className="btn" onClick={claim} disabled={!!busy || enrolled === 0}
              style={{ width: '100%', marginTop: 14 }}>
              {busy === 'claim' ? <Spinner /> : 'try to claim'}
            </button>
            {enrolled === 0 && (
              <p className="small dim" style={{ marginBottom: 0, marginTop: 8 }}>
                Enrol somebody first — a sweep of an empty roster finds
                nothing, which is true and proves nothing.
              </p>
            )}
          </Card>

          {result && !signed && (
            <Card title={u.verdict === 'REVIEW' ? 'this one needs a person' : 'settle it'}>
              {u.verdict === 'REVIEW' ? (
                <>
                  <p className="small muted" style={{ marginTop: 0 }}>
                    Nothing to sign. The gate is queued with its evidence,
                    waiting for someone whose name goes on the outcome. This
                    page has no name to give it.
                  </p>
                  <Link className="btn" to="/review" style={{ width: '100%' }}>
                    open the reviewer console →
                  </Link>
                </>
              ) : (
                <>
                  <p className="small muted" style={{ marginTop: 0 }}>
                    {u.verdict === 'DUPLICATE'
                      ? 'Walked to FAIL, because that is what the sweep found. A refusal is still signed and certified — a contract needs to know a claim was refused, and by what.'
                      : 'Walked to SIGNED one legal step at a time. The last step admits no agent; that refusal is the product.'}
                  </p>
                  <button className="btn" onClick={sign} disabled={!!busy}
                    style={{ width: '100%' }}>
                    {busy === 'sign'
                      ? <Spinner />
                      : u.verdict === 'DUPLICATE' ? 'record the refusal' : 'sign and issue the claim'}
                  </button>
                </>
              )}
            </Card>
          )}
        </aside>
      </div>
    </main>
  )
}
