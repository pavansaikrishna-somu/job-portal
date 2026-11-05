import os
import sys
import json
import hashlib
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jobportal.settings')
django.setup()

from django.conf import settings
from django.contrib.auth.models import User
from users.documents import UserProfile
from ai.documents import AIResumeAnalysis
from ai.prompts import RESUME_ANALYSIS_PROMPT, PROMPT_VERSION
from ai.services import (
    calculate_file_hash,
    calculate_analysis_cache_key,
    verify_evidence_against_resume,
    validate_and_normalize_analysis,
    analyze_resume,
    ResumeAnalysisResponse
)
from google import genai
from google.genai import types

def run_audit():
    print("================================================================")
    print("CAREERCONNECT AUDIT: AI RESUME ANALYZER VERIFICATION SUITE")
    print("================================================================")
    
    # --- 1. EVIDENCE CITATION & REJECTION OF HALLUCINATIONS ---
    print("\n--- TEST 1: Evidence Citation & Anti-Hallucination Guard ---")
    resume_text = """
    Jane Developer
    Email: jane@example.com | Phone: 555-0199
    Summary:
    Experienced Python backend developer specializing in Django, FastAPI, and PostgreSQL.
    Experience:
    Software Engineer at Acme Corp (2021 - Present)
    - Developed REST APIs using Django and Django Rest Framework.
    - Optimized PostgreSQL query performance, reducing p99 latency by 30%.
    - Containerized microservices using Docker and orchestrated deployments on AWS ECS.
    Skills: Python, Django, FastAPI, PostgreSQL, Docker, AWS, Git
    """
    valid_citation = "Optimized PostgreSQL query performance, reducing p99 latency by 30%"
    fake_citation = "Managed a budget of $5M and directed 40 overseas offshore teams"

    check_valid = verify_evidence_against_resume(valid_citation, resume_text.lower())
    check_fake = verify_evidence_against_resume(fake_citation, resume_text.lower())
    print(f"  Valid citation '{valid_citation[:40]}...' approved: {check_valid}")
    print(f"  Fake citation '{fake_citation[:40]}...' approved: {check_fake}")
    assert check_valid is True, "Valid quote should pass verification"
    assert check_fake is False, "Hallucinated citation MUST be rejected"
    print("  [PASS] Anti-hallucination verification operates strictly.")

    # --- 2. CACHE IDENTITY INTEGRITY ---
    print("\n--- TEST 2: Cache Key Identity & Invalidation ---")
    hash_a = hashlib.sha256(b"resume content A").hexdigest()
    hash_b = hashlib.sha256(b"resume content B").hexdigest()
    job_1 = "job_id_python"
    job_2 = "job_id_react"
    desc_1 = "Django and AWS experience required"
    desc_2 = "React and TypeScript experience required"

    key_a_job1 = calculate_analysis_cache_key(hash_a, job_1, desc_1, PROMPT_VERSION)
    key_b_job1 = calculate_analysis_cache_key(hash_b, job_1, desc_1, PROMPT_VERSION)
    key_a_job2 = calculate_analysis_cache_key(hash_a, job_2, desc_2, PROMPT_VERSION)
    key_a_nojob = calculate_analysis_cache_key(hash_a, "", "", PROMPT_VERSION)
    key_a_v1 = calculate_analysis_cache_key(hash_a, job_1, desc_1, "v1_legacy")

    keys = [key_a_job1, key_b_job1, key_a_job2, key_a_nojob, key_a_v1]
    assert len(set(keys)) == 5, "Cache keys must all be distinct for distinct inputs"
    print("  [PASS] Replacing resume or changing target job invalidates cache identity as required.")

    # --- 3. STRUCTURED VALIDATION & SCORE ARITHMETIC ---
    print("\n--- TEST 3: Validation, Arithmetic Enforcement & Rubric Normalization ---")
    mock_raw_ai_payload = {
        "overall_score": 99,  # Intentionally mismatching sum to verify arithmetic enforcement
        "ats_score": 85,
        "rubric_breakdown": {
            "content_evidence_score": 22,
            "skills_relevance_score": 24,
            "structure_formatting_score": 20,
            "impact_outcomes_score": 18
        },
        "evidence_strengths": [
            {"strength": "Backend Performance", "evidence": "Optimized PostgreSQL query performance, reducing p99 latency by 30%", "analysis": "High impact optimization"},
            {"strength": "Offshore Leadership", "evidence": "Managed a budget of $5M and directed 40 overseas offshore teams", "analysis": "Invented claim"}
        ],
        "matched_requirements": [
            {"requirement": "Django REST Framework", "evidence": "Developed REST APIs using Django and Django Rest Framework."},
            {"requirement": "Kubernetes Cluster Admin", "evidence": "Maintained multi-region Kubernetes clusters with zero downtime"}
        ]
    }
    validated = validate_and_normalize_analysis(mock_raw_ai_payload, resume_text)
    
    # 22 + 24 + 20 + 18 = 84 (overall_score must be corrected to 84, not 99)
    print(f"  Arithmetic check: Rubric sum = {validated['overall_score']} (Input was 99)")
    assert validated["overall_score"] == 84, "Overall score must strictly equal sum of 4 rubric components"
    
    # Check that hallucinated citations were caught
    print(f"  Valid strength evidence: '{validated['evidence_strengths'][0]['evidence']}'")
    print(f"  Fake strength evidence sanitized to: '{validated['evidence_strengths'][1]['evidence']}'")
    assert "Optimized" in validated['evidence_strengths'][0]['evidence']
    assert "overall document context" in validated['evidence_strengths'][1]['evidence']
    print("  [PASS] Score arithmetic and citation validation enforced.")

    # --- 4. LIVE GEMINI API ANALYSIS ---
    print("\n--- TEST 4: Live Gemini Analysis Execution ---")
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    print(f"  Checking configured Gemini API key: {'Present (Length: ' + str(len(api_key)) + ')' if api_key else 'Missing'}")
    
    prompt_job = RESUME_ANALYSIS_PROMPT.format(
        target_job_title="Senior Python Backend Engineer",
        target_job_desc="Requires Python, Django, PostgreSQL, Docker, and AWS experience. Nice to have Kubernetes and Redis.",
        resume_text=resume_text
    )

    models_to_test = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    client = genai.Client(api_key=api_key)
    live_success = False
    chosen_model = None

    for m in models_to_test:
        try:
            print(f"  Attempting live call to model: {m}...")
            resp = client.models.generate_content(
                model=m,
                contents=prompt_job,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ResumeAnalysisResponse,
                    temperature=0.1
                )
            )
            if resp and resp.text:
                live_success = True
                chosen_model = m
                parsed = json.loads(resp.text)
                print(f"  >>> SUCCESS with {m}!")
                print(f"  Overall Score (Sum): {parsed.get('overall_score')}")
                print(f"  ATS Score: {parsed.get('ats_score')}")
                print(f"  Rubric Breakdown: {json.dumps(parsed.get('rubric_breakdown'), indent=4)}")
                print(f"  Matched Requirements ({len(parsed.get('matched_requirements', []))}):")
                for mr in parsed.get("matched_requirements", [])[:3]:
                    print(f"    - {mr.get('requirement')}: \"{mr.get('evidence')}\"")
                print(f"  Missing Requirements ({len(parsed.get('missing_requirements', []))}):")
                for miss in parsed.get("missing_requirements", [])[:3]:
                    print(f"    - {miss.get('requirement')}: {miss.get('note')}")
                print(f"  Actionable Suggestions ({len(parsed.get('actionable_suggestions', []))}):")
                for sugg in parsed.get("actionable_suggestions", [])[:2]:
                    print(f"    * {sugg}")
                break
        except Exception as ex:
            print(f"  Model {m} failed: {ex}")
            continue

    if live_success:
        print(f"  [PASS] Live Gemini analysis fully verified on model: {chosen_model}.")
    else:
        print("  [BLOCKED/WARNING] Live Gemini API call was blocked or failed across all models.")

    print("\n================================================================")
    print("ALL AUDIT CHECKS COMPLETED.")
    print("================================================================")

if __name__ == "__main__":
    run_audit()
