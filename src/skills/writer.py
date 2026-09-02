from typing import Optional

from llm import LLMClient, LLMRequest, build_llm_client
from domain import JobPosting, MatchResult
from knowledge import KnowledgeBase
from observability import get_logger, CostAccumulator

logger = get_logger("writer")

class WriterError(Exception):
    """Raised when the LLM returns invalid, empty, or too-short cover letter output."""
    pass

SYSTEM_PROMPT = """1. ROLE:
You are an expert cover letter writer with deep knowledge of the target role.

2. ANTI-FABRICATION RULE (NON-NEGOTIABLE):
The letter may ONLY assert qualifications, skills, experiences, and achievements explicitly present in the CV section below. If the role requires something absent from the CV, address transferable experience honestly — never invent or imply qualifications that are not there.

3. WRITING INSTRUCTIONS:
Follow the persona constraints (tone, voice, length, structure) defined in the Persona section below exactly.

4. OUTPUT FORMAT:
Return ONLY the cover letter text, ready to send. No preamble, no "here is your letter", no subject line, no metadata, no markdown formatting.
"""

USER_PROMPT_TEMPLATE = """PERSONA & WRITING CONSTRAINTS
{persona}

CANDIDATE CV
{cv}

JOB DETAILS
Company: {company}
Title: {title}
Location: {location}
Description:
{description}

WHY THIS JOB MATCHES
{match_reasons}

TASK
Write a cover letter for this role following all constraints above.
"""

class Writer:
    """
    Writer component generating targeted cover letters based on candidate CV, persona, and job details.
    """
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or build_llm_client()

    async def generate(
        self,
        job: JobPosting,
        knowledge: KnowledgeBase,
        match_result: MatchResult,
        cost_accumulator: Optional[CostAccumulator] = None
    ) -> str:
        """
        Generates a cover letter using high-temperature, persona-grounded LLM completions.
        """
        reasons_list = "\n".join(f"- {reason}" for reason in match_result.match_reasons)

        user_prompt = USER_PROMPT_TEMPLATE.format(
            persona=knowledge.persona,
            cv=knowledge.cv,
            company=job.company or "Unknown Company",
            title=job.title or "Unknown Title",
            location=job.location or "Unknown Location",
            description=job.description or "",
            match_reasons=reasons_list
        )

        request = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=1500,
            temperature=0.7,
            json_mode=False
        )

        response = await self.llm_client.complete(request, cost_accumulator=cost_accumulator)
        raw_text = response.text

        if not raw_text or len(raw_text.strip()) < 100:
            raise WriterError("Cover letter generation failed or generated response was too short (under 100 characters).")

        # Log metadata only
        logger.info(
            "Cover letter generation complete",
            identity_hash=job.identity_hash,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model
        )

        return raw_text
