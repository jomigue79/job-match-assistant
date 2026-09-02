import pytest
from datetime import datetime, timezone

from config import get_settings
from domain import Run, RunStatus, JobPosting, JobStatus
from persistence import init_db, PersistenceService, Counters, JobWithMatch
from ingestion import RawScrapeCache, ScrapeQuery
from coordinator import build_run_coordinator
from ui.page import build_view_state, MatchCard, NonMatchCompact, PendingCompact
from helpers import load_fixture_scrape

@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    from persistence.ledger import GoogleSheetLedger, SyncSummary
    async def mock_sync(self, rows):
        return SyncSummary(
            rows_updated=0,
            rows_added=len(rows),
            user_columns_preserved=[],
            worksheet_created=False
        )
    monkeypatch.setattr(GoogleSheetLedger, "sync", mock_sync)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

class FakeScorer:
    async def score(self, job, knowledge, cost_accumulator=None):
        from domain import MatchResult
        # Assign 85 for Google (matched) and 55 for Acme (no_match)
        score = 85 if "google" in job.company.lower() else 55
        return MatchResult(
            identity_hash=job.identity_hash,
            score=score,
            dimension_breakdown={"overall": float(score)},
            match_reasons=[f"Score is {score}"],
            scored_at=datetime.now(timezone.utc)
        )

def test_build_view_state_idle():
    active_run = None
    jobs_with_match = []
    counters = Counters(total=0, rejected=0, written=0, applied=0)
    status_breakdown = {}
    
    vs = build_view_state(active_run, jobs_with_match, counters, status_breakdown)
    
    assert vs.status_text == "idle"
    assert vs.n_scraped == 0
    assert vs.n_new == 0
    assert vs.total_count == 0
    assert vs.run_button_disabled is False
    assert vs.matches == []
    assert vs.non_matches == []
    assert vs.pending == []
    assert vs.matched_count == 0
    assert vs.no_match_count == 0
    assert vs.scraped_count == 0

def test_build_view_state_running_and_grouping():
    active_run = Run(
        run_id="run-1",
        source="test_source",
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        n_scraped=10,
        n_new=5
    )
    
    j1 = JobPosting(company="Google", title="Engineer", location="Remote", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.MATCHED, identity_hash="hash-1")
    from domain import MatchResult
    mr1 = MatchResult(identity_hash="hash-1", score=85, dimension_breakdown={"overall": 8.5}, match_reasons=["R1"], scored_at=datetime.now(timezone.utc))
    
    j2 = JobPosting(company="Acme", title="Dev", location="Lisbon", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.NO_MATCH, identity_hash="hash-2")
    mr2 = MatchResult(identity_hash="hash-2", score=55, dimension_breakdown={"overall": 5.5}, match_reasons=["R2"], scored_at=datetime.now(timezone.utc))
    
    j3 = JobPosting(company="Pending Co", title="Intern", location="Remote", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="hash-3")
    
    jobs_with_match = [
        JobWithMatch(job=j1, match=mr1),
        JobWithMatch(job=j2, match=mr2),
        JobWithMatch(job=j3, match=None)
    ]
    
    counters = Counters(total=3, rejected=0, written=0, applied=0)
    status_breakdown = {
        JobStatus.MATCHED: 1,
        JobStatus.NO_MATCH: 1,
        JobStatus.SCRAPED: 1
    }
    
    vs = build_view_state(active_run, jobs_with_match, counters, status_breakdown)
    
    assert vs.status_text == "running"
    assert vs.n_scraped == 10
    assert vs.n_new == 5
    assert vs.total_count == 3
    assert vs.run_button_disabled is True
    
    assert len(vs.matches) == 1
    assert vs.matches[0].company == "Google"
    assert vs.matches[0].score == 85
    assert vs.matches[0].dimension_breakdown == {"overall": 8.5}
    
    assert len(vs.non_matches) == 1
    assert vs.non_matches[0].company == "Acme"
    assert vs.non_matches[0].score == 55
    
    assert len(vs.pending) == 1
    assert vs.pending[0].company == "Pending Co"
    
    assert vs.matched_count == 1
    assert vs.no_match_count == 1
    assert vs.scraped_count == 1

def test_cards_signature_stability_and_changes():
    active_run = None
    counters_1 = Counters(total=2, rejected=0, written=0, applied=0)
    breakdown_1 = {JobStatus.MATCHED: 1, JobStatus.NO_MATCH: 1}
    
    vs1 = build_view_state(active_run, [], counters_1, breakdown_1)
    vs2 = build_view_state(active_run, [], counters_1, breakdown_1)
    
    # Stable signature
    assert vs1.cards_signature == vs2.cards_signature
    
    # Signature changes when status counts change
    breakdown_2 = {JobStatus.MATCHED: 2, JobStatus.NO_MATCH: 0}
    vs3 = build_view_state(active_run, [], counters_1, breakdown_2)
    assert vs1.cards_signature != vs3.cards_signature

@pytest.mark.asyncio
async def test_ui_data_path_integration(tmp_path, monkeypatch):
    # Setup directories
    db_file = tmp_path / "app.db"
    raw_dir = tmp_path / "raw_scrapes"
    raw_dir.mkdir()
    
    # Configure env settings
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(raw_dir))
    monkeypatch.setenv("REPLAY_FROM_CACHE", "True")
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-key")
    monkeypatch.setenv("SCORE_THRESHOLD", "70")
    
    get_settings.cache_clear()
    settings = get_settings()
    
    # Initialize DB and PersistenceService
    init_db(settings.db_path)
    persistence = PersistenceService(settings.db_path)
    
    # Write linkedin_jobs fixture to the raw scrapes cache
    cache = RawScrapeCache(settings.raw_scrape_dir)
    query = ScrapeQuery(terms="Software Engineer", location="Remote", limit=5)
    records = load_fixture_scrape("linkedin_jobs")
    cache.write(records, "apify_linkedin", query)
    
    # Initialize RunCoordinator using the factory with a FakeScorer
    coordinator = build_run_coordinator(persistence_service=persistence, scorer=FakeScorer())
    
    # Execute run
    run = coordinator.start_run()
    await coordinator.wait()
    
    # Retrieve updated system outputs
    active_run = coordinator.get_active_run()
    jobs_with_match = await persistence.list_jobs_with_match()
    counters = await persistence.counters()
    status_breakdown = await persistence.status_breakdown()
    
    # Translate to ViewState
    vs = build_view_state(active_run, jobs_with_match, counters, status_breakdown)
    
    # Assert ViewState metrics
    assert vs.status_text == "done"
    assert vs.n_scraped == 2
    assert vs.n_new == 2
    assert vs.total_count == 2
    assert vs.run_button_disabled is False
    
    # Google (85 >= 70) matches, Acme (55 < 70) no_match
    assert len(vs.matches) == 1
    assert vs.matches[0].company == "Google"
    assert vs.matches[0].score == 85
    
    assert len(vs.non_matches) == 1
    assert vs.non_matches[0].company == "Acme   Corp."
    assert vs.non_matches[0].score == 55
    
    assert vs.matched_count == 1
    assert vs.no_match_count == 1
    assert vs.scraped_count == 0
