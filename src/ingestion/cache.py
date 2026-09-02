from datetime import datetime, timezone
import json
from pathlib import Path
from typing import List, Optional, Union
from .types import CacheEntry, CacheError, RawRecord, ScrapeQuery

def parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo != timezone.utc:
        dt = dt.astimezone(timezone.utc)
    return dt

class RawScrapeCache:
    """
    Cache reader/writer persisting raw record payloads inside a JSON envelope on disk.
    """
    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)

    def write(self, records: List[RawRecord], source: str, query: ScrapeQuery) -> Path:
        """
        Dumps the list of raw record dictionaries inside a JSON envelope.
        Returns the Path of the saved file.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Format filename using source and utc timestamp to guarantee sorting ordering
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        file_path = self.cache_dir / f"scrape_{source}_{timestamp_str}.json"
        
        envelope = {
            "source": source,
            "query": {
                "terms": query.terms,
                "location": query.location,
                "limit": query.limit
            },
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "count": len(records),
            "records": records
        }
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2)
            
        return file_path

    def list_cached(self, source: Optional[str] = None) -> List[CacheEntry]:
        """
        Scans raw scrapes directory, parsing valid envelopes.
        Returns entries ordered by fetched_at descending (newest first).
        """
        if not self.cache_dir.exists():
            return []
            
        entries = []
        # Search all scrape_*.json files
        for path in self.cache_dir.glob("scrape_*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    envelope = json.load(f)
                
                # Validate envelope signature fields
                if (
                    not isinstance(envelope, dict)
                    or "records" not in envelope
                    or "source" not in envelope
                    or "fetched_at" not in envelope
                ):
                    continue
                
                fetched_at = parse_dt(envelope["fetched_at"])
                if not fetched_at:
                    continue
                    
                entry_source = envelope["source"]
                if source is not None and entry_source != source:
                    continue
                    
                entries.append(
                    CacheEntry(
                        path=path,
                        source=entry_source,
                        fetched_at=fetched_at,
                        count=envelope.get("count", len(envelope["records"]))
                    )
                )
            except Exception:
                # Ignore unreadable/corrupt files during listing
                continue
                
        # Sort newest first
        entries.sort(key=lambda x: x.fetched_at, reverse=True)
        return entries

    def read(self, path: Union[str, Path]) -> List[RawRecord]:
        """
        Reads and returns records list from a specific cache file path.
        Raises CacheError on missing file, invalid JSON, or invalid envelope.
        """
        p = Path(path)
        if not p.exists():
            raise CacheError(f"Cache file {path} does not exist.")
            
        try:
            with open(p, "r", encoding="utf-8") as f:
                envelope = json.load(f)
        except Exception as e:
            raise CacheError(f"Failed to parse cache file JSON: {e}") from e
            
        if (
            not isinstance(envelope, dict)
            or "records" not in envelope
            or "source" not in envelope
            or "fetched_at" not in envelope
        ):
            raise CacheError(f"Invalid cache envelope structure in {path}.")
            
        return envelope["records"]

    def read_latest(self, source: Optional[str] = None) -> Optional[List[RawRecord]]:
        """
        Retrieves the newest cached scrape list for the given source.
        Returns None if no files are found.
        """
        entries = self.list_cached(source=source)
        if not entries:
            return None
            
        return self.read(entries[0].path)
