"""
Data model tests (implementation.md Step 3a).

Two things are load-bearing here: raw biometric data must be structurally
impossible to store, and the audit table must be append-only. Both are
asserted against the schema itself rather than against usage, so a future
column addition cannot quietly break them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schema  # noqa: E402
from store import Store  # noqa: E402

REQUIRED_TABLES = {
    "tenants", "enrolments", "workflows", "gates", "captures",
    "evidence", "reviews", "attestations", "audit_events", "claims",
}


def test_every_table_is_defined():
    assert set(schema.TABLES) == REQUIRED_TABLES


def test_every_table_is_created():
    s = Store()
    rows = s.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert REQUIRED_TABLES <= {r["name"] for r in rows}


def test_captures_cannot_hold_raw_image_bytes():
    """
    GDPR Art. 9 / BIPA / CUBI. The defence is structural: there is no column
    to put an image in. Derived scores and point sets only.

    Pinned exactly, so adding any column to `captures` fails here and forces a
    deliberate decision rather than an accidental biometric store.
    """
    assert {c.name for c in schema.TABLES["captures"]} == {
        "id", "gate_id", "frame_index", "pc_task_id", "derived", "created_at",
    }
    # `derived` is json, not a binary column; nothing here can take bytes.
    assert {c.type for c in schema.TABLES["captures"]} <= {
        "uuid", "text", "int", "json", "timestamp",
    }


def test_no_table_anywhere_has_a_binary_column():
    """A blob column is the only way raw biometrics could reach the database."""
    for table, cols in schema.TABLES.items():
        for c in cols:
            assert c.type in schema._SQLITE_TYPE, f"{table}.{c.name}: {c.type}"
            assert c.type != "blob"


def test_no_table_stores_a_plaintext_api_key():
    for table, cols in schema.TABLES.items():
        for c in cols:
            assert c.name != "api_key", f"{table}.{c.name} must be a hash"


def test_audit_events_is_the_only_append_only_table():
    assert schema.APPEND_ONLY == ["audit_events"]


def test_append_only_triggers_exist():
    s = Store()
    rows = s.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    names = {r["name"] for r in rows}
    assert {"audit_events_no_update", "audit_events_no_delete"} <= names


def test_gates_carries_mode_as_a_column():
    """Mode is data. That is what makes Step 11 ninety minutes instead of two days."""
    assert "mode" in {c.name for c in schema.TABLES["gates"]}


def test_audit_events_has_both_hash_columns():
    cols = {c.name for c in schema.TABLES["audit_events"]}
    assert {"prev_hash", "hash", "ts"} <= cols


def test_every_table_has_id_and_created_at():
    for table, cols in schema.TABLES.items():
        names = {c.name for c in cols}
        assert "id" in names and "created_at" in names, table


def test_foreign_keys_point_at_real_tables():
    for table, cols in schema.TABLES.items():
        for c in cols:
            if c.ref:
                assert c.ref in schema.TABLES, f"{table}.{c.name} -> {c.ref}"


def test_xano_export_covers_every_table():
    export = schema.xano_export()
    assert {t["name"] for t in export["tables"]} == REQUIRED_TABLES
    audit = next(t for t in export["tables"] if t["name"] == "audit_events")
    assert audit["append_only"] is True


def test_foreign_keys_are_enforced():
    s = Store()
    assert s.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
