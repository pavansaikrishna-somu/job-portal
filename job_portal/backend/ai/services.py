import hashlib
import json
import logging
import os
import re
from typing import List, Optional, Dict, Any

from django.conf import settings
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from pypdf import PdfReader

from ai.documents import AIResumeAnalysis
from ai.prompts import RESUME_ANALYSIS_PROMPT, PROMPT_VERSION

logger = logging.getLogger(__name__)


# --- Pydantic Schemas for Structured Output & Validation ---

class FactItem(BaseModel):
    item: str = Field(description="Name of skill, role, or qualification explicitly found in the resume")
    evidence: str = Field(default="", description="Exact verbatim quote or snippet from resume supporting this fact")
    section: str = Field(default="", description="Section of resume where found, e.g., Skills, Experience, Education")


class StrengthEvidence(BaseModel):
    strength: str = Field(description="Identified candidate strength")
    evidence: str = Field(default="", description="Verbatim excerpt from resume directly supporting this strength")
    analysis: str = Field(default="", description="Objective reasoning for why this is an asset")


class ImprovementItem(BaseModel):
    issue: str = Field(description="Identified weakness, gap, or area for enhancement")
    evidence_or_gap: str = Field(default="", description="Section or bullet point where the gap or missing detail occurs")
    actionable_fix: str = Field(description="Specific actionable fix. Never fabricate metrics; instruct user to supply real numbers.")


class MatchedRequirement(BaseModel):
    requirement: str = Field(description="Target job requirement candidate meets")
    evidence: str = Field(default="", description="Verbatim quote from resume demonstrating this qualification")


class MissingRequirement(BaseModel):
    requirement: str = Field(description="Target job requirement with no verified evidence in the resume")
    note: str = Field(default="", description="Explanation or recommendation on how the candidate can bridge this gap")


class RubricBreakdown(BaseModel):
    content_evidence_score: int = Field(default=20, ge=0, le=25, description="Score (0-25) for depth of responsibilities and evidence")
    content_evidence_rationale: str = Field(default="Clear responsibility descriptions.", description="Rationale based on resume text")
    skills_relevance_score: int = Field(default=20, ge=0, le=25, description="Score (0-25) for clarity and relevance of skills")
    skills_relevance_rationale: str = Field(default="Skills clearly listed and relevant.", description="Rationale based on resume skills")
    structure_formatting_score: int = Field(default=20, ge=0, le=25, description="Score (0-25) for ATS parseability, headings, and layout")
    structure_formatting_rationale: str = Field(default="Standard section headings detected.", description="Rationale based on layout")
    impact_outcomes_score: int = Field(default=20, ge=0, le=25, description="Score (0-25) for demonstrated accomplishments and impact")
    impact_outcomes_rationale: str = Field(default="Demonstrates work deliverables.", description="Rationale based on impact")


class ResumeAnalysisResponse(BaseModel):
    overall_score: int = Field(default=80, ge=0, le=100, description="Overall score based on the rubric sum (0-100)")
    ats_score: int = Field(default=80, ge=0, le=100, description="Estimated ATS Parseability Score (0-100)")
    rubric_breakdown: Optional[RubricBreakdown] = Field(default_factory=RubricBreakdown)
    explicit_skills: List[FactItem] = Field(default_factory=list, description="Skills explicitly evidenced in the resume")
    evidence_strengths: List[StrengthEvidence] = Field(default_factory=list, description="Strengths backed by verbatim quotes")
    improvement_areas: List[ImprovementItem] = Field(default_factory=list, description="Weaknesses and specific areas to improve")
    actionable_suggestions: List[str] = Field(default_factory=list, description="Concrete suggestions specific to the resume")
    suggested_roles: List[str] = Field(default_factory=list, description="Suggested potential roles (general analysis only)")
    matched_requirements: List[MatchedRequirement] = Field(default_factory=list, description="Matched job requirements with quotes")
    missing_requirements: List[MissingRequirement] = Field(default_factory=list, description="Missing job requirements")
    job_fit_summary: str = Field(default="", description="Summary of evaluation against target job or general profile")
    # Legacy compatibility fields
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    recommended_roles: List[str] = Field(default_factory=list)


# --- Hashing & Document Extraction Helpers ---

def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA-256 hash of a file for caching purposes."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def calculate_analysis_cache_key(
    resume_hash: str,
    target_job_id: str = "",
    target_job_desc: str = "",
    prompt_version: str = PROMPT_VERSION
) -> str:
    """
    Ensure cache identity includes resume content AND relevant analysis inputs:
    target job identity/description hash and prompt version.
    """
    hasher = hashlib.sha256()
    hasher.update(resume_hash.encode("utf-8"))
    hasher.update((target_job_id or "").strip().encode("utf-8"))
    hasher.update((target_job_desc or "").strip()[:500].encode("utf-8"))
    hasher.update(prompt_version.encode("utf-8"))
    return hasher.hexdigest()


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pypdf."""
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        
        stripped_text = text.strip()
        if not stripped_text:
            raise ValueError("The PDF file contains no readable text. Scanned resumes without selectable text cannot be processed by the ATS parser.")
        return stripped_text
    except ValueError:
        raise
    except Exception as e:
        logger.error("Failed to parse PDF file at %s: %s", file_path, str(e))
        raise ValueError(f"Could not parse the PDF file: {str(e)}")


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from a DOCX file using python standard library."""
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(file_path) as z:
            xml_content = z.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        paragraphs = []
        for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = [node.text for node in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if node.text]
            if texts:
                paragraphs.append("".join(texts))
        stripped_text = "\n".join(paragraphs).strip()
        if not stripped_text:
            raise ValueError("The DOCX file contains no readable text.")
        return stripped_text
    except ValueError:
        raise
    except Exception as e:
        logger.error("Failed to parse DOCX file at %s: %s", file_path, str(e))
        raise ValueError(f"Could not parse the DOCX file: {str(e)}")


def verify_evidence_against_resume(evidence_str: str, resume_text_normalized: str) -> bool:
    """
    Validate that an evidence quote actually appears or has significant token overlap
    with the extracted resume text to guard against hallucinated citations.
    """
    if not evidence_str:
        return False
    norm_evidence = re.sub(r"\s+", " ", evidence_str.lower().strip())
    if norm_evidence in resume_text_normalized:
        return True
    
    # Check token overlap for slight whitespace/punctuation differences
    evidence_tokens = set(re.findall(r"\b\w{3,}\b", norm_evidence))
    if not evidence_tokens:
        return True
    resume_tokens = set(re.findall(r"\b\w{3,}\b", resume_text_normalized))
    matched_ratio = len(evidence_tokens.intersection(resume_tokens)) / max(1, len(evidence_tokens))
    return matched_ratio >= 0.7


def validate_and_normalize_analysis(data: dict, resume_text: str) -> dict:
    """
    Validate structured AI response before saving or displaying:
    1. Enforces score bounds and verifies overall_score = sum(components).
    2. Verifies evidence quotes against actual resume text.
    3. Normalizes backward-compatible fields.
    """
    norm_resume = re.sub(r"\s+", " ", resume_text.lower())
    
    # 1. Rubric normalization
    rb_data = data.get("rubric_breakdown") or {}
    if not isinstance(rb_data, dict):
        rb_data = {}
        
    c_score = max(0, min(25, int(rb_data.get("content_evidence_score", 20))))
    s_score = max(0, min(25, int(rb_data.get("skills_relevance_score", 20))))
    f_score = max(0, min(25, int(rb_data.get("structure_formatting_score", 20))))
    i_score = max(0, min(25, int(rb_data.get("impact_outcomes_score", 20))))
    
    # Calculate arithmetic sum
    calculated_overall = c_score + s_score + f_score + i_score
    
    if data.get("rubric_breakdown"):
        # When rubric breakdown is provided, overall score MUST strictly equal the sum of components
        overall_score = calculated_overall
    else:
        overall_score = data.get("overall_score")
        if overall_score is not None:
            try:
                overall_score = max(0, min(100, int(overall_score)))
            except (ValueError, TypeError):
                overall_score = calculated_overall
        else:
            overall_score = calculated_overall

        # Rebalance component scores for legacy payloads without explicit rubric
        quarter = overall_score // 4
        rem = overall_score % 4
        c_score = min(25, quarter + (1 if rem > 0 else 0))
        s_score = min(25, quarter + (1 if rem > 1 else 0))
        f_score = min(25, quarter + (1 if rem > 2 else 0))
        i_score = min(25, quarter)

    ats_score = data.get("ats_score", overall_score)
    try:
        ats_score = max(0, min(100, int(ats_score)))
    except (ValueError, TypeError):
        ats_score = 80

    rubric_breakdown = {
        "content_evidence_score": c_score,
        "content_evidence_rationale": rb_data.get("content_evidence_rationale", "Evaluated based on verifiable responsibility details."),
        "skills_relevance_score": s_score,
        "skills_relevance_rationale": rb_data.get("skills_relevance_rationale", "Evaluated based on clear technical/domain skill coverage."),
        "structure_formatting_score": f_score,
        "structure_formatting_rationale": rb_data.get("structure_formatting_rationale", "Evaluated based on ATS readability and standard headings."),
        "impact_outcomes_score": i_score,
        "impact_outcomes_rationale": rb_data.get("impact_outcomes_rationale", "Evaluated based on tangible project and business outcomes.")
    }

    # 2. Evidence validation
    valid_strengths = []
    raw_strengths = data.get("evidence_strengths") or []
    if isinstance(raw_strengths, list):
        for item in raw_strengths:
            if isinstance(item, dict):
                ev = item.get("evidence", "")
                if ev and not verify_evidence_against_resume(ev, norm_resume):
                    # Flag unverified quotes rather than displaying hallucinated citations
                    item["evidence"] = "Derived from overall document context"
                valid_strengths.append(item)
    
    # Backwards compatibility flat strengths
    flat_strengths = data.get("strengths") or [item.get("strength", "") for item in valid_strengths if isinstance(item, dict)]
    if not valid_strengths and flat_strengths:
        valid_strengths = [{"strength": s, "evidence": "Referenced in resume", "analysis": "Positive professional indicator"} for s in flat_strengths if s]

    # Improvements
    valid_improvements = []
    raw_improvements = data.get("improvement_areas") or []
    if isinstance(raw_improvements, list):
        for item in raw_improvements:
            if isinstance(item, dict):
                valid_improvements.append(item)
    
    flat_weaknesses = data.get("weaknesses") or [item.get("issue", "") for item in valid_improvements if isinstance(item, dict)]
    if not valid_improvements and flat_weaknesses:
        valid_improvements = [{"issue": w, "evidence_or_gap": "Identified in resume structure", "actionable_fix": f"Review and refine: {w}"} for w in flat_weaknesses if w]

    # Actionable suggestions
    suggestions = data.get("actionable_suggestions") or data.get("suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = []

    # Roles
    suggested_roles = data.get("suggested_roles") or data.get("recommended_roles") or []
    if not isinstance(suggested_roles, list):
        suggested_roles = []

    # Matched / Missing requirements
    matched_reqs = []
    for item in data.get("matched_requirements") or []:
        if isinstance(item, dict):
            ev = item.get("evidence", "")
            if ev and not verify_evidence_against_resume(ev, norm_resume):
                item["evidence"] = "Referenced in candidate background"
            matched_reqs.append(item)

    missing_reqs = [item for item in (data.get("missing_requirements") or []) if isinstance(item, dict)]

    return {
        "overall_score": overall_score,
        "ats_score": ats_score,
        "rubric_breakdown": rubric_breakdown,
        "explicit_skills": [item for item in (data.get("explicit_skills") or []) if isinstance(item, dict)],
        "evidence_strengths": valid_strengths,
        "improvement_areas": valid_improvements,
        "actionable_suggestions": suggestions,
        "suggested_roles": suggested_roles,
        "matched_requirements": matched_reqs,
        "missing_requirements": missing_reqs,
        "job_fit_summary": str(data.get("job_fit_summary", "")),
        # Legacy fields
        "strengths": [s for s in flat_strengths if isinstance(s, str)],
        "weaknesses": [w for w in flat_weaknesses if isinstance(w, str)],
        "missing_skills": [m.get("requirement", "") if isinstance(m, dict) else str(m) for m in missing_reqs] or data.get("missing_skills", []),
        "suggestions": [s for s in suggestions if isinstance(s, str)],
        "recommended_roles": [r for r in suggested_roles if isinstance(r, str)],
    }


def analyze_resume(
    user_id: int,
    relative_resume_path: str,
    target_job_id: str = "",
    target_job_title: str = "",
    target_job_desc: str = "",
    force_reanalyze: bool = False
) -> AIResumeAnalysis:
    """
    Extract text, hash, call Gemini API for structured analysis, validate, and cache/store results.
    Guarantees cache identity includes resume content + target job + prompt version.
    """
    if not relative_resume_path:
        raise ValueError("No resume file path was provided.")

    # Determine full path
    full_path = os.path.join(settings.MEDIA_ROOT, relative_resume_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError("The uploaded resume file could not be found on the server.")

    # Check file emptiness
    if os.path.getsize(full_path) == 0:
        raise ValueError("The uploaded resume file is empty.")

    # Check file format
    ext = os.path.splitext(full_path)[1].lower()
    if ext not in (".pdf", ".docx"):
        raise ValueError("Unsupported file format. Only PDF and DOCX resumes are supported.")

    # Compute file hash and composite cache key
    resume_hash = calculate_file_hash(full_path)
    cache_key = calculate_analysis_cache_key(
        resume_hash=resume_hash,
        target_job_id=target_job_id,
        target_job_desc=target_job_desc,
        prompt_version=PROMPT_VERSION
    )

    # Check caching
    if not force_reanalyze:
        existing_analysis = AIResumeAnalysis.objects(user_id=user_id, cache_key=cache_key).first()
        if existing_analysis:
            logger.info("Found cached resume analysis for user %s with cache_key %s", user_id, cache_key)
            return existing_analysis

    # Parse document text
    if ext == ".pdf":
        resume_text = extract_text_from_pdf(full_path)
    elif ext == ".docx":
        resume_text = extract_text_from_docx(full_path)
    else:
        raise ValueError("Unsupported file format. Only PDF and DOCX resumes are supported.")

    # Fetch Gemini API Key securely
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        logger.error("Gemini API key is not configured in settings.")
        raise ValueError("Gemini API key is not configured. Please check environment configuration.")

    # Format prompt with target job inputs
    prompt_text = RESUME_ANALYSIS_PROMPT.format(
        target_job_title=target_job_title.strip() or "General Resume Review (No target job specified)",
        target_job_desc=target_job_desc.strip() or "NONE. Perform general evidence-based review.",
        resume_text=resume_text
    )

    # Call Gemini API with resilient fallback model chain
    candidate_models = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    response = None
    last_error = None

    try:
        client = genai.Client(api_key=api_key)

        for model_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ResumeAnalysisResponse,
                        temperature=0.1,
                    ),
                )
                if response and response.text:
                    break
            except Exception as me:
                last_error = me
                logger.warning("Gemini model %s failed: %s", model_name, str(me))
                continue

        if not response or not response.text:
            if last_error:
                err_str = str(last_error)
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    raise RuntimeError("The AI resume analysis service is currently experiencing high demand. Please try again in a few moments.")
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    raise RuntimeError("AI rate limit reached. Please wait a moment before trying again.")
            raise RuntimeError("An error occurred while communicating with the AI resume analysis service.")

        raw_text = response.text
        # Parse JSON results
        analysis_data = json.loads(raw_text)
        
    except (ValueError, RuntimeError):
        raise
    except json.JSONDecodeError as je:
        logger.error("Failed to parse Gemini response as JSON: %s", str(je))
        raise RuntimeError("Failed to parse the analysis results from Gemini API.")
    except Exception as e:
        logger.error("Gemini service call failed for user %s: %s", user_id, str(e))
        raise RuntimeError("An error occurred while communicating with the AI resume analysis service.")

    # Validate and normalize AI response
    validated = validate_and_normalize_analysis(analysis_data, resume_text)

    # Save to MongoDB
    analysis = AIResumeAnalysis(
        user_id=user_id,
        resume_path=relative_resume_path,
        resume_hash=resume_hash,
        target_job_id=target_job_id,
        target_job_title=target_job_title,
        target_job_desc_hash=hashlib.sha256(target_job_desc.encode("utf-8")).hexdigest() if target_job_desc else "",
        has_target_job=bool(target_job_title or target_job_desc),
        cache_key=cache_key,
        prompt_version=PROMPT_VERSION,
        overall_score=validated["overall_score"],
        ats_score=validated["ats_score"],
        rubric_breakdown=validated["rubric_breakdown"],
        explicit_skills=validated["explicit_skills"],
        evidence_strengths=validated["evidence_strengths"],
        improvement_areas=validated["improvement_areas"],
        actionable_suggestions=validated["actionable_suggestions"],
        suggested_roles=validated["suggested_roles"],
        matched_requirements=validated["matched_requirements"],
        missing_requirements=validated["missing_requirements"],
        job_fit_summary=validated["job_fit_summary"],
        # Legacy backwards compatibility
        strengths=validated["strengths"],
        weaknesses=validated["weaknesses"],
        missing_skills=validated["missing_skills"],
        suggestions=validated["suggestions"],
        recommended_roles=validated["recommended_roles"],
        raw_response=raw_text,
    )
    analysis.save()
    return analysis
