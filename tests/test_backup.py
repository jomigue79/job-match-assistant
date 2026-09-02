import os
import sys
import sqlite3
import zipfile
import pytest
import threading
import time
from pathlib import Path

# Add scripts/ to path or import run_backup from scripts.backup
sys.path.append(str(Path(__file__).parent.parent / "scripts"))
from backup import run_backup

def test_backup_happy_path(tmp_path):
    # Setup test files
    db_path = tmp_path / "app.db"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "cv.md").write_text("CV Content", encoding="utf-8")
    (knowledge_dir / "persona.md").write_text("Persona Content", encoding="utf-8")
    (knowledge_dir / "ats_criteria.md").write_text("ATS Content", encoding="utf-8")
    
    env_path = tmp_path / ".env"
    env_path.write_text("ENV Content", encoding="utf-8")
    
    service_account_path = tmp_path / "service_account.json"
    service_account_path.write_text('{"type": "service_account"}', encoding="utf-8")
    
    # Initialize a valid sqlite db
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO jobs (title) VALUES ('Software Engineer')")
    conn.commit()
    conn.close()
    
    output_dir = tmp_path / "backups"
    
    # Run backup
    zip_path = run_backup(
        db_path=str(db_path),
        knowledge_dir=str(knowledge_dir),
        service_account_path=str(service_account_path),
        env_path=str(env_path),
        output_dir=str(output_dir),
        keep=5
    )
    
    # Verify zip file exists
    assert os.path.exists(zip_path)
    assert zip_path.endswith(".zip")
    
    # Verify contents of zip
    with zipfile.ZipFile(zip_path, 'r') as zip_file:
        file_list = zip_file.namelist()
        assert "app.db" in file_list
        assert "cv.md" in file_list
        assert "persona.md" in file_list
        assert "ats_criteria.md" in file_list
        assert ".env" in file_list
        assert "service_account.json" in file_list
        
        # Verify non-database file contents
        assert zip_file.read("cv.md").decode("utf-8") == "CV Content"
        assert zip_file.read("persona.md").decode("utf-8") == "Persona Content"
        assert zip_file.read("ats_criteria.md").decode("utf-8") == "ATS Content"
        assert zip_file.read(".env").decode("utf-8") == "ENV Content"
        assert zip_file.read("service_account.json").decode("utf-8") == '{"type": "service_account"}'
        
        # Extract db and verify it's a valid queryable SQLite DB
        extracted_db = tmp_path / "extracted_app.db"
        with open(extracted_db, "wb") as f:
            f.write(zip_file.read("app.db"))
            
        conn_check = sqlite3.connect(extracted_db)
        cursor = conn_check.cursor()
        cursor.execute("SELECT title FROM jobs")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Software Engineer"
        conn_check.close()

def test_backup_abort_on_missing_files(tmp_path):
    db_path = tmp_path / "app.db"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "cv.md").write_text("CV Content", encoding="utf-8")
    (knowledge_dir / "persona.md").write_text("Persona Content", encoding="utf-8")
    # ats_criteria.md is missing!
    
    env_path = tmp_path / ".env"
    env_path.write_text("ENV Content", encoding="utf-8")
    
    service_account_path = tmp_path / "service_account.json"
    service_account_path.write_text("SA Content", encoding="utf-8")
    
    # Initialize SQLite db
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.close()
    
    output_dir = tmp_path / "backups"
    
    # Check that run_backup raises FileNotFoundError and doesn't create any archive
    with pytest.raises(FileNotFoundError) as exc_info:
        run_backup(
            db_path=str(db_path),
            knowledge_dir=str(knowledge_dir),
            service_account_path=str(service_account_path),
            env_path=str(env_path),
            output_dir=str(output_dir),
            keep=5
        )
        
    assert "ats_criteria.md" in str(exc_info.value)
    
    # Assert output directory is empty or doesn't have backup zip files
    if os.path.exists(output_dir):
        assert len(os.listdir(output_dir)) == 0

def test_backup_retention_pruning(tmp_path):
    db_path = tmp_path / "app.db"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "cv.md").write_text("CV", encoding="utf-8")
    (knowledge_dir / "persona.md").write_text("Persona", encoding="utf-8")
    (knowledge_dir / "ats_criteria.md").write_text("ATS", encoding="utf-8")
    
    env_path = tmp_path / ".env"
    env_path.write_text("ENV", encoding="utf-8")
    
    service_account_path = tmp_path / "service_account.json"
    service_account_path.write_text("SA", encoding="utf-8")
    
    # Create valid db
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.close()
    
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    
    # Create 4 old dummy backups
    old_backups = [
        "backup_2026-06-14_100000.zip",
        "backup_2026-06-14_100001.zip",
        "backup_2026-06-14_100002.zip",
        "backup_2026-06-14_100003.zip",
    ]
    for b in old_backups:
        (output_dir / b).write_text("dummy zip content")
        
    # We have 4 existing backups, and run_backup will create a 5th one.
    # If we keep=3, we should only keep the 3 newest backups, i.e., backup_2026-06-14_100002.zip, backup_2026-06-14_100003.zip, and the newly created one.
    # backup_2026-06-14_100000.zip and backup_2026-06-14_100001.zip should be deleted.
    new_zip = run_backup(
        db_path=str(db_path),
        knowledge_dir=str(knowledge_dir),
        service_account_path=str(service_account_path),
        env_path=str(env_path),
        output_dir=str(output_dir),
        keep=3
    )
    
    remaining = sorted(os.listdir(output_dir))
    # There should be exactly 3 files
    assert len(remaining) == 3
    assert "backup_2026-06-14_100000.zip" not in remaining
    assert "backup_2026-06-14_100001.zip" not in remaining
    assert "backup_2026-06-14_100002.zip" in remaining
    assert "backup_2026-06-14_100003.zip" in remaining
    assert os.path.basename(new_zip) in remaining

def test_backup_retention_keep_zero(tmp_path):
    db_path = tmp_path / "app.db"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "cv.md").write_text("CV", encoding="utf-8")
    (knowledge_dir / "persona.md").write_text("Persona", encoding="utf-8")
    (knowledge_dir / "ats_criteria.md").write_text("ATS", encoding="utf-8")
    
    env_path = tmp_path / ".env"
    env_path.write_text("ENV", encoding="utf-8")
    
    service_account_path = tmp_path / "service_account.json"
    service_account_path.write_text("SA", encoding="utf-8")
    
    # Create valid db
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.close()
    
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    
    # Create 3 old dummy backups
    old_backups = [
        "backup_2026-06-14_100000.zip",
        "backup_2026-06-14_100001.zip",
        "backup_2026-06-14_100002.zip",
    ]
    for b in old_backups:
        (output_dir / b).write_text("dummy zip content")
        
    # With keep=0, it should keep exactly 1 backup (the new one)
    new_zip = run_backup(
        db_path=str(db_path),
        knowledge_dir=str(knowledge_dir),
        service_account_path=str(service_account_path),
        env_path=str(env_path),
        output_dir=str(output_dir),
        keep=0
    )
    
    remaining = os.listdir(output_dir)
    assert len(remaining) == 1
    assert remaining[0] == os.path.basename(new_zip)

def test_backup_concurrent_write_safety(tmp_path):
    db_path = tmp_path / "app.db"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "cv.md").write_text("CV", encoding="utf-8")
    (knowledge_dir / "persona.md").write_text("Persona", encoding="utf-8")
    (knowledge_dir / "ats_criteria.md").write_text("ATS", encoding="utf-8")
    
    env_path = tmp_path / ".env"
    env_path.write_text("ENV", encoding="utf-8")
    
    service_account_path = tmp_path / "service_account.json"
    service_account_path.write_text("SA", encoding="utf-8")
    
    # Initialize the database in WAL mode
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, val TEXT)")
    conn.commit()
    conn.close()
    
    output_dir = tmp_path / "backups"
    
    stop_event = threading.Event()
    write_error = None
    
    def writer_loop():
        nonlocal write_error
        try:
            # Open its own connection to write to the DB
            conn_write = sqlite3.connect(db_path)
            conn_write.execute("PRAGMA journal_mode=WAL;")
            i = 0
            while not stop_event.is_set():
                conn_write.execute("INSERT INTO jobs (val) VALUES (?)", (f"row {i}",))
                conn_write.commit()
                i += 1
                time.sleep(0.001)
            conn_write.close()
        except Exception as e:
            write_error = e
            
    # Start writing thread
    t = threading.Thread(target=writer_loop)
    t.start()
    
    # Let the writer thread execute and commit some rows
    time.sleep(0.05)
    
    try:
        # Run backup while writing thread is active
        zip_path = run_backup(
            db_path=str(db_path),
            knowledge_dir=str(knowledge_dir),
            service_account_path=str(service_account_path),
            env_path=str(env_path),
            output_dir=str(output_dir),
            keep=5
        )
    finally:
        # Stop and join the thread
        stop_event.set()
        t.join()
        
    assert write_error is None, f"Writer thread failed: {write_error}"
    assert os.path.exists(zip_path)
    
    # Verify the backed up database can be read and is not corrupt
    extracted_db = tmp_path / "extracted_app.db"
    with zipfile.ZipFile(zip_path, 'r') as zip_file:
        with open(extracted_db, "wb") as f:
            f.write(zip_file.read("app.db"))
            
    conn_check = sqlite3.connect(extracted_db)
    cursor = conn_check.cursor()
    # Should run successfully and return rows
    cursor.execute("SELECT count(*) FROM jobs")
    count = cursor.fetchone()[0]
    assert count > 0
    
    # Try querying some rows to verify integrity
    cursor.execute("SELECT val FROM jobs LIMIT 1")
    val = cursor.fetchone()[0]
    assert val.startswith("row ")
    conn_check.close()
