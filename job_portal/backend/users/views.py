from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.conf import settings
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes
from django.utils.safestring import mark_safe

from applications.documents import Application
from jobs.documents import Job
from users.forms import JobSeekerProfileForm, LoginForm, RecruiterProfileForm, RegisterForm, ResendVerificationForm
from users.services import get_dashboard_route, get_profile_completion, get_user_profile


def send_verification_email(request, user):
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    verification_link = request.build_absolute_uri(
        reverse("users:verify_email", kwargs={"uidb64": uid, "token": token})
    )
    
    subject = "Verify Your CareerConnect Account"
    message = (
        f"Welcome to CareerConnect!\n\n"
        f"Please click the link below to verify your account:\n"
        f"{verification_link}\n\n"
        f"This verification link will expire in 3 days.\n\n"
        f"If you did not sign up for CareerConnect, you can ignore this email."
    )
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )


def register_view(request):
    if request.user.is_authenticated:
        profile = get_user_profile(request.user.id)
        return redirect(get_dashboard_route(profile))

    form = RegisterForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        user, profile = form.save()
        try:
            send_verification_email(request, user)
            messages.success(request, "Registration successful. A verification email has been sent to your registered email address.")
        except Exception as e:
            messages.warning(request, f"Registration successful, but there was an error sending the verification email: {str(e)}")
        return redirect("users:login")
    return render(request, "users/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        profile = get_user_profile(request.user.id)
        if request.user.is_superuser or request.user.is_staff:
            return redirect("admin:index")
        return redirect(get_dashboard_route(profile))

    if request.method == "GET" and request.GET.get('expired') == '1':
        messages.warning(request, "Your session has expired. Please log in again.")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        ip = request.META.get("REMOTE_ADDR") or "127.0.0.1"

        import hashlib
        from django.core.cache import cache
        hashed_email = hashlib.sha256(email.encode('utf-8')).hexdigest()
        cache_key = f"login_attempts:{hashed_email}:{ip}"
        attempts = cache.get(cache_key, 0)

        if attempts >= 5:
            messages.error(request, "Too many login attempts. Please try again in 15 minutes.")
            return render(request, "users/login.html", {"form": form})

        user = authenticate(request, username=email, password=password)
        if user:
            cache.delete(cache_key)
            request.session.cycle_key()
            login(request, user)
            profile = get_user_profile(user.id)
            messages.success(request, "Logged in successfully.")

            # Safe redirect
            next_url = request.GET.get('next') or request.POST.get('next')
            from django.utils.http import url_has_allowed_host_and_scheme
            if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts=settings.ALLOWED_HOSTS, require_https=request.is_secure()):
                # Enforce role-authorized next target filtering
                if profile:
                    role = profile.role
                    if role == "jobseeker" and ("/recruiter/" in next_url or "/jobs/post/" in next_url or "/jobs/manage/" in next_url):
                        next_url = None
                    elif role == "recruiter" and "/jobseeker/" in next_url:
                        next_url = None
                if next_url:
                    return redirect(next_url)

            if user.is_superuser or user.is_staff:
                return redirect("admin:index")
            return redirect(get_dashboard_route(profile))

        # Failed attempt
        attempts += 1
        cache.set(cache_key, attempts, 900)

        # Prevent account enumeration
        UserModel = get_user_model()
        user_exists = UserModel.objects.filter(email__iexact=email).first() or UserModel.objects.filter(username__iexact=email).first()
        if user_exists and not user_exists.is_active and user_exists.check_password(password):
            resend_url = reverse("users:resend_verification")
            messages.error(
                request,
                mark_safe(f"Please verify your email before logging in. If you did not receive the link, you can <a href='{resend_url}'>resend the verification link</a>.")
            )
        else:
            messages.error(request, "Invalid email or password.")
    return render(request, "users/login.html", {"form": form})


@login_required
def logout_view(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"], "Method Not Allowed")
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect("core:home")


@login_required
def change_password_view(request):
    from django.contrib.auth.forms import PasswordChangeForm
    from django.contrib.auth import update_session_auth_hash
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Your password was successfully updated!")
        return redirect("users:profile")
    return render(request, "users/change_password.html", {"form": form})


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


def verify_email_view(request, uidb64, token):
    UserModel = get_user_model()
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = UserModel.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, UserModel.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        
        login(request, user, backend='users.backends.EmailOrUsernameModelBackend')
        profile = get_user_profile(user.id)
        messages.success(request, "Your email has been verified successfully. Welcome to CareerConnect!")
        return redirect(get_dashboard_route(profile))
    else:
        messages.error(request, "The verification link is invalid or has expired.")
        return redirect("users:login")


def resend_verification_view(request):
    if request.user.is_authenticated:
        profile = get_user_profile(request.user.id)
        return redirect(get_dashboard_route(profile))

    form = ResendVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        UserModel = get_user_model()
        user = UserModel.objects.filter(email__iexact=email).first()
        if user:
            if user.is_active:
                messages.info(request, "Your account is already verified. Please log in.")
                return redirect("users:login")
            else:
                try:
                    send_verification_email(request, user)
                    messages.success(request, "A new verification email has been sent. Please check your inbox.")
                except Exception as e:
                    messages.error(request, f"Error sending verification email: {str(e)}")
                return redirect("users:login")
    return render(request, "users/resend_verification.html", {"form": form})


@login_required
def onboard_view(request):
    import logging
    logger = logging.getLogger("security")
    
    # 1. One-time assignment: repeated GET/POST requests must be rejected and redirected
    profile = get_user_profile(request.user.id)
    if profile:
        if request.method == "POST":
            logger.warning("Security event: Repeated onboarding attempt by user ID %s", request.user.id)
        return redirect(get_dashboard_route(profile))

    if request.method == "POST":
        role = request.POST.get("role")
        # 2. Strict canonical role values validation
        if role not in ("jobseeker", "recruiter"):
            logger.warning("Security event: Rejected invalid role selection '%s' for user ID %s", role, request.user.id)
            messages.error(request, "Invalid role selected.")
            return render(request, "users/onboard.html")

        user = request.user
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.save()

        from users.documents import UserProfile
        try:
            # 3. Save role exactly once
            profile = UserProfile(
                auth_user_id=user.id,
                name=user.first_name or user.username or "Google User",
                email=user.email,
                password="GoogleOAuthAccountNoPassword",
                role=role
            )
            profile.save()
            logger.info("Security event: Successful onboarding for user ID %s with role '%s'", user.id, role)
        except Exception as e:
            # 4. Concurrency checks & Compensating rollback
            existing_profile = UserProfile.objects(auth_user_id=user.id).first()
            if existing_profile:
                logger.info("Security event: Onboarding completed concurrently for user ID %s", user.id)
                return redirect(get_dashboard_route(existing_profile))
            else:
                from django.contrib.auth import logout
                logout(request)
                user.delete()
                logger.error("Security event: Profile creation failed for user ID %s. Rolled back user: %s", user.id, str(e))
                messages.error(request, "Failed to complete onboarding. Please try again.")
                return redirect("users:login")

        messages.success(request, "Welcome to CareerConnect!")
        return redirect(get_dashboard_route(profile))

    return render(request, "users/onboard.html")

