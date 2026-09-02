import json
from pathlib import Path
from typing import List
from ingestion.types import RawRecord

def load_fixture_scrape(name: str) -> List[RawRecord]:
    """
    Loader helper reading mock scraper json records from test fixtures.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "raw_scrapes" / f"{name}.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["records"]
