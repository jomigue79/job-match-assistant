from typing import Any, Iterable, List, Set
from pydantic import BaseModel, ConfigDict
from domain import JobPosting
from observability import get_logger

logger = get_logger("dedup_service")

class DedupResult(BaseModel):
    """
    Metadata representation of deduplication metrics.
    """
    model_config = ConfigDict(frozen=True)

    new_jobs: List[JobPosting]
    n_total_in: int
    n_intra_batch_duplicates: int
    n_already_seen: int
    n_new: int

class DedupService:
    """
    Service filtering scraped job postings down to genuinely-new postings
    by checking intra-batch duplicates and database-level ledger entries.
    """
    def __init__(self, hashes_provider: Any):
        """
        Decoupled constructor accepting any object that provides:
        async def existing_hashes(hashes: Iterable[str]) -> Set[str]
        """
        self.hashes_provider = hashes_provider

    async def filter_new(self, postings: List[JobPosting]) -> DedupResult:
        """
        Filters out duplicate job postings.
        """
        n_total_in = len(postings)
        if n_total_in == 0:
            return DedupResult(
                new_jobs=[],
                n_total_in=0,
                n_intra_batch_duplicates=0,
                n_already_seen=0,
                n_new=0
            )

        # 1. Intra-batch deduplication (keep the FIRST occurrence per identity.hash)
        unique_batch_postings = []
        seen_batch_hashes = set()
        n_intra_batch_duplicates = 0

        for p in postings:
            h = p.identity_hash
            if h in seen_batch_hashes:
                n_intra_batch_duplicates += 1
            else:
                seen_batch_hashes.add(h)
                unique_batch_postings.append(p)

        # 2. Ledger check: single batch query against the hashes provider
        existing_hashes = await self.hashes_provider.existing_hashes(seen_batch_hashes)

        # 3. Filter down to genuinely new jobs (not in ledger)
        new_jobs = []
        n_already_seen = 0

        for p in unique_batch_postings:
            if p.identity_hash in existing_hashes:
                n_already_seen += 1
            else:
                new_jobs.append(p)

        n_new = len(new_jobs)

        # Invariant check
        invariant_ok = (n_total_in == n_new + n_intra_batch_duplicates + n_already_seen)
        if not invariant_ok:
            logger.error(
                "Deduplication metrics invariant violated",
                total_in=n_total_in,
                new=n_new,
                intra_batch_dupes=n_intra_batch_duplicates,
                already_seen=n_already_seen
            )
            # We raise ValueError to prevent corrupt stats from writing to DB
            raise ValueError(
                f"DedupService invariant check failed: {n_total_in} != {n_new} + {n_intra_batch_duplicates} + {n_already_seen}"
            )

        logger.info(
            "Completed scraped jobs deduplication",
            total_in=n_total_in,
            intra_batch_dupes=n_intra_batch_duplicates,
            already_seen=n_already_seen,
            new_jobs=n_new
        )

        return DedupResult(
            new_jobs=new_jobs,
            n_total_in=n_total_in,
            n_intra_batch_duplicates=n_intra_batch_duplicates,
            n_already_seen=n_already_seen,
            n_new=n_new
        )
