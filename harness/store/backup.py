"""Full-graph backup / restore / wipe for Neo4j (online, driver-based, APOC-free).

Exports every node (labels + properties) and relationship (type + properties +
endpoints) plus constraint/index definitions to a single JSON file, and can
restore them or wipe the database. Used before a clean rebuild so the prior
graph is never lost.

Recovery model: each node is exported with its server ``elementId``; on restore
that id is stored transiently as ``_bk_id`` so relationships can be re-linked,
then removed. Neo4j temporal/spatial property values are serialized to strings
(ISO-8601 for temporals); on restore they are written back as-is.

CLI:
    uv run python -m harness.store.backup export [out.json]
    uv run python -m harness.store.backup info <backup.json>
    uv run python -m harness.store.backup restore <backup.json>
    uv run python -m harness.store.backup wipe --yes        # drops schema + all data
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.kg.config import REPO_ROOT, get_settings
from harness.kg.driver import GraphDB

#: Default directory for backup files.
BACKUP_DIR: Path = REPO_ROOT / "data" / "backups"

#: Identifiers (labels / rel types) are interpolated into Cypher on restore, so
#: they are validated against this conservative pattern (Cypher-injection guard).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _json_default(obj: Any) -> str:
    """Serialize neo4j temporal/spatial values (and any leftover) to strings."""
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        return iso()
    return str(obj)


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def export_graph(db: GraphDB, path: Path | None = None) -> Path:
    """Export the entire graph + schema to a JSON file. Returns the file path."""
    nodes = db.read(
        "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props"
    )
    rels = db.read(
        "MATCH (a)-[r]->(b) "
        "RETURN elementId(r) AS id, type(r) AS type, "
        "elementId(a) AS start, elementId(b) AS end, properties(r) AS props"
    )
    try:
        constraints = db.read("SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties")
    except Exception:  # noqa: BLE001
        constraints = []
    try:
        indexes = db.read("SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties")
    except Exception:  # noqa: BLE001
        indexes = []

    payload = {
        "meta": {
            "exported_at": datetime.now(UTC).isoformat(),
            "database": db.database,
            "node_count": len(nodes),
            "relationship_count": len(rels),
            "constraint_count": len(constraints),
            "index_count": len(indexes),
        },
        "nodes": nodes,
        "relationships": rels,
        "constraints": constraints,
        "indexes": indexes,
    }

    if path is None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / f"neo4j-backup-{_ts()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=_json_default, indent=2), encoding="utf-8")
    return path


def verify_backup(db: GraphDB, path: Path) -> dict[str, Any]:
    """Re-read a backup file and confirm its counts match the live DB."""
    data = json.loads(path.read_text(encoding="utf-8"))
    live_nodes = db.read("MATCH (n) RETURN count(n) AS c")[0]["c"]
    live_rels = db.read("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
    ok = (
        len(data["nodes"]) == live_nodes
        and len(data["relationships"]) == live_rels
    )
    return {
        "ok": ok,
        "file_nodes": len(data["nodes"]),
        "live_nodes": live_nodes,
        "file_rels": len(data["relationships"]),
        "live_rels": live_rels,
        "path": str(path),
    }


def wipe(db: GraphDB) -> dict[str, int]:
    """Drop all constraints + (non-LOOKUP) indexes, then DETACH DELETE all nodes."""
    dropped_c = 0
    for row in db.read("SHOW CONSTRAINTS YIELD name"):
        name = row["name"]
        db.write(f"DROP CONSTRAINT `{name}` IF EXISTS")
        dropped_c += 1
    dropped_i = 0
    for row in db.read("SHOW INDEXES YIELD name, type"):
        if row.get("type") == "LOOKUP":  # leave built-in token-lookup indexes
            continue
        name = row["name"]
        db.write(f"DROP INDEX `{name}` IF EXISTS")
        dropped_i += 1
    # 1.4k nodes is tiny; one statement is fine. (Use IN TRANSACTIONS if huge.)
    res = db.write("MATCH (n) DETACH DELETE n RETURN count(n) AS c")
    deleted = res[0]["c"] if res else 0
    return {"constraints_dropped": dropped_c, "indexes_dropped": dropped_i, "nodes_deleted": deleted}


def restore_graph(db: GraphDB, path: Path) -> dict[str, int]:
    """Restore nodes + relationships from a backup file (schema NOT restored)."""
    data = json.loads(path.read_text(encoding="utf-8"))

    # Nodes — grouped by their (sorted) label set so labels can be interpolated.
    by_labels: dict[tuple[str, ...], list[dict]] = {}
    for n in data["nodes"]:
        labels = tuple(sorted(n["labels"]))
        for lbl in labels:
            if not _IDENT_RE.match(lbl):
                raise ValueError(f"Unsafe label in backup: {lbl!r}")
        by_labels.setdefault(labels, []).append({"id": n["id"], "props": n["props"]})

    n_nodes = 0
    for labels, rows in by_labels.items():
        label_cypher = "".join(f":`{lbl}`" for lbl in labels) if labels else ""
        db.write(
            f"UNWIND $rows AS row CREATE (n{label_cypher}) "
            "SET n = row.props SET n._bk_id = row.id",
            rows=rows,
        )
        n_nodes += len(rows)

    # Relationships — grouped by type.
    by_type: dict[str, list[dict]] = {}
    for r in data["relationships"]:
        rtype = r["type"]
        if not _IDENT_RE.match(rtype):
            raise ValueError(f"Unsafe relationship type in backup: {rtype!r}")
        by_type.setdefault(rtype, []).append(
            {"start": r["start"], "end": r["end"], "props": r["props"]}
        )

    n_rels = 0
    for rtype, rows in by_type.items():
        db.write(
            "UNWIND $rows AS row "
            "MATCH (a {_bk_id: row.start}) MATCH (b {_bk_id: row.end}) "
            f"CREATE (a)-[r:`{rtype}`]->(b) SET r = row.props",
            rows=rows,
        )
        n_rels += len(rows)

    # Drop the transient mapping property.
    db.write("MATCH (n) WHERE n._bk_id IS NOT NULL REMOVE n._bk_id")
    return {"nodes_restored": n_nodes, "relationships_restored": n_rels}


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    db = GraphDB.from_settings(get_settings())
    try:
        db.verify()
        if cmd == "export":
            out = Path(argv[1]) if len(argv) > 1 else None
            path = export_graph(db, out)
            v = verify_backup(db, path)
            print(f"Exported → {path}")
            print(f"  nodes={v['file_nodes']} rels={v['file_rels']} (live nodes={v['live_nodes']} rels={v['live_rels']})")
            print(f"  verified: {'OK' if v['ok'] else 'MISMATCH'}")
            return 0 if v["ok"] else 1
        if cmd == "info":
            data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
            print(json.dumps(data["meta"], indent=2))
            return 0
        if cmd == "restore":
            res = restore_graph(db, Path(argv[1]))
            print(f"Restored: {res}")
            return 0
        if cmd == "wipe":
            if "--yes" not in argv:
                print("Refusing to wipe without --yes")
                return 2
            res = wipe(db)
            print(f"Wiped: {res}")
            return 0
        print(f"Unknown command: {cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
