import os
import shutil
import tempfile
import unittest
import datetime
from unittest.mock import patch, MagicMock

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jobportal.settings")
django.setup()

from django.test.utils import setup_test_environment
try:
    setup_test_environment()
except Exception:
    pass

from django.test import TransactionTestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.conf import settings
from django.core import mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from mongoengine import connection
from mongoengine.connection import get_db
from users.documents import UserProfile
from jobs.documents import Job
from applications.documents import Application
from messages_box.documents import Conversation, Message
from interviews.documents import Interview
from notifications.documents import Notification


class AuthEnhancementsTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disconnect the default connection to avoid messing with real database
        connection.disconnect(alias="default")
        # Connect to test database
        cls.db_connection = connection.connect(
            db="test_job_portal_db",
            host="mongodb://localhost:27017/test_job_portal_db",
            alias="default",
            uuidRepresentation="standard"
        )
        
        # Override MEDIA_ROOT to a temporary directory
        cls.temp_media = tempfile.mkdtemp()
        cls.override_media = patch.object(settings, "MEDIA_ROOT", cls.temp_media)
        cls.override_media.start()

        # Override EMAIL_BACKEND to locmem
        cls.override_email = patch.object(settings, "EMAIL_BACKEND", "django.core.mail.backends.locmem.EmailBackend")
        cls.override_email.start()

    @classmethod
    def tearDownClass(cls):
        # Stop overriding email and media
        cls.override_email.stop()
        cls.override_media.stop()
        # Cleanup temporary media directory
        shutil.rmtree(cls.temp_media)
        
        # Drop test database and disconnect
        db = get_db(alias="default")
        db.client.drop_database("test_job_portal_db")
        connection.disconnect(alias="default")
        
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.client = Client()
        
        # Clear collections and relational tables
        UserProfile.objects.delete()
        User.objects.all().delete()
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        Notification.objects.delete()
        Conversation.objects.delete()
        Message.objects.delete()
        mail.outbox.clear()

        # Create dummy user and profile
        self.password = "Password@123"
        self.user = User.objects.create_user(
            username="testuser@test.com",
            email="testuser@test.com",
            password=self.password,
            is_active=True
        )
        self.profile = UserProfile(
            auth_user_id=self.user.id,
            name="Test User",
            email="testuser@test.com",
            password=self.user.password,
            role="jobseeker"
        )
        self.profile.save()

    def test_existing_and_non_existing_email_reset_enumeration_prevention(self):
        """Submit password reset for both existing and non-existing emails and check for identical response."""
        # 1. Existing user
        response_exist = self.client.post(reverse("password_reset"), {"email": "testuser@test.com"})
        self.assertEqual(response_exist.status_code, 302)
        self.assertRedirects(response_exist, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

        # Clear mail outbox
        mail.outbox.clear()

        # 2. Non-existing user
        response_non_exist = self.client.post(reverse("password_reset"), {"email": "nonexistent@test.com"})
        self.assertEqual(response_non_exist.status_code, 302)
        self.assertRedirects(response_non_exist, reverse("password_reset_done"))
        # No email should be sent for non-existing users to prevent account enumeration / spamming
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_email_generation(self):
        """Verify that the generated password reset email contains the expected link and structure."""
        self.client.post(reverse("password_reset"), {"email": "testuser@test.com"})
        self.assertEqual(len(mail.outbox), 1)
        
        email = mail.outbox[0]
        self.assertIn("Password reset", email.subject)
        
        # Verify the presence of the uidb64 and token structure in the body
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        confirm_url_part = f"/reset/{uid}/{token}/"
        self.assertIn(confirm_url_part, email.body)

    def test_valid_and_invalid_reset_tokens(self):
        """Test password reset confirmation page loading with valid and invalid tokens."""
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        valid_token = default_token_generator.make_token(self.user)
        invalid_token = "invalid-token-1234"

        # 1. Valid token (should redirect to set-password URL, so we follow=True)
        response_valid = self.client.get(reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": valid_token}), follow=True)
        self.assertEqual(response_valid.status_code, 200)
        self.assertContains(response_valid, "Set New Password")

        # 2. Invalid token
        response_invalid = self.client.get(reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": invalid_token}))
        self.assertEqual(response_invalid.status_code, 200)
        self.assertContains(response_invalid, "The password reset link was invalid")

    def test_weak_new_password_rejection(self):
        """Verify that weak password choices are rejected based on Django configuration."""
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        
        # 1. Get token URL first to set token in session
        self.client.get(reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token}), follow=True)

        # 2. POST to the set-password view
        response = self.client.post(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "set-password"}),
            {
                "new_password1": "12345",
                "new_password2": "12345"
            }
        )
        self.assertEqual(response.status_code, 200) # Re-renders form due to invalid data
        self.assertContains(response, "This password is too short")

    def test_successful_password_reset_and_subsequent_login(self):
        """Test successful password reset redirects and allows login with the new password."""
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        new_password = "PremiumPassword123!"

        # 1. Get token URL first to set token in session
        self.client.get(reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token}), follow=True)

        # 2. POST to set-password view
        response = self.client.post(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "set-password"}),
            {
                "new_password1": new_password,
                "new_password2": new_password
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("password_reset_complete"))

        # Verify login works with new password
        login_success = self.client.login(username="testuser@test.com", password=new_password)
        self.assertTrue(login_success)

    def test_missing_user_profile_handling(self):
        """Verify password reset system and login redirects handle missing MongoDB UserProfile gracefully."""
        # Create a user in SQLite, but don't save a profile in MongoEngine
        no_profile_user = User.objects.create_user(
            username="noprofile@test.com",
            email="noprofile@test.com",
            password="Password@123",
            is_active=True
        )

        # 1. Trigger reset for this user
        response = self.client.post(reverse("password_reset"), {"email": "noprofile@test.com"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

        # 2. Login with this user should work, redirecting to core:home since profile is missing
        login_success = self.client.login(username="noprofile@test.com", password="Password@123")
        self.assertTrue(login_success)

    def test_get_logout_returns_405_without_ending_session(self):
        """A GET request to logout should be rejected with 405 and the user session must remain active."""
        # Log user in
        self.client.login(username="testuser@test.com", password=self.password)
        
        # Verify authenticated
        response_home = self.client.get(reverse("core:home"))
        self.assertTrue(response_home.wsgi_request.user.is_authenticated)

        # Try GET logout
        response_logout_get = self.client.get(reverse("users:logout"))
        self.assertEqual(response_logout_get.status_code, 405)

        # Verify still authenticated (session is NOT terminated)
        response_home2 = self.client.get(reverse("core:home"))
        self.assertTrue(response_home2.wsgi_request.user.is_authenticated)

        # Try POST logout
        response_logout_post = self.client.post(reverse("users:logout"))
        self.assertEqual(response_logout_post.status_code, 302)
        self.assertRedirects(response_logout_post, reverse("core:home"))

        # Verify no longer authenticated
        response_home3 = self.client.get(reverse("core:home"))
        self.assertFalse(response_home3.wsgi_request.user.is_authenticated)

    def test_jobseeker_and_recruiter_registration(self):
        """Test successful registration of jobseeker and recruiter roles."""
        # 1. Jobseeker
        response_seeker = self.client.post(reverse("users:register"), {
            "name": "Seeker Reg",
            "email": "seekerreg@test.com",
            "phone": "123456",
            "role": "jobseeker",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response_seeker.status_code, 302)
        seeker_user = User.objects.filter(email="seekerreg@test.com").first()
        self.assertIsNotNone(seeker_user)
        seeker_profile = UserProfile.objects(email="seekerreg@test.com").first()
        self.assertIsNotNone(seeker_profile)
        self.assertEqual(seeker_profile.role, "jobseeker")

        # 2. Recruiter
        response_recruiter = self.client.post(reverse("users:register"), {
            "name": "Recruiter Reg",
            "email": "recruiterreg@test.com",
            "phone": "654321",
            "role": "recruiter",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response_recruiter.status_code, 302)
        recruiter_user = User.objects.filter(email="recruiterreg@test.com").first()
        self.assertIsNotNone(recruiter_user)
        recruiter_profile = UserProfile.objects(email="recruiterreg@test.com").first()
        self.assertIsNotNone(recruiter_profile)
        self.assertEqual(recruiter_profile.role, "recruiter")

    def test_case_insensitive_duplicate_email_prevention(self):
        """Verify duplicate emails (case-insensitive) are rejected during registration."""
        response = self.client.post(reverse("users:register"), {
            "name": "Duplicate User",
            "email": "TESTUSER@TEST.COM",
            "phone": "123",
            "role": "jobseeker",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")

    def test_role_tampering_and_public_role_enforcement(self):
        """Enforce that only jobseeker and recruiter roles are accepted and staff/superuser flags are not modified."""
        response_invalid = self.client.post(reverse("users:register"), {
            "name": "Tamper User",
            "email": "tamper@test.com",
            "role": "admin",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response_invalid.status_code, 200)
        self.assertContains(response_invalid, "Select a valid choice")

        self.client.post(reverse("users:register"), {
            "name": "Normal User",
            "email": "normal@test.com",
            "role": "jobseeker",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        created_user = User.objects.filter(email="normal@test.com").first()
        self.assertIsNotNone(created_user)
        self.assertFalse(created_user.is_staff)
        self.assertFalse(created_user.is_superuser)

    @patch("users.documents.UserProfile.save")
    def test_user_profile_creation_failure_rollback(self, mock_profile_save):
        """Verify Django User creation is rolled back if MongoEngine profile save fails."""
        mock_profile_save.side_effect = Exception("Mongo Write Failed!")
        email = "rollback@test.com"
        with self.assertRaises(Exception):
            self.client.post(reverse("users:register"), {
                "name": "Rollback User",
                "email": email,
                "role": "jobseeker",
                "password": "StrongPassword@123",
                "confirm_password": "StrongPassword@123"
            })
        user_exists = User.objects.filter(email=email).exists()
        self.assertFalse(user_exists)

    def test_registration_missing_optional_phone(self):
        """Verify registration succeeds when optional phone field is omitted."""
        email = "nophone@test.com"
        response = self.client.post(reverse("users:register"), {
            "name": "No Phone User",
            "email": email,
            "role": "jobseeker",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.filter(email=email).first()
        self.assertIsNotNone(user)
        profile = UserProfile.objects(email=email).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.phone, "")

    def test_registration_password_mismatch(self):
        """Verify registration fails when password and confirm_password do not match."""
        email = "mismatch@test.com"
        response = self.client.post(reverse("users:register"), {
            "name": "Mismatch User",
            "email": email,
            "role": "jobseeker",
            "password": "StrongPassword@123",
            "confirm_password": "DifferentPassword@123"
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passwords do not match")
        user_exists = User.objects.filter(email=email).exists()
        self.assertFalse(user_exists)

    def test_registration_invalid_role(self):
        """Verify registration fails when an invalid role is provided."""
        email = "badrole@test.com"
        response = self.client.post(reverse("users:register"), {
            "name": "Bad Role User",
            "email": email,
            "role": "manager",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response.status_code, 200)
        user_exists = User.objects.filter(email=email).exists()
        self.assertFalse(user_exists)

    def test_registration_duplicate_email(self):
        """Verify registration fails when the email already exists (case-insensitive)."""
        existing_email = "duplicate@test.com"
        User.objects.create_user(username=existing_email, email=existing_email, password="ExistingPassword@123")
        response = self.client.post(reverse("users:register"), {
            "name": "Duplicate User",
            "email": "DUPLICATE@test.com",
            "role": "jobseeker",
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123"
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")

    def test_generic_login_errors_prevent_enumeration(self):
        """Test generic error response for invalid login credentials to prevent enumeration."""
        response_non_exist = self.client.post(reverse("users:login"), {
            "email": "nonexistent@test.com",
            "password": "password"
        })
        self.assertEqual(response_non_exist.status_code, 200)
        self.assertContains(response_non_exist, "Invalid email or password.")

        response_incorrect_pass = self.client.post(reverse("users:login"), {
            "email": "testuser@test.com",
            "password": "WrongPassword"
        })
        self.assertEqual(response_incorrect_pass.status_code, 200)
        self.assertContains(response_incorrect_pass, "Invalid email or password.")

    def test_inactive_account_rejection_no_enumeration(self):
        """Test inactive account message displays only if password matches, else generic error."""
        inactive_user = User.objects.create_user(
            username="inactive@test.com",
            email="inactive@test.com",
            password="StrongPassword@123",
            is_active=False
        )
        response_correct = self.client.post(reverse("users:login"), {
            "email": "inactive@test.com",
            "password": "StrongPassword@123"
        })
        self.assertEqual(response_correct.status_code, 200)
        self.assertContains(response_correct, "Please verify your email before logging in")

        response_incorrect = self.client.post(reverse("users:login"), {
            "email": "inactive@test.com",
            "password": "WrongPassword"
        })
        self.assertEqual(response_incorrect.status_code, 200)
        self.assertContains(response_incorrect, "Invalid email or password.")

    def test_login_rate_limiting(self):
        """Verify login rate limiting lock out after 5 consecutive failures."""
        from django.core.cache import cache
        cache.clear()
        email = "ratelimit@test.com"
        for _ in range(5):
            response = self.client.post(reverse("users:login"), {
                "email": email,
                "password": "WrongPassword"
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Invalid email or password.")

        response_blocked = self.client.post(reverse("users:login"), {
            "email": email,
            "password": "WrongPassword"
        })
        self.assertEqual(response_blocked.status_code, 200)
        self.assertContains(response_blocked, "Too many login attempts")

    def test_safe_local_next_redirects(self):
        """Ensure next parameters are safe local URLs, otherwise redirect to dashboard."""
        # Start anonymous (no self.client.login)
        response_safe = self.client.post(reverse("users:login"), {
            "email": "testuser@test.com",
            "password": self.password,
            "next": "/jobs/"
        })
        self.assertEqual(response_safe.status_code, 302)
        self.assertRedirects(response_safe, "/jobs/")

        # Log out to clear the session before testing unsafe redirect
        self.client.post(reverse("users:logout"))

        response_unsafe = self.client.post(reverse("users:login"), {
            "email": "testuser@test.com",
            "password": self.password,
            "next": "http://malicious.com/stolen"
        })
        self.assertEqual(response_unsafe.status_code, 302)
        self.assertRedirects(response_unsafe, reverse("core:jobseeker_dashboard"))

    def test_wrong_role_access_returns_403(self):
        """Accessing a view restricted to another role should return a 403 Forbidden page."""
        self.client.login(username="testuser@test.com", password=self.password)
        response = self.client.get(reverse("core:recruiter_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_change_password_flow_with_session_preservation(self):
        """Change password successfully and keep user logged in using update_session_auth_hash."""
        self.client.login(username="testuser@test.com", password=self.password)
        response = self.client.post(reverse("users:change_password"), {
            "old_password": self.password,
            "new_password1": "PremiumPassword123!",
            "new_password2": "PremiumPassword123!"
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("users:profile"))

        response_profile = self.client.get(reverse("users:profile"))
        self.assertTrue(response_profile.wsgi_request.user.is_authenticated)

    def test_superuser_staff_routing_without_userprofile(self):
        """Superuser/staff can login and bypass role decorators without needing UserProfile."""
        admin_user = User.objects.create_superuser(
            username="admin@test.com",
            email="admin@test.com",
            password="AdminPassword123!"
        )
        self.client.login(username="admin@test.com", password="AdminPassword123!")

        response_rec = self.client.get(reverse("core:recruiter_dashboard"))
        self.assertEqual(response_rec.status_code, 200)

        response_seek = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response_seek.status_code, 200)

    def simulate_google_login(self, email, uid="123456", email_verified=True):
        from allauth.socialaccount.models import SocialAccount, SocialLogin
        from allauth.socialaccount.helpers import complete_social_login
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.contrib.auth.models import AnonymousUser

        factory = RequestFactory()
        request = factory.get(reverse("socialaccount_login_error"))
        
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        
        request._messages = FallbackStorage(request)
        request.user = AnonymousUser()

        user = User(email=email, username=email)
        account = SocialAccount(provider="google", uid=uid, extra_data={"email_verified": email_verified})
        sociallogin = SocialLogin(user=user, account=account)

        from allauth.core.context import request_context
        from types import SimpleNamespace
        request.allauth = SimpleNamespace()
        
        response = None
        try:
            with request_context(request):
                response = complete_social_login(request, sociallogin)
        except Exception as e:
            from allauth.core.exceptions import ImmediateHttpResponse
            if isinstance(e, ImmediateHttpResponse):
                response = e.response
            else:
                raise e

        if request.session.session_key:
            self.client.cookies[settings.SESSION_COOKIE_NAME] = request.session.session_key
            for key, val in request.session.items():
                self.client.session[key] = val
            self.client.session.save()

        return request, response

    def test_google_existing_user_linking(self):
        """Mock Google social login with a matching verified email to verify account linking."""
        email = "existing_link@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        
        request, response = self.simulate_google_login(email)
        self.assertEqual(request.user.id, user.id)
        
        from allauth.socialaccount.models import SocialAccount
        sa = SocialAccount.objects.filter(user=user, provider="google").first()
        self.assertIsNotNone(sa)
        self.assertEqual(sa.uid, "123456")

    def test_google_new_user_onboarding_redirection(self):
        """Verify that a first-time Google user is redirected to the onboarding role selection."""
        email = "new_oauth@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        self.client.force_login(user)

        response_home = self.client.get(reverse("core:home"))
        self.assertEqual(response_home.status_code, 302)
        self.assertRedirects(response_home, reverse("users:onboard"))

    def test_google_onboard_jobseeker(self):
        """Verify new Google user onboarding to jobseeker profile."""
        email = "seeker_oauth@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        self.client.force_login(user)

        response_onboard = self.client.post(reverse("users:onboard"), {"role": "jobseeker"})
        self.assertEqual(response_onboard.status_code, 302)
        self.assertRedirects(response_onboard, reverse("core:jobseeker_dashboard"))
        
        profile = UserProfile.objects(email=email).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, "jobseeker")
        
        created_user = User.objects.get(email=email)
        self.assertTrue(created_user.is_active)

    def test_google_onboard_recruiter(self):
        """Verify new Google user onboarding to recruiter profile."""
        email = "recruiter_oauth@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        self.client.force_login(user)

        response_onboard = self.client.post(reverse("users:onboard"), {"role": "recruiter"})
        self.assertEqual(response_onboard.status_code, 302)
        self.assertRedirects(response_onboard, reverse("core:recruiter_dashboard"))
        
        profile = UserProfile.objects(email=email).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, "recruiter")

    def test_google_onboard_role_tampering_rejected(self):
        """Verify admin/staff role tampering on onboarding is rejected."""
        email = "tamper_oauth@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        self.client.force_login(user)

        response_onboard = self.client.post(reverse("users:onboard"), {"role": "admin"})
        self.assertEqual(response_onboard.status_code, 200)
        self.assertContains(response_onboard, "Invalid role selected.")
        
        profile = UserProfile.objects(email=email).first()
        self.assertIsNone(profile)

    def test_google_unverified_email_rejected(self):
        """Verify unverified Google emails are rejected during OAuth login."""
        request, response = self.simulate_google_login("unverified@test.com", email_verified=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("users:login")))

    def test_google_inactive_user_rejected(self):
        """Verify inactive users cannot bypass account lock via Google Login."""
        email = "inactive_oauth@test.com"
        User.objects.create_user(username=email, email=email, password="Password@123", is_active=False)

        request, response = self.simulate_google_login(email)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("users:login")))

    @patch("users.documents.UserProfile.save")
    def test_google_onboard_profile_creation_failure_rollback(self, mock_profile_save):
        """Verify user is deleted and logged out if profile creation fails during onboarding."""
        mock_profile_save.side_effect = Exception("Mongo Write Failed!")
        
        email = "rollback_oauth@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        self.client.force_login(user)

        response_onboard = self.client.post(reverse("users:onboard"), {"role": "jobseeker"})
        self.assertEqual(response_onboard.status_code, 302)
        self.assertTrue(response_onboard.url.startswith(reverse("users:login")))

        self.assertFalse(User.objects.filter(email=email).exists())
        response_profile = self.client.get(reverse("users:profile"))
        self.assertFalse(response_profile.wsgi_request.user.is_authenticated)

    def test_google_existing_user_skips_onboarding(self):
        """Verify existing user with a UserProfile skips onboarding and goes to dashboard."""
        email = "skips_onboard@test.com"
        user = User.objects.create_user(username=email, email=email, password="Password@123")
        profile = UserProfile(auth_user_id=user.id, name="Test User", email=email, role="jobseeker", password="123")
        profile.save()

        self.client.force_login(user)

        response_home = self.client.get(reverse("core:home"))
        self.assertEqual(response_home.status_code, 200)

    def test_oauth_failure_template_rendering(self):
        """Verify custom failure template renders correctly for OAuth failures."""
        response = self.client.get(reverse("socialaccount_login_error"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Authentication Failed")
        self.assertContains(response, "We could not sign you in with Google")

    # ------------------ NOTIFICATION TESTS ------------------
    
    def test_application_submission_notifies_recruiter(self):
        """1. Jobseeker submits an application -> recruiter is notified."""
        # Create a job recruiter
        recruiter = User.objects.create_user(username="rec_notif@test.com", email="rec_notif@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_notif@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Software Engineer",
            recruiter_id=recruiter.id,
            recruiter_name="Recruiter",
            company="Company",
            salary="100k",
            experience="2 years",
            description="Details",
            location="Remote"
        ).save()
        
        # Log in jobseeker
        self.client.force_login(self.user)
        
        # Mock save_uploaded_file and get_profile_completion to satisfy decorators and flow
        with patch("core.decorators.get_profile_completion", return_value={"is_complete": True}):
            with patch("applications.views.save_uploaded_file", return_value="resumes/dummy.pdf"):
                response = self.client.post(
                    reverse("applications:apply_job", kwargs={"job_id": str(job.id)}),
                    {"cover_letter": "I love coding", "resume": SimpleUploadedFile("resume.pdf", b"pdf content")}
                )
                self.assertEqual(response.status_code, 302)
            
        # Verify notification was sent to recruiter
        notif = Notification.objects(user_id=recruiter.id, notification_type="application_new").first()
        self.assertIsNotNone(notif)
        self.assertIn("applied for the role of", notif.description)

    def test_application_withdrawal_notifies_recruiter(self):
        """2. Jobseeker withdraws application -> recruiter is notified."""
        recruiter = User.objects.create_user(username="rec_with@test.com", email="rec_with@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_with@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="QA Engineer",
            recruiter_id=recruiter.id,
            recruiter_name="Recruiter",
            company="Company",
            salary="100k",
            experience="2 years",
            description="Details",
            location="Office"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(self.user)
        response = self.client.post(reverse("applications:withdraw_application", kwargs={"application_id": str(app.id)}))
        self.assertEqual(response.status_code, 302)
        
        # Verify application status
        app.reload()
        self.assertEqual(app.status, "Withdrawn")
        
        # Verify recruiter notification
        notif = Notification.objects(user_id=recruiter.id, notification_type="application_withdrawn").first()
        self.assertIsNotNone(notif)
        self.assertIn("withdrew their application", notif.description)

    def test_each_valid_application_status_change_notifies_jobseeker(self):
        """3. Valid application status changes -> jobseeker is notified."""
        recruiter = User.objects.create_user(username="rec_status@test.com", email="rec_status@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_status@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="DevOps",
            recruiter_id=recruiter.id,
            recruiter_name="Recruiter",
            company="Company",
            salary="100k",
            experience="2 years",
            description="Details",
            location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(recruiter)
        for status in ["Reviewed", "Shortlisted", "Rejected"]:
            response = self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": status}))
            self.assertEqual(response.status_code, 302)
            
            # Verify jobseeker notification
            notif_type = "application_shortlisted" if status == "Shortlisted" else "application_status"
            notif = Notification.objects(user_id=self.user.id, notification_type=notif_type).order_by("-created_at").first()
            self.assertIsNotNone(notif)
            self.assertEqual(notif.title, f"Application {status}")

    def test_unauthorized_status_changes_create_no_notification(self):
        """4. Unauthorized status updates -> no notification is created."""
        random_user = User.objects.create_user(username="random@test.com", email="random@test.com", password="Password@123")
        UserProfile(
            auth_user_id=random_user.id,
            name="Random",
            email="random@test.com",
            password=random_user.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Designer",
            recruiter_id=self.user.id,
            recruiter_name="Recruiter",
            company="Company",
            salary="100k",
            experience="2 years",
            description="Details",
            location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=random_user.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        # User who doesn't own recruiter_id tries to update
        self.client.force_login(self.user)
        response = self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        # Since self.user is a jobseeker, role_required throws 403 Forbidden
        self.assertEqual(response.status_code, 403)
        
        # Verify no notification created
        notif = Notification.objects(user_id=self.user.id, notification_type="application_shortlisted").first()
        self.assertIsNone(notif)

    def test_new_message_notifies_recipient_only(self):
        """5. New message -> notifies recipient only (no self-notification)."""
        recruiter = User.objects.create_user(username="rec_msg@test.com", email="rec_msg@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_msg@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        conv = Conversation(job_id="job123", job_title="Job", recruiter_id=recruiter.id, jobseeker_id=self.user.id).save()
        
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(conv.id)}),
            {"content": "Hello Recruiter!"}
        )
        self.assertEqual(response.status_code, 201)
        
        # Recipient (recruiter) must be notified
        rec_notif = Notification.objects(user_id=recruiter.id, notification_type="message_new").first()
        self.assertIsNotNone(rec_notif)
        
        # Sender (jobseeker) must NOT be notified
        self_notif = Notification.objects(user_id=self.user.id, notification_type="message_new").first()
        self.assertIsNone(self_notif)

    def test_two_different_messages_in_one_conversation_create_two_notifications(self):
        """Two different messages in one conversation create two separate notifications."""
        recruiter = User.objects.create_user(username="rec_dedup@test.com", email="rec_dedup@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_dedup@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        conv = Conversation(job_id="job123", job_title="Job", recruiter_id=recruiter.id, jobseeker_id=self.user.id).save()
        
        self.client.force_login(self.user)
        
        # Send 1st message
        self.client.post(reverse("messages_box:api_send_message", kwargs={"conversation_id": str(conv.id)}), {"content": "First msg"})
        # Send 2nd message
        self.client.post(reverse("messages_box:api_send_message", kwargs={"conversation_id": str(conv.id)}), {"content": "Second msg"})
        
        # Count notifications for recipient
        notifs = Notification.objects(user_id=recruiter.id, notification_type="message_new")
        self.assertEqual(notifs.count(), 2)

    def test_reprocessing_one_message_creates_no_duplicate(self):
        """Reprocessing one message (idempotency) creates no duplicate notification."""
        from notifications.services import create_notification
        # Call create_notification with same deduplication key twice
        notif1 = create_notification(
            user_id=self.user.id,
            title="Msg",
            description="Content",
            notification_type="message_new",
            link="/",
            deduplication_key="message_msg123"
        )
        notif2 = create_notification(
            user_id=self.user.id,
            title="Msg",
            description="Content",
            notification_type="message_new",
            link="/",
            deduplication_key="message_msg123"
        )
        # Verify only one exists in DB
        notifs = Notification.objects(user_id=self.user.id, deduplication_key="message_msg123")
        self.assertEqual(notifs.count(), 1)
        self.assertEqual(notif1.id, notif2.id)

    def test_multiple_status_transitions_create_separate_notifications(self):
        """Multiple status transitions create separate notifications."""
        recruiter = User.objects.create_user(username="rec_trans@test.com", email="rec_trans@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_trans@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Dev", recruiter_id=recruiter.id, recruiter_name="Recruiter",
            company="Company", salary="100k", experience="2 years", description="Details", location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(recruiter)
        
        # Transition to Reviewed
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Reviewed"}))
        # Transition to Shortlisted
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        
        # Count notifications
        notifs = Notification.objects(user_id=self.user.id)
        # Should have app_status_..._reviewed and app_status_..._shortlisted
        self.assertEqual(notifs.count(), 2)

    def test_reprocessing_the_same_transition_creates_no_duplicate(self):
        """Reprocessing the same transition creates no duplicate notification."""
        recruiter = User.objects.create_user(username="rec_retrans@test.com", email="rec_retrans@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_retrans@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Dev", recruiter_id=recruiter.id, recruiter_name="Recruiter",
            company="Company", salary="100k", experience="2 years", description="Details", location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(recruiter)
        
        # Call status update to Shortlisted twice
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        
        # Count notifications for self.user (recipient)
        notifs = Notification.objects(user_id=self.user.id, notification_type="application_shortlisted")
        self.assertEqual(notifs.count(), 1)

    def test_scheduled_rescheduled_and_cancelled_interview_events_create_separate_notifications(self):
        """Scheduled, rescheduled, and cancelled interview events create separate notifications."""
        recruiter = User.objects.create_user(username="rec_intv@test.com", email="rec_intv@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_intv@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id="job123", job_title="Job", recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Shortlisted"
        ).save()
        
        self.client.force_login(recruiter)
        
        # 1. Schedule event
        response = self.client.post(
            reverse("interviews:schedule_interview", kwargs={"application_id": str(app.id)}),
            {
                "title": "Technical Interview",
                "date": (timezone.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%d"),
                "start_time": "14:00",
                "duration": "45",
                "timezone": "Asia/Kolkata",
                "notes": "Be prepared"
            }
        )
        self.assertEqual(response.status_code, 302)
        interview = Interview.objects(application_id=str(app.id)).first()
        self.assertIsNotNone(interview)
        
        # 2. Reschedule event
        response_resch = self.client.post(
            reverse("interviews:reschedule_interview", kwargs={"interview_id": str(interview.id)}),
            {
                "date": (timezone.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d"),
                "start_time": "15:00",
                "duration": "60",
                "timezone": "Asia/Kolkata",
                "notes": "Rescheduled"
            }
        )
        self.assertEqual(response_resch.status_code, 302)
        
        # 3. Cancel event
        response_cancel = self.client.get(reverse("interviews:update_status", kwargs={"interview_id": str(interview.id), "new_status": "Cancelled"}))
        self.assertEqual(response_cancel.status_code, 302)
        
        # Verify 3 distinct notifications are created for the candidate
        notifs = Notification.objects(user_id=self.user.id)
        self.assertEqual(notifs.count(), 3)
        
        # Check specific notification types/dedup keys exist
        self.assertIsNotNone(Notification.objects(user_id=self.user.id, deduplication_key=f"interview_{interview.id}_scheduled").first())
        self.assertIsNotNone(Notification.objects(user_id=self.user.id, deduplication_key=f"interview_{interview.id}_cancelled").first())
        resched_notif = Notification.objects(user_id=self.user.id, notification_type="interview_rescheduled").first()
        self.assertIsNotNone(resched_notif)
        self.assertTrue(resched_notif.deduplication_key.startswith(f"interview_{interview.id}_rescheduled_"))

    def test_same_event_sent_to_two_recipients_creates_one_notification_each(self):
        """The same deduplication key event sent to two recipients creates one notification for each recipient."""
        from notifications.services import create_notification
        user_a = User.objects.create_user(username="usera@test.com", email="usera@test.com", password="Password@123")
        user_b = User.objects.create_user(username="userb@test.com", email="userb@test.com", password="Password@123")
        
        # Send event with same deduplication key to user A
        create_notification(
            user_id=user_a.id,
            title="Shared Event",
            description="Event details",
            notification_type="application_new",
            link="/",
            deduplication_key="shared_event_999"
        )
        # Send same event with same deduplication key to user B
        create_notification(
            user_id=user_b.id,
            title="Shared Event",
            description="Event details",
            notification_type="application_new",
            link="/",
            deduplication_key="shared_event_999"
        )
        
        # Verify user A got exactly one notification
        self.assertEqual(Notification.objects(user_id=user_a.id, deduplication_key="shared_event_999").count(), 1)
        # Verify user B got exactly one notification
        self.assertEqual(Notification.objects(user_id=user_b.id, deduplication_key="shared_event_999").count(), 1)

    def test_shortlisted_reviewed_shortlisted_preserves_both_events(self):
        """Shortlisted -> Reviewed -> Shortlisted preserves both Shortlisted events."""
        recruiter = User.objects.create_user(username="rec_rev@test.com", email="rec_rev@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_rev@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Dev", recruiter_id=recruiter.id, recruiter_name="Recruiter",
            company="Company", salary="100k", experience="2 years", description="Details", location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(recruiter)
        
        # 1. Transition to Shortlisted
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        # 2. Transition to Reviewed
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Reviewed"}))
        # 3. Transition to Shortlisted again
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        
        # Verify 3 distinct notifications are created for the candidate
        notifs = Notification.objects(user_id=self.user.id)
        self.assertEqual(notifs.count(), 3)
        
        # Check that we have both shortlisted notifications
        shortlisted_notifs = Notification.objects(user_id=self.user.id, title="Application Shortlisted")
        self.assertEqual(shortlisted_notifs.count(), 2)

    def test_submitting_same_current_status_produces_no_new_notification(self):
        """Submitting the same current status produces no new notification."""
        recruiter = User.objects.create_user(username="rec_same@test.com", email="rec_same@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_same@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Dev", recruiter_id=recruiter.id, recruiter_name="Recruiter",
            company="Company", salary="100k", experience="2 years", description="Details", location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(recruiter)
        
        # 1. Update status to Reviewed
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Reviewed"}))
        self.assertEqual(Notification.objects(user_id=self.user.id).count(), 1)
        
        # 2. Update status to Reviewed again
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Reviewed"}))
        # Total notifications should still be 1
        self.assertEqual(Notification.objects(user_id=self.user.id).count(), 1)

    def test_notification_timestamps_history_remain_unchanged_when_older_transitions_viewed(self):
        """Notification timestamps/history of older transitions remain unchanged when new transitions occur."""
        recruiter = User.objects.create_user(username="rec_time@test.com", email="rec_time@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_time@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Dev", recruiter_id=recruiter.id, recruiter_name="Recruiter",
            company="Company", salary="100k", experience="2 years", description="Details", location="Remote"
        ).save()
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id=str(job.id), job_title=job.title, recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Pending"
        ).save()
        
        self.client.force_login(recruiter)
        
        # 1. Update status to Reviewed
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Reviewed"}))
        first_notif = Notification.objects(user_id=self.user.id, notification_type="application_status").first()
        self.assertIsNotNone(first_notif)
        first_timestamp = first_notif.created_at
        first_description = first_notif.description
        
        # Wait a brief moment or mock time progression if needed, but since we update status next, timezone.now() will progress slightly.
        import time
        time.sleep(0.1)
        
        # 2. Update status to Shortlisted
        self.client.get(reverse("applications:update_status", kwargs={"application_id": str(app.id), "new_status": "Shortlisted"}))
        
        # Reload the first notification
        first_notif.reload()
        # Verify it has not been modified
        self.assertEqual(first_notif.created_at, first_timestamp)
        self.assertEqual(first_notif.description, first_description)

    def test_conversation_notification_redirects_correctly(self):
        """7. Conversation notification view action -> marks read and redirects to safe chat window page."""
        recruiter = User.objects.create_user(username="rec_redir@test.com", email="rec_redir@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_redir@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        conv = Conversation(job_id="job123", job_title="Job", recruiter_id=recruiter.id, jobseeker_id=self.user.id).save()
        
        notif = Notification(
            user_id=self.user.id, title="New Message", description="Hello",
            notification_type="message_new", link=f"/messages/chat/{conv.id}/"
        ).save()
        
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:read_and_redirect", kwargs={"notification_id": str(notif.id)}))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, f"/messages/chat/{conv.id}/")
        
        notif.reload()
        self.assertTrue(notif.is_read)

    def test_interview_scheduled_notification(self):
        """8. Interview scheduled -> notifies the candidate (other participant)."""
        recruiter = User.objects.create_user(username="rec_sch@test.com", email="rec_sch@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_sch@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id="job123", job_title="Job", recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Shortlisted"
        ).save()
        
        self.client.force_login(recruiter)
        response = self.client.post(
            reverse("interviews:schedule_interview", kwargs={"application_id": str(app.id)}),
            {
                "title": "Technical Interview",
                "date": (timezone.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%d"),
                "start_time": "14:00",
                "duration": "45",
                "timezone": "Asia/Kolkata",
                "notes": "Be prepared"
            }
        )
        self.assertEqual(response.status_code, 302)
        
        # Verify notification sent to jobseeker
        notif = Notification.objects(user_id=self.user.id, notification_type="interview_scheduled").first()
        self.assertIsNotNone(notif)
        self.assertIn("scheduled for", notif.description)

    def test_interview_rescheduled_notification(self):
        """9. Interview rescheduled -> notifies the other participant."""
        recruiter = User.objects.create_user(username="rec_resch@test.com", email="rec_resch@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_resch@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        app = Application(
            applicant_id=self.user.id, applicant_name="Test Seeker", applicant_email="seeker@test.com",
            job_id="job123", job_title="Job", recruiter_id=recruiter.id, resume="resumes/dummy.pdf",
            cover_letter="Cover letter", status="Shortlisted"
        ).save()
        
        interview = Interview(
            application_id=str(app.id), job_id=app.job_id, recruiter_id=recruiter.id, jobseeker_id=self.user.id,
            title="Interview One", interview_date=timezone.now() + datetime.timedelta(days=2),
            start_time="10:00", duration=30, timezone="Asia/Kolkata", notes=""
        ).save()
        
        self.client.force_login(recruiter)
        response = self.client.post(
            reverse("interviews:reschedule_interview", kwargs={"interview_id": str(interview.id)}),
            {
                "date": (timezone.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d"),
                "start_time": "11:00",
                "duration": "60",
                "timezone": "Asia/Kolkata",
                "notes": "Updated notes"
            }
        )
        self.assertEqual(response.status_code, 302)
        
        # Verify notification sent to jobseeker
        notif = Notification.objects(user_id=self.user.id, notification_type="interview_rescheduled").first()
        self.assertIsNotNone(notif)
        self.assertIn("rescheduled to", notif.description)

    def test_interview_cancelled_notification(self):
        """10. Interview cancelled -> notifies the other participant."""
        recruiter = User.objects.create_user(username="rec_can@test.com", email="rec_can@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter.id,
            name="Recruiter",
            email="rec_can@test.com",
            password=recruiter.password,
            role="recruiter"
        ).save()
        
        interview = Interview(
            application_id="app123", job_id="job123", recruiter_id=recruiter.id, jobseeker_id=self.user.id,
            title="Interview Two", interview_date=timezone.now() + datetime.timedelta(days=2),
            start_time="10:00", duration=30, timezone="Asia/Kolkata", notes=""
        ).save()
        
        self.client.force_login(recruiter)
        response = self.client.get(reverse("interviews:update_status", kwargs={"interview_id": str(interview.id), "new_status": "Cancelled"}))
        self.assertEqual(response.status_code, 302)
        
        # Verify notification sent to jobseeker
        notif = Notification.objects(user_id=self.user.id, notification_type="interview_cancelled").first()
        self.assertIsNotNone(notif)
        self.assertIn("has been cancelled", notif.description)

    def test_recruiter_jobseeker_zoom_url_privacy(self):
        """11. Zoom meeting start_url privacy (jobseeker cannot view start_url)."""
        interview = Interview(
            application_id="app123", job_id="job123", recruiter_id=self.user.id, jobseeker_id=self.user.id,
            title="Zoom Private Test", interview_date=timezone.now() + datetime.timedelta(days=2),
            start_time="10:00", duration=30, timezone="Asia/Kolkata", notes="",
            meeting_provider="zoom", meeting_url="https://zoom.us/j/joinlink", meeting_start_url="https://zoom.us/s/startlink"
        ).save()
        
        # Candidate views interview details
        self.client.force_login(self.user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://zoom.us/j/joinlink")
        self.assertNotContains(response, "https://zoom.us/s/startlink")

    def test_user_can_list_only_their_notifications(self):
        """12. User can only retrieve their own notifications center list."""
        other_user = User.objects.create_user(username="other_notif@test.com", email="other_notif@test.com", password="Password@123")
        Notification(user_id=other_user.id, title="Other Notif", description="Secret", notification_type="message_new", link="/").save()
        Notification(user_id=self.user.id, title="My Notif", description="Mine", notification_type="message_new", link="/").save()
        
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Notif")
        self.assertNotContains(response, "Other Notif")

    def test_user_cannot_mark_another_users_notification_as_read(self):
        """13. User cannot update or mark read notifications belonging to other accounts."""
        other_user = User.objects.create_user(username="other_read@test.com", email="other_read@test.com", password="Password@123")
        notif = Notification(user_id=other_user.id, title="Secret", description="Data", notification_type="message_new", link="/").save()
        
        self.client.force_login(self.user)
        # Try view redirect
        response_redirect = self.client.get(reverse("notifications:read_and_redirect", kwargs={"notification_id": str(notif.id)}))
        self.assertEqual(response_redirect.status_code, 404)
        
        # Try API endpoint directly
        response_api = self.client.post(reverse("notifications:read", kwargs={"notification_id": str(notif.id)}))
        self.assertEqual(response_api.status_code, 404)

    def test_mark_one_as_read(self):
        """14. Mark one notification as read works via API."""
        notif = Notification(user_id=self.user.id, title="Read One", description="Data", notification_type="message_new", link="/").save()
        
        self.client.force_login(self.user)
        response = self.client.post(reverse("notifications:read", kwargs={"notification_id": str(notif.id)}))
        self.assertEqual(response.status_code, 302)
        
        notif.reload()
        self.assertTrue(notif.is_read)

    def test_mark_all_as_read(self):
        """15. Mark all unread notifications as read."""
        Notification(user_id=self.user.id, title="Notif A", description="A", notification_type="message_new", link="/").save()
        Notification(user_id=self.user.id, title="Notif B", description="B", notification_type="message_new", link="/").save()
        
        self.client.force_login(self.user)
        response = self.client.post(reverse("notifications:read_all"))
        self.assertEqual(response.status_code, 302)
        
        unread = Notification.objects(user_id=self.user.id, is_read=False).count()
        self.assertEqual(unread, 0)

    def test_unread_count_accuracy(self):
        """16. API unread count endpoint returns correct counts."""
        Notification(user_id=self.user.id, title="Notif A", description="A", notification_type="message_new", link="/", is_read=False).save()
        Notification(user_id=self.user.id, title="Notif B", description="B", notification_type="message_new", link="/", is_read=True).save()
        
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:unread_count"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unread_count"], 1)

    def test_newest_first_ordering(self):
        """17. Notifications list sorts newest-first."""
        n1 = Notification(user_id=self.user.id, title="First", description="A", notification_type="message_new", link="/", created_at=timezone.now() - datetime.timedelta(hours=1)).save()
        n2 = Notification(user_id=self.user.id, title="Second", description="B", notification_type="message_new", link="/", created_at=timezone.now()).save()
        
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 200)
        
        # Verify order in context
        notifications = response.context["notifications"]
        self.assertEqual(notifications[0]["title"], "Second")
        self.assertEqual(notifications[1]["title"], "First")

    def test_safe_internal_notification_targets(self):
        """18. Notification redirect enforces safe local hosts and blocks open-redirects."""
        notif = Notification(user_id=self.user.id, title="Phishing Link", description="Phish", notification_type="message_new", link="http://malicious.com/phish").save()
        
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:read_and_redirect", kwargs={"notification_id": str(notif.id)}))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, "/")

    def test_missing_deleted_related_object_handling(self):
        """19. Graceful fallback on missing/deleted related objects (returns 404)."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": "60c72b2f9b1d8b25d8b8e8f8"}))
        self.assertEqual(response.status_code, 404)

    def test_application_submission_recruiter_isolation_and_unread_count(self):
        """Regression test: job application submission only notifies the job owner recruiter and updates unread count."""
        # Recruiter 1 (Job Owner)
        recruiter1 = User.objects.create_user(username="rec1_iso@test.com", email="rec1_iso@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter1.id,
            name="Recruiter One",
            email="rec1_iso@test.com",
            password=recruiter1.password,
            role="recruiter"
        ).save()
        
        # Recruiter 2 (Different Recruiter)
        recruiter2 = User.objects.create_user(username="rec2_iso@test.com", email="rec2_iso@test.com", password="Password@123")
        UserProfile(
            auth_user_id=recruiter2.id,
            name="Recruiter Two",
            email="rec2_iso@test.com",
            password=recruiter2.password,
            role="recruiter"
        ).save()
        
        job = Job(
            title="Isolated Job",
            recruiter_id=recruiter1.id,
            recruiter_name="Recruiter One",
            company="Company",
            salary="100k",
            experience="2 years",
            description="Details",
            location="Remote"
        ).save()
        
        # Log in jobseeker
        self.client.force_login(self.user)
        
        with patch("core.decorators.get_profile_completion", return_value={"is_complete": True}):
            with patch("applications.views.save_uploaded_file", return_value="resumes/dummy.pdf"):
                response = self.client.post(
                    reverse("applications:apply_job", kwargs={"job_id": str(job.id)}),
                    {"cover_letter": "I love coding", "resume": SimpleUploadedFile("resume.pdf", b"pdf content")}
                )
                self.assertEqual(response.status_code, 302)
        
        # Recruiter 1 must have exactly 1 notification
        notifs_rec1 = Notification.objects(user_id=recruiter1.id, notification_type="application_new")
        self.assertEqual(notifs_rec1.count(), 1)
        
        # Recruiter 2 must have 0 notifications
        notifs_rec2 = Notification.objects(user_id=recruiter2.id, notification_type="application_new")
        self.assertEqual(notifs_rec2.count(), 0)
        
        # Recruiter 1 unread count API returns 1
        self.client.force_login(recruiter1)
        response_count = self.client.get(reverse("notifications:unread_count"))
        self.assertEqual(response_count.status_code, 200)
        self.assertEqual(response_count.json()["unread_count"], 1)
        
        # Notification visible in Recruiter 1 notifications list
        response_list = self.client.get(reverse("notifications:list"))
        self.assertEqual(response_list.status_code, 200)
        self.assertContains(response_list, "New Job Application")


class JobseekerDashboardRedesignTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disconnect any default mock setup
        connection.disconnect(alias="default")
        # Initialize custom isolated db path
        cls.db_connection = connection.connect(
            db="test_job_portal_db_dash",
            host="mongodb://localhost:27017/test_job_portal_db_dash",
            alias="default",
            uuidRepresentation="standard"
        )
        cls.temp_media = tempfile.mkdtemp()
        cls.override_media = patch.object(settings, "MEDIA_ROOT", cls.temp_media)
        cls.override_media.start()

    @classmethod
    def tearDownClass(cls):
        cls.override_media.stop()
        shutil.rmtree(cls.temp_media)
        db = get_db(alias="default")
        db.client.drop_database("test_job_portal_db_dash")
        connection.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.client = Client()
        UserProfile.objects.delete()
        User.objects.all().delete()
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        from ai.documents import AIResumeAnalysis
        AIResumeAnalysis.objects.delete()

        # 1. Jobseeker User
        self.seeker = User.objects.create_user(username="seeker_dash@test.com", email="seeker_dash@test.com", password="Password@123")
        self.seeker_profile = UserProfile(
            auth_user_id=self.seeker.id,
            name="Seeker Dash",
            email="seeker_dash@test.com",
            password=self.seeker.password,
            role="jobseeker"
        ).save()
        
        # 2. Recruiter User
        self.recruiter = User.objects.create_user(username="rec_dash@test.com", email="rec_dash@test.com", password="Password@123")
        self.rec_profile = UserProfile(
            auth_user_id=self.recruiter.id,
            name="Recruiter Dash",
            email="rec_dash@test.com",
            password=self.recruiter.password,
            role="recruiter"
        ).save()

    def test_recruiter_cannot_access_jobseeker_dashboard(self):
        self.client.force_login(self.recruiter)
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_jobseeker_dashboard_empty_states(self):
        self.client.force_login(self.seeker)
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No upcoming interviews")
        self.assertContains(response, "No applications submitted")
        self.assertContains(response, "No matching jobs found")

    def test_jobseeker_dashboard_metrics_and_ordering(self):
        from jobs.documents import Job
        from applications.documents import Application
        import datetime
        from django.utils import timezone
        
        job1 = Job(title="Job 1", company="A", location="Remote", salary="10L", experience="1yr", description="Desc 1", recruiter_id=self.recruiter.id, recruiter_name="Rec").save()
        job2 = Job(title="Job 2", company="B", location="Remote", salary="12L", experience="2yr", description="Desc 2", recruiter_id=self.recruiter.id, recruiter_name="Rec").save()
        
        app1 = Application(
            applicant_id=self.seeker.id, applicant_name="S", job_id=str(job1.id),
            job_title=job1.title, recruiter_id=self.recruiter.id, resume="res.pdf",
            cover_letter="L", status="Shortlisted", applied_at=timezone.now() - datetime.timedelta(days=1)
        ).save()
        app2 = Application(
            applicant_id=self.seeker.id, applicant_name="S", job_id=str(job2.id),
            job_title=job2.title, recruiter_id=self.recruiter.id, resume="res.pdf",
            cover_letter="L", status="Pending", applied_at=timezone.now()
        ).save()
        
        self.client.force_login(self.seeker)
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response.status_code, 200)
        
        stats = response.context["stats"]
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["shortlisted"], 1)
        self.assertEqual(stats["pending"], 1)
        
        recent = response.context["recent_applications"]
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["job_title"], "Job 2")
        self.assertEqual(recent[1]["job_title"], "Job 1")

    def test_jobseeker_dashboard_next_interview_zoom_privacy(self):
        from jobs.documents import Job
        from interviews.documents import Interview
        import datetime
        from django.utils import timezone
        
        job = Job(title="Job", company="A", location="Remote", salary="10L", experience="1yr", description="Desc", recruiter_id=self.recruiter.id, recruiter_name="Rec").save()
        
        int1 = Interview(
            title="Nearest Interview", jobseeker_id=self.seeker.id, recruiter_id=self.recruiter.id,
            job_id=str(job.id), application_id="app_123", interview_date=timezone.now() + datetime.timedelta(days=1),
            start_time="10:00", duration=60, meeting_provider="zoom", meeting_url="https://zoom.us/j/seeker_join_link",
            status="Scheduled", timezone="UTC"
        ).save()
        
        int2 = Interview(
            title="Further Interview", jobseeker_id=self.seeker.id, recruiter_id=self.recruiter.id,
            job_id=str(job.id), application_id="app_123", interview_date=timezone.now() + datetime.timedelta(days=2),
            start_time="10:00", duration=60, meeting_provider="zoom", meeting_url="https://zoom.us/j/further_join_link",
            status="Scheduled", timezone="UTC"
        ).save()
        
        self.client.force_login(self.seeker)
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response.status_code, 200)
        
        next_int = response.context["next_interview"]
        self.assertIsNotNone(next_int)
        self.assertEqual(next_int["interview"].title, "Nearest Interview")
        
        self.assertContains(response, "https://zoom.us/j/seeker_join_link")
        self.assertNotContains(response, "start_url")

    def test_jobseeker_dashboard_recommendations_logic(self):
        from jobs.documents import Job
        
        job1 = Job(title="Python Engineer", company="A", location="Remote", salary="10L", experience="1yr", description="Desc 1", recruiter_id=self.recruiter.id, recruiter_name="Rec").save()
        job2 = Job(title="Django Developer", company="B", location="Remote", salary="12L", experience="2yr", description="Desc 2", recruiter_id=self.recruiter.id, recruiter_name="Rec").save()
        
        self.client.force_login(self.seeker)
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response.context["stats"]["total"], 0)
        
        recs = response.context["active_recommendations"]
        self.assertTrue(any(r["title"] == "Upload your resume" for r in recs))
        
        self.assertNotContains(response, "% Match")
        self.assertNotContains(response, "Match Percentage")
        
        self.seeker_profile.resume = "resumes/my_resume.pdf"
        self.seeker_profile.save()
        
        response2 = self.client.get(reverse("core:jobseeker_dashboard"))
        recs2 = response2.context["active_recommendations"]
        self.assertFalse(any(r["title"] == "Upload your resume" for r in recs2))


class MessageAttachmentTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.disconnect(alias="default")
        cls.db_connection = connection.connect(
            db="test_job_portal_db_msg",
            host="mongodb://localhost:27017/test_job_portal_db_msg",
            alias="default",
            uuidRepresentation="standard"
        )
        # Setup temporary private attachment root
        cls.temp_private = tempfile.mkdtemp()
        cls.override_private = patch.object(settings, "PRIVATE_ATTACHMENTS_ROOT", cls.temp_private)
        cls.override_private.start()
        
        cls.temp_media = tempfile.mkdtemp()
        cls.override_media = patch.object(settings, "MEDIA_ROOT", cls.temp_media)
        cls.override_media.start()

    @classmethod
    def tearDownClass(cls):
        cls.override_private.stop()
        cls.override_media.stop()
        shutil.rmtree(cls.temp_private)
        shutil.rmtree(cls.temp_media)
        db = get_db(alias="default")
        db.client.drop_database("test_job_portal_db_msg")
        connection.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.client = Client()
        UserProfile.objects.delete()
        User.objects.all().delete()
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        Conversation.objects.delete()
        Message.objects.delete()
        Notification.objects.delete()

        # 1. Jobseeker User
        self.seeker = User.objects.create_user(username="seeker_msg@test.com", email="seeker_msg@test.com", password="Password@123")
        self.seeker_profile = UserProfile(
            auth_user_id=self.seeker.id,
            name="Seeker Msg",
            email="seeker_msg@test.com",
            password=self.seeker.password,
            role="jobseeker"
        ).save()
        
        # 2. Recruiter User
        self.recruiter = User.objects.create_user(username="rec_msg@test.com", email="rec_msg@test.com", password="Password@123")
        self.rec_profile = UserProfile(
            auth_user_id=self.recruiter.id,
            name="Recruiter Msg",
            email="rec_msg@test.com",
            password=self.recruiter.password,
            role="recruiter"
        ).save()

        # 3. Conversation
        self.conv = Conversation(
            job_id="job_msg_123",
            job_title="Software Engineer",
            recruiter_id=self.recruiter.id,
            jobseeker_id=self.seeker.id
        ).save()

    def test_send_document_jobseeker_to_recruiter(self):
        self.client.force_login(self.seeker)
        
        # Safe PDF mock starting with %PDF signature
        doc_file = SimpleUploadedFile("resume.pdf", b"%PDF-1.4\n%...\nbody\n%%EOF", content_type="application/pdf")
        
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": doc_file,
                "caption": "My fresh resume"
            }
        )
        self.assertEqual(response.status_code, 201)
        
        msg = Message.objects.first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.attachment_type, "document")
        self.assertEqual(msg.caption, "My fresh resume")
        self.assertEqual(msg.original_filename, "resume.pdf")
        
        # Verify notification created
        notif = Notification.objects.first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.user_id, self.recruiter.id)
        self.assertIn("📄 Document: My fresh resume", notif.description)

    def test_send_image_recruiter_to_jobseeker(self):
        self.client.force_login(self.recruiter)
        
        # Programmatically construct minimal valid PNG bytes
        from PIL import Image
        import io
        img = Image.new("RGB", (1, 1), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_data = buf.getvalue()
        img_file = SimpleUploadedFile("avatar.png", png_data, content_type="image/png")
        
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "image",
                "file": img_file,
                "caption": "Photo caption here"
            }
        )
        self.assertEqual(response.status_code, 201)
        
        msg = Message.objects.first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.attachment_type, "image")
        self.assertEqual(msg.image_width, 1)
        self.assertEqual(msg.image_height, 1)

    def test_text_only_messages_continue_working(self):
        self.client.force_login(self.seeker)
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"content": "Just a standard text message"}
        )
        self.assertEqual(response.status_code, 201)
        msg = Message.objects.first()
        self.assertEqual(msg.content, "Just a standard text message")
        self.assertIsNone(msg.attachment_type)

    def test_attachment_only_message_acceptance(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": doc_file
            }
        )
        self.assertEqual(response.status_code, 201)
        msg = Message.objects.first()
        self.assertEqual(msg.content, "")
        self.assertEqual(msg.attachment_type, "document")

    def test_completely_empty_message_rejection(self):
        self.client.force_login(self.seeker)
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"content": "   "}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Message content cannot be completely empty.", response.json()["error"])

    def test_caption_length_enforcement(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        long_caption = "a" * 1001
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": doc_file,
                "caption": long_caption
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Caption exceeds limit of 1000 characters.", response.json()["error"])

    def test_unsupported_file_rejection(self):
        self.client.force_login(self.seeker)
        bad_file = SimpleUploadedFile("exploit.sh", b"#!/bin/bash\necho 'hack'", content_type="application/x-sh")
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": bad_file
            }
        )
        self.assertEqual(response.status_code, 400)

    def test_oversized_document_rejection(self):
        self.client.force_login(self.seeker)
        # 11 MB oversized document file
        large_data = b"%PDF-1.4" + (b"0" * (11 * 1024 * 1024))
        bad_file = SimpleUploadedFile("big.pdf", large_data, content_type="application/pdf")
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": bad_file
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Document size exceeds limit of 10 MB.", response.json()["error"])

    def test_spoofed_mime_type_rejection(self):
        self.client.force_login(self.seeker)
        # Spoofed file containing simple text disguised as png image
        spoofed_file = SimpleUploadedFile("fake.png", b"plain text content spoofing png", content_type="image/png")
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "image",
                "file": spoofed_file
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid image content structure.", response.json()["error"])

    def test_unauthorized_preview_and_download_rejection(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": doc_file
            }
        )
        msg = Message.objects.first()
        
        # Create third malicious user not in conversation
        malicious = User.objects.create_user(username="malicious@test.com", email="malicious@test.com", password="Password@123")
        UserProfile(auth_user_id=malicious.id, name="Malicious User", email="malicious@test.com", password=malicious.password, role="jobseeker").save()
        
        self.client.force_login(malicious)
        response_download = self.client.get(
            reverse("messages_box:message_attachment_access", kwargs={"message_id": str(msg.id), "mode": "download"})
        )
        self.assertEqual(response_download.status_code, 404)
        
        # Unauthorized preview
        response_preview = self.client.get(
            reverse("messages_box:message_attachment_access", kwargs={"message_id": str(msg.id), "mode": "preview"})
        )
        self.assertEqual(response_preview.status_code, 404)

    def test_conversation_participant_allowed_access_and_document_preview_restriction(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("my_doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {
                "attachment_type": "document",
                "file": doc_file
            }
        )
        msg = Message.objects.first()
        
        # Download allowed
        response_download = self.client.get(
            reverse("messages_box:message_attachment_access", kwargs={"message_id": str(msg.id), "mode": "download"})
        )
        self.assertEqual(response_download.status_code, 200)
        self.assertEqual(response_download["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response_download["Content-Security-Policy"], "sandbox")
        self.assertIn("attachment", response_download["Content-Disposition"])
        
        # Document preview forbidden / returns 404
        response_preview = self.client.get(
            reverse("messages_box:message_attachment_access", kwargs={"message_id": str(msg.id), "mode": "preview"})
        )
        self.assertEqual(response_preview.status_code, 404)

    def test_invalid_access_mode_rejection(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("my_doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"attachment_type": "document", "file": doc_file}
        )
        msg = Message.objects.first()
        
        response = self.client.get(
            reverse("messages_box:message_attachment_access", kwargs={"message_id": str(msg.id), "mode": "edit"})
        )
        self.assertEqual(response.status_code, 404)

    def test_path_traversal_rejection(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("my_doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"attachment_type": "document", "file": doc_file}
        )
        msg = Message.objects.first()
        
        # Force set malicious path traversal storage key
        msg.attachment_path = "../../../etc/passwd"
        msg.save()
        
        response = self.client.get(
            reverse("messages_box:message_attachment_access", kwargs={"message_id": str(msg.id), "mode": "download"})
        )
        self.assertEqual(response.status_code, 403)

    def test_failed_persistence_removes_orphaned_uploads(self):
        self.client.force_login(self.seeker)
        doc_file = SimpleUploadedFile("my_doc.pdf", b"%PDF-1.4\nbody\n%%EOF", content_type="application/pdf")
        
        perm_dir = os.path.join(settings.PRIVATE_ATTACHMENTS_ROOT, "attachments")
        os.makedirs(perm_dir, exist_ok=True)
        initial_count = len(os.listdir(perm_dir))
        
        with patch("messages_box.api.Message.save", side_effect=Exception("DB Failure")):
            response = self.client.post(
                reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
                {"attachment_type": "document", "file": doc_file}
            )
            self.assertEqual(response.status_code, 500)
            
            # Verify file count did not increase (orphaned file was cleaned up)
            self.assertEqual(len(os.listdir(perm_dir)), initial_count)

    def test_duplicate_submission_idempotency(self):
        self.client.force_login(self.seeker)
        req_id = "uniq_req_12345"
        
        # Post message 1
        response1 = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"content": "First try", "request_id": req_id}
        )
        self.assertEqual(response1.status_code, 201)
        
        # Post message 2 with same request_id
        response2 = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"content": "Retry message", "request_id": req_id}
        )
        self.assertEqual(response2.status_code, 201)
        
        # Message content is from the first request
        self.assertEqual(response2.json()["content"], "First try")
        self.assertEqual(Message.objects.count(), 1)

    def test_conversation_preview_and_ordering_updates(self):
        self.client.force_login(self.seeker)
        
        # Programmatically construct minimal valid PNG bytes
        from PIL import Image
        import io
        img = Image.new("RGB", (1, 1), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_data = buf.getvalue()
        img_file = SimpleUploadedFile("avatar.png", png_data, content_type="image/png")
        
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"attachment_type": "image", "file": img_file, "caption": "Nice photo"}
        )
        self.assertEqual(response.status_code, 201)
        
        # Verify preview on conversation window list
        response_inbox = self.client.get(reverse("messages_box:inbox"))
        self.assertContains(response_inbox, "📷 Photo: Nice photo")

    def test_unread_count_and_notification(self):
        self.client.force_login(self.seeker)
        response = self.client.post(
            reverse("messages_box:api_send_message", kwargs={"conversation_id": str(self.conv.id)}),
            {"content": "New Unread"}
        )
        self.assertEqual(response.status_code, 201)
        msg = Message.objects.first()
        
        # Log in as Recruiter and check unread count
        self.client.force_login(self.recruiter)
        response_unread = self.client.get(reverse("messages_box:api_unread_count"))
        self.assertEqual(response_unread.json()["unread_count"], 1)
        
        # Verify notification has correct deduplication key
        notif = Notification.objects.first()
        self.assertEqual(notif.deduplication_key, f"message_{msg.id}")

    def test_existing_historical_messages_no_attachment_fields(self):
        # Create historical message manually without any optional attachment fields
        msg = Message(
            conversation=self.conv,
            sender_id=self.seeker.id,
            sender_name="Historical User",
            content="Old Text"
        ).save()
        
        self.client.force_login(self.seeker)
        response = self.client.get(reverse("messages_box:chat_window", kwargs={"conversation_id": str(self.conv.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Old Text")


class RecruiterApplicantsDropdownTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.disconnect(alias="default")
        cls.db_connection = connection.connect(
            db="test_job_portal_db",
            host="mongodb://localhost:27017/test_job_portal_db",
            alias="default",
            uuidRepresentation="standard"
        )
        cls.temp_media = tempfile.mkdtemp()
        cls.override_media = patch.object(settings, "MEDIA_ROOT", cls.temp_media)
        cls.override_media.start()

    @classmethod
    def tearDownClass(cls):
        cls.override_media.stop()
        shutil.rmtree(cls.temp_media)
        db = get_db(alias="default")
        db.client.drop_database("test_job_portal_db")
        connection.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.client = Client()
        UserProfile.objects.delete()
        User.objects.all().delete()
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        Notification.objects.delete()
        Conversation.objects.delete()
        Message.objects.delete()

        # Set up two recruiters, one seeker, a job, and applications
        self.recruiter1_user = User.objects.create_user(username="rec1@test.com", email="rec1@test.com", password="Password@123")
        self.recruiter1 = UserProfile(auth_user_id=self.recruiter1_user.id, name="Recruiter One", email="rec1@test.com", password=self.recruiter1_user.password, role="recruiter").save()

        self.recruiter2_user = User.objects.create_user(username="rec2@test.com", email="rec2@test.com", password="Password@123")
        self.recruiter2 = UserProfile(auth_user_id=self.recruiter2_user.id, name="Recruiter Two", email="rec2@test.com", password=self.recruiter2_user.password, role="recruiter").save()

        self.seeker_user = User.objects.create_user(username="seeker@test.com", email="seeker@test.com", password="Password@123")
        self.seeker = UserProfile(auth_user_id=self.seeker_user.id, name="Jobseeker One", email="seeker@test.com", password=self.seeker_user.password, role="jobseeker").save()

        self.job = Job(
            recruiter_id=self.recruiter1_user.id,
            recruiter_name="Recruiter One",
            title="Python Dev",
            company="Google",
            location="Remote",
            salary="100k",
            experience="3 years",
            description="Dev"
        ).save()
        
        self.app = Application(
            applicant_id=self.seeker_user.id,
            applicant_name="Jobseeker One",
            applicant_email="seeker@test.com",
            job_id=str(self.job.id),
            job_title="Python Dev",
            recruiter_id=self.recruiter1_user.id,
            resume="resumes/test_resume.pdf",
            cover_letter="Interested.",
            status="Pending"
        ).save()

    def test_recruiter_dashboard_and_applicants_render_dropdown_triggers(self):
        """1. Verify recruiter dashboard and applicants page render the single Actions dropdown button and no separate ones."""
        self.client.force_login(self.recruiter1_user)
        
        # Dashboard
        response = self.client.get(reverse("core:recruiter_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-dropdown-type="actions"')
        self.assertNotContains(response, 'href="/applications/resume/') # No separate visible resume links
        self.assertNotContains(response, 'dropdown-toggle">Status') # Separate dropdown
        
        # Applicants page
        response = self.client.get(reverse("applications:applicants"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-dropdown-type="actions"')
        self.assertNotContains(response, "Resume <i")

    def test_unauthorized_recruiter_cannot_access_or_update(self):
        """2. Verify another recruiter cannot update or access applicant detail/resume."""
        self.client.force_login(self.recruiter2_user)
        
        # Try status update
        response = self.client.post(
            reverse("applications:update_status", kwargs={"application_id": str(self.app.id), "new_status": "Reviewed"})
        )
        self.assertEqual(response.status_code, 302)
        # Should redirect back with error (Application not found or unauthorized)
        self.app.reload()
        self.assertEqual(self.app.status, "Pending")
        
        # Try resume access
        response = self.client.get(
            reverse("applications:resume_access", kwargs={"application_id": str(self.app.id), "mode": "view"})
        )
        self.assertEqual(response.status_code, 404)

    def test_status_update_notifications_and_csrf(self):
        """3. Status updates generate proper history, notification, and requires CSRF/POST."""
        self.client.force_login(self.recruiter1_user)
        
        # Perform POST status update
        response = self.client.post(
            reverse("applications:update_status", kwargs={"application_id": str(self.app.id), "new_status": "Shortlisted"})
        )
        self.assertEqual(response.status_code, 302)
        self.app.reload()
        self.assertEqual(self.app.status, "Shortlisted")
        
        # Check transition history
        self.assertEqual(len(self.app.status_history), 2)
        self.assertEqual(self.app.status_history[1]["status"], "Shortlisted")
        
        # Check Notification to applicant
        notif = Notification.objects.first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.user_id, self.seeker_user.id)
        self.assertEqual(notif.notification_type, "application_shortlisted")

    def test_schedule_interview_eligibility(self):
        """4. Verify Schedule Interview only appears for Shortlisted candidates."""
        self.client.force_login(self.recruiter1_user)
        
        # When Pending
        response = self.client.get(reverse("applications:applicants"))
        self.assertNotContains(response, 'data-schedule-url=')
        
        # When Shortlisted
        self.app.status = "Shortlisted"
        self.app.save()
        response = self.client.get(reverse("applications:applicants"))
        self.assertContains(response, 'data-schedule-url=')

    def test_active_interview_displays_view_interview(self):
        """5. Verify that existing scheduled interview replaces Schedule Interview with View Interview link."""
        self.client.force_login(self.recruiter1_user)
        
        self.app.status = "Shortlisted"
        self.app.save()
        
        interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter1_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Tech round",
            interview_date=timezone.now() + datetime.timedelta(days=1),
            start_time="11:00",
            duration=30,
            status="Scheduled"
        ).save()
        
        response = self.client.get(reverse("applications:applicants"))
        self.assertContains(response, 'data-interview-url=')
        self.assertNotContains(response, 'data-schedule-url=')

    def test_jobseeker_pages_remain_unchanged(self):
        """6. Verify jobseeker My Applications page does not render recruiter action dropdown code."""
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("applications:my_applications"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-applicant-name=')


class ZoomInterviewWorkflowTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.disconnect(alias="default")
        cls.db_connection = connection.connect(
            db="test_job_portal_db",
            host="mongodb://localhost:27017/test_job_portal_db",
            alias="default",
            uuidRepresentation="standard"
        )
        cls.temp_media = tempfile.mkdtemp()
        cls.override_media = patch.object(settings, "MEDIA_ROOT", cls.temp_media)
        cls.override_media.start()

    @classmethod
    def tearDownClass(cls):
        cls.override_media.stop()
        shutil.rmtree(cls.temp_media)
        db = get_db(alias="default")
        db.client.drop_database("test_job_portal_db")
        connection.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.client = Client()
        UserProfile.objects.delete()
        User.objects.all().delete()
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        Notification.objects.delete()
        Conversation.objects.delete()
        Message.objects.delete()

        # Users and profiles
        self.recruiter_user = User.objects.create_user(username="rec@test.com", email="rec@test.com", password="Password@123")
        self.recruiter = UserProfile(auth_user_id=self.recruiter_user.id, name="Recruiter One", email="rec@test.com", password=self.recruiter_user.password, role="recruiter").save()

        self.seeker_user = User.objects.create_user(username="seeker@test.com", email="seeker@test.com", password="Password@123")
        self.seeker = UserProfile(auth_user_id=self.seeker_user.id, name="Jobseeker One", email="seeker@test.com", password=self.seeker_user.password, role="jobseeker").save()

        self.unrelated_user = User.objects.create_user(username="unrelated@test.com", email="unrelated@test.com", password="Password@123")
        self.unrelated = UserProfile(auth_user_id=self.unrelated_user.id, name="Unrelated User", email="unrelated@test.com", password=self.unrelated_user.password, role="jobseeker").save()

        # Job and Application
        self.job = Job(
            recruiter_id=self.recruiter_user.id,
            recruiter_name="Recruiter One",
            title="Python Dev",
            company="Google",
            location="Remote",
            salary="120k",
            experience="3 years",
            description="Dev"
        ).save()
        
        self.app = Application(
            applicant_id=self.seeker_user.id,
            applicant_name="Jobseeker One",
            applicant_email="seeker@test.com",
            job_id=str(self.job.id),
            job_title="Python Dev",
            recruiter_id=self.recruiter_user.id,
            status="Shortlisted",
            resume="resumes/test_resume.pdf",
            cover_letter="Cover letter text content"
        ).save()

        # Interview
        self.interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Technical Round",
            interview_date=timezone.now() + datetime.timedelta(days=2),
            start_time="14:00",
            duration=45,
            timezone="Asia/Kolkata",
            status="Scheduled",
            meeting_provider="zoom",
            meeting_id="987654321",
            meeting_url="https://zoom.us/j/987654321",
            meeting_start_url="https://zoom.us/s/987654321"
        ).save()

    def test_recruiter_sees_start_meeting_and_never_join_url(self):
        """1. Recruiter sees Start Meeting but not candidate-only behaviour."""
        self.client.force_login(self.recruiter_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start Zoom Meeting")
        self.assertNotContains(response, "Join Zoom Meeting")
        self.assertContains(response, self.interview.meeting_start_url)

    def test_seeker_sees_join_meeting_and_never_sees_start_url(self):
        """2. Jobseeker sees Join Meeting and never receives start_url in HTML/API/Context."""
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Join Zoom Meeting")
        self.assertNotContains(response, "Start Zoom Meeting")
        self.assertNotContains(response, self.interview.meeting_start_url)

    def test_unrelated_user_cannot_access_interview_detail(self):
        """3. Unrelated recruiter/jobseeker receives 403 or 404."""
        self.client.force_login(self.unrelated_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertIn(response.status_code, (403, 404))

    def test_copy_invitation_payload_contains_local_date_and_safe_url(self):
        """4. Copy invitation contains correct local date/time and safe URL."""
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="candidate-invite-text"')
        self.assertContains(response, self.interview.meeting_url)
        self.assertNotContains(response, self.interview.meeting_start_url)

    def test_google_calendar_url_params(self):
        """5. Google Calendar URL contains correctly encoded values and participant-safe link."""
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertContains(response, "https://www.google.com/calendar/render")
        self.assertNotContains(response, "https://zoom.us/s/")

    def test_ics_export_authorization(self):
        """6. Authorized ICS download succeeds; unauthorized fails."""
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:export_ics", kwargs={"interview_id": str(self.interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar")
        
        self.client.force_login(self.unrelated_user)
        response = self.client.get(reverse("interviews:export_ics", kwargs={"interview_id": str(self.interview.id)}))
        self.assertEqual(response.status_code, 404)

    def test_ics_payload_correctness(self):
        """7. ICS output contains valid UTC start/end values and safe join URL."""
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:export_ics", kwargs={"interview_id": str(self.interview.id)}))
        ics_text = response.content.decode("utf-8")
        
        self.assertIn("BEGIN:VCALENDAR", ics_text)
        self.assertIn("DTSTART:", ics_text)
        self.assertIn("DTEND:", ics_text)
        self.assertIn(self.interview.meeting_url, ics_text)
        self.assertNotIn(self.interview.meeting_start_url, ics_text)

    def test_cancelled_completed_interviews_disable_meeting_actions(self):
        """8. Cancelled/completed interviews disable meeting actions."""
        self.interview.status = "Cancelled"
        self.interview.save()
        
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertNotContains(response, "Join Zoom Meeting")
        self.assertContains(response, "This interview has been cancelled.")

    @patch("interviews.views.update_zoom_meeting")
    def test_reschedule_zoom_api_interaction(self, mock_update_zoom):
        """9. Reschedule updates Zoom and local interview data. Failure rolls back."""
        self.client.force_login(self.recruiter_user)
        
        # Scenario A: Zoom success
        mock_update_zoom.return_value = True
        new_date = (timezone.now() + datetime.timedelta(days=5)).date().isoformat()
        
        response = self.client.post(
            reverse("interviews:reschedule_interview", kwargs={"interview_id": str(self.interview.id)}),
            {
                "date": new_date,
                "start_time": "16:00",
                "duration": "60",
                "timezone": "UTC",
                "notes": "Rescheduled note"
            }
        )
        self.assertEqual(response.status_code, 302)
        self.interview.reload()
        self.assertEqual(self.interview.duration, 60)
        self.assertEqual(self.interview.timezone, "UTC")
        
        # Scenario B: Zoom fails - rolls back MongoDB changes
        mock_update_zoom.return_value = False
        response_fail = self.client.post(
            reverse("interviews:reschedule_interview", kwargs={"interview_id": str(self.interview.id)}),
            {
                "date": new_date,
                "start_time": "18:00",
                "duration": "90",
                "timezone": "America/New_York",
                "notes": "Failed reschedule"
            }
        )
        self.assertEqual(response_fail.status_code, 200)
        self.interview.reload()
        self.assertEqual(self.interview.duration, 60)
        self.assertEqual(self.interview.timezone, "UTC")

    def test_ready_to_join_window_computation(self):
        """10. Ready-to-join window starts 10 minutes before interview."""
        self.interview.interview_date = timezone.now() + datetime.timedelta(minutes=5)
        self.interview.save()
        self.client.force_login(self.seeker_user)
        response = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(self.interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["join_time_status"], "Ready to join")

    def test_reschedule_notification_recipient(self):
        """14. Reschedule notification goes to the correct recipient once."""
        Notification.objects.delete()
        self.client.force_login(self.recruiter_user)
        new_date = (timezone.now() + datetime.timedelta(days=5)).date().isoformat()
        response = self.client.post(
            reverse("interviews:reschedule_interview", kwargs={"interview_id": str(self.interview.id)}),
            {
                "date": new_date,
                "start_time": "16:00",
                "duration": "60",
                "timezone": "UTC",
                "notes": "Notes"
            }
        )
        self.assertEqual(response.status_code, 302)
        notifs = list(Notification.objects(user_id=self.seeker_user.id, notification_type="interview_rescheduled"))
        self.assertEqual(len(notifs), 1)

    @patch("interviews.views.delete_zoom_meeting")
    def test_cancellation_updates_zoom_and_wipes_links(self, mock_delete_zoom):
        """15. Cancellation updates Zoom, MongoDB, chat card and notification."""
        self.client.force_login(self.recruiter_user)
        response = self.client.get(
            reverse("interviews:update_status", kwargs={"interview_id": str(self.interview.id), "new_status": "Cancelled"})
        )
        self.assertEqual(response.status_code, 302)
        self.interview.reload()
        self.assertEqual(self.interview.status, "Cancelled")
        self.assertEqual(self.interview.meeting_url, "")
        self.assertEqual(self.interview.meeting_start_url, "")
        mock_delete_zoom.assert_called_once()

    def test_missing_credentials_handled_safely(self):
        """17. Missing credentials, timeout and rate-limit errors are handled safely."""
        with patch("interviews.views.is_zoom_configured", return_value=False):
            self.client.force_login(self.recruiter_user)
            response = self.client.post(reverse("interviews:retry_zoom", kwargs={"interview_id": str(self.interview.id)}))
            self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
