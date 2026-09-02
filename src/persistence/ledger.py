import asyncio
import importlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List
import gspread

# Dynamic import to satisfy import boundary checks
_oauth2 = importlib.import_module("google.oauth2.service_account")
Credentials = _oauth2.Credentials

from observability import get_logger

logger = get_logger("ledger")

class LedgerSyncError(Exception):
    """Raised when synchronization encounters credentials, permission, API, or structural errors."""
    pass

@dataclass
class LedgerRow:
    identity_hash: str
    company: Optional[str]
    title: Optional[str]
    location: Optional[str]
    url: Optional[str]
    source: str
    status: str
    score: Optional[int]
    scored_at: Optional[datetime]
    has_letter: str  # "yes" or "no"
    letter_version: Optional[int]
    letter_updated_at: Optional[datetime]
    first_seen_at: datetime

@dataclass
class SyncSummary:
    rows_updated: int
    rows_added: int
    user_columns_preserved: List[str]
    worksheet_created: bool

APP_OWNED_COLUMNS = [
    "identity_hash",
    "company",
    "title",
    "location",
    "url",
    "source",
    "status",
    "score",
    "scored_at",
    "has_letter",
    "letter_version",
    "letter_updated_at",
    "first_seen_at",
    "last_synced_at"
]

def col_to_letter(col_idx: int) -> str:
    letter = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        letter = chr(65 + remainder) + letter
    return letter

def rowcol_to_a1(row_idx: int, col_idx: int) -> str:
    return f"{col_to_letter(col_idx)}{row_idx}"

def format_cell_value(field_name: str, row: LedgerRow, now_str: str):
    if field_name == "identity_hash":
        return row.identity_hash
    elif field_name == "company":
        return row.company or ""
    elif field_name == "title":
        return row.title or ""
    elif field_name == "location":
        return row.location or ""
    elif field_name == "url":
        return row.url or ""
    elif field_name == "source":
        return row.source or ""
    elif field_name == "status":
        return row.status or ""
    elif field_name == "score":
        return row.score if row.score is not None else ""
    elif field_name == "scored_at":
        if not row.scored_at:
            return ""
        return row.scored_at.isoformat() if hasattr(row.scored_at, "isoformat") else str(row.scored_at)
    elif field_name == "has_letter":
        return row.has_letter or ""
    elif field_name == "letter_version":
        return row.letter_version if row.letter_version is not None else ""
    elif field_name == "letter_updated_at":
        if not row.letter_updated_at:
            return ""
        return row.letter_updated_at.isoformat() if hasattr(row.letter_updated_at, "isoformat") else str(row.letter_updated_at)
    elif field_name == "first_seen_at":
        if not row.first_seen_at:
            return ""
        return row.first_seen_at.isoformat() if hasattr(row.first_seen_at, "isoformat") else str(row.first_seen_at)
    elif field_name == "last_synced_at":
        return now_str
    return ""

class GoogleSheetLedger:
    """
    Handles atomic, non-destructive synchronization of database records
    to a Google Sheets worksheet.
    """
    def __init__(
        self,
        service_account_path: str,
        sheet_id: str,
        worksheet_title: str = "Ledger",
        lock: Optional[asyncio.Lock] = None
    ):
        self.service_account_path = service_account_path
        self.sheet_id = sheet_id
        self.worksheet_title = worksheet_title
        self.lock = lock or asyncio.Lock()

    async def sync(self, rows: List[LedgerRow]) -> SyncSummary:
        """
        Runs the synchronization process in a separate thread.
        Uses the provided asyncio lock to serialize Google Sheets operations.
        """
        async with self.lock:
            return await asyncio.to_thread(self._sync_blocking, rows)

    def _sync_blocking(self, rows: List[LedgerRow]) -> SyncSummary:
        if not self.service_account_path or not self.sheet_id:
            raise LedgerSyncError(
                "Missing required Google Sheets configuration. Ensure GOOGLE_SERVICE_ACCOUNT_PATH and LEDGER_SHEET_ID are set."
            )

        # 1. Authenticate
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            credentials = Credentials.from_service_account_file(
                self.service_account_path,
                scopes=scopes
            )
            gc = gspread.authorize(credentials)
        except Exception as e:
            raise LedgerSyncError(f"Google authentication failed: {e}") from e

        # 2. Open spreadsheet and worksheet
        worksheet_created = False
        try:
            sh = gc.open_by_key(self.sheet_id)
            try:
                ws = sh.worksheet(self.worksheet_title)
            except gspread.exceptions.WorksheetNotFound:
                ws = sh.add_worksheet(title=self.worksheet_title, rows=1000, cols=20)
                worksheet_created = True
        except (gspread.exceptions.APIError, PermissionError) as e:
            cause = e
            if isinstance(e, PermissionError) and getattr(e, "__cause__", None) is not None:
                cause = e.__cause__
            email = None
            try:
                email = credentials.service_account_email
            except Exception:
                pass
            email_msg = f" (email: {email})" if email else ""
            raise LedgerSyncError(
                f"Failed to open Google Sheet by ID {self.sheet_id}{email_msg}. "
                f"Ensure the sheet is shared with the service account email as Editor. Details: {cause}"
            ) from e
        except Exception as e:
            raise LedgerSyncError(f"Failed to access Google Sheet by ID {self.sheet_id}: {e}") from e

        # 4. Get all values in one read call
        try:
            all_values = ws.get_all_values()
        except Exception as e:
            raise LedgerSyncError(f"Failed to read worksheet data: {e}") from e

        now_str = datetime.now(timezone.utc).isoformat()
        is_empty = not all_values or len(all_values) == 0 or (len(all_values) == 1 and not all_values[0])

        # 5. Handle empty sheet case
        if is_empty:
            headers = list(APP_OWNED_COLUMNS)
            grid = [headers]
            for row in rows:
                grid.append([format_cell_value(h, row, now_str) for h in headers])
            
            try:
                ws.clear()
                ws.update(values=grid)
            except Exception as e:
                raise LedgerSyncError(f"Failed to initialize worksheet with rows: {e}") from e

            return SyncSummary(
                rows_updated=0,
                rows_added=len(rows),
                user_columns_preserved=[],
                worksheet_created=worksheet_created
            )

        # 6. Handle existing sheet case
        headers = all_values[0]
        headers = [str(h).strip() if h is not None else "" for h in headers]

        if "identity_hash" not in headers:
            raise LedgerSyncError("Missing identity_hash column in sheet headers")

        identity_hash_idx = headers.index("identity_hash") + 1
        user_columns = [h for h in headers if h not in APP_OWNED_COLUMNS and h != ""]

        # Append any missing app-owned columns to the headers (schema migration)
        headers_set = set(headers)
        new_app_cols = []
        for col in APP_OWNED_COLUMNS:
            if col not in headers_set:
                new_app_cols.append(col)
                headers.append(col)

        # Re-build column mapping
        col_indices = {col_name: idx + 1 for idx, col_name in enumerate(headers)}

        # Build existing row index mapping by identity_hash
        row_number_by_identity_hash = {}
        for r_idx, row_values in enumerate(all_values[1:], start=2):
            if len(row_values) >= identity_hash_idx:
                row_hash = row_values[identity_hash_idx - 1]
                if row_hash is not None:
                    row_hash = str(row_hash).strip()
                if row_hash:
                    row_number_by_identity_hash[row_hash] = r_idx

        updates_batch = []

        # Add new headers to the first row updates
        for col_name in new_app_cols:
            c_idx = col_indices[col_name]
            updates_batch.append({
                "range": rowcol_to_a1(1, c_idx),
                "values": [[col_name]]
            })

        incoming_by_hash = {row.identity_hash: row for row in rows}
        updated_count = 0
        added_count = 0
        synced_hashes = set()

        # Build update batch for existing matching rows
        for row_hash, r_idx in row_number_by_identity_hash.items():
            if row_hash in incoming_by_hash:
                row_data = incoming_by_hash[row_hash]
                for col_name in APP_OWNED_COLUMNS:
                    c_idx = col_indices[col_name]
                    new_val = format_cell_value(col_name, row_data, now_str)

                    # Optimize: only update if cell value has changed
                    existing_val = ""
                    if r_idx - 1 < len(all_values):
                        row_vals = all_values[r_idx - 1]
                        if c_idx - 1 < len(row_vals):
                            existing_val = row_vals[c_idx - 1]

                    if str(existing_val) != str(new_val):
                        updates_batch.append({
                            "range": rowcol_to_a1(r_idx, c_idx),
                            "values": [[new_val]]
                        })

                synced_hashes.add(row_hash)
                updated_count += 1

        # Build append batch for new rows
        new_rows_data = []
        for row_data in rows:
            if row_data.identity_hash not in synced_hashes:
                new_row = [""] * len(headers)
                for col_name in APP_OWNED_COLUMNS:
                    c_idx = col_indices[col_name]
                    new_row[c_idx - 1] = format_cell_value(col_name, row_data, now_str)
                new_rows_data.append(new_row)
                added_count += 1

        # 7. Execute batched writes (bounded API calls)
        if updates_batch:
            try:
                ws.batch_update(updates_batch)
            except Exception as e:
                raise LedgerSyncError(f"Failed to execute batch updates: {e}") from e

        if new_rows_data:
            try:
                ws.append_rows(new_rows_data, value_input_option="RAW")
            except Exception as e:
                raise LedgerSyncError(f"Failed to append new rows: {e}") from e

        return SyncSummary(
            rows_updated=updated_count,
            rows_added=added_count,
            user_columns_preserved=user_columns,
            worksheet_created=worksheet_created
        )
