from django.shortcuts import render
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from ai.documents import AIResumeAnalysis
from ai.services import analyze_resume
from core.decorators import role_required

logger = logging.getLogger(__name__)


@role_required("jobseeker")
@require_http_methods(["POST"])
def trigger_analysis_view(request):
    """
    Trigger AI resume analysis for the logged-in jobseeker.
    """
    profile = request.user_profile
    if not profile.resume:
        return JsonResponse(
            {"error": "No resume uploaded. Please upload a resume in your profile first."},
            status=400,
        )

    try:
        analysis = analyze_resume(request.user.id, profile.resume)
        return JsonResponse(
            {
                "id": str(analysis.id),
                "overall_score": analysis.overall_score,
                "ats_score": analysis.ats_score,
                "strengths": analysis.strengths,
                "weaknesses": analysis.weaknesses,
                "missing_skills": analysis.missing_skills,
                "suggestions": analysis.suggestions,
                "recommended_roles": analysis.recommended_roles,
                "created_at": analysis.created_at.isoformat(),
            },
            status=200,
        )
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

    return JsonResponse(
        {
            "id": str(analysis.id),
            "overall_score": analysis.overall_score,
            "ats_score": analysis.ats_score,
            "strengths": analysis.strengths,
            "weaknesses": analysis.weaknesses,
            "missing_skills": analysis.missing_skills,
            "suggestions": analysis.suggestions,
            "recommended_roles": analysis.recommended_roles,
            "created_at": analysis.created_at.isoformat(),
        },
        status=200,
    )


@role_required("jobseeker")
@require_http_methods(["GET"])
def get_latest_analysis_view(request):
    """
    Retrieve the latest resume analysis for the logged-in jobseeker.
    """
    analysis = AIResumeAnalysis.objects(user_id=request.user.id).order_by("-created_at").first()
    if not analysis:
        return JsonResponse({"analysis": None}, status=200)

    return JsonResponse(
        {
            "id": str(analysis.id),
            "overall_score": analysis.overall_score,
            "ats_score": analysis.ats_score,
            "strengths": analysis.strengths,
            "weaknesses": analysis.weaknesses,
            "missing_skills": analysis.missing_skills,
            "suggestions": analysis.suggestions,
            "recommended_roles": analysis.recommended_roles,
            "created_at": analysis.created_at.isoformat(),
        },
        status=200,
    )


@role_required("jobseeker")
def analyzer_page_view(request):
    """
    Render the main AI Resume Analyzer interface page.
    """
    profile = request.user_profile
    analysis = AIResumeAnalysis.objects(user_id=request.user.id).order_by("-created_at").first()

    resume_changed = False
    if analysis and profile.resume:
        import os
        from django.conf import settings
        from ai.services import calculate_file_hash
        full_path = os.path.join(settings.MEDIA_ROOT, profile.resume)
        if os.path.exists(full_path):
            current_hash = calculate_file_hash(full_path)
            if analysis.resume_hash != current_hash:
                resume_changed = True

    return render(
        request,
        "ai/analyzer.html",
        {
            "profile": profile,
            "analysis": analysis,
            "resume_changed": resume_changed,
        },
    )

