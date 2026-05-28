from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from users.services import get_profile_completion, get_user_profile


def role_required(*allowed_roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            profile = get_user_profile(request.user.id)
            if not profile:
                messages.error(request, "Profile not found. Please update your profile.")
                return redirect("users:profile")
            if profile.role not in allowed_roles:
                messages.error(request, "You are not authorized to access this page.")
                return redirect("core:home")
            request.user_profile = profile
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def profile_completion_required(role_label=None):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            profile = getattr(request, "user_profile", None) or get_user_profile(request.user.id)
            if not profile:
                messages.error(request, "Profile not found. Please update your profile.")
                return redirect("users:profile")

            completion = get_profile_completion(profile)
            if not completion["is_complete"]:
                label = role_label or profile.role
                messages.warning(
                    request,
                    f"Complete your {label} profile to continue.",
                )
                return redirect("users:profile")

            request.user_profile = profile
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
