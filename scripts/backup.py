import argparse
import sys
import os
import shutil
import zipfile
import tempfile
import sqlite3
from datetime import datetime
from pathlib import Path

# Add src/ to path
src_dir = str(Path(__file__).parent.parent / "src")
if src_dir not in sys.path:
    sys.path.append(src_dir)

try:
    from config import get_settings
except ImportError:
    # Fail gracefully if imported from tests that configure the pythonpath separately
    pass

def run_backup(
    db_path: str,
    knowledge_dir: str,
    service_account_path: str,
    env_path: str,
    output_dir: str,
    keep: int
) -> str:
    """
    Validates source files, creates a consistent SQLite db backup, compresses all 6 operational
    files into an atomic timestamped zip archive, and enforces retention pruning.
    """
    # 1. Validation Step
    missing = []
    if not os.path.exists(db_path):
        missing.append(f"Database file: {db_path}")
    k_dir = Path(knowledge_dir)
    for k_file in ["cv.md", "persona.md", "ats_criteria.md"]:
        kp = k_dir / k_file
        if not kp.exists():
            missing.append(f"Knowledge file: {kp}")
    if not os.path.exists(env_path):
        missing.append(f"Environment file: {env_path}")
    if not os.path.exists(service_account_path):
        missing.append(f"Service Account key: {service_account_path}")
        
    if missing:
        raise FileNotFoundError(
            "Backup aborted: The following required source files are missing:\n" +
            "\n".join(f"  - {f}" for f in missing)
        )

    # 2. SQLite online backup to temp file
    temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
    os.close(temp_db_fd)
    
    try:
        src_conn = sqlite3.connect(db_path)
        try:
            src_conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error:
            pass
        dst_conn = sqlite3.connect(temp_db_path)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
    except Exception as e:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
        raise RuntimeError(f"SQLite backup failed: {e}") from e

    # 3. Create temp zip file in output directory
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    final_zip_name = f"backup_{timestamp}.zip"
    final_zip_path = os.path.join(output_dir, final_zip_name)
    
    temp_zip_fd, temp_zip_path = tempfile.mkstemp(suffix=".zip", dir=output_dir)
    os.close(temp_zip_fd)
    
    try:
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Write db snapshot as app.db
            zip_file.write(temp_db_path, "app.db")
            # Write knowledge files
            zip_file.write(k_dir / "cv.md", "cv.md")
            zip_file.write(k_dir / "persona.md", "persona.md")
            zip_file.write(k_dir / "ats_criteria.md", "ats_criteria.md")
            # Write env file
            zip_file.write(env_path, ".env")
            # Write service account
            zip_file.write(service_account_path, "service_account.json")
            
        # Atomic replace
        os.replace(temp_zip_path, final_zip_path)
    except Exception as e:
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
        raise RuntimeError(f"Failed to create backup archive: {e}") from e
    finally:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)

    # 4. Pruning / Retention
    all_items = os.listdir(output_dir)
    backups = []
    for item in all_items:
        item_path = os.path.join(output_dir, item)
        if item.startswith("backup_") and (item.endswith(".zip") or os.path.isdir(item_path)):
            backups.append(item)
    backups.sort()
    
    keep_count = max(1, keep)
    pruned_count = 0
    if len(backups) > keep_count:
        to_prune = backups[:-keep_count]
        for item in to_prune:
            item_path = os.path.join(output_dir, item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
                pruned_count += 1
            except Exception as e:
                print(f"Warning: Failed to prune {item_path}: {e}", file=sys.stderr)

    # Print summary
    archive_size_bytes = os.path.getsize(final_zip_path)
    archive_size_kb = archive_size_bytes / 1024
    
    print("\n" + "="*40)
    print("BACKUP SUMMARY")
    print("="*40)
    print(f"Backup Path: {final_zip_path}")
    print(f"Total Size:  {archive_size_kb:.2f} KB ({archive_size_bytes} bytes)")
    print("Files Included:")
    print("  - app.db (SQLite database snapshot)")
    print("  - cv.md")
    print("  - persona.md")
    print("  - ats_criteria.md")
    print("  - .env")
    print("  - service_account.json")
    print(f"Pruned Backups: {pruned_count}")
    print("="*40)

    return final_zip_path

def main():
    parser = argparse.ArgumentParser(description="Backup job-match-assistant operational data.")
    parser.add_argument("--output-dir", default="data/backups", help="Directory where backups are saved (default: data/backups)")
    parser.add_argument("--keep", type=int, default=10, help="Number of recent backups to keep (default: 10)")
    args = parser.parse_args()

    try:
        settings = get_settings()
        db_path = settings.db_path
        knowledge_dir = settings.knowledge_dir
        service_account_path = settings.google_service_account_path
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        run_backup(
            db_path=db_path,
            knowledge_dir=knowledge_dir,
            service_account_path=service_account_path,
            env_path=".env",
            output_dir=args.output_dir,
            keep=args.keep
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Backup Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
