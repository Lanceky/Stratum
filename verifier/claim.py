"""
Turning a settled gate into something a smart contract can act on.

Two pieces, and they solve different problems.

**The nullifier** is what lets a campaign enforce one-claim-per-human without
learning who anyone is. It is an HMAC over (enrolment, context) under a server
secret: stable for the same person in the same campaign, so a second claim
collides; unlinkable across campaigns, so the same person claiming in two
airdrops cannot be joined up by comparing the two chains. The enrolment id
never leaves the server, and the nullifier cannot be reversed to it.

Be clear about what this is not. Semaphore and similar schemes derive a
nullifier the holder proves in zero knowledge, so nobody — not even the issuer
— can link it back. This one is computed *by* STRATUM, which means STRATUM
could link it back, and anyone holding the secret could forge one. It removes
the need for the *chain* to be trusted with identity; it does not remove the
need to trust the issuer. That is a real limitation and it is carried in the
payload rather than left for a reader to work out.

**The signature** is EIP-191 `personal_sign` over the claim. That specific
encoding because Solidity's `ecrecover` needs the `\\x19Ethereum Signed
Message:\\n` prefix to recover the signer, so a contract can check a claim with
about four lines and no oracle, no bridge, and no trusted relayer. A raw hash
signature would be cheaper to produce and useless on chain.

What gets signed is deliberately the whole decision and not just a yes: the
verdict, the wallet it is bound to, the context, the roster size the sweep
covered, and the audit chain head at the moment of issue. A contract that only
sees "approved" cannot tell an auto-decided claim from one a named human ruled
on after the sweep came back ambiguous — and for an allocation worth real
money, that is exactly the distinction worth putting on chain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from eth_account import Account
from eth_account.messages import encode_defunct

SECRET_ENV = "STRATUM_NULLIFIER_SECRET"
KEY_ENV = "STRATUM_SIGNING_KEY"

# EIP-55 is not checked here — a checksum is a typo guard, and rejecting a
# lowercase address that is otherwise valid would refuse a correct claim.
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

# A context is a campaign identifier chosen by the caller. Constrained because
# it is the domain separator: two campaigns that pick colliding contexts share
# a nullifier space, and one human's two honest claims would collide.
CONTEXT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,63}$")

LIMITATIONS = [
    "The nullifier is computed by STRATUM under a server-held secret, not "
    "proven in zero knowledge by the holder. STRATUM can therefore link a "
    "nullifier back to the enrolment it came from, and anyone holding the "
    "secret could forge one. This shifts trust off the chain and onto the "
    "issuer; it does not eliminate it.",
    "Unlinkability across contexts rests on that same secret staying secret. "
    "If it leaks, every nullifier ever issued becomes linkable in retrospect.",
    "A signature says what the checks found and who ruled on it. It is not a "
    "claim that the person is unique in the world — only that no enrolment on "
    "this context's roster matched, within the stated false-match bound.",
]


class NotSignable(Exception):
    """Raised when a claim is missing something a contract would need."""


def _secret() -> bytes:
    """
    The HMAC key.

    Falls back to a development constant rather than a random per-process
    value: a nullifier that changes when the server restarts would let the same
    person claim twice, which is the one failure this whole module exists to
    prevent. Loud in the payload (`dev_secret`) so a demo is never mistaken for
    a deployment.
    """
    return os.getenv(SECRET_ENV, "stratum-development-nullifier-secret").encode()


def using_dev_secret() -> bool:
    return SECRET_ENV not in os.environ


def nullifier(enrolment_id: str, context: str) -> str:
    """
    One human, one claim, per context.

    Ordered and length-prefixed rather than concatenated: "ab" + "c" and "a" +
    "bc" would otherwise hash identically, which is a way for two different
    people to share a nullifier and for one of them to lose their claim.
    """
    if not CONTEXT.match(context):
        raise NotSignable(
            f"context {context!r} is not a valid campaign identifier — it is "
            "the domain separator that keeps two campaigns' nullifiers apart")
    msg = f"{len(enrolment_id)}:{enrolment_id}|{len(context)}:{context}".encode()
    return "0x" + hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def signing_key() -> str | None:
    return os.getenv(KEY_ENV)


def issuer_address() -> str | None:
    """The address a verifying contract would compare against, if we can sign."""
    key = signing_key()
    if not key:
        return None
    try:
        return Account.from_key(key).address
    except (ValueError, TypeError):
        return None


@dataclass
class Claim:
    """The statement that gets signed. Field order is part of the format."""

    context: str
    address: str
    nullifier: str
    verdict: str
    gate_id: str
    chain_head: str
    roster_size: int
    comparisons: int
    false_match_bound: float
    decided_by: str
    issued_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict:
        return {"context": self.context, "address": self.address,
                "nullifier": self.nullifier, "verdict": self.verdict,
                "gate_id": self.gate_id, "chain_head": self.chain_head,
                "roster_size": self.roster_size,
                "comparisons": self.comparisons,
                "false_match_bound": self.false_match_bound,
                "decided_by": self.decided_by, "issued_at": self.issued_at}

    def message(self) -> str:
        """
        The exact bytes signed.

        `sort_keys` and no whitespace, so the string a contract or a client
        reconstructs is byte-identical to the one signed here. A signature over
        a differently-spaced encoding of the same facts verifies as garbage.
        """
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def build(*, context: str, address: str, enrolment_id: str, verdict: str,
          gate_id: str, chain_head: str, roster_size: int, comparisons: int,
          false_match_bound: float, decided_by: str) -> Claim:
    if not ADDRESS.match(address or ""):
        raise NotSignable(
            f"{address!r} is not a 20-byte hex address — a claim bound to an "
            "address nobody controls is worse than no claim")
    return Claim(
        context=context, address=address,
        nullifier=nullifier(enrolment_id, context),
        verdict=verdict, gate_id=gate_id, chain_head=chain_head,
        roster_size=roster_size, comparisons=comparisons,
        false_match_bound=false_match_bound, decided_by=decided_by,
    )


def sign(claim: Claim) -> dict:
    """
    Sign a claim so `ecrecover` can recover the issuer.

    Returns v/r/s separately as well as the packed signature: older Solidity
    takes the three, newer takes 65 bytes, and splitting on chain costs gas for
    something already known here.
    """
    key = signing_key()
    if not key:
        raise NotSignable(
            f"no signing key — set {KEY_ENV} to issue a claim a contract can "
            "verify. Unsigned, this is a report and not an authorisation")

    signed = Account.sign_message(encode_defunct(text=claim.message()), key)
    return {
        "claim": claim.as_dict(),
        "message": claim.message(),
        "signature": signed.signature.hex() if isinstance(signed.signature, str)
        else "0x" + signed.signature.hex().removeprefix("0x"),
        "v": signed.v,
        "r": hex(signed.r),
        "s": hex(signed.s),
        "issuer": Account.from_key(key).address,
        "scheme": "EIP-191 personal_sign",
        "dev_secret": using_dev_secret(),
        "limitations": LIMITATIONS,
    }


def recover(message: str, signature: str) -> str:
    """Who signed this — the same operation a contract performs."""
    return Account.recover_message(encode_defunct(text=message),
                                   signature=signature)
