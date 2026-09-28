from django.shortcuts import render

from applications.documents import Application
from core.decorators import role_required
from jobs.documents import Job


def home(request):
    latest_jobs = Job.objects.order_by("-created_at")[:6]
    context = {
        "latest_jobs": latest_jobs,
        "total_jobs": Job.objects.count(),
        "total_applications": Application.objects.count(),
    }
    return render(request, "core/home.html", context)


def about(request):
    return render(request, "core/about.html")


def contact(request):
    return render(request, "core/contact.html")


@role_required("jobseeker")
def jobseeker_dashboard(request):
    from django.urls import reverse
    from jobs.documents import Job
    from applications.documents import Application
    from interviews.documents import Interview
    from users.services import get_user_profile, get_profile_completion
    from ai.documents import AIResumeAnalysis
    
    applications = Application.objects(applicant_id=request.user.id).order_by("-applied_at")
    stats = {
        "total": applications.count(),
        "pending": Application.objects(applicant_id=request.user.id, status="Pending").count(),
        "reviewed": Application.objects(applicant_id=request.user.id, status="Reviewed").count(),
        "shortlisted": Application.objects(applicant_id=request.user.id, status="Shortlisted").count(),
        "rejected": Application.objects(applicant_id=request.user.id, status="Rejected").count(),
    }
    
    # 1. Nearest upcoming interview
    upcoming_qs = Interview.objects(jobseeker_id=request.user.id, status="Scheduled").order_by("interview_date")
    upcoming = []
    for item in upcoming_qs:
        rec_prof = get_user_profile(item.recruiter_id)
        job = Job.get_or_none(item.job_id)
        upcoming.append({
            "interview": item,
            "recruiter_name": rec_prof.name if rec_prof else "Recruiter",
            "job_title": job.title if job else "Job Position",
            "iso_date": item.interview_date.isoformat()
        })
    
    next_interview = upcoming[0] if upcoming else None
    
    # 2. Recent Applications with company name mapped (up to 5)
    recent_applications = []
    for app in applications[:5]:
        job = Job.get_or_none(app.job_id)
        recent_applications.append({
            "id": str(app.id),
            "job_id": app.job_id,
            "job_title": app.job_title,
            "company": job.company if job else "Organization",
            "applied_at": app.applied_at,
            "status": app.status,
            "resume": app.resume,
        })
        
    # 3. Jobs for you (up to 2 active jobs not applied to)
    applied_job_ids = [app.job_id for app in applications]
    recommended_jobs_qs = Job.objects().order_by("-created_at")
    recommended_jobs = []
    for job in recommended_jobs_qs:
        if str(job.id) not in applied_job_ids:
            recommended_jobs.append(job)
            if len(recommended_jobs) == 2:
                break
                
    # 4. Recommended next steps
    profile = get_user_profile(request.user.id)
    if profile:
        profile_completion = get_profile_completion(profile)
        is_profile_complete = profile_completion["is_complete"]
        has_resume = bool(profile.resume)
    else:
        is_profile_complete = False
        has_resume = False
    
    analysis = AIResumeAnalysis.objects(user_id=request.user.id).order_by("-created_at").first()
    
    recommendations = []
    if next_interview:
        recommendations.append({
            "title": "Prepare for your interview",
            "desc": "Review company details and Zoom link.",
            "url": reverse("interviews:view_interview", kwargs={"interview_id": str(next_interview["interview"].id)}),
            "icon": "fa-calendar-check text-success",
            "priority": 1
        })
    if not has_resume:
        recommendations.append({
            "title": "Upload your resume",
            "desc": "Upload your resume to apply for positions.",
            "url": reverse("users:profile"),
            "icon": "fa-file-arrow-up text-danger",
            "priority": 2
        })
    elif not is_profile_complete:
        recommendations.append({
            "title": "Complete your profile",
            "desc": "Fill in missing details to unlock applications.",
            "url": reverse("users:profile"),
            "icon": "fa-user-pen text-warning",
            "priority": 2
        })
    if has_resume and not analysis:
        recommendations.append({
            "title": "Analyze your resume",
            "desc": "Get AI ATS feedback and recommendations.",
            "url": reverse("ai:analyzer_page"),
            "icon": "fa-wand-magic-sparkles text-primary",
            "priority": 3
        })
    if stats["pending"] > 0:
        recommendations.append({
            "title": f"Track {stats['pending']} pending application(s)",
            "desc": "Monitor status changes and communication logs.",
            "url": reverse("applications:my_applications"),
            "icon": "fa-clock-rotate-left text-info",
            "priority": 4
        })
    if stats["total"] == 0:
        recommendations.append({
            "title": "Apply for your first job",
            "desc": "Browse active positions and submit applications.",
            "url": reverse("jobs:job_list"),
            "icon": "fa-briefcase text-secondary",
            "priority": 5
        })
    else:
        available_jobs_count = Job.objects().count() - stats["total"]
        if available_jobs_count > 0:
            recommendations.append({
                "title": f"Review {available_jobs_count} matched jobs",
                "desc": "Check jobs matching your professional criteria.",
                "url": reverse("jobs:job_list"),
                "icon": "fa-sparkles text-accent",
                "priority": 5
            })
            
    recommendations.sort(key=lambda x: x["priority"])
    active_recommendations = recommendations[:3]
    
    context = {
        "stats": stats,
        "next_interview": next_interview,
        "recent_applications": recent_applications,
        "recommended_jobs": recommended_jobs,
        "active_recommendations": active_recommendations,
        "current_user_profile": profile,
    }
    return render(request, "dashboards/jobseeker_dashboard.html", context)


@role_required("recruiter")
def recruiter_dashboard(request):
    jobs = Job.objects(recruiter_id=request.user.id).order_by("-created_at")
    applications = Application.objects(recruiter_id=request.user.id).order_by("-applied_at")
    stats = {
        "active_jobs": jobs.count(),
        "total_applicants": applications.count(),
        "pending_reviews": Application.objects(recruiter_id=request.user.id, status="Pending").count(),
        "shortlisted": Application.objects(recruiter_id=request.user.id, status="Shortlisted").count(),
        "rejected": Application.objects(recruiter_id=request.user.id, status="Rejected").count(),
    }
    chart_labels = ["Pending", "Reviewed", "Shortlisted", "Rejected"]
    chart_data = [
        Application.objects(recruiter_id=request.user.id, status="Pending").count(),
        Application.objects(recruiter_id=request.user.id, status="Reviewed").count(),
        Application.objects(recruiter_id=request.user.id, status="Shortlisted").count(),
        Application.objects(recruiter_id=request.user.id, status="Rejected").count(),
    ]
    from interviews.documents import Interview
    from users.services import get_user_profile
    upcoming_qs = Interview.objects(recruiter_id=request.user.id, status="Scheduled").order_by("interview_date")[:5]
    upcoming = []
    for item in upcoming_qs:
        seek_prof = get_user_profile(item.jobseeker_id)
        job = Job.get_or_none(item.job_id)
        upcoming.append({
            "interview": item,
            "candidate_name": seek_prof.name if seek_prof else "Candidate",
            "job_title": job.title if job else "Job Position",
            "iso_date": item.interview_date.isoformat()
        })

    context = {
        "jobs": jobs[:6],
        "latest_applicants": applications[:10],
        "stats": stats,
        "chart_labels": chart_labels,
        "chart_data": chart_data,
        "status_options": ["Pending", "Reviewed", "Shortlisted", "Rejected"],
        "upcoming_interviews": upcoming,
    }
    return render(request, "dashboards/recruiter_dashboard.html", context)


def custom_404(request, exception):
    return render(request, "core/404.html", status=404)


def custom_403(request, exception=None):
    return render(request, "core/403.html", status=403)
