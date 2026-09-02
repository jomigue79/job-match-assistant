import asyncio
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from domain import (
    JobPosting,
    JobStatus,
    MatchResult,
    CoverLetter,
    Run,
    RunStatus,
    RunCost,
)
from persistence import init_db, PersistenceService

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "service_test.db"
    init_db(str(path))
    return str(path)

@pytest.mark.asyncio
async def test_job_round_trip_gate1(db_path):
    service = PersistenceService(db_path)
    
    scraped_at = datetime.now(timezone.utc)
    job = JobPosting(
        company="Acme Corp",
        title="Software Engineer",
        location="Remote",
        url="https://example.com/acme",
        description="A great job",
        source="apify_linkedin",
        scraped_at=scraped_at,
        status=JobStatus.SCRAPED,
        identity_hash=""
    )
    
    # Save
    await service.upsert_job(job)
    
    # Get
    retrieved = await service.get_job(job.identity_hash)
    
    assert retrieved is not None
    assert retrieved.identity_hash == job.identity_hash
    assert retrieved.company == "Acme Corp"
    assert retrieved.title == "Software Engineer"
    assert retrieved.location == "Remote"
    assert retrieved.url == "https://example.com/acme"
    assert retrieved.description == "A great job"
    assert retrieved.source == "apify_linkedin"
    assert abs((retrieved.scraped_at - job.scraped_at).total_seconds()) < 1.0
    assert retrieved.status == JobStatus.SCRAPED

@pytest.mark.asyncio
async def test_upsert_preserves_status_refreshes_metadata(db_path):
    service = PersistenceService(db_path)
    
    job = JobPosting(
        company="Acme Corp",
        title="Software Engineer",
        location="Remote",
        url="https://example.com/acme-1",
        description="Version 1 description",
        source="apify",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
        identity_hash=""
    )
    
    # First save
    await service.upsert_job(job)
    
    # Transition to matched
    await service.set_status(job.identity_hash, JobStatus.MATCHED)
    
    # Upsert same job with refreshed metadata
    refreshed_job = JobPosting(
        company="Acme Corp",
        title="Software Engineer",
        location="Remote",
        url="https://example.com/acme-refreshed",
        description="Version 2 description",
        source="apify_new",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED, # This should NOT clobber matched status
        identity_hash=""
    )
    
    await service.upsert_job(refreshed_job)
    
    retrieved = await service.get_job(job.identity_hash)
    assert retrieved.status == JobStatus.MATCHED
    assert retrieved.description == "Version 2 description"
    assert retrieved.url == "https://example.com/acme-refreshed"
    assert retrieved.source == "apify_new"

@pytest.mark.asyncio
async def test_concurrent_writes_stress(db_path):
    service = PersistenceService(db_path)
    
    # Generate 60 tasks (distinct jobs) + some match results to write concurrently
    tasks = []
    
    for i in range(60):
        job = JobPosting(
            company=f"Company {i}",
            title=f"Engineer {i}",
            location="Remote",
            url=f"https://example.com/{i}",
            description="desc",
            source="test",
            scraped_at=datetime.now(timezone.utc),
            status=JobStatus.SCRAPED,
            identity_hash=""
        )
        tasks.append(service.upsert_job(job))
        
        # Save a match result for some
        if i % 3 == 0:
            match = MatchResult(
                identity_hash=job.identity_hash,
                score=i + 20,
                dimension_breakdown={"overall": i + 20},
                match_reasons=["reason"],
                scored_at=datetime.now(timezone.utc)
            )
            tasks.append(service.save_match_result(match))

    # Run all tasks concurrently
    # This should complete without raises any sqlite3.OperationalError: database is locked
    await asyncio.gather(*tasks)
    
    # Verify final counts in database
    jobs = await service.list_jobs()
    assert len(jobs) == 60
    
    # Fetch existing hashes
    hashes = [j.identity_hash for j in jobs]
    retrieved_hashes = await service.existing_hashes(hashes)
    assert len(retrieved_hashes) == 60

@pytest.mark.asyncio
async def test_counters_calculation(db_path):
    service = PersistenceService(db_path)
    
    # We will build:
    # 1 job as MATCHED
    # 1 job as REJECTED (with cover letter) -> should count in written, and rejected
    # 1 job as APPLIED (with cover letter) -> should count in written, and applied
    # 1 job as SCRAPED
    
    j1 = JobPosting(company="C1", title="T1", location="L1", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="")
    j2 = JobPosting(company="C2", title="T2", location="L2", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="")
    j3 = JobPosting(company="C3", title="T3", location="L3", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="")
    j4 = JobPosting(company="C4", title="T4", location="L4", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="")
    
    await service.upsert_job(j1)
    await service.upsert_job(j2)
    await service.upsert_job(j3)
    await service.upsert_job(j4)
    
    # Move j1 to MATCHED
    await service.set_status(j1.identity_hash, JobStatus.MATCHED)
    
    # Move j2 to MATCHED, then WRITTEN, then REJECTED (with letter)
    await service.set_status(j2.identity_hash, JobStatus.MATCHED)
    await service.set_status(j2.identity_hash, JobStatus.WRITTEN)
    await service.save_cover_letter(j2.identity_hash, "Letter 2")
    await service.set_status(j2.identity_hash, JobStatus.REJECTED)
    
    # Move j3 to MATCHED, then WRITTEN, then APPLIED (with letter)
    await service.set_status(j3.identity_hash, JobStatus.MATCHED)
    await service.set_status(j3.identity_hash, JobStatus.WRITTEN)
    await service.save_cover_letter(j3.identity_hash, "Letter 3")
    await service.set_status(j3.identity_hash, JobStatus.APPLIED)
    
    # Retrieve counters
    cnt = await service.counters()
    assert cnt.total == 4
    assert cnt.rejected == 1 # j2
    assert cnt.written == 2  # j2 and j3 (even though j2 was rejected, we "ever wrote" a letter for it)
    assert cnt.applied == 1  # j3
    
    # Verify status breakdown
    breakdown = await service.status_breakdown()
    assert breakdown[JobStatus.SCRAPED] == 1 # j4
    assert breakdown[JobStatus.MATCHED] == 1 # j1
    assert breakdown[JobStatus.REJECTED] == 1 # j2
    assert breakdown[JobStatus.APPLIED] == 1 # j3

@pytest.mark.asyncio
async def test_cover_letter_versioning(db_path):
    service = PersistenceService(db_path)
    
    job = JobPosting(company="Acme", title="Dev", location="Lisbon", source="s", scraped_at=datetime.now(timezone.utc), identity_hash="")
    await service.upsert_job(job)
    
    # Save first letter
    cl1 = await service.save_cover_letter(job.identity_hash, "Dear Acme, I am writing v1...")
    assert cl1.version == 1
    assert cl1.text == "Dear Acme, I am writing v1..."
    
    # Save second letter
    cl2 = await service.save_cover_letter(job.identity_hash, "Dear Acme, updated cover letter v2...")
    assert cl2.version == 2
    assert cl2.text == "Dear Acme, updated cover letter v2..."
    
    # Get latest
    latest = await service.get_latest_cover_letter(job.identity_hash)
    assert latest is not None
    assert latest.version == 2
    assert latest.text == "Dear Acme, updated cover letter v2..."

@pytest.mark.asyncio
async def test_status_transition_rules_safety(db_path):
    service = PersistenceService(db_path)
    
    job = JobPosting(company="Acme", title="Dev", location="Lisbon", source="s", scraped_at=datetime.now(timezone.utc), identity_hash="")
    await service.upsert_job(job)
    
    # Scraped -> Matched succeeds
    await service.set_status(job.identity_hash, JobStatus.MATCHED)
    
    # Matched -> Applied succeeds (valid now)
    await service.set_status(job.identity_hash, JobStatus.APPLIED)
    
    # Applied -> Matched raises ValueError (invalid)
    with pytest.raises(ValueError):
        await service.set_status(job.identity_hash, JobStatus.MATCHED)

@pytest.mark.asyncio
async def test_save_match_result_upserts(db_path):
    service = PersistenceService(db_path)
    
    job = JobPosting(company="Acme", title="Dev", location="Lisbon", source="s", scraped_at=datetime.now(timezone.utc), identity_hash="")
    await service.upsert_job(job)
    
    # First match result
    mr1 = MatchResult(
        identity_hash=job.identity_hash,
        score=75,
        dimension_breakdown={"overall": 75},
        match_reasons=["looks good"],
        scored_at=datetime.now(timezone.utc)
    )
    await service.save_match_result(mr1)
    
    # Second match result (overwrite)
    mr2 = MatchResult(
        identity_hash=job.identity_hash,
        score=85,
        dimension_breakdown={"overall": 85, "niche": 90},
        match_reasons=["looks great now"],
        scored_at=datetime.now(timezone.utc)
    )
    await service.save_match_result(mr2)
    
    retrieved = await service.get_match_result(job.identity_hash)
    assert retrieved.score == 85
    assert retrieved.dimension_breakdown == {"overall": 85, "niche": 90}
    assert retrieved.match_reasons == ["looks great now"]

@pytest.mark.asyncio
async def test_run_crud_operations(db_path):
    service = PersistenceService(db_path)
    
    run = Run(
        run_id="run-100",
        source="apify_linkedin",
        status=RunStatus.QUEUED,
        started_at=datetime.now(timezone.utc),
        cost=RunCost()
    )
    
    # Save queued run
    await service.save_run(run)
    
    # Update to running
    run.status = RunStatus.RUNNING
    run.n_scraped = 10
    run.n_new = 5
    await service.save_run(run)
    
    # Finalize with RunCost
    run.status = RunStatus.DONE
    run.finished_at = datetime.now(timezone.utc)
    run.cost = RunCost(
        total_input_tokens=15000,
        total_output_tokens=5000,
        total_llm_calls=5,
        apify_compute_units=0.8,
        apify_results=10,
        estimated_cost_usd=0.35
    )
    await service.save_run(run)
    
    # Retrieve and verify reconstruction
    retrieved = await service.get_run("run-100")
    assert retrieved is not None
    assert retrieved.run_id == "run-100"
    assert retrieved.status == RunStatus.DONE
    assert retrieved.n_scraped == 10
    assert retrieved.n_new == 5
    assert retrieved.cost.total_input_tokens == 15000
    assert retrieved.cost.estimated_cost_usd == 0.35
    assert retrieved.finished_at is not None
    assert abs((retrieved.finished_at - run.finished_at).total_seconds()) < 1.0
    
    # List recent runs
    recent = await service.list_recent_runs(limit=10)
    assert len(recent) == 1
    assert recent[0].run_id == "run-100"

@pytest.mark.asyncio
async def test_list_jobs_with_match(db_path):
    service = PersistenceService(db_path)
    
    j_scraped = JobPosting(company="Scraped Co", title="Scraped Job", location="L1", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="")
    await service.upsert_job(j_scraped)
    
    j_matched = JobPosting(company="Matched Co", title="Matched Job", location="L2", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.SCRAPED, identity_hash="")
    await service.upsert_job(j_matched)
    await service.set_status(j_matched.identity_hash, JobStatus.MATCHED)
    
    mr = MatchResult(
        identity_hash=j_matched.identity_hash,
        score=85,
        dimension_breakdown={"overall": 85.0},
        match_reasons=["reason"],
        scored_at=datetime.now(timezone.utc)
    )
    await service.save_match_result(mr)
    
    all_results = await service.list_jobs_with_match()
    assert len(all_results) == 2
    
    scraped_item = next(item for item in all_results if item.job.identity_hash == j_scraped.identity_hash)
    assert scraped_item.match is None
    assert scraped_item.job.status == JobStatus.SCRAPED
    
    matched_item = next(item for item in all_results if item.job.identity_hash == j_matched.identity_hash)
    assert matched_item.match is not None
    assert matched_item.match.score == 85
    assert matched_item.job.status == JobStatus.MATCHED
    
    matched_only = await service.list_jobs_with_match(status=JobStatus.MATCHED)
    assert len(matched_only) == 1
    assert matched_only[0].job.identity_hash == j_matched.identity_hash
