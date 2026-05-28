from django.contrib import messages
from django.shortcuts import redirect, render

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
        messages.success(request, "Application submitted successfully.")
        return redirect("applications:my_applications")

    return render(request, "applications/apply_job.html", {"form": form, "job": job})


@role_required("jobseeker")
def my_applications(request):
    applications = Application.objects(applicant_id=request.user.id).order_by("-applied_at")
    return render(request, "applications/my_applications.html", {"applications": applications})


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


@role_required("recruiter")
def resume_access(request, application_id, mode):
    if mode not in {"view", "download"}:
        raise Http404("Resume not found")

    application = Application.get_or_none(application_id)
    if not application or application.recruiter_id != request.user.id:
        raise Http404("Resume not found")

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

    application.status = new_status
    application.save()
    messages.success(request, "Application status updated successfully.")
    next_url = request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("applications:applicants")
