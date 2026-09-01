"""
The data model (implementation.md Step 3a).

One declarative source of truth, used three ways:

  1. `store.py` builds a local SQLite database from it, so the gate logic is
     runnable and testable before a Xano credential exists.
  2. `xano_export()` emits table definitions to create in Xano.
  3. Tests assert structural properties — most importantly that captures
     cannot hold raw image bytes.

Xano remains the system of record in production. This exists so that a missing
Day-0 credential cannot block the step that defines the whole product boundary.
"""

from __future__ import annotations

from typing import Literal

ColumnType = Literal["uuid", "text", "int", "bool", "json", "timestamp"]

# SQLite has no native json/uuid/timestamp; store as TEXT and convert at the
# edges. The type names here are Xano's, because Xano is the real target.
_SQLITE_TYPE = {
    "uuid": "TEXT",
    "text": "TEXT",
    "int": "INTEGER",
    "bool": "INTEGER",
    "json": "TEXT",
    "timestamp": "TEXT",
}


class Column:
    def __init__(self, name: str, type_: ColumnType, *, null: bool = True,
                 pk: bool = False, ref: str | None = None, doc: str = ""):
        self.name = name
        self.type = type_
        self.null = null and not pk
        self.pk = pk
        self.ref = ref
        self.doc = doc

    def sqlite(self) -> str:
        parts = [self.name, _SQLITE_TYPE[self.type]]
        if self.pk:
            parts.append("PRIMARY KEY")
        elif not self.null:
            parts.append("NOT NULL")
        return " ".join(parts)


def _id() -> Column:
    return Column("id", "uuid", pk=True)


def _created() -> Column:
    return Column("created_at", "timestamp", null=False)


TABLES: dict[str, list[Column]] = {
    "tenants": [
        _id(),
        Column("domain", "text", null=False, doc="used by the DNS attestation in Step 10"),
        Column("api_key_hash", "text", null=False, doc="hash only — never the key"),
        Column("policy_defaults", "json"),
        _created(),
    ],
    "enrolments": [
        _id(),
        Column("tenant_id", "uuid", null=False, ref="tenants"),
        Column("subject_ref", "text", null=False, doc="pseudonymous; not a legal name"),
        Column("identity_vector", "json", null=False,
               doc="STABLE dimensions only — see dimensions.py"),
        Column("enrolled_at", "timestamp", null=False),
        _created(),
    ],
    "workflows": [
        _id(),
        Column("tenant_id", "uuid", null=False, ref="tenants"),
        Column("kind", "text", null=False),
        Column("payload", "json"),
        Column("agent_session_id", "text",
               doc="which agent asked. Recorded, never trusted."),
        _created(),
    ],
    "gates": [
        _id(),
        Column("workflow_id", "uuid", null=False, ref="workflows"),
        # A column, not a fork in the code. This is what makes Step 11 cheap.
        Column("mode", "text", null=False,
               doc="authorise_action | verify_identity | one_human_one_claim"),
        Column("state", "text", null=False),
        Column("nonce", "text", null=False, doc="binds the challenge to this gate"),
        Column("challenge_spec", "json"),
        Column("expires_at", "timestamp", null=False),
        _created(),
    ],
    "captures": [
        _id(),
        Column("gate_id", "uuid", null=False, ref="gates"),
        Column("frame_index", "int", null=False),
        Column("pc_task_id", "text", doc="Perfect Corp task id, for provenance"),
        # There is deliberately no bytes/image/base64 column. Raw biometric data
        # never reaches the database — GDPR Art. 9 / BIPA / CUBI. Tested.
        Column("derived", "json", null=False, doc="scores and constellations only"),
        _created(),
    ],
    "evidence": [
        _id(),
        Column("gate_id", "uuid", null=False, ref="gates"),
        Column("check_no", "int", null=False, doc="1 presence, 2 authenticity, 3 binding"),
        Column("score", "text", null=False, doc="float as text; SQLite/Xano parity"),
        Column("detail", "json"),
        _created(),
    ],
    "reviews": [
        _id(),
        Column("gate_id", "uuid", null=False, ref="gates"),
        Column("reviewer_id", "text", null=False),
        Column("decision", "text", null=False, doc="approved | rejected"),
        Column("triggering_signal", "text", doc="why this landed in REVIEW"),
        Column("notes", "text"),
        _created(),
    ],
    "claims": [
        _id(),
        Column("gate_id", "uuid", null=False, ref="gates"),
        Column("enrolment_id", "uuid", null=False, ref="enrolments"),
        Column("context", "text", null=False,
               doc="campaign identifier; the nullifier's domain separator"),
        # Not the enrolment id, and not reversible to it. See claim.py.
        Column("nullifier", "text", null=False,
               doc="HMAC(enrolment, context) — one human, one claim, per context"),
        Column("address", "text", null=False, doc="the wallet the claim binds to"),
        Column("verdict", "text", null=False, doc="UNIQUE | DUPLICATE | REVIEW"),
        Column("decided_by", "text", null=False,
               doc="machine, or the reviewer who settled it"),
        Column("signature", "text", doc="EIP-191; absent when no signing key is set"),
        _created(),
    ],
    "attestations": [
        _id(),
        Column("gate_id", "uuid", null=False, ref="gates"),
        Column("sha256", "text", null=False),
        Column("dns_record_id", "text"),
        Column("sealed_pdf_url", "text"),
        _created(),
    ],
    "audit_events": [
        _id(),
        Column("gate_id", "uuid", null=False, ref="gates"),
        Column("type", "text", null=False),
        Column("payload", "json"),
        Column("prev_hash", "text", null=False),
        Column("hash", "text", null=False),
        Column("ts", "timestamp", null=False),
        _created(),
    ],
}

# Append-only. Enforced by SQLite triggers here and by a Xano trigger in
# production; the tamper-evidence claim rests on this.
APPEND_ONLY = ["audit_events"]

APPEND_ONLY_OPS = ("UPDATE", "DELETE")

# Constraints the database enforces itself, rather than trusting every caller
# to check first. The claims one is the Sybil guard: a uniqueness rule that
# lives only in application code is one concurrent request away from admitting
# the second claim it exists to refuse, and this is exactly the kind of race a
# rewarded airdrop attracts.
#
# Scoped to (context, nullifier) and not to nullifier alone, because the same
# person claiming in a different campaign is a different, legitimate claim.
UNIQUE_INDEXES: dict[str, list[tuple[str, ...]]] = {
    "claims": [("context", "nullifier")],
}


def trigger_name(table: str, op: str) -> str:
    return f"{table}_no_{op.lower()}"


def trigger_sql(table: str, op: str) -> str:
    """
    The guard that makes a table append-only.

    One definition, used both when the database is created and when the demo
    puts the trigger back after deliberately going around it. Two copies of
    this string would eventually disagree, and the restored guard would be
    weaker than the original without anything saying so.
    """
    return (f"CREATE TRIGGER IF NOT EXISTS {trigger_name(table, op)} "
            f"BEFORE {op} ON {table} BEGIN "
            f"SELECT RAISE(ABORT, '{table} is append-only'); END")


def create_sql() -> list[str]:
    stmts = []
    for table, cols in TABLES.items():
        body = ",\n  ".join(c.sqlite() for c in cols)
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table} (\n  {body}\n)")
        for c in cols:
            if c.ref:
                stmts.append(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_{c.name} ON {table}({c.name})")

    for table, indexes in UNIQUE_INDEXES.items():
        for cols_ in indexes:
            name = f"ux_{table}_{'_'.join(cols_)}"
            stmts.append(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} "
                         f"ON {table}({', '.join(cols_)})")

    for table in APPEND_ONLY:
        for op in APPEND_ONLY_OPS:
            stmts.append(trigger_sql(table, op))
    return stmts


def xano_export() -> dict:
    """Table definitions in a shape that maps directly onto Xano's schema editor."""
    return {
        "tables": [
            {
                "name": table,
                "append_only": table in APPEND_ONLY,
                "unique": [list(c) for c in UNIQUE_INDEXES.get(table, [])],
                "fields": [
                    {"name": c.name, "type": c.type, "nullable": c.null,
                     "primary": c.pk, "references": c.ref, "description": c.doc}
                    for c in cols
                ],
            }
            for table, cols in TABLES.items()
        ]
    }
