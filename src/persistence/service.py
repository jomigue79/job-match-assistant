import asyncio
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Set
from pydantic import BaseModel, ConfigDict
from config import get_settings
from domain import (
    JobPosting,
    JobStatus,
    MatchResult,
    CoverLetter,
    Run,
    RunStatus,
    RunCost,
    validate_transition,
)
from .database import connect
from .ledger import LedgerRow

class Counters(BaseModel):
    total: int
    rejected: int
    written: int
    applied: int

class JobWithMatch(BaseModel):
    """
    Representational struct grouping a JobPosting and its optional scored MatchResult.
    """
    model_config = ConfigDict(frozen=True)
    job: JobPosting
    match: Optional[MatchResult] = None
    letter_text: Optional[str] = None
    letter_version: Optional[int] = None
    letter_created_at: Optional[datetime] = None

def parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo != timezone.utc:
        dt = dt.astimezone(timezone.utc)
    return dt

class PersistenceService:
    """
    Async persistence service mediating reads/writes to SQLite database.
    Serializes writes using a single asyncio.Lock.
    All operations execute blockingly inside worker threads via asyncio.to_thread.
    """
    def __init__(self, db_path: Optional[str] = None):
        settings = get_settings()
        self.db_path = db_path if db_path is not None else settings.db_path
        self._write_lock = asyncio.Lock()

    # --- Jobs Operations ---

    async def upsert_job(self, job: JobPosting) -> None:
        """
        Upserts job posting. On conflict, refreshes description metadata but preserves existing status.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO jobs (identity_hash, company, title, location, url, description, source, scraped_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(identity_hash) DO UPDATE SET
                      url = excluded.url,
                      description = excluded.description,
                      source = excluded.source,
                      scraped_at = excluded.scraped_at;
                    """,
                    (
                        job.identity_hash,
                        job.company,
                        job.title,
                        job.location,
                        job.url,
                        job.description,
                        job.source,
                        job.scraped_at.isoformat(),
                        job.status.value,
                    )
                )
                conn.commit()
            finally:
                conn.close()

        async with self._write_lock:
            await asyncio.to_thread(_execute)

    async def get_job(self, identity_hash: str) -> Optional[JobPosting]:
        """
        Fetches job by identity hash.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT company, title, location, url, description, source, scraped_at, status, identity_hash
                    FROM jobs WHERE identity_hash = ?;
                    """,
                    (identity_hash,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return JobPosting(
                    company=row[0],
                    title=row[1],
                    location=row[2],
                    url=row[3],
                    description=row[4],
                    source=row[5],
                    scraped_at=parse_dt(row[6]),
                    status=JobStatus(row[7]),
                    identity_hash=row[8]
                )
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def list_jobs(self, status: Optional[JobStatus] = None) -> List[JobPosting]:
        """
        Lists all jobs, optionally filtering by status.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                if status is not None:
                    cursor.execute(
                        """
                        SELECT company, title, location, url, description, source, scraped_at, status, identity_hash
                        FROM jobs WHERE status = ?;
                        """,
                        (status.value,)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT company, title, location, url, description, source, scraped_at, status, identity_hash
                        FROM jobs;
                        """
                    )
                
                jobs = []
                for row in cursor.fetchall():
                    jobs.append(
                        JobPosting(
                            company=row[0],
                            title=row[1],
                            location=row[2],
                            url=row[3],
                            description=row[4],
                            source=row[5],
                            scraped_at=parse_dt(row[6]),
                            status=JobStatus(row[7]),
                            identity_hash=row[8]
                        )
                    )
                return jobs
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def list_jobs_with_match(self, status: Optional[JobStatus] = None) -> List[JobWithMatch]:
        """
        Lists all jobs joined with their match results (LEFT JOIN).
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                query = """
                    SELECT 
                        j.company, j.title, j.location, j.url, j.description, j.source, j.scraped_at, j.status, j.identity_hash,
                        m.score, m.dimensions, m.reasons, m.scored_at,
                        cl.text, cl.version, cl.created_at
                    FROM jobs j
                    LEFT JOIN match_results m ON j.identity_hash = m.identity_hash
                    LEFT JOIN (
                        SELECT identity_hash, text, version, created_at
                        FROM cover_letters c1
                        WHERE version = (SELECT MAX(version) FROM cover_letters c2 WHERE c2.identity_hash = c1.identity_hash)
                    ) cl ON j.identity_hash = cl.identity_hash
                """
                params = ()
                if status is not None:
                    query += " WHERE j.status = ?;"
                    params = (status.value,)
                else:
                    query += ";"
                    
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    job = JobPosting(
                        company=row[0],
                        title=row[1],
                        location=row[2],
                        url=row[3],
                        description=row[4],
                        source=row[5],
                        scraped_at=parse_dt(row[6]),
                        status=JobStatus(row[7]),
                        identity_hash=row[8]
                    )
                    match = None
                    if row[9] is not None:
                        match = MatchResult(
                            identity_hash=row[8],
                            score=row[9],
                            dimension_breakdown=json.loads(row[10]),
                            match_reasons=json.loads(row[11]),
                            scored_at=parse_dt(row[12])
                        )
                    
                    letter_text = row[13]
                    letter_version = row[14]
                    letter_created_at = parse_dt(row[15]) if row[15] is not None else None
                    
                    results.append(JobWithMatch(
                        job=job,
                        match=match,
                        letter_text=letter_text,
                        letter_version=letter_version,
                        letter_created_at=letter_created_at
                    ))
                return results
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def existing_hashes(self, hashes: Iterable[str]) -> Set[str]:
        """
        Returns a set of identity hashes that exist in the database from the provided list.
        """
        def _execute():
            hash_list = list(hashes)
            if not hash_list:
                return set()
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in hash_list)
                query = f"SELECT identity_hash FROM jobs WHERE identity_hash IN ({placeholders});"
                cursor.execute(query, hash_list)
                return {row[0] for row in cursor.fetchall()}
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def set_status(self, identity_hash: str, new_status: JobStatus) -> None:
        """
        Validates transition via domain rules and updates status.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM jobs WHERE identity_hash = ?;", (identity_hash,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Job with hash {identity_hash} does not exist.")
                
                current_status = JobStatus(row[0])
                validate_transition(current_status, new_status)
                
                conn.execute(
                    "UPDATE jobs SET status = ? WHERE identity_hash = ?;",
                    (new_status.value, identity_hash)
                )
                conn.commit()
            finally:
                conn.close()

        async with self._write_lock:
            await asyncio.to_thread(_execute)

    # --- Match Results ---

    async def save_match_result(self, match: MatchResult) -> None:
        """
        Saves match result. Re-scoring overwrites existing match results.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO match_results (identity_hash, score, dimensions, reasons, scored_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(identity_hash) DO UPDATE SET
                      score = excluded.score,
                      dimensions = excluded.dimensions,
                      reasons = excluded.reasons,
                      scored_at = excluded.scored_at;
                    """,
                    (
                        match.identity_hash,
                        match.score,
                        json.dumps(match.dimension_breakdown),
                        json.dumps(match.match_reasons),
                        match.scored_at.isoformat(),
                    )
                )
                conn.commit()
            finally:
                conn.close()

        async with self._write_lock:
            await asyncio.to_thread(_execute)

    async def get_match_result(self, identity_hash: str) -> Optional[MatchResult]:
        """
        Fetches match result.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT score, dimensions, reasons, scored_at FROM match_results WHERE identity_hash = ?;",
                    (identity_hash,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return MatchResult(
                    identity_hash=identity_hash,
                    score=row[0],
                    dimension_breakdown=json.loads(row[1]),
                    match_reasons=json.loads(row[2]),
                    scored_at=parse_dt(row[3])
                )
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    # --- Cover Letters ---

    async def save_cover_letter(self, identity_hash: str, text: str) -> CoverLetter:
        """
        Saves cover letter. Computes incremented version inside write lock and inserts it.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                # Max version lookup
                cursor.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM cover_letters WHERE identity_hash = ?;",
                    (identity_hash,)
                )
                max_version = cursor.fetchone()[0]
                next_version = max_version + 1
                
                created_at_str = datetime.now(timezone.utc).isoformat()
                
                conn.execute(
                    "INSERT INTO cover_letters (identity_hash, text, version, created_at) VALUES (?, ?, ?, ?);",
                    (identity_hash, text, next_version, created_at_str)
                )
                conn.commit()
                
                return CoverLetter(
                    identity_hash=identity_hash,
                    text=text,
                    version=next_version,
                    created_at=parse_dt(created_at_str)
                )
            finally:
                conn.close()

        async with self._write_lock:
            return await asyncio.to_thread(_execute)

    async def get_latest_cover_letter(self, identity_hash: str) -> Optional[CoverLetter]:
        """
        Gets cover letter with highest version.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT text, version, created_at FROM cover_letters WHERE identity_hash = ? ORDER BY version DESC LIMIT 1;",
                    (identity_hash,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return CoverLetter(
                    identity_hash=identity_hash,
                    text=row[0],
                    version=row[1],
                    created_at=parse_dt(row[2])
                )
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def update_cover_letter(
        self,
        identity_hash: str,
        text: str,
        expected_version: Optional[int] = None
    ) -> CoverLetter:
        """
        Overwrites the text of the job's current (highest) letter version in place.

        An edit, not a regeneration: version and created_at are unchanged, so v2 still
        means the LLM wrote the letter again, and the Applied tab's ordering holds.
        Destructive: the previous text is not kept.

        Raises ValueError if the job has no letter, if expected_version is given and is
        no longer the current version (the letter was regenerated after the edit began),
        or if the row disappeared before the UPDATE landed.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT version, created_at FROM cover_letters WHERE identity_hash = ? ORDER BY version DESC LIMIT 1;",
                    (identity_hash,)
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"No cover letter exists for job {identity_hash}.")
                version, created_at_str = row

                if expected_version is not None and version != expected_version:
                    raise ValueError(
                        f"The letter was regenerated (now v{version}) after this edit began "
                        f"on v{expected_version}; nothing was updated."
                    )

                cursor.execute(
                    "UPDATE cover_letters SET text = ? WHERE identity_hash = ? AND version = ?;",
                    (text, identity_hash, version)
                )
                # _write_lock serializes this app's writes, not other connections. A row
                # deleted outside the app between the SELECT and the UPDATE matches nothing.
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise ValueError(
                        f"Cover letter v{version} for job {identity_hash} no longer exists; nothing was updated."
                    )
                conn.commit()

                return CoverLetter(
                    identity_hash=identity_hash,
                    text=text,
                    version=version,
                    created_at=parse_dt(created_at_str)
                )
            finally:
                conn.close()

        async with self._write_lock:
            return await asyncio.to_thread(_execute)

    # --- Counters & Breakdown ---

    async def status_breakdown(self) -> Dict[JobStatus, int]:
        """
        Returns group count of statuses.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status;")
                breakdown = {}
                for row in cursor.fetchall():
                    breakdown[JobStatus(row[0])] = row[1]
                return breakdown
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def counters(self) -> Counters:
        """
        Computes derived counters dynamically.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM jobs;")
                total = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'rejected';")
                rejected = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(DISTINCT identity_hash) FROM cover_letters;")
                written = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied';")
                applied = cursor.fetchone()[0]
                
                return Counters(total=total, rejected=rejected, written=written, applied=applied)
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    # --- Runs ---

    async def save_run(self, run: Run) -> None:
        """
        Saves run information, flattening RunCost metrics.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, source, status, started_at, finished_at,
                        n_scraped, n_new, n_matched, n_no_match, n_errors,
                        cost_total_input_tokens, cost_total_output_tokens, cost_total_llm_calls,
                        cost_apify_compute_units, cost_apify_results, cost_estimated_usd
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                      source = excluded.source,
                      status = excluded.status,
                      started_at = excluded.started_at,
                      finished_at = excluded.finished_at,
                      n_scraped = excluded.n_scraped,
                      n_new = excluded.n_new,
                      n_matched = excluded.n_matched,
                      n_no_match = excluded.n_no_match,
                      n_errors = excluded.n_errors,
                      cost_total_input_tokens = excluded.cost_total_input_tokens,
                      cost_total_output_tokens = excluded.cost_total_output_tokens,
                      cost_total_llm_calls = excluded.cost_total_llm_calls,
                      cost_apify_compute_units = excluded.cost_apify_compute_units,
                      cost_apify_results = excluded.cost_apify_results,
                      cost_estimated_usd = excluded.cost_estimated_usd;
                    """,
                    (
                        run.run_id,
                        run.source,
                        run.status.value,
                        run.started_at.isoformat(),
                        run.finished_at.isoformat() if run.finished_at else None,
                        run.n_scraped,
                        run.n_new,
                        run.n_matched,
                        run.n_no_match,
                        run.n_errors,
                        run.cost.total_input_tokens,
                        run.cost.total_output_tokens,
                        run.cost.total_llm_calls,
                        run.cost.apify_compute_units,
                        run.cost.apify_results,
                        run.cost.estimated_cost_usd,
                    )
                )
                conn.commit()
            finally:
                conn.close()

        async with self._write_lock:
            await asyncio.to_thread(_execute)

    async def get_run(self, run_id: str) -> Optional[Run]:
        """
        Fetches and reconstructs Run and nested RunCost attributes.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT run_id, source, status, started_at, finished_at,
                           n_scraped, n_new, n_matched, n_no_match, n_errors,
                           cost_total_input_tokens, cost_total_output_tokens, cost_total_llm_calls,
                           cost_apify_compute_units, cost_apify_results, cost_estimated_usd
                    FROM runs WHERE run_id = ?;
                    """,
                    (run_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                
                cost = RunCost(
                    total_input_tokens=row[10],
                    total_output_tokens=row[11],
                    total_llm_calls=row[12],
                    apify_compute_units=row[13],
                    apify_results=row[14],
                    estimated_cost_usd=row[15]
                )
                
                return Run(
                    run_id=row[0],
                    source=row[1],
                    status=RunStatus(row[2]),
                    started_at=parse_dt(row[3]),
                    finished_at=parse_dt(row[4]),
                    n_scraped=row[5],
                    n_new=row[6],
                    n_matched=row[7],
                    n_no_match=row[8],
                    n_errors=row[9],
                    cost=cost
                )
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def list_recent_runs(self, limit: int = 20) -> List[Run]:
        """
        Lists runs ordered by start time descending.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT run_id, source, status, started_at, finished_at,
                           n_scraped, n_new, n_matched, n_no_match, n_errors,
                           cost_total_input_tokens, cost_total_output_tokens, cost_total_llm_calls,
                           cost_apify_compute_units, cost_apify_results, cost_estimated_usd
                    FROM runs ORDER BY started_at DESC LIMIT ?;
                    """,
                    (limit,)
                )
                
                runs = []
                for row in cursor.fetchall():
                    cost = RunCost(
                        total_input_tokens=row[10],
                        total_output_tokens=row[11],
                        total_llm_calls=row[12],
                        apify_compute_units=row[13],
                        apify_results=row[14],
                        estimated_cost_usd=row[15]
                    )
                    runs.append(
                        Run(
                            run_id=row[0],
                            source=row[1],
                            status=RunStatus(row[2]),
                            started_at=parse_dt(row[3]),
                            finished_at=parse_dt(row[4]),
                            n_scraped=row[5],
                            n_new=row[6],
                            n_matched=row[7],
                            n_no_match=row[8],
                            n_errors=row[9],
                            cost=cost
                        )
                    )
                return runs
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)

    async def list_ledger_rows(self) -> List[LedgerRow]:
        """
        Fetches all jobs from the database for ledger synchronization.
        """
        def _execute():
            conn = connect(self.db_path)
            try:
                cursor = conn.cursor()
                query = """
                    SELECT 
                        j.identity_hash,
                        j.company,
                        j.title,
                        j.location,
                        j.url,
                        j.source,
                        j.status,
                        m.score,
                        m.scored_at,
                        cl.version,
                        cl.created_at,
                        j.scraped_at
                    FROM jobs j
                    LEFT JOIN match_results m ON j.identity_hash = m.identity_hash
                    LEFT JOIN (
                        SELECT identity_hash, version, created_at
                        FROM cover_letters c1
                        WHERE version = (SELECT MAX(version) FROM cover_letters c2 WHERE c2.identity_hash = c1.identity_hash)
                    ) cl ON j.identity_hash = cl.identity_hash;
                """
                cursor.execute(query)
                
                rows = []
                for row in cursor.fetchall():
                    identity_hash = row[0]
                    company = row[1]
                    title = row[2]
                    location = row[3]
                    url = row[4]
                    source = row[5]
                    status = row[6]
                    score = row[7]
                    scored_at = parse_dt(row[8]) if row[8] is not None else None
                    letter_version = row[9]
                    letter_updated_at = parse_dt(row[10]) if row[10] is not None else None
                    first_seen_at = parse_dt(row[11])
                    
                    has_letter = "yes" if letter_version is not None else "no"
                    
                    rows.append(LedgerRow(
                        identity_hash=identity_hash,
                        company=company,
                        title=title,
                        location=location,
                        url=url,
                        source=source,
                        status=status,
                        score=score,
                        scored_at=scored_at,
                        has_letter=has_letter,
                        letter_version=letter_version,
                        letter_updated_at=letter_updated_at,
                        first_seen_at=first_seen_at
                    ))
                return rows
            finally:
                conn.close()

        return await asyncio.to_thread(_execute)
