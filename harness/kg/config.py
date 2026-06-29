"""Runtime configuration for the ThoughtWire Causal Knowledge Graph.

Loads settings from environment variables and ``.env`` files. ``REPO_ROOT`` is
computed from this file's location so that the MCP server, CLI, and hooks all
resolve the same absolute ``.env`` paths regardless of the working directory
they are launched from.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at <repo>/harness/kg/config.py. The repo root is the parent
# of the 'harness' package directory: parents[0]=kg, [1]=harness, [2]=repo root.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings sourced from env vars and ``.env`` files.

    pydantic-settings maps each field from the matching uppercase env var
    (e.g. ``neo4j_uri`` <- ``NEO4J_URI``) case-insensitively. Both the repo-root
    ``.env`` and ``harness/.env`` are consulted (later files win).
    """

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "harness" / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    business_id: str = "rare-seeds"

    # --- Snowflake (read-only marts overlay; optional ``counts`` extra) ---
    # Credentials for the runtime active_campaigns COUNT overlay
    # (:mod:`harness.marts.snowflake_reader`). They map from ``SNOWFLAKE_*`` env
    # vars (case-insensitive) via the same ``.env`` chain as the Neo4j fields.
    # All default to empty so the overlay degrades gracefully (``stale=True``)
    # when Snowflake is unconfigured -- nothing here is ever persisted to Neo4j.
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_role: str = ""
    snowflake_warehouse: str = ""
    snowflake_database: str = ""
    # Schema where the dbt marts live (BC_2 ``snowflake_dbt_schema`` default).
    snowflake_schema: str = "MARTS"
    # Optional key-pair auth (PEM path). When set it supersedes the password.
    snowflake_private_key_path: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
