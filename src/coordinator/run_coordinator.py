import asyncio
import contextlib
from datetime import datetime, timezone
from typing import List, Optional, Tuple
import uuid

from domain import Run, RunStatus, RunCost, JobStatus, JobPosting, MatchResult
from ingestion import IngestionService, Normalizer, DedupService, ScrapeQuery, PartialFetchError
from persistence import PersistenceService, GoogleSheetLedger
from observability import get_logger, bind_run, CostAccumulator
from config import get_settings
from skills import Scorer, ScorerError
from llm import LLMError
from knowledge import KnowledgeLoader

logger = get_logger("run_coordinator")

class RunAlreadyActiveError(Exception):
    """Raised when trying to start a scraper run while one is already running."""
    pass

def project_apify_cost(queries, actor_start_usd, result_usd, results_per_limit) -> float:
    """
    Projects a run's Apify spend before any Actor Start: one start per query plus
    limit * results_per_limit records each, since the actor applies the limit per
    platform. LLM cost is not included; it depends on how many postings are new.
    """
    return sum(actor_start_usd + q.limit * results_per_limit * result_usd for q in queries)

class RunCoordinator:
    """
    Coordinates job ingestion, normalization, deduplication, and persistence
    as a background asyncio task.
    """
    def __init__(
        self,
        ingestion_service: IngestionService,
        normalizer: Normalizer,
        dedup_service: DedupService,
        persistence_service: PersistenceService,
        scorer: Optional[Scorer] = None,
        knowledge_loader: Optional[KnowledgeLoader] = None,
        score_threshold: Optional[int] = None,
        source_name: Optional[str] = None
    ):
        settings = get_settings()
        self.ingestion_service = ingestion_service
        self.normalizer = normalizer
        self.dedup_service = dedup_service
        self.persistence_service = persistence_service
        self.scorer = scorer
        self.knowledge_loader = knowledge_loader
        self.score_threshold = score_threshold if score_threshold is not None else settings.score_threshold
        self.source_name = source_name if source_name is not None else settings.scraper_source
        self.run_budget_cap_usd = settings.run_budget_cap_usd
        self.apify_actor_start_usd = settings.apify_actor_start_usd
        self.apify_result_usd = settings.apify_result_usd
        self.scraper_results_per_limit = settings.scraper_results_per_limit
        self.replay_from_cache = settings.replay_from_cache
        self.ledger_sync = GoogleSheetLedger(
            service_account_path=settings.google_service_account_path,
            sheet_id=settings.ledger_sheet_id or "",
            worksheet_title="Ledger",
            lock=self.persistence_service._write_lock
        )
        
        self._active_run: Optional[Run] = None
        self._active_task: Optional[asyncio.Task] = None

    def start_run(self, queries: Optional[List[ScrapeQuery]] = None) -> Run:
        """
        Starts a run in the background if none is currently running.
        Without explicit queries, builds one per SCRAPER_QUERY title.
        Returns the constructed Run object immediately.
        """
        if queries is None:
            settings = get_settings()
            queries = [
                ScrapeQuery(
                    terms=title,
                    location=settings.scraper_location,
                    limit=settings.scraper_limit,
                    country=settings.scraper_country or "Portugal",
                    posted_since=settings.scraper_posted_since
                )
                for title in settings.scraper_queries
            ]

        # Reject before the active-run guard and before any Run exists, so a bad
        # argument cannot leave a phantom RUNNING run behind.
        if not queries:
            raise ValueError("start_run() requires at least one ScrapeQuery.")

        # Synchronous check-and-set lock BEFORE any await
        if self._active_run is not None and self._active_run.status == RunStatus.RUNNING:
            raise RunAlreadyActiveError("A run is already active.")

        # A run executes as a task on the caller's event loop. Acquire the loop
        # before constructing the Run so a failure leaves no phantom RUNNING run.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            raise RuntimeError(
                "start_run() requires a running asyncio event loop; "
                "call it from async context (the NiceGUI server loop)."
            ) from e

        run = Run(
            run_id=str(uuid.uuid4()),
            source=self.source_name,
            status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc)
        )
        self._active_run = run
        self._active_task = loop.create_task(self._execute_run(run, queries))

        return run

    async def _execute_run(self, run: Run, queries: List[ScrapeQuery]) -> None:
        """
        Background coroutine executing the fetch-normalize-dedup-persist-score pipeline.
        """
        cost_accum = CostAccumulator()
        with bind_run(run.run_id):
            try:
                # 1. Persist run as running
                await self.persistence_service.save_run(run)

                # Budget check before any Actor Start. Replay makes none, so it projects zero.
                # A refusal returns through the finally below, which records the run as failed.
                if self.replay_from_cache:
                    projected_usd = 0.0
                else:
                    projected_usd = project_apify_cost(
                        queries,
                        self.apify_actor_start_usd,
                        self.apify_result_usd,
                        self.scraper_results_per_limit
                    )
                logger.info(
                    "Projected Apify cost for run",
                    run_id=run.run_id,
                    n_queries=len(queries),
                    terms=[q.terms for q in queries],
                    projected_usd=round(projected_usd, 4),
                    cap_usd=self.run_budget_cap_usd,
                    replay=self.replay_from_cache,
                    note="Apify only; LLM cost depends on how many postings are new after dedup"
                )
                if projected_usd > self.run_budget_cap_usd:
                    logger.error(
                        "Run refused: projected Apify cost exceeds RUN_BUDGET_CAP_USD",
                        run_id=run.run_id,
                        projected_usd=round(projected_usd, 4),
                        cap_usd=self.run_budget_cap_usd
                    )
                    run.n_errors += 1
                    run.status = RunStatus.FAILED
                    return

                # 2. Fetch raw records for every query, pooled (live or replay)
                try:
                    records = await self.ingestion_service.fetch(queries)
                except PartialFetchError as e:
                    # Some queries failed; continue with what the others returned
                    run.n_errors += len(e.failures)
                    records = e.records
                run.n_scraped = len(records)
                cost_accum.add_apify_usage(compute_units=0.0, results=len(records))
                
                # 3. Normalize records
                norm = self.normalizer.normalize(records)
                run.n_errors += len(norm.errors)
                
                # 4. Deduplicate postings
                dedup = await self.dedup_service.filter_new(norm.job_postings)
                run.n_new = dedup.n_new
                
                # 5. Persist unique postings checkpoint
                for job in dedup.new_jobs:
                    await self.persistence_service.upsert_job(job)
                    
                # 6. Scoring Phase
                if self.knowledge_loader is not None and self.scorer is not None:
                    try:
                        knowledge = self.knowledge_loader.load()
                    except Exception as e:
                        logger.error("Failed to load knowledge base files", run_id=run.run_id, error=str(e))
                        raise RuntimeError(f"Knowledge load failed: {e}") from e

                    to_score = await self.persistence_service.list_jobs(status=JobStatus.SCRAPED)
                    if to_score:
                        tasks = [
                            self._score_and_persist(job, knowledge, cost_accum, run)
                            for job in to_score
                        ]
                        await asyncio.gather(*tasks)

                run.status = RunStatus.DONE
                try:
                    await self.sync_ledger()
                except Exception as sync_err:
                    logger.warning("Google Sheet sync failed during coordinator run, continuing...", error=str(sync_err))

            except asyncio.CancelledError:
                run.status = RunStatus.FAILED
                logger.warning("Run cancelled during shutdown", run_id=run.run_id)
                raise

            except Exception as e:
                run.status = RunStatus.FAILED
                run.n_errors += 1
                logger.exception("Background scraper run failed", run_id=run.run_id, error=str(e))
                
            finally:
                if run.status == RunStatus.RUNNING:
                    run.status = RunStatus.DONE if run.n_errors == 0 else RunStatus.FAILED
                run.finished_at = datetime.now(timezone.utc)
                summary = cost_accum.summary()
                run.cost = RunCost(
                    total_input_tokens=summary.total_input_tokens,
                    total_output_tokens=summary.total_output_tokens,
                    total_llm_calls=summary.total_llm_calls,
                    apify_compute_units=summary.apify_compute_units,
                    apify_results=summary.apify_results,
                    estimated_cost_usd=summary.estimated_cost_usd
                )
                await self.persistence_service.save_run(run)

    async def _score_and_transition(
        self,
        job: JobPosting,
        knowledge,
        cost_accumulator: Optional[CostAccumulator],
        respect_threshold: bool = True
    ) -> Tuple[MatchResult, JobStatus]:
        """
        Scores a job, saves its MatchResult, and transitions its status.
        Shared by run scoring and score_one; raises on any failure.
        Below the threshold a job goes to no_match only when respect_threshold is True.
        """
        match = await self.scorer.score(job, knowledge, cost_accumulator)
        if respect_threshold and match.score < self.score_threshold:
            new_status = JobStatus.NO_MATCH
        else:
            new_status = JobStatus.MATCHED
        await self.persistence_service.save_match_result(match)
        await self.persistence_service.set_status(job.identity_hash, new_status)
        return match, new_status

    async def _score_and_persist(
        self,
        job: JobPosting,
        knowledge,
        cost_accumulator: CostAccumulator,
        run: Run
    ) -> None:
        """Helper to evaluate, save, and transition a single job posting."""
        if job.status in (JobStatus.APPLIED, JobStatus.REJECTED, JobStatus.WRITTEN):
            logger.info("Skipping scoring for already actioned job", identity_hash=job.identity_hash, status=job.status)
            return
        try:
            # The threshold exemption follows the job, not the code path: a manual job
            # whose scoring failed on submit is still 'scraped' here, and no_match is terminal.
            match, new_status = await self._score_and_transition(
                job, knowledge, cost_accumulator, respect_threshold=(job.source != "manual")
            )
            if new_status == JobStatus.MATCHED:
                run.n_matched += 1
            else:
                run.n_no_match += 1
            await self.persistence_service.save_run(run)
        except (ScorerError, LLMError) as e:
            logger.error(
                "Scoring failure on job",
                run_id=run.run_id,
                identity_hash=job.identity_hash,
                company=job.company,
                title=job.title,
                error=str(e)
            )
            run.n_errors += 1
            await self.persistence_service.save_run(run)

    async def score_one(self, identity_hash: str, respect_threshold: bool = True) -> MatchResult:
        """
        Scores a single job outside a run: no Run row, no run counters. Raises on failure.

        A job not in 'scraped' returns its stored MatchResult with no LLM call and no
        transition. Every other status rejects a transition to matched, and re-scoring
        would overwrite the score an existing letter or application was based on.

        Refused while a run is active: the run's scoring phase could score the same job,
        and the second scraped -> matched transition would raise and fail the run.
        """
        if self.scorer is None or self.knowledge_loader is None:
            raise RuntimeError("score_one() requires a scorer and a knowledge loader.")

        active = self.get_active_run()
        if active is not None and active.status == RunStatus.RUNNING:
            raise RunAlreadyActiveError("Cannot score a job while a pipeline run is active.")

        job = await self.persistence_service.get_job(identity_hash)
        if job is None:
            raise ValueError(f"Job with hash {identity_hash} does not exist.")

        if job.status != JobStatus.SCRAPED:
            stored = await self.persistence_service.get_match_result(identity_hash)
            if stored is None:
                raise ValueError(
                    f"Job is already '{job.status.value}' and has no stored score to return."
                )
            logger.info(
                "Job already past scraped; returning stored score without re-scoring",
                identity_hash=identity_hash,
                status=job.status.value,
                score=stored.score
            )
            return stored

        knowledge = self.knowledge_loader.load()
        # No run to attribute the cost to; this accumulator exists only for the log line.
        cost_accum = CostAccumulator()
        match, new_status = await self._score_and_transition(
            job, knowledge, cost_accum, respect_threshold=respect_threshold
        )
        summary = cost_accum.summary()
        logger.info(
            "Scored single job outside a run",
            identity_hash=identity_hash,
            source=job.source,
            score=match.score,
            status=new_status.value,
            respect_threshold=respect_threshold,
            input_tokens=summary.total_input_tokens,
            output_tokens=summary.total_output_tokens,
            estimated_cost_usd=summary.estimated_cost_usd,
            note="Not attributed to any run; this log line is the only record of the cost"
        )
        return match

    def get_active_run(self) -> Optional[Run]:
        """
        Returns the current active run (if any) to observe status and counts.
        Guarantees status is synchronized if background execution has completed.
        """
        if self._active_run is not None and self._active_run.status == RunStatus.RUNNING:
            if self._active_task is not None and self._active_task.done():
                self._active_run.status = RunStatus.DONE if self._active_run.n_errors == 0 else RunStatus.FAILED
        return self._active_run

    async def wait(self) -> None:
        """
        Awaits the current active background run task if it is running.
        """
        if self._active_task is not None:
            await self._active_task

    async def shutdown(self) -> None:
        """
        Cancels any in-flight run and records it as failed before the process exits.
        Idempotent and non-raising: a failure to persist is logged, never propagated.
        """
        task = self._active_task
        if task is None or task.done():
            return

        # Awaiting the cancelled task and recording its outcome are independent
        # operations with independent failure paths. _execute_run's own finally
        # writes the run too, and if that write raises, the exception surfaces
        # here out of `await task`. Sharing one try block would let that failure
        # skip our write entirely, leaving no failed row at all.
        try:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except Exception as e:
            logger.warning("Cancelled run raised while unwinding", error=str(e))

        try:
            run = self._active_run
            if run is not None:
                run.n_errors += 1
                run.status = RunStatus.FAILED
                run.finished_at = datetime.now(timezone.utc)
                await self.persistence_service.save_run(run)
                logger.info("In-flight run cancelled and recorded as failed", run_id=run.run_id)
        except Exception as e:
            logger.exception("Shutdown failed to record cancelled run", error=str(e))

    async def sync_ledger(self) -> None:
        """
        Fetches all ledger rows from persistence and updates the Google Sheet mirror.
        """
        try:
            logger.info("Starting ledger synchronization")
            rows = await self.persistence_service.list_ledger_rows()
            summary = await self.ledger_sync.sync(rows)
            logger.info(
                "Ledger synchronization complete",
                rows_added=summary.rows_added,
                rows_updated=summary.rows_updated,
                user_columns=summary.user_columns_preserved
            )
            try:
                from nicegui import ui
                ui.notify("Google Sheet sync completed successfully.", type="positive", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show background sync success notification", error=str(re))
        except Exception as e:
            logger.exception("Ledger synchronization failed", error=str(e))
            try:
                from nicegui import ui
                ui.notify(f"Ledger sync failed: {e}", type="negative", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show background sync failure notification", error=str(re))
            raise


def build_run_coordinator(
    persistence_service: Optional[PersistenceService] = None,
    source: Optional[str] = None,
    scorer: Optional[Scorer] = None,
    knowledge_loader: Optional[KnowledgeLoader] = None
) -> RunCoordinator:
    """
    Factory creating a RunCoordinator instance.
    Wires the real stack using settings, build_ingestion_service, Normalizer,
    DedupService, Scorer, KnowledgeLoader, and PersistenceService.
    """
    from config import get_settings
    from ingestion import build_ingestion_service, Normalizer, DedupService
    from persistence import PersistenceService
    from skills import Scorer
    from llm import build_llm_client
    from knowledge import KnowledgeLoader

    settings = get_settings()
    
    if persistence_service is None:
        persistence_service = PersistenceService()
        
    ingestion_service = build_ingestion_service()
    source_name = source if source is not None else settings.scraper_source
    
    normalizer = Normalizer(source_name=source_name)
    dedup_service = DedupService(persistence_service)
    
    if scorer is None:
        scorer = Scorer(build_llm_client())
        
    if knowledge_loader is None:
        knowledge_loader = KnowledgeLoader(settings.knowledge_dir)
        
    return RunCoordinator(
        ingestion_service=ingestion_service,
        normalizer=normalizer,
        dedup_service=dedup_service,
        persistence_service=persistence_service,
        scorer=scorer,
        knowledge_loader=knowledge_loader,
        score_threshold=settings.score_threshold,
        source_name=source_name
    )

