import pytest
import asyncio
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials

from persistence import GoogleSheetLedger, LedgerSyncError, LedgerRow

def a1_to_rowcol(a1: str):
    import re
    match = re.match(r"^([A-Z]+)([0-9]+)$", a1)
    if not match:
        raise ValueError(f"Invalid A1 range: {a1}")
    col_str, row_str = match.groups()
    row_idx = int(row_str)
    
    col_idx = 0
    for char in col_str:
        col_idx = col_idx * 26 + (ord(char) - 64)
    return row_idx, col_idx

def make_ledger_row(identity_hash, company="Test Co", title="Software Engineer"):
    return LedgerRow(
        identity_hash=identity_hash,
        company=company,
        title=title,
        location="Remote",
        url="http://example.com",
        source="linkedin",
        status="matched",
        score=85,
        scored_at=datetime.now(timezone.utc),
        has_letter="no",
        letter_version=None,
        letter_updated_at=None,
        first_seen_at=datetime.now(timezone.utc)
    )

class FakeCredentials:
    service_account_email = "service-account@test-project.iam.gserviceaccount.com"
    
    @classmethod
    def from_service_account_file(cls, path, scopes=None):
        return cls()

class FakeWorksheet:
    def __init__(self, title, grid=None):
        self.title = title
        self.grid = grid if grid is not None else []
        self.batch_update_calls = 0
        self.append_rows_calls = 0
        self.update_calls = 0
        self.clear_called = False

    def get_all_values(self):
        return [list(row) for row in self.grid]

    def clear(self):
        self.clear_called = True
        self.grid = []

    def update(self, values, **kwargs):
        self.update_calls += 1
        self.grid = [list(row) for row in values]

    def batch_update(self, data, **kwargs):
        self.batch_update_calls += 1
        for update in data:
            range_name = update["range"]
            values = update["values"]
            row_idx, col_idx = a1_to_rowcol(range_name)
            while len(self.grid) < row_idx:
                self.grid.append([])
            row = self.grid[row_idx - 1]
            while len(row) < col_idx:
                row.append("")
            row[col_idx - 1] = values[0][0]

    def append_rows(self, values, value_input_option="RAW", **kwargs):
        self.append_rows_calls += 1
        for row in values:
            self.grid.append(list(row))

class FakeSpreadsheet:
    def __init__(self, worksheets=None, should_raise_permission_error=False):
        self.worksheets_dict = worksheets or {}
        self.should_raise_permission_error = should_raise_permission_error
        self.add_worksheet_calls = 0

    def worksheet(self, title):
        if self.should_raise_permission_error:
            import requests
            response = requests.Response()
            response.status_code = 403
            response._content = b'{"error": {"code": 403, "message": "Permission denied", "status": "PERMISSION_DENIED"}}'
            raise gspread.exceptions.APIError(response)
        
        if title in self.worksheets_dict:
            return self.worksheets_dict[title]
        raise gspread.exceptions.WorksheetNotFound()

    def add_worksheet(self, title, rows, cols):
        self.add_worksheet_calls += 1
        ws = FakeWorksheet(title)
        self.worksheets_dict[title] = ws
        return ws

class FakeGSpreadClient:
    def __init__(self, spreadsheet=None):
        self.spreadsheet = spreadsheet or FakeSpreadsheet()

    def open_by_key(self, sheet_id):
        return self.spreadsheet

@pytest.fixture
def mock_gspread(monkeypatch):
    fake_ws = FakeWorksheet("Ledger")
    fake_sh = FakeSpreadsheet(worksheets={"Ledger": fake_ws})
    fake_client = FakeGSpreadClient(fake_sh)
    
    monkeypatch.setattr(gspread, "authorize", lambda creds: fake_client)
    monkeypatch.setattr(Credentials, "from_service_account_file", lambda path, scopes=None: FakeCredentials())
    
    return fake_ws, fake_sh

@pytest.mark.asyncio
async def test_sync_create_from_empty(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    fake_ws.grid = []
    
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    rows = [
        make_ledger_row("h1", company="Company A"),
        make_ledger_row("h2", company="Company B")
    ]
    
    summary = await sync.sync(rows)
    assert summary.rows_added == 2
    assert summary.rows_updated == 0
    assert summary.worksheet_created is False
    
    values = fake_ws.get_all_values()
    assert len(values) == 3
    assert values[0][0] == "identity_hash"
    assert values[1][0] == "h1"
    assert values[1][1] == "Company A"
    assert values[2][0] == "h2"
    assert values[2][1] == "Company B"

@pytest.mark.asyncio
async def test_sync_worksheet_created_flag(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    fake_sh.worksheets_dict.clear()
    
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    rows = [make_ledger_row("h1")]
    
    summary = await sync.sync(rows)
    assert summary.worksheet_created is True
    assert fake_sh.add_worksheet_calls == 1
    
    created_ws = fake_sh.worksheets_dict["Ledger"]
    assert len(created_ws.get_all_values()) == 2

@pytest.mark.asyncio
async def test_sync_idempotent_update(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    
    rows = [make_ledger_row("h1", company="Company A")]
    await sync.sync(rows)
    
    rows[0].title = "Senior Engineer"
    summary = await sync.sync(rows)
    assert summary.rows_updated == 1
    assert summary.rows_added == 0
    
    values = fake_ws.get_all_values()
    assert len(values) == 2
    assert values[1][2] == "Senior Engineer"

@pytest.mark.asyncio
async def test_sync_user_column_preserved(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    
    from persistence.ledger import APP_OWNED_COLUMNS
    headers = list(APP_OWNED_COLUMNS) + ["Notes", "Follow-up"]
    row_values = ["h1"] + [""] * (len(APP_OWNED_COLUMNS) - 1) + ["Interested", "Call tomorrow"]
    fake_ws.grid = [headers, row_values]
    
    rows = [make_ledger_row("h1", company="Company A")]
    summary = await sync.sync(rows)
    
    assert summary.rows_updated == 1
    assert "Notes" in summary.user_columns_preserved
    assert "Follow-up" in summary.user_columns_preserved
    
    values = fake_ws.get_all_values()
    assert len(values) == 2
    assert values[1][headers.index("Notes")] == "Interested"
    assert values[1][headers.index("Follow-up")] == "Call tomorrow"
    assert values[1][headers.index("company")] == "Company A"

@pytest.mark.asyncio
async def test_sync_sort_independence(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    
    rows = [
        make_ledger_row("h1", company="Company A"),
        make_ledger_row("h2", company="Company B")
    ]
    await sync.sync(rows)
    
    grid = fake_ws.get_all_values()
    shuffled_grid = [grid[0], grid[2], grid[1]]
    fake_ws.grid = shuffled_grid
    
    rows[0].title = "Staff Engineer"
    rows[1].title = "Principal Engineer"
    
    await sync.sync(rows)
    
    final_values = fake_ws.get_all_values()
    assert final_values[1][0] == "h2"
    assert final_values[1][2] == "Principal Engineer"
    assert final_values[2][0] == "h1"
    assert final_values[2][2] == "Staff Engineer"

@pytest.mark.asyncio
async def test_sync_missing_identity_hash_column(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    fake_ws.grid = [
        ["company", "title"],
        ["Company A", "Engineer"]
    ]
    
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    rows = [make_ledger_row("h1")]
    
    with pytest.raises(LedgerSyncError, match="Missing identity_hash column in sheet headers"):
        await sync.sync(rows)

@pytest.mark.asyncio
async def test_sync_no_delete(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    
    rows = [
        make_ledger_row("h1", company="Company A"),
        make_ledger_row("h2", company="Company B")
    ]
    await sync.sync(rows)
    
    await sync.sync([rows[0]])
    
    values = fake_ws.get_all_values()
    assert len(values) == 3
    hashes = [r[0] for r in values[1:]]
    assert "h1" in hashes
    assert "h2" in hashes

@pytest.mark.asyncio
async def test_sync_batching(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    
    rows = [
        make_ledger_row("h1", company="Company A"),
        make_ledger_row("h2", company="Company B"),
        make_ledger_row("h3", company="Company C")
    ]
    await sync.sync(rows)
    assert fake_ws.update_calls == 1
    assert fake_ws.batch_update_calls == 0
    assert fake_ws.append_rows_calls == 0
    
    rows[0].title = "New Developer 1"
    rows[1].title = "New Developer 2"
    rows.append(make_ledger_row("h4", company="Company D"))
    
    await sync.sync(rows)
    
    assert fake_ws.batch_update_calls == 1
    assert fake_ws.append_rows_calls == 1

@pytest.mark.asyncio
async def test_sync_auth_failure_path(mock_gspread):
    fake_ws, fake_sh = mock_gspread
    fake_sh.should_raise_permission_error = True
    
    sync = GoogleSheetLedger("secrets/service_account.json", "fake_sheet_id")
    rows = [make_ledger_row("h1")]
    
    with pytest.raises(LedgerSyncError) as exc_info:
        await sync.sync(rows)
        
    err_msg = str(exc_info.value)
    assert "Ensure the sheet is shared with the service account email as Editor" in err_msg
    assert "service-account@test-project.iam.gserviceaccount.com" in err_msg
