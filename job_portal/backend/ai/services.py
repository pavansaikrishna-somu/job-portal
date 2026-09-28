import hashlib
import json
import logging
import os
from typing import List

from django.conf import settings
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from pypdf import PdfReader

from ai.documents import AIResumeAnalysis
from ai.prompts import RESUME_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


class ResumeAnalysisResponse(BaseModel):
    overall_score: int = Field(description="An overall score for the resume from 0 to 100")
    ats_score: int = Field(description="ATS compatibility score from 0 to 100")
    strengths: List[str] = Field(description="List of key strengths identified in the resume")
    weaknesses: List[str] = Field(description="List of weaknesses or areas of concern in the resume")
    missing_skills: List[str] = Field(description="Skills that are missing or recommended based on the candidate's profile and experience")
    suggestions: List[str] = Field(description="Specific suggestions for improving the resume")
    recommended_roles: List[str] = Field(description="List of job roles suitable for the candidate based on the resume")


def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA-256 hash of a file for caching purposes."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
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
            raise ValueError("The PDF file contains no readable text.")
        return stripped_text
    except Exception as e:
        logger.error("Failed to parse PDF file at %s: %s", file_path, str(e))
        raise ValueError(f"Could not parse the PDF file: {str(e)}")


def analyze_resume(user_id: int, relative_resume_path: str) -> AIResumeAnalysis:
    """
    Extract text, hash, call Gemini API for structured analysis, and cache/store results.
    """
    if not relative_resume_path:
        raise ValueError("No resume file path was provided.")

    # Determine full path
    full_path = os.path.join(settings.MEDIA_ROOT, relative_resume_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError("The uploaded resume file could not be found on the server.")

    # Check file format
    ext = os.path.splitext(full_path)[1].lower()
    if ext != ".pdf":
        raise ValueError("Unsupported file format. Only PDF resumes are supported.")

    # Compute file hash
    resume_hash = calculate_file_hash(full_path)

    # Check caching
    existing_analysis = AIResumeAnalysis.objects(user_id=user_id, resume_hash=resume_hash).first()
    if existing_analysis:
        logger.info("Found cached resume analysis for user %s with hash %s", user_id, resume_hash)
        return existing_analysis

    # Parse PDF text
    resume_text = extract_text_from_pdf(full_path)

    # Fetch Gemini API Key securely
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        logger.error("Gemini API key is not configured in settings.")
        raise ValueError("Gemini API key is not configured. Please check environment configuration.")

    # Call Gemini API
    try:
        client = genai.Client(api_key=api_key)
        prompt_text = RESUME_ANALYSIS_PROMPT.format(resume_text=resume_text)

        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt_text,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ResumeAnalysisResponse,
                temperature=0.1,
            ),
        )

        if not response or not response.text:
            raise ValueError("Empty response received from the Gemini API.")

        raw_text = response.text
        # Parse JSON results
        analysis_data = json.loads(raw_text)
        
    except json.JSONDecodeError as je:
        logger.error("Failed to parse Gemini response as JSON: %s", str(je))
        raise RuntimeError("Failed to parse the analysis results from Gemini API.")
    except Exception as e:
        logger.error("Gemini service call failed for user %s: %s", user_id, str(e))
        # Ensure we do not leak the api_key in the exception message
        raise RuntimeError("An error occurred while communicating with the AI resume analysis service.")

    # Save to MongoDB
    analysis = AIResumeAnalysis(
        user_id=user_id,
        resume_path=relative_resume_path,
        resume_hash=resume_hash,
        overall_score=analysis_data.get("overall_score", 0),
        ats_score=analysis_data.get("ats_score", 0),
        strengths=analysis_data.get("strengths", []),
        weaknesses=analysis_data.get("weaknesses", []),
        missing_skills=analysis_data.get("missing_skills", []),
        suggestions=analysis_data.get("suggestions", []),
        recommended_roles=analysis_data.get("recommended_roles", []),
        raw_response=raw_text,
    )
    analysis.save()
    return analysis
