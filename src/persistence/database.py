import sqlite3
from pathlib import Path
from typing import Optional
from config import get_settings

def connect(db_path: Optional[str] = None, busy_timeout_ms: Optional[int] = None) -> sqlite3.Connection:
    """
    Factory creating a sqlite3 Connection configured with WAL, foreign keys, and busy timeout.
    Defaults pull from config.get_settings(); explicit arguments override them for testing.
    """
    settings = get_settings()
    path = db_path if db_path is not None else settings.db_path
    timeout = busy_timeout_ms if busy_timeout_ms is not None else settings.sqlite_busy_timeout_ms

    # Ensure parent directory exists for file-based DB
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    
    # Enable WAL mode, foreign keys, and busy timeout
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(f"PRAGMA busy_timeout = {timeout};")
    
    return conn

def init_db(db_path: Optional[str] = None) -> None:
    """
    Idempotent database initializer/migration seam.
    Creates tables if user_version is 0, then sets user_version to 1.
    """
    conn = connect(db_path=db_path)
    try:
        # Check user_version
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        row = cursor.fetchone()
        user_version = row[0] if row else 0

        if user_version == 0:
            # Create jobs table
            # status mirrors domain.JobStatus; keep in sync
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    identity_hash TEXT PRIMARY KEY,
                    company TEXT,
                    title TEXT,
                    location TEXT,
                    url TEXT,
                    description TEXT,
                    source TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('scraped','no_match','matched','written','applied','rejected'))
                );
            """)
            
            # Create match_results table (1:1 with jobs)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS match_results (
                    identity_hash TEXT PRIMARY KEY REFERENCES jobs(identity_hash) ON DELETE CASCADE,
                    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
                    dimensions TEXT NOT NULL,
                    reasons TEXT NOT NULL,
                    scored_at TEXT NOT NULL
                );
            """)

            # Create cover_letters table (versioned, 1:many with jobs)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cover_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_hash TEXT NOT NULL REFERENCES jobs(identity_hash) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    created_at TEXT NOT NULL,
                    UNIQUE (identity_hash, version)
                );
            """)

            # Create runs table
            # status mirrors domain.RunStatus; keep in sync
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    n_scraped INTEGER NOT NULL DEFAULT 0,
                    n_new INTEGER NOT NULL DEFAULT 0,
                    n_matched INTEGER NOT NULL DEFAULT 0,
                    n_no_match INTEGER NOT NULL DEFAULT 0,
                    n_errors INTEGER NOT NULL DEFAULT 0,
                    cost_total_input_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_total_output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_total_llm_calls INTEGER NOT NULL DEFAULT 0,
                    cost_apify_compute_units REAL NOT NULL DEFAULT 0,
                    cost_apify_results INTEGER NOT NULL DEFAULT 0,
                    cost_estimated_usd REAL NOT NULL DEFAULT 0
                );
            """)
            
            # Set user_version to 1
            conn.execute("PRAGMA user_version = 1;")
            conn.commit()

        # Sanitize orphaned running/queued runs from previous process crashes
        conn.execute("UPDATE runs SET status = 'failed' WHERE status IN ('running', 'queued');")
        conn.commit()
    finally:
        conn.close()
