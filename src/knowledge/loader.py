import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from observability import get_logger

logger = get_logger("knowledge_loader")

class KnowledgeLoadError(ValueError):
    """Raised when knowledge layer files are missing or unreadable."""
    pass

class KnowledgeBase(BaseModel):
    """
    Immutable representation of the local knowledge parameters.
    """
    model_config = ConfigDict(frozen=True)

    cv: str
    persona: str
    ats_criteria: str

class KnowledgeLoader:
    """
    Reader component loading cv, persona, and ats rubrics fresh from file paths.
    """
    def __init__(self, knowledge_dir: str):
        self.knowledge_dir = Path(knowledge_dir)

    def load(self) -> KnowledgeBase:
        """
        Reads knowledge markdown files. Logs last modified times and SHA256 content hashes.
        Fails fast if files are missing, empty, or corrupt.
        Logs warning on unfilled placeholders.
        """
        files = {
            "cv": self.knowledge_dir / "cv.md",
            "persona": self.knowledge_dir / "persona.md",
            "ats_criteria": self.knowledge_dir / "ats_criteria.md",
        }

        contents = {}
        for name, path in files.items():
            if not path.exists():
                raise KnowledgeLoadError(
                    f"Missing required knowledge file: {path.name} at {path.absolute()}"
                )

            try:
                content = path.read_text(encoding="utf-8")
                
                # Raise error if file stripped content is empty (FIX 4)
                if not content.strip():
                    raise KnowledgeLoadError(
                        f"Knowledge file '{path.name}' is empty or contains only whitespace"
                    )

                contents[name] = content

                # Extract modified time and compute content hash
                stat_result = path.stat()
                mtime_dt = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
                mtime_iso = mtime_dt.isoformat()
                version_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]

                logger.info(
                    "Loaded knowledge layer file",
                    file_name=path.name,
                    last_modified=mtime_iso,
                    version_hash=version_hash
                )

                # Log warning on placeholders (FIX 6)
                if re.search(r"<[^>\n]+>", content):
                    logger.warning(
                        "Knowledge file appears unfilled (contains placeholders)",
                        file_name=path.name
                    )

            except KnowledgeLoadError:
                # Re-raise explicit validation errors
                raise
            except Exception as e:
                raise KnowledgeLoadError(
                    f"Failed to read knowledge file {path.name}: {e}"
                ) from e

        return KnowledgeBase(
            cv=contents["cv"],
            persona=contents["persona"],
            ats_criteria=contents["ats_criteria"]
        )
