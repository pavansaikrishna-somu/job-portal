import json
import logging
import os
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from ai.documents import AIResumeAnalysis
from ai.services import analyze_resume, calculate_file_hash
from core.decorators import role_required
from core.utils import save_uploaded_file
from jobs.documents import Job

logger = logging.getLogger(__name__)


def serialize_analysis(analysis: AIResumeAnalysis) -> dict:
    """Helper to serialize AIResumeAnalysis document into a complete response dictionary."""
    return {
        "id": str(analysis.id),
        "overall_score": analysis.overall_score,
        "ats_score": analysis.ats_score,
        "rubric_breakdown": analysis.rubric_breakdown or {},
        "explicit_skills": analysis.explicit_skills or [],
        "evidence_strengths": analysis.evidence_strengths or [],
        "improvement_areas": analysis.improvement_areas or [],
        "actionable_suggestions": analysis.actionable_suggestions or analysis.suggestions or [],
        "suggested_roles": analysis.suggested_roles or analysis.recommended_roles or [],
        "has_target_job": analysis.has_target_job,
        "target_job_title": analysis.target_job_title or "",
        "matched_requirements": analysis.matched_requirements or [],
        "missing_requirements": analysis.missing_requirements or [],
        "job_fit_summary": analysis.job_fit_summary or "",
        # Legacy fields for backwards compatibility
        "strengths": analysis.strengths or [],
        "weaknesses": analysis.weaknesses or [],
        "missing_skills": analysis.missing_skills or [],
        "suggestions": analysis.suggestions or analysis.actionable_suggestions or [],
        "recommended_roles": analysis.recommended_roles or analysis.suggested_roles or [],
        "created_at": analysis.created_at.isoformat() if analysis.created_at else "",
    }


@role_required("jobseeker")
@require_http_methods(["POST"])
def trigger_analysis_view(request):
    """
    Trigger AI resume analysis for the logged-in jobseeker.
    Optionally accepts target_job_id, custom job_title, and job_description.
    """
    profile = request.user_profile
    if not profile or not profile.resume:
        return JsonResponse(
            {"error": "No resume uploaded. Please upload a resume in your profile first."},
            status=400,
        )

    # Parse optional request parameters
    target_job_id = ""
    target_job_title = ""
    target_job_desc = ""
    force_reanalyze = False

    try:
        if request.content_type == "application/json" and request.body:
            body_data = json.loads(request.body.decode("utf-8"))
            target_job_id = body_data.get("job_id", "").strip()
            target_job_title = body_data.get("job_title", "").strip()
            target_job_desc = body_data.get("job_description", "").strip()
            force_reanalyze = bool(body_data.get("force_reanalyze", False))
        else:
            target_job_id = request.POST.get("job_id", "").strip()
            target_job_title = request.POST.get("job_title", "").strip()
            target_job_desc = request.POST.get("job_description", "").strip()
            force_reanalyze = bool(request.POST.get("force_reanalyze", False))
    except Exception:
        pass

    # If job_id was provided, look up job from database
    if target_job_id:
        job = Job.get_or_none(target_job_id)
        if job:
            target_job_title = job.title
            target_job_desc = f"{job.title} at {job.company}\nLocation: {job.location}\nSkills Required: {', '.join(job.skills_required)}\nDescription:\n{job.description}"

    try:
        analysis = analyze_resume(
            user_id=request.user.id,
            relative_resume_path=profile.resume,
            target_job_id=target_job_id,
            target_job_title=target_job_title,
            target_job_desc=target_job_desc,
            force_reanalyze=force_reanalyze
        )
        return JsonResponse(serialize_analysis(analysis), status=200)
    except FileNotFoundError as fnfe:
        return JsonResponse({"error": str(fnfe)}, status=404)
    except ValueError as ve:
        return JsonResponse({"error": str(ve)}, status=400)
    except Exception as e:
        logger.error("Failed to trigger analysis: %s", str(e))
        return JsonResponse(
            {"error": "Failed to analyze resume. Please try again later."},
            status=500,
        )


@role_required("jobseeker")
@require_http_methods(["POST"])
def replace_resume_view(request):
    """
    Allow candidate to upload/replace their resume directly from the analyzer.
    Validates file format, size, and emptiness, and updates profile.resume.
    """
    if "resume" not in request.FILES:
        return JsonResponse({"error": "No file was uploaded."}, status=400)

    uploaded_file = request.FILES["resume"]
    max_size = getattr(settings, "MAX_RESUME_UPLOAD_SIZE", 5 * 1024 * 1024)

    if uploaded_file.size == 0:
        return JsonResponse({"error": "The uploaded resume file is empty."}, status=400)

    if uploaded_file.size > max_size:
        return JsonResponse({"error": "File size exceeds the 5MB limit."}, status=400)

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in (".pdf", ".docx"):
        return JsonResponse({"error": "Unsupported file format. Only PDF and DOCX resumes are supported."}, status=400)

    try:
        rel_path = save_uploaded_file(uploaded_file, "resumes")
        profile = request.user_profile
        profile.resume = rel_path
        profile.save()
        
        return JsonResponse({
            "success": True,
            "message": "Resume uploaded successfully.",
            "resume_path": rel_path,
            "filename": os.path.basename(uploaded_file.name),
        }, status=200)
    except Exception as e:
        logger.error("Failed to replace resume: %s", str(e))
        return JsonResponse({"error": "Failed to save the uploaded resume. Please try again."}, status=500)


@role_required("jobseeker")
@require_http_methods(["GET"])
def get_analysis_view(request, analysis_id):
    """
    Retrieve details of a specific resume analysis.
    Only the owner of the analysis can access it.
    """
    analysis = AIResumeAnalysis.get_or_none(analysis_id)
    if not analysis:
        return JsonResponse({"error": "Resume analysis not found."}, status=404)

    # Ownership check
    if analysis.user_id != request.user.id:
        return JsonResponse(
            {"error": "Access denied. You do not own this resume analysis."},
            status=403,
        )

    return JsonResponse(serialize_analysis(analysis), status=200)


@role_required("jobseeker")
@require_http_methods(["GET"])
def get_latest_analysis_view(request):
    """
    Retrieve the latest resume analysis for the logged-in jobseeker.
    """
    analysis = AIResumeAnalysis.objects(user_id=request.user.id).order_by("-created_at").first()
    if not analysis:
        return JsonResponse({"analysis": None}, status=200)

    return JsonResponse(serialize_analysis(analysis), status=200)


@role_required("jobseeker")
def analyzer_page_view(request):
    """
    Render the main AI Resume Analyzer interface page with target job options.
    """
    profile = request.user_profile
    analysis = AIResumeAnalysis.objects(user_id=request.user.id).order_by("-created_at").first()

    resume_changed = False
    if analysis and profile and profile.resume:
        full_path = os.path.join(settings.MEDIA_ROOT, profile.resume)
        if os.path.exists(full_path):
            current_hash = calculate_file_hash(full_path)
            if analysis.resume_hash != current_hash:
                resume_changed = True

    # Retrieve active jobs for target job matching dropdown
    available_jobs = Job.objects.order_by("-created_at")[:25]

    return render(
        request,
        "ai/analyzer.html",
        {
            "profile": profile,
            "analysis": analysis,
            "resume_changed": resume_changed,
            "available_jobs": available_jobs,
        },
    )
