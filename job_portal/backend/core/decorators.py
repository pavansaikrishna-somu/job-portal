from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from users.services import get_dashboard_route, get_profile_completion, get_user_profile


def role_required(*allowed_roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser or request.user.is_staff:
                return view_func(request, *args, **kwargs)
            profile = get_user_profile(request.user.id)
            if not profile:
                messages.error(request, "Profile not found. Please complete onboarding.")
                return redirect("users:onboard")
            if profile.role not in allowed_roles:
                import logging
                logger = logging.getLogger("security")
                logger.warning("Security event: Cross-role access attempt by user ID %s (role: '%s') to views requiring %s", request.user.id, profile.role, allowed_roles)
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You do not have access to this resource.")
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
                messages.error(request, "Profile not found. Please complete onboarding.")
                return redirect("users:onboard")

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
