from django.contrib import messages
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required

import mimetypes

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.utils.encoding import smart_str
from django.utils.http import url_has_allowed_host_and_scheme

from applications.documents import Application
from applications.forms import ApplicationForm
from core.decorators import profile_completion_required, role_required
from core.utils import save_uploaded_file
from jobs.documents import Job
from notifications.services import create_notification


@role_required("jobseeker")
@profile_completion_required("jobseeker")
def apply_job(request, job_id):
    job = Job.get_or_none(job_id)
    if not job:
        return render(request, "core/404.html", status=404)

    existing = Application.objects(applicant_id=request.user.id, job_id=str(job.id)).first()
    if existing:
        messages.info(request, "You have already applied for this job.")
        return redirect("applications:my_applications")

    form = ApplicationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        resume_path = save_uploaded_file(form.cleaned_data["resume"], "resumes")
        application = Application(
            applicant_id=request.user.id,
            applicant_name=request.user_profile.name,
            applicant_email=request.user_profile.email,
            job_id=str(job.id),
            job_title=job.title,
            recruiter_id=job.recruiter_id,
            resume=resume_path,
            cover_letter=form.cleaned_data["cover_letter"],
            status="Pending",
        )
        application.save()
        
        # Notify Recruiter
        create_notification(
            user_id=job.recruiter_id,
            title="New Job Application",
            description=f"{request.user_profile.name} applied for the role of '{job.title}'.",
            notification_type="application_new",
            link=f"/applications/applicants/?job={job.id}",
            deduplication_key=f"app_status_{application.id}_pending"
        )
        
        messages.success(request, "Application submitted successfully.")
        return redirect("applications:my_applications")

    return render(request, "applications/apply_job.html", {"form": form, "job": job})


@role_required("jobseeker")
def my_applications(request):
    applications_qs = Application.objects(applicant_id=request.user.id).order_by("-applied_at")
    from jobs.documents import Job
    applications = []
    for app in applications_qs:
        job = Job.get_or_none(app.job_id)
        applications.append({
            "id": str(app.id),
            "job_id": app.job_id,
            "job_title": app.job_title,
            "company": job.company if job else "Organization",
            "applied_at": app.applied_at,
            "status": app.status,
            "resume": app.resume,
        })
    stats = {
        "total": len(applications),
        "pending": Application.objects(applicant_id=request.user.id, status="Pending").count(),
        "reviewed": Application.objects(applicant_id=request.user.id, status="Reviewed").count(),
        "shortlisted": Application.objects(applicant_id=request.user.id, status="Shortlisted").count(),
        "rejected": Application.objects(applicant_id=request.user.id, status="Rejected").count(),
    }
    return render(
        request,
        "applications/my_applications.html",
        {"applications": applications, "stats": stats}
    )


@role_required("recruiter")
def applicants(request):
    selected_job = request.GET.get("job", "").strip()
    selected_status = request.GET.get("status", "").strip()
    jobs = Job.objects(recruiter_id=request.user.id).order_by("-created_at")

    applications = Application.objects(recruiter_id=request.user.id).order_by("-applied_at")
    if selected_job:
        applications = applications.filter(job_id=selected_job)
    if selected_status:
        applications = applications.filter(status=selected_status)

    context = {
        "jobs": jobs,
        "applications": applications,
        "selected_job": selected_job,
        "selected_status": selected_status,
        "status_options": ["Pending", "Reviewed", "Shortlisted", "Rejected"],
    }
    return render(request, "applications/applicants.html", context)


@login_required
def resume_access(request, application_id, mode):
    if mode not in {"view", "download"}:
        raise Http404("Resume not found")

    application = Application.get_or_none(application_id)
    if not application:
        raise Http404("Resume not found")

    # Only the submitting jobseeker (owner) or the associated recruiter can access
    if request.user.id != application.applicant_id and request.user.id != application.recruiter_id:
        raise Http404("Resume not found")

    from users.services import get_user_profile
    profile = get_user_profile(request.user.id)
    if not profile or profile.role not in ("jobseeker", "recruiter"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Unauthorized role.")

    file_path = application.resume
    if not file_path:
        raise Http404("Resume not found")

    content_type, _ = mimetypes.guess_type(file_path)
    if not default_storage.exists(file_path):
        raise Http404("Resume not found")

    response = FileResponse(
        default_storage.open(file_path, "rb"),
        content_type=content_type or "application/octet-stream",
    )

    filename = smart_str(file_path.split("/")[-1])
    disposition = "inline" if mode == "view" else "attachment"
    response["Content-Disposition"] = f"{disposition}; filename=\"{filename}\""
    return response


@role_required("recruiter")
def update_status(request, application_id, new_status):
    valid_statuses = {"Pending", "Reviewed", "Shortlisted", "Rejected"}
    if new_status not in valid_statuses:
        messages.error(request, "Invalid status selected.")
        return redirect("applications:applicants")

    application = Application.get_or_none(application_id)
    if not application or application.recruiter_id != request.user.id:
        messages.error(request, "Application not found or unauthorized.")
        return redirect("applications:applicants")

    from django.utils import timezone
    if not getattr(application, "status_history", None):
        application.status_history = [{
            "status": "Pending",
            "transition_id": "pending_0",
            "timestamp": application.applied_at.isoformat() if getattr(application, "applied_at", None) else timezone.now().isoformat()
        }]

    if new_status != application.status:
        transition_id = f"{new_status.lower()}_{len(application.status_history)}"
        application.status_history.append({
            "status": new_status,
            "transition_id": transition_id,
            "timestamp": timezone.now().isoformat()
        })
        application.status = new_status
        application.save()
    else:
        latest_transition = application.status_history[-1]
        transition_id = latest_transition["transition_id"]
    
    # Notify Seeker
    notif_type = "application_shortlisted" if new_status == "Shortlisted" else "application_status"
    create_notification(
        user_id=application.applicant_id,
        title=f"Application {new_status}",
        description=f"Your application for '{application.job_title}' has been updated to '{new_status}'.",
        notification_type=notif_type,
        link="/applications/my/",
        deduplication_key=f"app_status_{application_id}_{transition_id}"
    )
    
    messages.success(request, "Application status updated successfully.")
    next_url = request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("applications:applicants")


@role_required("jobseeker")
def withdraw_application(request, application_id):
    application = Application.get_or_none(application_id)
    if not application or application.applicant_id != request.user.id:
        messages.error(request, "Application not found or unauthorized.")
        return redirect("applications:my_applications")

    recruiter_id = application.recruiter_id
    job_title = application.job_title
    applicant_name = request.user_profile.name
    job_id = application.job_id

    from django.utils import timezone
    if not getattr(application, "status_history", None):
        application.status_history = [{
            "status": application.status,
            "transition_id": f"{application.status.lower()}_0",
            "timestamp": application.applied_at.isoformat() if getattr(application, "applied_at", None) else timezone.now().isoformat()
        }]

    new_status = "Withdrawn"
    if application.status != new_status:
        transition_id = f"{new_status.lower()}_{len(application.status_history)}"
        application.status_history.append({
            "status": new_status,
            "transition_id": transition_id,
            "timestamp": timezone.now().isoformat()
        })
        application.status = new_status
        application.save()
    else:
        latest_transition = application.status_history[-1]
        transition_id = latest_transition["transition_id"]

    # Notify Recruiter
    create_notification(
        user_id=recruiter_id,
        title="Application Withdrawn",
        description=f"{applicant_name} withdrew their application for '{job_title}'.",
        notification_type="application_withdrawn",
        link=f"/applications/applicants/?job={job_id}",
        deduplication_key=f"app_status_{application_id}_{transition_id}"
    )

    messages.success(request, "Application withdrawn successfully.")
    return redirect("applications:my_applications")
