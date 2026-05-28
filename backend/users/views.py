from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from applications.documents import Application
from jobs.documents import Job
from users.forms import JobSeekerProfileForm, LoginForm, RecruiterProfileForm, RegisterForm
from users.services import get_dashboard_route, get_profile_completion, get_user_profile


def register_view(request):
    if request.user.is_authenticated:
        profile = get_user_profile(request.user.id)
        return redirect(get_dashboard_route(profile))

    form = RegisterForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        user, profile = form.save()
        email = form.cleaned_data["email"].lower()
        password = form.cleaned_data["password"]
        authenticated_user = authenticate(request, username=email, password=password)
        if authenticated_user:
            login(request, authenticated_user)
            messages.success(request, "Registration successful. Welcome to CareerConnect!")
            return redirect(get_dashboard_route(profile))
        messages.error(request, "Registration completed, but login failed. Please sign in.")
        return redirect("users:login")
    return render(request, "users/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        profile = get_user_profile(request.user.id)
        return redirect(get_dashboard_route(profile))

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        password = form.cleaned_data["password"]
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            profile = get_user_profile(user.id)
            messages.success(request, "Logged in successfully.")
            return redirect(get_dashboard_route(profile))
        messages.error(request, "Invalid email or password.")
    return render(request, "users/login.html", {"form": form})


@login_required
def logout_view(request):
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect("core:home")


@login_required
def profile_view(request):
    profile = get_user_profile(request.user.id)
    if not profile:
        messages.error(request, "Profile not found. Please contact support.")
        return redirect("core:home")
    completion = get_profile_completion(profile)
    job_count = 0
    application_count = 0
    if profile.role == "recruiter":
        job_count = Job.objects(recruiter_id=request.user.id).count()
    else:
        application_count = Application.objects(applicant_id=request.user.id).count()

    return render(
        request,
        "users/profile.html",
        {
            "profile": profile,
            "completion": completion,
            "edit_mode": False,
            "job_count": job_count,
            "application_count": application_count,
        },
    )


@login_required
def profile_edit_view(request):
    profile = get_user_profile(request.user.id)
    if not profile:
        messages.error(request, "Profile not found. Please contact support.")
        return redirect("core:home")

    form_class = JobSeekerProfileForm if profile.role == "jobseeker" else RecruiterProfileForm
    form = form_class(request.POST or None, request.FILES or None, user=request.user, profile=profile)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("users:profile")
        if profile.role == "recruiter" and "company_email" in form.errors:
            messages.error(request, "Company email is required for recruiters.")
        else:
            messages.error(request, "Please correct the highlighted fields and try again.")

    completion = get_profile_completion(profile)
    job_count = 0
    application_count = 0
    if profile.role == "recruiter":
        job_count = Job.objects(recruiter_id=request.user.id).count()
    else:
        application_count = Application.objects(applicant_id=request.user.id).count()

    return render(
        request,
        "users/profile.html",
        {
            "form": form,
            "profile": profile,
            "completion": completion,
            "edit_mode": True,
            "job_count": job_count,
            "application_count": application_count,
        },
    )
