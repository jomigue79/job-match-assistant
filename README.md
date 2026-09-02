# Job Match Assistant

A local-first job-matching tool and scoring assistant.

## Operational Backups

To prevent data loss of operational configuration, knowledge bases, and SQLite database states, a backup script is provided.

### Running a Backup

To trigger a backup on demand, run:

```powershell
py scripts/backup.py [--output-dir data/backups] [--keep N]
```

- **`--output-dir`**: Target folder where backup archives are written (defaults to `data/backups/`).
- **`--keep`**: Number of most recent backups to retain (defaults to `10`).

The backup script runs safely while the application is active by performing a consistent online database snapshot. The resulting `.zip` file is timestamped (e.g. `backup_2026-06-14_153045.zip`) and contains:
- `app.db` (Database snapshot)
- `cv.md` (Candidate CV profile)
- `persona.md` (Writing constraints and persona)
- `ats_criteria.md` (ATS review overrides)
- `.env` (Environment configurations and credentials)
- `service_account.json` (Google Sheets authorization key)

---

### Restoring from a Backup

To restore data from an archived backup:
1. **Stop the Application**: Ensure the NiceGUI/control room app is completely stopped.
2. **Extract the Archive**: Locate the desired `.zip` file in `data/backups/`.
3. **Restore Files**: Copy the files back to their active locations:
   - Extract `app.db` and copy to `data/app.db`.
   - Extract `cv.md`, `persona.md`, and `ats_criteria.md` to `data/knowledge/`.
   - Extract `.env` to the project root directory (`.env`).
   - Extract `service_account.json` to `secrets/service_account.json`.
4. **Restart the Application**: Restart the server.
