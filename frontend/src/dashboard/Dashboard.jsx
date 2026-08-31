// Tenant dashboard — gates, attestations, typosquat threat sweep.
// Implemented in Step 10.

import React from 'react'

import { Pending } from '../ui.jsx'

export default function Dashboard() {
  return (
    <Pending
      title="Tenant dashboard"
      step="Step 10"
      line="Gate throughput, issued attestations and the typosquat sweep across
            look-alike domains."
    />
  )
}
