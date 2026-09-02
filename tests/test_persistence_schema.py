import sqlite3
import pytest
from persistence import connect, init_db

def test_init_db_creates_tables(tmp_path):
    db_file = tmp_path / "test.db"
    
    # Initialize the database
    init_db(str(db_file))
    
    # Query sqlite_master to verify tables exist
    conn = connect(str(db_file))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        assert "jobs" in tables
        assert "match_results" in tables
        assert "cover_letters" in tables
        assert "runs" in tables
    finally:
        conn.close()

def test_connection_pragmas(tmp_path):
    db_file = tmp_path / "test.db"
    
    # Connect and assert pragmas (journal_mode, foreign_keys, busy_timeout)
    conn = connect(str(db_file), busy_timeout_ms=3500)
    try:
        cursor = conn.cursor()
        
        # WAL Mode check
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.lower() == "wal"
        
        # Foreign Keys check (must be 1/ON)
        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys = cursor.fetchone()[0]
        assert foreign_keys == 1
        
        # Busy Timeout check
        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        assert busy_timeout == 3500
    finally:
        conn.close()

def test_jobs_dedup_constraint(tmp_path):
    db_file = tmp_path / "test.db"
    init_db(str(db_file))
    
    conn = connect(str(db_file))
    try:
        # Insert a job
        conn.execute("""
            INSERT INTO jobs (identity_hash, company, title, location, source, scraped_at, status)
            VALUES ('hash123', 'Acme', 'Dev', 'Lisbon', 'apify', '2026-06-10T12:00:00Z', 'scraped');
        """)
        conn.commit()
        
        # Insert duplicate identity_hash (should raise IntegrityError)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO jobs (identity_hash, company, title, location, source, scraped_at, status)
                VALUES ('hash123', 'Acme Different', 'Dev Different', 'Lisbon', 'apify', '2026-06-10T13:00:00Z', 'scraped');
            """)
            conn.commit()
    finally:
        conn.close()

def test_foreign_key_constraints(tmp_path):
    db_file = tmp_path / "test.db"
    init_db(str(db_file))
    
    conn = connect(str(db_file))
    try:
        # Child insertion with bad identity_hash raises IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO match_results (identity_hash, score, dimensions, reasons, scored_at)
                VALUES ('non-existent-hash', 85, '{}', '[]', '2026-06-10T12:00:00Z');
            """)
            conn.commit()
            
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO cover_letters (identity_hash, text, version, created_at)
                VALUES ('non-existent-hash', 'letter text', 1, '2026-06-10T12:00:00Z');
            """)
            conn.commit()

        # Succeeds with a valid identity_hash
        conn.execute("""
            INSERT INTO jobs (identity_hash, company, title, location, source, scraped_at, status)
            VALUES ('hash123', 'Acme', 'Dev', 'Lisbon', 'apify', '2026-06-10T12:00:00Z', 'scraped');
        """)
        conn.execute("""
            INSERT INTO match_results (identity_hash, score, dimensions, reasons, scored_at)
            VALUES ('hash123', 85, '{}', '[]', '2026-06-10T12:00:00Z');
        """)
        conn.execute("""
            INSERT INTO cover_letters (identity_hash, text, version, created_at)
            VALUES ('hash123', 'letter text', 1, '2026-06-10T12:00:00Z');
        """)
        conn.commit()
        
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM match_results;")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM cover_letters;")
        assert cursor.fetchone()[0] == 1
    finally:
        conn.close()

def test_cascade_delete(tmp_path):
    db_file = tmp_path / "test.db"
    init_db(str(db_file))
    
    conn = connect(str(db_file))
    try:
        # Setup job and child records
        conn.execute("""
            INSERT INTO jobs (identity_hash, company, title, location, source, scraped_at, status)
            VALUES ('hash123', 'Acme', 'Dev', 'Lisbon', 'apify', '2026-06-10T12:00:00Z', 'scraped');
        """)
        conn.execute("""
            INSERT INTO match_results (identity_hash, score, dimensions, reasons, scored_at)
            VALUES ('hash123', 85, '{}', '[]', '2026-06-10T12:00:00Z');
        """)
        conn.execute("""
            INSERT INTO cover_letters (identity_hash, text, version, created_at)
            VALUES ('hash123', 'letter text', 1, '2026-06-10T12:00:00Z');
        """)
        conn.commit()
        
        # Verify setup
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM match_results WHERE identity_hash='hash123';")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM cover_letters WHERE identity_hash='hash123';")
        assert cursor.fetchone()[0] == 1
        
        # Delete job
        conn.execute("DELETE FROM jobs WHERE identity_hash='hash123';")
        conn.commit()
        
        # Verify cascade deletion has taken effect on children
        cursor.execute("SELECT count(*) FROM match_results WHERE identity_hash='hash123';")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM cover_letters WHERE identity_hash='hash123';")
        assert cursor.fetchone()[0] == 0
    finally:
        conn.close()

def test_cover_letters_unique_version(tmp_path):
    db_file = tmp_path / "test.db"
    init_db(str(db_file))
    
    conn = connect(str(db_file))
    try:
        # Setup job
        conn.execute("""
            INSERT INTO jobs (identity_hash, company, title, location, source, scraped_at, status)
            VALUES ('hash123', 'Acme', 'Dev', 'Lisbon', 'apify', '2026-06-10T12:00:00Z', 'scraped');
        """)
        # Version 1 cover letter
        conn.execute("""
            INSERT INTO cover_letters (identity_hash, text, version, created_at)
            VALUES ('hash123', 'letter text v1', 1, '2026-06-10T12:00:00Z');
        """)
        conn.commit()
        
        # Duplicate version (hash123, 1) raises IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO cover_letters (identity_hash, text, version, created_at)
                VALUES ('hash123', 'another text v1', 1, '2026-06-10T13:00:00Z');
            """)
            conn.commit()
            
        # Distinct version (v2) succeeds
        conn.execute("""
            INSERT INTO cover_letters (identity_hash, text, version, created_at)
            VALUES ('hash123', 'letter text v2', 2, '2026-06-10T14:00:00Z');
        """)
        conn.commit()
        
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM cover_letters WHERE identity_hash='hash123';")
        assert cursor.fetchone()[0] == 2
    finally:
        conn.close()

def test_init_db_idempotency_and_version(tmp_path):
    db_file = tmp_path / "test.db"
    
    # First execution
    init_db(str(db_file))
    
    # Second execution (should be safe and do nothing)
    init_db(str(db_file))
    
    conn = connect(str(db_file))
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        user_version = cursor.fetchone()[0]
        assert user_version == 1
    finally:
        conn.close()
