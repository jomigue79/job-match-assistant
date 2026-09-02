import asyncio
from datetime import datetime, timezone
from typing import Optional
import uuid

from domain import Run, RunStatus, RunCost, JobStatus, JobPosting
from ingestion import IngestionService, Normalizer, DedupService, ScrapeQuery
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
        self.ledger_sync = GoogleSheetLedger(
            service_account_path=settings.google_service_account_path,
            sheet_id=settings.ledger_sheet_id or "",
            worksheet_title="Ledger",
            lock=self.persistence_service._write_lock
        )
        
        self._active_run: Optional[Run] = None
        self._active_task: Optional[asyncio.Task] = None

    def start_run(self, query: Optional[ScrapeQuery] = None) -> Run:
        """
        Starts a run in the background if none is currently running.
        Returns the constructed Run object immediately.
        """
        if query is None:
            settings = get_settings()
            query = ScrapeQuery(
                terms=settings.scraper_query,
                location=settings.scraper_location,
                limit=settings.scraper_limit,
                country=settings.scraper_country or "Portugal",
                posted_since=settings.scraper_posted_since
            )
            
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
        self._active_task = loop.create_task(self._execute_run(run, query))

        return run

    async def _execute_run(self, run: Run, query: ScrapeQuery) -> None:
        """
        Background coroutine executing the fetch-normalize-dedup-persist-score pipeline.
        """
        cost_accum = CostAccumulator()
        with bind_run(run.run_id):
            try:
                # 1. Persist run as running
                await self.persistence_service.save_run(run)
                
                # 2. Fetch raw records (live or replay)
                records = await self.ingestion_service.fetch(query)
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
            match = await self.scorer.score(job, knowledge, cost_accumulator)
            new_status = JobStatus.MATCHED if match.score >= self.score_threshold else JobStatus.NO_MATCH
            await self.persistence_service.save_match_result(match)
            await self.persistence_service.set_status(job.identity_hash, new_status)
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

