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
    applications = Application.objects(applicant_id=request.user.id).order_by("-applied_at")
    stats = {
        "total": applications.count(),
        "pending": Application.objects(applicant_id=request.user.id, status="Pending").count(),
        "reviewed": Application.objects(applicant_id=request.user.id, status="Reviewed").count(),
        "shortlisted": Application.objects(applicant_id=request.user.id, status="Shortlisted").count(),
        "rejected": Application.objects(applicant_id=request.user.id, status="Rejected").count(),
    }
    context = {"applications": applications[:10], "stats": stats}
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
    context = {
        "jobs": jobs[:6],
        "latest_applicants": applications[:10],
        "stats": stats,
        "chart_labels": chart_labels,
        "chart_data": chart_data,
        "status_options": ["Pending", "Reviewed", "Shortlisted", "Rejected"],
    }
    return render(request, "dashboards/recruiter_dashboard.html", context)


def custom_404(request, exception):
    return render(request, "core/404.html", status=404)
