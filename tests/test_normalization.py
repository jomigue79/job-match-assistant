from datetime import datetime, timezone
import pytest
from structlog.testing import capture_logs
from domain import JobPosting, JobStatus
from ingestion import (
    Normalizer,
    NormalizationError,
    LinkedInJobsAdapter,
    IndeedJobsAdapter,
    get_adapter,
)
from helpers import load_fixture_scrape

def test_normalization_linkedin_fixture():
    # Load linkedin jobs raw fixture
    records = load_fixture_scrape("linkedin_jobs")
    assert len(records) == 2
    
    # Instantiate normalizer for linkedin
    normalizer = Normalizer(source_name="apify_linkedin")
    scraped_time = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    
    res = normalizer.normalize(records, scraped_at=scraped_time)
    
    # Assert successful mappings
    assert len(res.job_postings) == 2
    assert len(res.errors) == 0
    
    j1, j2 = res.job_postings
    
    # Check fields mappings
    assert j1.company == "Acme   Corp." # display fields are trimmed but preserved (not fully normalized)
    assert j1.title == "Sénior Dev"
    assert j1.location == "Lisboa"
    assert j1.url == "https://linkedin.com/jobs/view/1"
    assert j1.description == "This is a great role for a Senior Developer in Lisbon."
    assert j1.source == "apify_linkedin"
    assert j1.status == JobStatus.SCRAPED
    assert j1.scraped_at == scraped_time
    
    # Canonical normalisation of JobIdentity is verified via hash
    # Verify that variant check yields the same hash as the expected canonical form:
    # "acme corp|senior dev|lisboa" -> hash matches e582fb2d7034f64f30dcf829e6cfcb6b2ffd9b8733104ba5e66c601f2acf7533
    expected_hash = "e582fb2d7034f64f30dcf829e6cfcb6b2ffd9b8733104ba5e66c601f2acf7533"
    assert j1.identity_hash == expected_hash

def test_normalization_missing_optional_field():
    records = load_fixture_scrape("linkedin_jobs")
    normalizer = Normalizer(source_name="apify_linkedin")
    res = normalizer.normalize(records)
    
    j2 = res.job_postings[1]
    assert j2.company == "Google"
    assert j2.title == "Staff Engineer"
    assert j2.location is None # missing optional field maps to None

def test_normalization_missing_required_fields():
    adapter = LinkedInJobsAdapter()
    
    # Malformed record missing company
    raw_missing_company = {
        "title": "Engineer",
        "url": "https://linkedin.com/jobs/view/99"
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.extract(raw_missing_company)
    assert "company" in str(exc_info.value)
    assert "https://linkedin.com/jobs/view/99" in str(exc_info.value)
    
    # Malformed record missing title
    raw_missing_title = {
        "company": "Acme",
        "url": "https://linkedin.com/jobs/view/99"
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.extract(raw_missing_title)
    assert "title" in str(exc_info.value)

def test_normalization_batch_skips_and_logs():
    records = [
        {"company": "C1", "title": "T1", "url": "url1"},
        {"company": "", "title": "T2", "url": "url2"}, # missing required company
        {"company": "C3", "title": "T3", "url": "url3"}
    ]
    
    normalizer = Normalizer(source_name="apify_linkedin")
    
    with capture_logs() as captured:
        res = normalizer.normalize(records)
        
    assert len(res.job_postings) == 2
    assert len(res.errors) == 1
    
    # Check warning log was captured
    warnings = [log for log in captured if log.get("log_level") == "warning"]
    assert len(warnings) == 1
    assert "malformed" in warnings[0]["event"].lower()
    assert warnings[0]["record_id"] == "url2"
    
    # Check details of error struct
    assert res.errors[0]["record_id"] == "url2"
    assert "company" in res.errors[0]["error"]
    assert res.errors[0]["raw"]["url"] == "url2"

def test_normalization_unknown_source():
    # Unknown source adapter query must raise ValueError from get_adapter / registry
    with pytest.raises(ValueError) as exc_info:
        get_adapter("apify_unknown_source")
    assert "unknown source adapter name" in str(exc_info.value).lower()
    
    with pytest.raises(ValueError):
        Normalizer(source_name="apify_unknown_source")

def test_normalization_new_scraper_fields():
    # Verify that the new fields format is correctly mapped and falls back correctly
    raw_record = {
        "company_name": "Google",
        "title": "Staff Engineer",
        "platform_url": "https://linkedin.com/jobs/view/2",
        "platform": "LinkedIn",
        "description": "Looking for a Staff Engineer.",
        "location": "München"
    }
    
    normalizer = Normalizer(source_name="apify_linkedin")
    res = normalizer.normalize([raw_record])
    
    assert len(res.job_postings) == 1
    assert len(res.errors) == 0
    
    j = res.job_postings[0]
    assert j.company == "Google"
    assert j.title == "Staff Engineer"
    assert j.url == "https://linkedin.com/jobs/view/2"
    assert j.description == "Looking for a Staff Engineer."
    assert j.source == "LinkedIn"
    assert j.location == "München"

def test_normalization_agentx_all_jobs():
    # Verify that agentx_all_jobs is mapped in the registry
    adapter = get_adapter("agentx_all_jobs")
    assert adapter is not None
    assert adapter.source_name == "apify_linkedin"

    # Verify normalization with agentx_all_jobs source name
    raw_record = {
        "company_name": "AgentX Corp",
        "title": "Agent Developer",
        "platform_url": "https://agentx.com/jobs/view/1",
        "platform": "AgentX",
        "description": "Building next-gen AI agents.",
        "location": "Remote"
    }

    normalizer = Normalizer(source_name="agentx_all_jobs")
    res = normalizer.normalize([raw_record])

    assert len(res.job_postings) == 1
    assert len(res.errors) == 0

    j = res.job_postings[0]
    assert j.company == "AgentX Corp"
    assert j.title == "Agent Developer"
    assert j.url == "https://agentx.com/jobs/view/1"
    assert j.description == "Building next-gen AI agents."
    assert j.source == "AgentX"
    assert j.location == "Remote"

