// Public attestation verifier — paste a hash, we resolve the DNS TXT record.
// Implemented in Step 10a.

import React from 'react'

import { Pending } from '../ui.jsx'

export default function Verify() {
  return (
    <Pending
      title="Public verifier"
      step="Step 10a"
      line="Paste an attestation hash and resolve it against the DNS TXT record
            published for it. Blocked on the name.com API token."
    />
  )
}
