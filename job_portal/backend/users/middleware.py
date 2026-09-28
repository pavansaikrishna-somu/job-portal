from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from users.services import get_user_profile

class SessionExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code == 302 and not request.user.is_authenticated:
            login_url = reverse('users:login')
            redirect_to = response.get('Location', '')
            if login_url in redirect_to:
                session_cookie = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
                if session_cookie and 'expired=1' not in redirect_to:
                    separator = '&' if '?' in redirect_to else '?'
                    response['Location'] = f"{redirect_to}{separator}expired=1"
        return response


class GoogleOnboardingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Skip for staff/superuser
            if not request.user.is_superuser and not request.user.is_staff:
                profile = get_user_profile(request.user.id)
                if not profile:
                    onboard_url = reverse('users:onboard')
                    logout_url = reverse('users:logout')
                    # Only allow accessing onboard, logout, or static/media files
                    if request.path != onboard_url and request.path != logout_url and not request.path.startswith('/static/') and not request.path.startswith('/media/'):
                        import logging
                        logger = logging.getLogger("security")
                        logger.warning("Security event: Incomplete user ID %s attempted to access protected URL '%s'", request.user.id, request.path)
                        return redirect(onboard_url)
                else:
                    # Check for corrupt/invalid role
                    if profile.role not in ("jobseeker", "recruiter"):
                        import logging
                        logger = logging.getLogger("security")
                        logger.warning("Security event: Corrupt/invalid role '%s' for authenticated user ID %s", profile.role, request.user.id)
                        from django.contrib.auth import logout
                        from django.contrib import messages
                        logout(request)
                        messages.error(request, "Your account has an invalid configuration. Please contact support.")
                        return redirect("users:login")
        return self.get_response(request)
