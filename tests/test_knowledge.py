import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs
from knowledge import KnowledgeLoader, KnowledgeBase, KnowledgeLoadError

def test_knowledge_loader_success(tmp_path):
    cv_file = tmp_path / "cv.md"
    persona_file = tmp_path / "persona.md"
    ats_file = tmp_path / "ats_criteria.md"

    cv_file.write_text("Test CV Content", encoding="utf-8")
    persona_file.write_text("Test Persona Content", encoding="utf-8")
    ats_file.write_text("Test ATS Content", encoding="utf-8")

    loader = KnowledgeLoader(str(tmp_path))
    kb = loader.load()

    assert kb.cv == "Test CV Content"
    assert kb.persona == "Test Persona Content"
    assert kb.ats_criteria == "Test ATS Content"

def test_knowledge_loader_reads_fresh(tmp_path):
    cv_file = tmp_path / "cv.md"
    persona_file = tmp_path / "persona.md"
    ats_file = tmp_path / "ats_criteria.md"

    cv_file.write_text("Original CV Content", encoding="utf-8")
    persona_file.write_text("Test Persona Content", encoding="utf-8")
    ats_file.write_text("Test ATS Content", encoding="utf-8")

    loader = KnowledgeLoader(str(tmp_path))
    kb1 = loader.load()
    assert kb1.cv == "Original CV Content"

    # Modify CV file on disk
    cv_file.write_text("Updated CV Content", encoding="utf-8")

    # Load again to check fresh read
    kb2 = loader.load()
    assert kb2.cv == "Updated CV Content"

def test_knowledge_base_immutability(tmp_path):
    cv_file = tmp_path / "cv.md"
    persona_file = tmp_path / "persona.md"
    ats_file = tmp_path / "ats_criteria.md"

    cv_file.write_text("Test CV Content", encoding="utf-8")
    persona_file.write_text("Test Persona Content", encoding="utf-8")
    ats_file.write_text("Test ATS Content", encoding="utf-8")

    loader = KnowledgeLoader(str(tmp_path))
    kb = loader.load()

    # Verify that trying to modify fields raises ValidationError or TypeError (Pydantic V2 ConfigDict(frozen=True))
    with pytest.raises((ValidationError, TypeError)):
        kb.cv = "Mutated CV Content"

def test_knowledge_loader_missing_files(tmp_path):
    # Empty directory
    loader = KnowledgeLoader(str(tmp_path))
    
    with pytest.raises(KnowledgeLoadError) as exc_info:
        loader.load()
    assert "Missing required knowledge file" in str(exc_info.value)

def test_knowledge_loader_empty_file(tmp_path):
    # Verify existing-but-empty file raises KnowledgeLoadError (FIX 4)
    cv_file = tmp_path / "cv.md"
    persona_file = tmp_path / "persona.md"
    ats_file = tmp_path / "ats_criteria.md"

    cv_file.write_text("   \n   ", encoding="utf-8")  # Empty (only whitespace)
    persona_file.write_text("Test Persona Content", encoding="utf-8")
    ats_file.write_text("Test ATS Content", encoding="utf-8")

    loader = KnowledgeLoader(str(tmp_path))
    with pytest.raises(KnowledgeLoadError) as exc_info:
        loader.load()
    assert "cv.md" in str(exc_info.value)
    assert "empty" in str(exc_info.value).lower()

def test_knowledge_loader_logs_metadata(tmp_path):
    cv_file = tmp_path / "cv.md"
    persona_file = tmp_path / "persona.md"
    ats_file = tmp_path / "ats_criteria.md"

    cv_file.write_text("Logged CV Content", encoding="utf-8")
    persona_file.write_text("Logged Persona Content", encoding="utf-8")
    ats_file.write_text("Logged ATS Content", encoding="utf-8")

    loader = KnowledgeLoader(str(tmp_path))

    with capture_logs() as captured:
        loader.load()

    # We expect 3 loader logs (one for each file)
    loader_logs = [log for log in captured if log.get("event") == "Loaded knowledge layer file"]
    assert len(loader_logs) == 3

    # Check CV file load metadata
    cv_log = next(log for log in loader_logs if log.get("file_name") == "cv.md")
    assert cv_log["last_modified"] is not None
    assert cv_log["version_hash"] is not None
    assert len(cv_log["version_hash"]) == 8  # 8 character SHA prefix

def test_knowledge_loader_placeholder_warning(tmp_path):
    # Warning check for placeholder angle brackets (FIX 6)
    cv_file = tmp_path / "cv.md"
    persona_file = tmp_path / "persona.md"
    ats_file = tmp_path / "ats_criteria.md"

    cv_file.write_text("This contains a <placeholder_here> angle-bracket text.", encoding="utf-8")
    persona_file.write_text("Test Persona Content", encoding="utf-8")
    ats_file.write_text("Test ATS Content", encoding="utf-8")

    loader = KnowledgeLoader(str(tmp_path))

    with capture_logs() as captured:
        loader.load()

    warning_logs = [log for log in captured if log.get("log_level") == "warning"]
    assert len(warning_logs) == 1
    assert "unfilled" in warning_logs[0]["event"].lower()
    assert warning_logs[0]["file_name"] == "cv.md"
