from django.contrib import messages
from django.shortcuts import redirect, render
from mongoengine.queryset.visitor import Q

from applications.documents import Application
from core.decorators import profile_completion_required, role_required
from jobs.documents import Job
from jobs.forms import JobForm


def job_list(request):
    search_query = request.GET.get("q", "").strip()
    location = request.GET.get("location", "").strip()
    skill = request.GET.get("skill", "").strip()
    experience = request.GET.get("experience", "").strip()

    jobs_queryset = Job.objects.order_by("-created_at")
    if search_query:
        jobs_queryset = jobs_queryset.filter(
            Q(title__icontains=search_query)
            | Q(company__icontains=search_query)
            | Q(description__icontains=search_query)
        )
    if location:
        jobs_queryset = jobs_queryset.filter(location__icontains=location)
    if experience:
        jobs_queryset = jobs_queryset.filter(experience__icontains=experience)

    jobs = list(jobs_queryset)
    if skill:
        jobs = [job for job in jobs if any(skill.lower() in item.lower() for item in job.skills_required)]

    all_jobs = Job.objects
    unique_locations = sorted({job.location for job in all_jobs})
    unique_skills = sorted({skill for job in all_jobs for skill in job.skills_required})

    context = {
        "jobs": jobs,
        "filters": {"q": search_query, "location": location, "skill": skill, "experience": experience},
        "unique_locations": unique_locations,
        "unique_skills": unique_skills,
    }
    return render(request, "jobs/job_list.html", context)


def job_detail(request, job_id):
    job = Job.get_or_none(job_id)
    if not job:
        return render(request, "core/404.html", status=404)

    already_applied = False
    if request.user.is_authenticated:
        already_applied = Application.objects(applicant_id=request.user.id, job_id=str(job.id)).first() is not None

    context = {"job": job, "already_applied": already_applied}
    return render(request, "jobs/job_detail.html", context)


@role_required("recruiter")
@profile_completion_required("recruiter")
def post_job(request):
    form = JobForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(recruiter_profile=request.user_profile, recruiter_user=request.user)
        messages.success(request, "Job posted successfully.")
        return redirect("jobs:manage_jobs")
    return render(request, "jobs/post_job.html", {"form": form, "page_title": "Post New Job"})


@role_required("recruiter")
def manage_jobs(request):
    jobs = Job.objects(recruiter_id=request.user.id).order_by("-created_at")
    return render(request, "jobs/manage_jobs.html", {"jobs": jobs})


@role_required("recruiter")
@profile_completion_required("recruiter")
def edit_job(request, job_id):
    job = Job.get_or_none(job_id)
    if not job or job.recruiter_id != request.user.id:
        messages.error(request, "Job not found or unauthorized.")
        return redirect("jobs:manage_jobs")

    form = JobForm(request.POST or None, instance=job)
    if request.method == "POST" and form.is_valid():
        form.save(recruiter_profile=request.user_profile, recruiter_user=request.user)
        messages.success(request, "Job updated successfully.")
        return redirect("jobs:manage_jobs")
    return render(request, "jobs/post_job.html", {"form": form, "page_title": "Edit Job"})


@role_required("recruiter")
def delete_job(request, job_id):
    job = Job.get_or_none(job_id)
    if not job or job.recruiter_id != request.user.id:
        messages.error(request, "Job not found or unauthorized.")
        return redirect("jobs:manage_jobs")

    Application.objects(job_id=str(job.id)).delete()
    job.delete()
    messages.success(request, "Job deleted successfully.")
    return redirect("jobs:manage_jobs")
