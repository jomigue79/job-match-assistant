import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import get_settings, Settings, ConfigurationError
from coordinator import RunCoordinator
from coordinator.run_coordinator import project_apify_cost
from domain import RunStatus, MatchResult
from ingestion import (
    IngestionService,
    RawScrapeCache,
    ReplayScraperClient,
    ScrapeQuery,
    ScraperError,
    PartialFetchError,
    Normalizer,
    NormalizeResult,
    DedupService,
)
from persistence import init_db, PersistenceService


OPTIONAL_KEYS = [
    "SCRAPER_QUERY",
    "SCRAPER_MAX_QUERIES",
    "APIFY_ACTOR_START_USD",
    "APIFY_RESULT_USD",
    "SCRAPER_RESULTS_PER_LIMIT",
    "RUN_BUDGET_CAP_USD",
    "REPLAY_FROM_CACHE",
]


@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    from persistence.ledger import GoogleSheetLedger, SyncSummary

    async def fake_sync(self, rows):
        return SyncSummary(
            rows_updated=0,
            rows_added=len(rows),
            user_columns_preserved=[],
            worksheet_created=False
        )

    monkeypatch.setattr(GoogleSheetLedger, "sync", fake_sync)
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-llm-key")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(tmp_path / "raw_scrapes"))
    for key in OPTIONAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def live_cost_env(monkeypatch):
    """Pins every value the budget check reads, so the developer's .env cannot move it."""
    monkeypatch.setenv("REPLAY_FROM_CACHE", "False")
    monkeypatch.setenv("APIFY_ACTOR_START_USD", "0.01")
    monkeypatch.setenv("APIFY_RESULT_USD", "0.003")
    monkeypatch.setenv("SCRAPER_RESULTS_PER_LIMIT", "4.0")
    monkeypatch.setenv("RUN_BUDGET_CAP_USD", "2.00")
    get_settings.cache_clear()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "multi_query_test.db"
    init_db(str(path))
    return str(path)


# --- Hand-rolled fakes (no mocking library) ---

class ScriptedScraperClient:
    """Returns fixed records per title; raises the given ScraperError for failing titles."""

    def __init__(self, records_by_terms=None, errors_by_terms=None):
        self.records_by_terms = records_by_terms or {}
        self.errors_by_terms = errors_by_terms or {}
        self.calls = []

    def fetch(self, query):
        self.calls.append(query.terms)
        if query.terms in self.errors_by_terms:
            raise self.errors_by_terms[query.terms]
        return [dict(r) for r in self.records_by_terms.get(query.terms, [])]


class RecordingIngestionService:
    def __init__(self, records=None):
        self.records = records or []
        self.calls = []

    async def fetch(self, queries):
        self.calls.append(queries)
        return self.records


class EmptyNormalizer:
    def normalize(self, records):
        return NormalizeResult(job_postings=[], errors=[])


class FixedScorer:
    async def score(self, job, knowledge, cost_accumulator=None):
        return MatchResult(
            identity_hash=job.identity_hash,
            score=80,
            dimension_breakdown={"Criteria": 80.0},
            match_reasons=["Score is 80"],
            scored_at=datetime.now(timezone.utc)
        )


class FakeKnowledgeLoader:
    def load(self):
        from knowledge import KnowledgeBase
        return KnowledgeBase(cv="cv", persona="persona", ats_criteria="criteria")


def query(terms, limit=10):
    return ScrapeQuery(terms=terms, location="Porto", limit=limit)


def posting_record(company, title):
    return {
        "company": company,
        "title": title,
        "location": "Porto",
        "url": f"https://example.org/{company}/{title}".replace(" ", "-"),
        "description": "A valid job description"
    }


def read_envelopes(cache_dir):
    envelopes = []
    for path in sorted(Path(cache_dir).glob("scrape_*.json")):
        with open(path, "r", encoding="utf-8") as f:
            envelopes.append(json.load(f))
    return envelopes


# --- Settings ---

def test_scraper_query_splits_strips_and_drops_empties(monkeypatch):
    monkeypatch.setenv("SCRAPER_QUERY", " Project Manager , ,Technical Project Manager ,")
    settings = Settings(_env_file=None)
    assert settings.scraper_queries == ["Project Manager", "Technical Project Manager"]
    assert settings.scraper_query == "Project Manager,Technical Project Manager"


def test_more_titles_than_max_fails_at_boot(monkeypatch):
    monkeypatch.setenv("SCRAPER_MAX_QUERIES", "2")
    monkeypatch.setenv("SCRAPER_QUERY", "A,B,C")
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
    assert "SCRAPER_QUERY lists 3 job titles; SCRAPER_MAX_QUERIES allows 2" in str(exc_info.value)


def test_default_max_allows_five_titles_and_rejects_six(monkeypatch):
    monkeypatch.setenv("SCRAPER_QUERY", "A,B,C,D,E")
    assert Settings(_env_file=None).scraper_queries == ["A", "B", "C", "D", "E"]

    monkeypatch.setenv("SCRAPER_QUERY", "A,B,C,D,E,F")
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
    assert "SCRAPER_QUERY lists 6 job titles; SCRAPER_MAX_QUERIES allows 5" in str(exc_info.value)


def test_duplicate_titles_rejected_case_insensitively(monkeypatch):
    monkeypatch.setenv("SCRAPER_QUERY", "Project Manager, project MANAGER")
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
    assert "same job title more than once" in str(exc_info.value)


def test_scraper_query_with_no_titles_rejected(monkeypatch):
    monkeypatch.setenv("SCRAPER_QUERY", " , ,")
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
    assert "at least one job title" in str(exc_info.value)


def test_max_queries_below_one_and_negative_cap_rejected(monkeypatch):
    monkeypatch.setenv("SCRAPER_MAX_QUERIES", "0")
    monkeypatch.setenv("RUN_BUDGET_CAP_USD", "-1")
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
    message = str(exc_info.value)
    assert "scraper_max_queries must be at least 1" in message
    assert "run_budget_cap_usd must not be negative" in message


def test_new_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.scraper_max_queries == 5
    assert settings.apify_actor_start_usd == 0.01
    assert settings.apify_result_usd == 0.003
    assert settings.scraper_results_per_limit == 4.5
    assert settings.run_budget_cap_usd == 2.00


# --- Projection ---

def test_project_apify_cost_literal_values():
    five = [query(t) for t in ["a", "b", "c", "d", "e"]]
    assert project_apify_cost(five, 0.01, 0.003, 4.0) == pytest.approx(0.65)
    assert project_apify_cost(five[:1], 0.01, 0.003, 4.0) == pytest.approx(0.13)
    assert project_apify_cost([query("a", 10), query("b", 20)], 0.01, 0.003, 4.0) == pytest.approx(0.38)
    assert project_apify_cost([], 0.01, 0.003, 4.0) == 0


# --- Cache envelope ---

def test_single_query_envelope_keeps_legacy_query_key(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    cache.write([{"jobId": "1"}], "src", [query("Project Manager")])
    (envelope,) = read_envelopes(tmp_path)
    assert envelope["query"] == {"terms": "Project Manager", "location": "Porto", "limit": 10}
    assert envelope["queries"] == [envelope["query"]]


def test_multi_query_envelope_has_queries_without_legacy_key(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    cache.write([{"jobId": "1"}, {"jobId": "2"}], "src", [query("A"), query("B")])
    (envelope,) = read_envelopes(tmp_path)
    assert "query" not in envelope
    assert [q["terms"] for q in envelope["queries"]] == ["A", "B"]
    assert envelope["count"] == 2


# --- IngestionService ---

@pytest.mark.asyncio
async def test_two_queries_make_two_calls_and_one_merged_envelope(tmp_path):
    client = ScriptedScraperClient(records_by_terms={
        "A": [{"jobId": "a1"}, {"jobId": "a2"}],
        "B": [{"jobId": "b1"}],
    })
    service = IngestionService(client, RawScrapeCache(str(tmp_path)), source="src")

    records = await service.fetch([query("A"), query("B")])

    assert client.calls == ["A", "B"]
    assert [r["jobId"] for r in records] == ["a1", "a2", "b1"]
    (envelope,) = read_envelopes(tmp_path)
    assert [q["terms"] for q in envelope["queries"]] == ["A", "B"]
    assert envelope["count"] == 3
    assert [r["jobId"] for r in envelope["records"]] == ["a1", "a2", "b1"]


@pytest.mark.asyncio
async def test_partial_failure_keeps_records_from_successful_queries(tmp_path):
    client = ScriptedScraperClient(
        records_by_terms={"A": [{"jobId": "a1"}], "C": [{"jobId": "c1"}]},
        errors_by_terms={"B": ScraperError("actor B exploded")},
    )
    service = IngestionService(client, RawScrapeCache(str(tmp_path)), source="src")

    with pytest.raises(PartialFetchError) as exc_info:
        await service.fetch([query("A"), query("B"), query("C")])

    assert client.calls == ["A", "B", "C"]
    assert [r["jobId"] for r in exc_info.value.records] == ["a1", "c1"]
    assert [(q.terms, msg) for q, msg in exc_info.value.failures] == [("B", "actor B exploded")]
    (envelope,) = read_envelopes(tmp_path)
    assert [q["terms"] for q in envelope["queries"]] == ["A", "C"]
    assert envelope["count"] == 2


@pytest.mark.asyncio
async def test_every_query_failing_raises_scraper_error_and_writes_nothing(tmp_path):
    error_b = ScraperError("B failed")
    client = ScriptedScraperClient(errors_by_terms={"A": ScraperError("A failed"), "B": error_b})
    service = IngestionService(client, RawScrapeCache(str(tmp_path)), source="src")

    with pytest.raises(ScraperError) as exc_info:
        await service.fetch([query("A"), query("B")])

    assert type(exc_info.value) is ScraperError
    assert "All 2 queries failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is error_b
    assert read_envelopes(tmp_path) == []


@pytest.mark.asyncio
async def test_single_failing_query_reraises_original_error(tmp_path):
    original = ScraperError("Apify actor call failed: boom")
    client = ScriptedScraperClient(errors_by_terms={"A": original})
    service = IngestionService(client, RawScrapeCache(str(tmp_path)), source="src")

    with pytest.raises(ScraperError) as exc_info:
        await service.fetch(query("A"))

    assert exc_info.value is original
    assert read_envelopes(tmp_path) == []


@pytest.mark.asyncio
async def test_replay_of_merged_envelope_returns_pooled_records_once(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    live_client = ScriptedScraperClient(records_by_terms={
        "A": [{"jobId": "a1"}, {"jobId": "a2"}],
        "B": [{"jobId": "b1"}],
    })
    queries = [query("A"), query("B")]
    pooled = await IngestionService(live_client, cache, source="src").fetch(queries)

    replay_service = IngestionService(
        ReplayScraperClient(cache=cache, source="src"),
        cache,
        source="src",
        write_to_cache=False
    )
    replayed = await replay_service.fetch(queries)

    assert replayed == pooled
    assert len(replayed) == 3
    assert len(read_envelopes(tmp_path)) == 1


@pytest.mark.asyncio
async def test_pre_change_envelope_still_replays(tmp_path):
    legacy = {
        "source": "src",
        "query": {"terms": "Project Manager", "location": "Porto", "limit": 10},
        "fetched_at": "2026-09-08T08:33:24.831841+00:00",
        "count": 2,
        "records": [{"jobId": "old1"}, {"jobId": "old2"}],
    }
    (tmp_path / "scrape_src_20260908_083324_831809.json").write_text(json.dumps(legacy), encoding="utf-8")
    cache = RawScrapeCache(str(tmp_path))
    service = IngestionService(
        ReplayScraperClient(cache=cache, source="src"),
        cache,
        source="src",
        write_to_cache=False
    )

    records = await service.fetch([query("Project Manager"), query("Technical Project Manager")])

    assert [r["jobId"] for r in records] == ["old1", "old2"]
    assert cache.list_cached(source="src")[0].count == 2


# --- RunCoordinator ---

@pytest.mark.asyncio
async def test_budget_refusal_records_failed_run_without_fetching(db_path, live_cost_env):
    persistence = PersistenceService(db_path)
    ingestion = RecordingIngestionService()
    coordinator = RunCoordinator(
        ingestion_service=ingestion,
        normalizer=EmptyNormalizer(),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        source_name="apify_linkedin"
    )

    # 0.01 + 1000 * 4.0 * 0.003 = 12.01, over the 2.00 cap
    run = coordinator.start_run([query("Project Manager", limit=1000)])
    await coordinator.wait()

    assert ingestion.calls == []
    assert run.status == RunStatus.FAILED
    assert run.n_errors == 1
    assert run.n_scraped == 0
    assert run.finished_at is not None
    persisted = await persistence.get_run(run.run_id)
    assert persisted.status == RunStatus.FAILED
    assert persisted.n_errors == 1
    assert coordinator.get_active_run().status == RunStatus.FAILED

    # The refusal released the run lock; a run under the cap proceeds.
    run2 = coordinator.start_run([query("Project Manager", limit=10)])
    await coordinator.wait()
    assert run2.status == RunStatus.DONE
    assert len(ingestion.calls) == 1


@pytest.mark.asyncio
async def test_replay_mode_projects_zero_and_is_not_refused(db_path, live_cost_env, monkeypatch):
    monkeypatch.setenv("REPLAY_FROM_CACHE", "True")
    get_settings.cache_clear()
    persistence = PersistenceService(db_path)
    ingestion = RecordingIngestionService()
    coordinator = RunCoordinator(
        ingestion_service=ingestion,
        normalizer=EmptyNormalizer(),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        source_name="apify_linkedin"
    )

    run = coordinator.start_run([query("Project Manager", limit=1000)])
    await coordinator.wait()

    assert len(ingestion.calls) == 1
    assert run.status == RunStatus.DONE
    assert run.n_errors == 0


@pytest.mark.asyncio
async def test_start_run_builds_one_query_per_title(db_path, live_cost_env, monkeypatch):
    monkeypatch.setenv("SCRAPER_QUERY", "Project Manager, Technical Project Manager")
    monkeypatch.setenv("SCRAPER_LOCATION", "Porto")
    monkeypatch.setenv("SCRAPER_LIMIT", "10")
    get_settings.cache_clear()
    persistence = PersistenceService(db_path)
    ingestion = RecordingIngestionService()
    coordinator = RunCoordinator(
        ingestion_service=ingestion,
        normalizer=EmptyNormalizer(),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        source_name="apify_linkedin"
    )

    coordinator.start_run()
    await coordinator.wait()

    (sent,) = ingestion.calls
    assert [q.terms for q in sent] == ["Project Manager", "Technical Project Manager"]
    assert {q.location for q in sent} == {"Porto"}
    assert {q.limit for q in sent} == {10}


@pytest.mark.asyncio
async def test_start_run_with_empty_query_list_leaves_no_phantom_run(db_path, live_cost_env):
    persistence = PersistenceService(db_path)
    ingestion = RecordingIngestionService()
    coordinator = RunCoordinator(
        ingestion_service=ingestion,
        normalizer=EmptyNormalizer(),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        source_name="apify_linkedin"
    )

    with pytest.raises(ValueError):
        coordinator.start_run([])

    assert coordinator.get_active_run() is None
    assert coordinator._active_task is None

    run = coordinator.start_run([query("Project Manager")])
    await coordinator.wait()
    assert run.status == RunStatus.DONE


@pytest.mark.asyncio
async def test_single_failing_query_leaves_run_alive_scraped_and_scored(db_path, live_cost_env, tmp_path):
    client = ScriptedScraperClient(
        records_by_terms={
            "Project Manager": [posting_record("Acme", "Project Manager")],
            "Technical Project Manager": [posting_record("Beta", "Technical Project Manager")],
        },
        errors_by_terms={"Producer": ScraperError("actor call failed")},
    )
    persistence = PersistenceService(db_path)
    coordinator = RunCoordinator(
        ingestion_service=IngestionService(client, RawScrapeCache(str(tmp_path / "cache")), source="apify_linkedin"),
        normalizer=Normalizer(source_name="apify_linkedin"),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        scorer=FixedScorer(),
        knowledge_loader=FakeKnowledgeLoader(),
        score_threshold=60,
        source_name="apify_linkedin"
    )

    run = coordinator.start_run([query("Project Manager"), query("Producer"), query("Technical Project Manager")])
    await coordinator.wait()

    assert client.calls == ["Project Manager", "Producer", "Technical Project Manager"]
    assert run.status == RunStatus.DONE
    assert run.n_errors == 1
    assert run.n_scraped == 2
    assert run.n_new == 2
    assert run.n_matched == 2
    assert len(await persistence.list_jobs()) == 2


@pytest.mark.asyncio
async def test_posting_found_by_two_queries_becomes_one_job(db_path, live_cost_env, tmp_path):
    same = posting_record("Acme", "Project Manager")
    client = ScriptedScraperClient(records_by_terms={
        "Project Manager": [same],
        "Technical Project Manager": [same],
    })
    persistence = PersistenceService(db_path)
    coordinator = RunCoordinator(
        ingestion_service=IngestionService(client, RawScrapeCache(str(tmp_path / "cache")), source="apify_linkedin"),
        normalizer=Normalizer(source_name="apify_linkedin"),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        source_name="apify_linkedin"
    )

    run = coordinator.start_run([query("Project Manager"), query("Technical Project Manager")])
    await coordinator.wait()

    assert run.status == RunStatus.DONE
    assert run.n_scraped == 2
    assert run.n_new == 1
    assert len(await persistence.list_jobs()) == 1
