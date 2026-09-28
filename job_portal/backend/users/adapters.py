from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth import get_user_model

class CustomAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        if not request.user.is_authenticated:
            return reverse('users:login')
        if request.user.is_superuser or request.user.is_staff:
            return '/admin/'
        from users.services import get_user_profile, get_dashboard_route
        profile = get_user_profile(request.user.id)
        if not profile:
            return reverse('users:onboard')
        return reverse(get_dashboard_route(profile))

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        import logging
        logger = logging.getLogger("security")

        # 1. Normalize Google email
        email = sociallogin.user.email
        if email:
            email = email.strip().lower()
            sociallogin.user.email = email
            sociallogin.user.username = email
        else:
            logger.warning("Security event: Google login rejected due to missing email provider attribute")
            messages.error(request, "An account resolution error occurred. Please log in using your password.")
            raise ImmediateHttpResponse(redirect("users:login"))

        # 2. Accept only verified email
        extra_data = sociallogin.account.extra_data
        email_verified = extra_data.get('email_verified', False)
        if str(email_verified).lower() != 'true' and email_verified is not True:
            logger.warning("Security event: Google login rejected for unverified email '%s'", email)
            messages.error(request, "An account resolution error occurred. Please log in using your password.")
            raise ImmediateHttpResponse(redirect("users:login"))

        # 3. Account matching: connect safely to existing user
        UserModel = get_user_model()
        existing_user = UserModel.objects.filter(email__iexact=email).first() or UserModel.objects.filter(username__iexact=email).first()
        if existing_user:
            # Google authentication must not bypass inactive/deactivated account restrictions
            if not existing_user.is_active:
                logger.warning("Security event: Blocked login attempt by inactive user ID %s via Google OAuth", existing_user.id)
                messages.error(request, "Your account is currently inactive. Please contact support.")
                raise ImmediateHttpResponse(redirect("users:login"))

            sociallogin.user = existing_user
            from allauth.socialaccount.models import SocialAccount
            
            # Check if this SocialAccount is already linked to another user
            sa_by_uid = SocialAccount.objects.filter(
                provider=sociallogin.account.provider,
                uid=sociallogin.account.uid
            ).first()
            if sa_by_uid and sa_by_uid.user != existing_user:
                logger.warning("Security event: Google UID %s already linked to user ID %s, mismatch with existing user ID %s", sociallogin.account.uid, sa_by_uid.user_id, existing_user.id)
                messages.error(request, "An account resolution error occurred. Please contact support.")
                raise ImmediateHttpResponse(redirect("users:login"))

            sa_exists = SocialAccount.objects.filter(
                user=existing_user,
                provider=sociallogin.account.provider,
                uid=sociallogin.account.uid
            ).exists()
            
            if not sa_exists:
                # Safe auto-linking: link only if roles or profile details do not conflict
                from users.services import get_user_profile
                existing_profile = get_user_profile(existing_user.id)
                
                # Check for profile conflict
                if existing_profile and existing_profile.email.strip().lower() != email:
                    logger.warning("Security event: Email mismatch/conflict between Django user %s and MongoDB profile %s", email, existing_profile.email)
                    messages.error(request, "An account resolution error occurred. Please contact support.")
                    raise ImmediateHttpResponse(redirect("users:login"))

                sociallogin.connect(request, existing_user)
                sa, created = SocialAccount.objects.get_or_create(
                    user=existing_user,
                    provider=sociallogin.account.provider,
                    defaults={'uid': sociallogin.account.uid, 'extra_data': sociallogin.account.extra_data}
                )
                if not created:
                    sa.uid = sociallogin.account.uid
                    sa.extra_data = sociallogin.account.extra_data
                    sa.save()
                logger.info("Security event: Successfully linked Google SocialAccount UID %s to local user ID %s", sociallogin.account.uid, existing_user.id)
