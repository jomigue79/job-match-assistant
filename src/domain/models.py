import ast
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class JobStatus(str, Enum):
    SCRAPED = "scraped"
    NO_MATCH = "no_match"
    MATCHED = "matched"
    WRITTEN = "written"
    APPLIED = "applied"
    REJECTED = "rejected"

class InvalidStateTransitionError(ValueError):
    """Raised when an invalid JobStatus transition is attempted."""
    pass

# Transition rules encoding exactly the state machine defined in requirements:
# scraped  -> {no_match, matched}
# matched  -> {written, rejected}
# written  -> {applied, rejected}
# no_match -> {} (terminal)
# applied  -> {} (terminal)
# rejected -> {} (terminal)
ALLOWED_TRANSITIONS = {
    JobStatus.SCRAPED: {JobStatus.NO_MATCH, JobStatus.MATCHED},
    JobStatus.MATCHED: {JobStatus.WRITTEN, JobStatus.APPLIED, JobStatus.REJECTED},
    JobStatus.WRITTEN: {JobStatus.APPLIED, JobStatus.REJECTED},
    JobStatus.APPLIED: {JobStatus.REJECTED},
    JobStatus.REJECTED: {JobStatus.MATCHED},
    JobStatus.NO_MATCH: set(),
}

def can_transition(from_status: JobStatus, to_status: JobStatus) -> bool:
    """Returns True if the transition from from_status to to_status is allowed."""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())

def validate_transition(from_status: JobStatus, to_status: JobStatus) -> None:
    """Raises InvalidStateTransitionError if the transition is not allowed."""
    if not can_transition(from_status, to_status):
        raise InvalidStateTransitionError(
            f"Cannot transition job status from '{from_status.value}' to '{to_status.value}'"
        )

def normalize_field(val: str | None) -> str:
    """
    Normalizes a job text field using the following sequential steps:
    a. None -> ""
    b. Unicode NFKD normalize and drop combining marks (accent-fold)
    c. lowercase
    d. replace every non-alphanumeric, non-space character with a space
    e. collapse internal whitespace runs to a single space
    f. strip leading/trailing whitespace
    """
    if val is None:
        return ""
    
    # b. Unicode NFKD normalize and drop combining marks (accent-fold)
    normalized = unicodedata.normalize("NFKD", val)
    accent_folded = "".join(c for c in normalized if not unicodedata.combining(c))
    
    # c. lowercase
    lowered = accent_folded.lower()
    
    # d. replace every non-alphanumeric, non-space character with a space
    spaced = re.sub(r"[^a-zA-Z0-9 ]", " ", lowered)
    
    # e. collapse internal whitespace runs to a single space
    collapsed = re.sub(r"\s+", " ", spaced)
    
    # f. strip leading/trailing whitespace
    return collapsed.strip()

def display_location(val: str | None) -> str | None:
    """
    Presentation-only rendering of a stored location.

    Some sources return location as an object, and the ingestion adapters
    stringify it with str(), yielding a Python dict repr rather than JSON.
    This returns the human-readable 'raw' value when it can, and the input
    unchanged otherwise.

    NOT used by JobIdentity. The stored value and the identity hash are
    unaffected -- see D7.
    """
    if val is None:
        return None

    text = val.strip()
    # Only dict-shaped text is parsed. Without this guard, literal_eval would
    # happily turn "41" into an int and "(1,2)" into a tuple.
    if not (text.startswith("{") and text.endswith("}")):
        return val

    try:
        # literal_eval, not json.loads: the stored text is a Python repr with
        # single quotes, which is not valid JSON. literal_eval evaluates only
        # literals and cannot execute code.
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return val

    if not isinstance(parsed, dict):
        return val

    raw = parsed.get("raw")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    locality = parsed.get("locality")
    if isinstance(locality, str) and locality.strip():
        return locality.strip()

    return val

class JobIdentity(BaseModel):
    """
    Immutable value object uniquely identifying a job posting.
    """
    model_config = ConfigDict(frozen=True)

    company: str
    title: str
    location: str
    hash: str

    @model_validator(mode="before")
    @classmethod
    def normalize_and_hash(cls, data: Any) -> Any:
        if isinstance(data, dict):
            company = normalize_field(data.get("company"))
            title = normalize_field(data.get("title"))
            location = normalize_field(data.get("location"))
            
            # identity string = the three normalized fields joined with "|" as a separator.
            identity_string = f"{company}|{title}|{location}"
            
            # hash = hashlib.sha256(identity_string.encode("utf-8")).hexdigest()
            h = hashlib.sha256(identity_string.encode("utf-8")).hexdigest()
            
            return {
                "company": company,
                "title": title,
                "location": location,
                "hash": h
            }
        return data

class JobPosting(BaseModel):
    """
    Entity representing a scraped job posting. Mutable state allowed for status transitions.
    """
    model_config = ConfigDict(frozen=False)

    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    source: str
    scraped_at: datetime
    status: JobStatus = JobStatus.SCRAPED
    identity_hash: str

    @model_validator(mode="before")
    @classmethod
    def resolve_identity(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "identity_hash" not in data or not data["identity_hash"]:
                comp = data.get("company")
                tit = data.get("title")
                loc = data.get("location")
                identity = JobIdentity(company=comp, title=tit, location=loc)
                data["identity_hash"] = identity.hash
        return data

    @field_validator("scraped_at", mode="after")
    @classmethod
    def validate_scraped_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("scraped_at must be timezone-aware")
        if v.tzinfo != timezone.utc:
            v = v.astimezone(timezone.utc)
        return v

class MatchResult(BaseModel):
    """
    Immutable entity capturing the match score and analysis details for a job.
    """
    model_config = ConfigDict(frozen=True)

    identity_hash: str
    score: int = Field(..., ge=0, le=100)
    dimension_breakdown: Dict[str, float]
    match_reasons: List[str]
    scored_at: datetime

    @field_validator("scored_at", mode="after")
    @classmethod
    def validate_scored_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("scored_at must be timezone-aware")
        if v.tzinfo != timezone.utc:
            v = v.astimezone(timezone.utc)
        return v

class CoverLetter(BaseModel):
    """
    Immutable entity capturing a generated cover letter for a job.
    """
    model_config = ConfigDict(frozen=True)

    identity_hash: str
    text: str
    version: int = Field(..., ge=1)
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def validate_created_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if v.tzinfo != timezone.utc:
            v = v.astimezone(timezone.utc)
        return v

class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

class RunCost(BaseModel):
    """
    Immutable value object tracking token and Apify resource costs for a run.
    """
    model_config = ConfigDict(frozen=True)

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_llm_calls: int = 0
    apify_compute_units: float = 0.0
    apify_results: int = 0
    estimated_cost_usd: float = 0.0

class Run(BaseModel):
    """
    Mutable entity tracking status, execution times, and outcomes of a background scraper/match pipeline run.
    """
    model_config = ConfigDict(frozen=False)

    run_id: str
    source: str
    status: RunStatus = RunStatus.QUEUED
    started_at: datetime
    finished_at: Optional[datetime] = None
    n_scraped: int = 0
    n_new: int = 0
    n_matched: int = 0
    n_no_match: int = 0
    n_errors: int = 0
    cost: RunCost = Field(default_factory=RunCost)

    @field_validator("started_at", "finished_at", mode="after")
    @classmethod
    def validate_run_datetimes(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        if v.tzinfo != timezone.utc:
            v = v.astimezone(timezone.utc)
        return v
