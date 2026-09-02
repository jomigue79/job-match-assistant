from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from domain import (
    JobIdentity,
    JobStatus,
    InvalidStateTransitionError,
    can_transition,
    validate_transition,
    JobPosting,
    MatchResult,
    CoverLetter,
    RunStatus,
    RunCost,
    Run,
)

def test_job_identity_normalization_equivalence():
    # Accent fold, lowercase, punctuation folding, and collapsing whitespace checks
    ref = JobIdentity(company="Acme Corp", title="Senior Dev", location="Lisboa")
    
    variant_1 = JobIdentity(company="  acme   corp ", title="SENIOR DEV", location="lisboa")
    variant_2 = JobIdentity(company="Acme, Corp.", title="Senior  Dev", location="Lisboa")
    variant_3 = JobIdentity(company="Acme Corp", title="Sénior Dev", location="Lisboa")
    
    assert ref.company == "acme corp"
    assert ref.title == "senior dev"
    assert ref.location == "lisboa"
    
    assert variant_1.hash == ref.hash
    assert variant_2.hash == ref.hash
    assert variant_3.hash == ref.hash

def test_job_identity_determinism_lock():
    # Asserting stable fixed expected SHA-256 hex
    identity = JobIdentity(company="Acme Corp", title="Senior Dev", location="Lisboa")
    expected_hash = "e582fb2d7034f64f30dcf829e6cfcb6b2ffd9b8733104ba5e66c601f2acf7533"
    
    assert identity.hash == expected_hash

def test_job_identity_distinctness():
    # Asserting that different fields produce different hashes and separators prevent collision
    id1 = JobIdentity(company="ab", title="c", location="")
    id2 = JobIdentity(company="a", title="bc", location="")
    
    assert id1.hash != id2.hash
    assert id1.company == "ab"
    assert id2.company == "a"

def test_job_identity_none_empty_handling():
    # None fields normalize to empty strings
    id_nones = JobIdentity(company=None, title=None, location=None)
    assert id_nones.company == ""
    assert id_nones.title == ""
    assert id_nones.location == ""
    assert len(id_nones.hash) == 64

def test_status_transitions():
    # Legal transitions passes
    assert can_transition(JobStatus.SCRAPED, JobStatus.NO_MATCH) is True
    assert can_transition(JobStatus.SCRAPED, JobStatus.MATCHED) is True
    assert can_transition(JobStatus.MATCHED, JobStatus.WRITTEN) is True
    assert can_transition(JobStatus.MATCHED, JobStatus.REJECTED) is True
    assert can_transition(JobStatus.WRITTEN, JobStatus.APPLIED) is True
    assert can_transition(JobStatus.WRITTEN, JobStatus.REJECTED) is True
    
    # Assert validate_transition does not raise on legal transition
    validate_transition(JobStatus.SCRAPED, JobStatus.NO_MATCH)
    validate_transition(JobStatus.WRITTEN, JobStatus.APPLIED)

    # Illegal transitions raise InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(JobStatus.SCRAPED, JobStatus.APPLIED)
        
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(JobStatus.NO_MATCH, JobStatus.MATCHED)
        
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(JobStatus.APPLIED, JobStatus.WRITTEN)

def test_match_result_score_constraints():
    # Out of range scores (score=101, score=-1) must raise ValidationError
    with pytest.raises(ValidationError):
        MatchResult(
            identity_hash="some-hash",
            score=101,
            dimension_breakdown={"hard": 10},
            match_reasons=["reason"],
            scored_at=datetime.now(timezone.utc)
        )
        
    with pytest.raises(ValidationError):
        MatchResult(
            identity_hash="some-hash",
            score=-1,
            dimension_breakdown={"hard": 10},
            match_reasons=["reason"],
            scored_at=datetime.now(timezone.utc)
        )

    # score=0 and score=100 must be accepted
    mr_min = MatchResult(
        identity_hash="some-hash",
        score=0,
        dimension_breakdown={"hard": 0},
        match_reasons=[],
        scored_at=datetime.now(timezone.utc)
    )
    mr_max = MatchResult(
        identity_hash="some-hash",
        score=100,
        dimension_breakdown={"hard": 100},
        match_reasons=["perfect"],
        scored_at=datetime.now(timezone.utc)
    )
    assert mr_min.score == 0
    assert mr_max.score == 100

def test_frozen_models_immutability():
    # Attempting to mutate frozen models raises ValidationError or TypeError
    identity = JobIdentity(company="Acme Corp", title="Senior Dev", location="Lisboa")
    with pytest.raises((ValidationError, TypeError)):
        identity.company = "New Name"

    cost = RunCost(total_input_tokens=100)
    with pytest.raises((ValidationError, TypeError)):
        cost.total_input_tokens = 200

    mr = MatchResult(
        identity_hash="some-hash",
        score=85,
        dimension_breakdown={"hard": 85},
        match_reasons=[],
        scored_at=datetime.now(timezone.utc)
    )
    with pytest.raises((ValidationError, TypeError)):
        mr.score = 90

    cl = CoverLetter(
        identity_hash="some-hash",
        text="Dear Sir...",
        version=1,
        created_at=datetime.now(timezone.utc)
    )
    with pytest.raises((ValidationError, TypeError)):
        cl.text = "New Letter"

def test_job_posting_identity_resolution():
    # JobPosting resolves identity hash if omitted
    jp = JobPosting(
        company="Acme Corp",
        title="Senior Dev",
        location="Lisboa",
        source="apify",
        scraped_at=datetime.now(timezone.utc),
        identity_hash=""
    )
    expected_hash = "e582fb2d7034f64f30dcf829e6cfcb6b2ffd9b8733104ba5e66c601f2acf7533"
    assert jp.identity_hash == expected_hash

def test_datetime_tz_aware_validation():
    # Datetime fields raise ValueError if naive datetimes are provided
    naive_dt = datetime(2026, 6, 10, 12, 0, 0)
    
    with pytest.raises(ValidationError):
        JobPosting(
            company="Acme Corp",
            title="Senior Dev",
            location="Lisboa",
            source="apify",
            scraped_at=naive_dt,
            identity_hash="hash"
        )

    # If non-UTC aware datetime is provided, it must convert to UTC
    # Use timezone offset of +2 hours (e.g. Europe/Paris)
    from datetime import timedelta
    paris_tz = timezone(timedelta(hours=2))
    paris_dt = datetime(2026, 6, 10, 12, 0, 0, tzinfo=paris_tz)
    
    jp = JobPosting(
        company="Acme Corp",
        title="Senior Dev",
        location="Lisboa",
        source="apify",
        scraped_at=paris_dt,
        identity_hash="hash"
    )
    # 12:00 UTC+2 is 10:00 UTC
    assert jp.scraped_at.hour == 10
    assert jp.scraped_at.tzinfo == timezone.utc
