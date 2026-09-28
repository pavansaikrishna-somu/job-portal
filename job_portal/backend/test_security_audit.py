import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jobportal.settings")
django.setup()

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404

from mongoengine import connection
from mongoengine.connection import get_db

from users.documents import UserProfile
from jobs.documents import Job
from applications.documents import Application
from interviews.documents import Interview
from notifications.documents import Notification
from messages_box.documents import Conversation, Message
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.adapter import get_adapter
from users.forms import RegisterForm


class SecurityAuditTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disconnect real DB connection
        connection.disconnect(alias="default")
        # Connect to test DB
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
        
        # Clear MongoDB collections
        UserProfile.objects.delete()
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        Notification.objects.delete()
        Conversation.objects.delete()
        Message.objects.delete()
        
        # Clear Django users and social accounts
        User.objects.all().delete()
        SocialAccount.objects.all().delete()

        # Seed standard Jobseeker user
        self.seeker_user = User.objects.create_user(
            username="seeker@test.com", password="Password123!", email="seeker@test.com"
        )
        self.seeker_profile = UserProfile(
            auth_user_id=self.seeker_user.id,
            name="Seeker User",
            email="seeker@test.com",
            password="Password123!",
            role="jobseeker"
        )
        self.seeker_profile.save()

        # Seed standard Recruiter user
        self.recruiter_user = User.objects.create_user(
            username="recruiter@test.com", password="Password123!", email="recruiter@test.com"
        )
        self.recruiter_profile = UserProfile(
            auth_user_id=self.recruiter_user.id,
            name="Recruiter User",
            email="recruiter@test.com",
            password="Password123!",
            role="recruiter",
            company_name="CareerConnect Inc"
        )
        self.recruiter_profile.save()

    def test_new_google_jobseeker_onboarding(self):
        """A new Google user with no profile is routed to onboard, selects jobseeker, and completes profile."""
        google_user = User.objects.create_user(
            username="newgoogle@test.com", password="Password123!", email="newgoogle@test.com"
        )
        self.client.force_login(google_user)
        
        # Accessing protected page redirects to onboard
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("users:onboard"), response.url)

        # Post valid role
        response = self.client.post(reverse("users:onboard"), {"role": "jobseeker"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:jobseeker_dashboard"), response.url)

        # Profile is created
        profile = UserProfile.objects(auth_user_id=google_user.id).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, "jobseeker")

    def test_new_google_recruiter_onboarding(self):
        """A new Google user with no profile selects recruiter and completes profile."""
        google_user = User.objects.create_user(
            username="newrecruiter@test.com", password="Password123!", email="newrecruiter@test.com"
        )
        self.client.force_login(google_user)

        # Post recruiter role
        response = self.client.post(reverse("users:onboard"), {"role": "recruiter"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:recruiter_dashboard"), response.url)

        profile = UserProfile.objects(auth_user_id=google_user.id).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, "recruiter")

    def test_confirmation_required_before_saving(self):
        """Merely loading the onboarding GET page must not instantiate the MongoDB profile."""
        google_user = User.objects.create_user(
            username="visitor@test.com", password="Password123!", email="visitor@test.com"
        )
        self.client.force_login(google_user)

        # GET request
        response = self.client.get(reverse("users:onboard"))
        self.assertEqual(response.status_code, 200)

        # Confirm no profile exists in MongoDB yet
        profile = UserProfile.objects(auth_user_id=google_user.id).first()
        self.assertIsNone(profile)

    def test_missing_invalid_tampered_role_values(self):
        """Tampered or missing role parameters are strictly rejected by the server side."""
        google_user = User.objects.create_user(
            username="tamper@test.com", password="Password123!", email="tamper@test.com"
        )
        self.client.force_login(google_user)

        # Unexpected role value
        response = self.client.post(reverse("users:onboard"), {"role": "superuser"})
        self.assertEqual(response.status_code, 200) # Form re-renders with error
        self.assertIsNone(UserProfile.objects(auth_user_id=google_user.id).first())

        # Mixed-case role value
        response = self.client.post(reverse("users:onboard"), {"role": "JobSeeker"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(UserProfile.objects(auth_user_id=google_user.id).first())

        # Missing role parameter
        response = self.client.post(reverse("users:onboard"), {})
        self.assertEqual(response.status_code, 200)

    def test_repeated_onboarding_get_and_post_rejection(self):
        """Once onboarded, repeated onboarding GET/POST requests are rejected and redirect to the dashboard."""
        self.client.force_login(self.seeker_user)

        # GET request redirects to jobseeker dashboard
        response = self.client.get(reverse("users:onboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:jobseeker_dashboard"), response.url)

        # POST request does not modify role and redirects to dashboard
        response = self.client.post(reverse("users:onboard"), {"role": "recruiter"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:jobseeker_dashboard"), response.url)
        
        # Verify role was not changed
        profile = UserProfile.objects(auth_user_id=self.seeker_user.id).first()
        self.assertEqual(profile.role, "jobseeker")

    def test_concurrent_profile_creation_idempotency(self):
        """Concurrent requests resulting in duplicate key error are handled gracefully by checking exists first."""
        google_user = User.objects.create_user(
            username="concurrent@test.com", password="Password123!", email="concurrent@test.com"
        )
        self.client.force_login(google_user)

        # Pre-seed profile to simulate a concurrent request completing first
        concurrent_profile = UserProfile(
            auth_user_id=google_user.id,
            name="Concurrent User",
            email="concurrent@test.com",
            password="GoogleOAuthAccountNoPassword",
            role="recruiter"
        )
        concurrent_profile.save()

        # Submit onboarding (simulating second concurrent POST thread)
        response = self.client.post(reverse("users:onboard"), {"role": "recruiter"})
        
        # Second thread must handle duplicate key gracefully and redirect to dashboard
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:recruiter_dashboard"), response.url)

    def test_partial_profile_creation_failure_rollback(self):
        """If profile creation fails unexpectedly, compensate rollback deletes Django user to allow retry."""
        google_user = User.objects.create_user(
            username="rollback@test.com", password="Password123!", email="rollback@test.com"
        )
        self.client.force_login(google_user)

        # Force profile save to throw exception
        with patch("users.documents.UserProfile.save", side_effect=Exception("DB Failure")):
            response = self.client.post(reverse("users:onboard"), {"role": "jobseeker"})
            
            # Should log out and redirect to login page with error
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("users:login"), response.url)
            
            # Django user should be deleted as a compensating rollback
            self.assertFalse(User.objects.filter(id=google_user.id).exists())

    def test_existing_local_account_safe_google_linking(self):
        """Google email matching existing local account connects safely without silently duplicate profiles or role overwrites."""
        # Local user exists with no social accounts
        local_user = User.objects.create_user(
            username="local@test.com", password="Password123!", email="local@test.com"
        )
        local_profile = UserProfile(
            auth_user_id=local_user.id,
            name="Local Recruiter",
            email="local@test.com",
            password="Password123!",
            role="recruiter"
        )
        local_profile.save()

        # Mock social login with same verified email
        sociallogin = MagicMock()
        sociallogin.user = User(email="local@test.com", username="local@test.com")
        sociallogin.account = MagicMock()
        sociallogin.account.provider = "google"
        sociallogin.account.uid = "google-uid-123"
        sociallogin.account.extra_data = {"email_verified": True}
        sociallogin.is_existing = False

        adapter = get_adapter()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False

        with patch("django.contrib.messages.error") as mock_msg:
            adapter.pre_social_login(request, sociallogin)
            
            # The social login user should be bound to the existing local user
            self.assertEqual(sociallogin.user, local_user)

    def test_email_case_and_whitespace_variants_normalization(self):
        """Trims whitespace and normalizes case during OAuth linking and local registration."""
        # 1. Normal registration normalizes whitespace/case
        form = RegisterForm({
            "name": "Normalizer seeker",
            "email": "   NoRMaLiZe@Test.Com   ",
            "phone": "99999",
            "role": "jobseeker",
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        self.assertTrue(form.is_valid())
        email = form.clean_email()
        self.assertEqual(email, "normalize@test.com")

        # 2. OAuth normalization in CustomSocialAccountAdapter
        sociallogin = MagicMock()
        sociallogin.user = User(email="   SoCiaL@TeSt.CoM   ")
        sociallogin.account = MagicMock()
        sociallogin.account.extra_data = {"email_verified": True}
        
        adapter = get_adapter()
        request = MagicMock()
        
        try:
            adapter.pre_social_login(request, sociallogin)
        except Exception:
            pass # Catch redirects since we don't mock the whole DB redirect flow
            
        self.assertEqual(sociallogin.user.email, "social@test.com")

    def test_missing_or_unverified_provider_email_rejection(self):
        """Rejects signups/logins lacking verified email properties in provider data."""
        # Unverified email
        sociallogin = MagicMock()
        sociallogin.user = User(email="unverified@test.com")
        sociallogin.account = MagicMock()
        sociallogin.account.extra_data = {"email_verified": False}

        adapter = get_adapter()
        request = MagicMock()

        from allauth.core.exceptions import ImmediateHttpResponse
        with self.assertRaises(ImmediateHttpResponse):
            adapter.pre_social_login(request, sociallogin)

        # Missing email
        sociallogin.user = User(email="")
        with self.assertRaises(ImmediateHttpResponse):
            adapter.pre_social_login(request, sociallogin)

    def test_incomplete_user_access_control(self):
        """Incomplete user is blocked from accessing dashboards, jobs, profile, and messages."""
        google_user = User.objects.create_user(
            username="incomplete@test.com", password="Password123!", email="incomplete@test.com"
        )
        self.client.force_login(google_user)

        protected_urls = [
            reverse("core:jobseeker_dashboard"),
            reverse("core:recruiter_dashboard"),
            reverse("jobs:manage_jobs"),
            reverse("users:profile"),
            reverse("messages_box:inbox"),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("users:onboard"), response.url)

    def test_role_based_access_controls(self):
        """Jobseekers cannot hit Recruiter endpoints; Recruiters cannot hit Jobseeker endpoints (returns 403)."""
        # Jobseeker attempting recruiter actions
        self.client.force_login(self.seeker_user)
        recruiter_urls = [
            reverse("jobs:post_job"),
            reverse("jobs:manage_jobs"),
        ]
        for url in recruiter_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

        # Recruiter attempting jobseeker actions
        self.client.force_login(self.recruiter_user)
        jobseeker_urls = [
            reverse("applications:my_applications"),
        ]
        for url in jobseeker_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

    def test_object_level_ownership_enforcement(self):
        """Enforces ownership: recruiters/jobseekers cannot manage/access resources of other recruiters/jobseekers."""
        # Seed recruiter 2
        recruiter2_user = User.objects.create_user(
            username="recruiter2@test.com", password="Password123!", email="recruiter2@test.com"
        )
        recruiter2_profile = UserProfile(
            auth_user_id=recruiter2_user.id,
            name="Recruiter 2",
            email="recruiter2@test.com",
            password="Password123!",
            role="recruiter"
        )
        recruiter2_profile.save()

        # Seed job owned by Recruiter 1
        job1 = Job(
            title="Recruiter 1 Job",
            description="Testing ownership",
            company="CareerConnect",
            location="Remote",
            experience="1 year",
            salary="5 LPA",
            skills_required=["Python"],
            recruiter_id=self.recruiter_user.id,
            recruiter_name="Recruiter User"
        )
        job1.save()

        # Seed Application for job 1
        app1 = Application(
            applicant_id=self.seeker_user.id,
            applicant_name="Seeker",
            applicant_email=self.seeker_user.email,
            job_id=str(job1.id),
            job_title=job1.title,
            recruiter_id=self.recruiter_user.id,
            resume="resumes/test.pdf",
            cover_letter="Cover letter text",
            status="Shortlisted"
        )
        app1.save()

        # Seed Interview
        import datetime
        interview1 = Interview(
            application_id=str(app1.id),
            job_id=str(job1.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Interview Title",
            duration=30,
            status="Scheduled",
            interview_date=datetime.datetime.now() + datetime.timedelta(days=1),
            start_time="10:00 AM"
        )
        interview1.save()

        # Recruiter 2 attempts to reschedule Recruiter 1's interview -> Returns 404 (preventing discovery)
        self.client.force_login(recruiter2_user)
        response = self.client.get(reverse("interviews:reschedule_interview", kwargs={"interview_id": str(interview1.id)}))
        self.assertEqual(response.status_code, 404)

        # Recruiter 2 attempts to download Recruiter 1's calendar invite -> Returns 404
        response = self.client.get(reverse("interviews:export_ics", kwargs={"interview_id": str(interview1.id)}))
        self.assertEqual(response.status_code, 404)

        # Recruiter 2 attempts to view Recruiter 1's applicant resume -> Returns 404
        response = self.client.get(reverse("applications:resume_access", kwargs={"application_id": str(app1.id), "mode": "view"}))
        self.assertEqual(response.status_code, 404)

        # Jobseeker 2 attempts to view/withdraw Jobseeker 1's application -> Returns 404/403
        seeker2_user = User.objects.create_user(
            username="seeker2@test.com", password="Password123!", email="seeker2@test.com"
        )
        seeker2_profile = UserProfile(
            auth_user_id=seeker2_user.id,
            name="Seeker 2",
            email="seeker2@test.com",
            password="Password123!",
            role="jobseeker"
        )
        seeker2_profile.save()
        
        self.client.force_login(seeker2_user)
        response = self.client.post(reverse("applications:withdraw_application", kwargs={"application_id": str(app1.id)}))
        self.assertEqual(response.status_code, 302) # Redirects back with error alert
        
        # Verify app status remains untouched
        app_db = Application.get_or_none(str(app1.id))
        self.assertEqual(app_db.status, "Shortlisted")

    def test_csrf_failure_protection(self):
        """State-modifying requests without CSRF token are blocked with 403 Forbidden."""
        from django.middleware.csrf import CsrfViewMiddleware
        from django.test import RequestFactory
        from users.views import onboard_view

        factory = RequestFactory()
        request = factory.post(reverse("users:onboard"), {"role": "jobseeker"})
        request.user = self.seeker_user
        
        # Mock session middleware
        from django.contrib.sessions.middleware import SessionMiddleware
        session_middleware = SessionMiddleware(lambda r: None)
        session_middleware.process_request(request)
        request.session.save()

        # Run CSRF check
        csrf_middleware = CsrfViewMiddleware(lambda r: None)
        csrf_middleware.process_request(request)
        response = csrf_middleware.process_view(request, onboard_view, (), {})
        
        # CSRF failure must return 403 Forbidden
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 403)

    def test_unsafe_redirect_next_prevention(self):
        """Validates next target redirect parameters to prevent open redirects."""
        self.client.force_login(self.seeker_user)
        
        # POST login or dashboard redirect with unsafe next parameter
        response = self.client.post(reverse("users:login"), {
            "email": self.seeker_user.email,
            "password": "Password123!",
            "next": "http://malicious-external-site.com/steal-token"
        })
        # Safe redirection should fall back to internal routing or ignore the external host redirect
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, "http://malicious-external-site.com/steal-token")
        self.assertIn(reverse("core:jobseeker_dashboard"), response.url)
        
        # Re-login with valid password & external next redirect check
        self.client.logout()
        response = self.client.post(reverse("users:login"), {
            "email": "seeker@test.com",
            "password": "Password123!"
        }, HTTP_REFERER="http://127.0.0.1:8000/users/login/?next=http://malicious-external-site.com")
        
        # Check that we did not redirect externally
        self.assertNotEqual(response.url, "http://malicious-external-site.com")

    def test_inactive_account_login_rejection(self):
        """Inactive/deactivated local or Google accounts are rejected from logging in."""
        self.seeker_user.is_active = False
        self.seeker_user.save()

        # Local auth
        response = self.client.post(reverse("users:login"), {
            "email": self.seeker_user.email,
            "password": "Password123!"
        })
        self.assertContains(response, "Please verify your email before logging in")

        # Social auth pre-login hook check
        sociallogin = MagicMock()
        sociallogin.user = self.seeker_user
        sociallogin.account = MagicMock()
        sociallogin.account.extra_data = {"email_verified": True}

        adapter = get_adapter()
        request = MagicMock()
        from allauth.core.exceptions import ImmediateHttpResponse
        with self.assertRaises(ImmediateHttpResponse):
            adapter.pre_social_login(request, sociallogin)

    def test_corrupt_invalid_stored_role(self):
        """User profile with invalid/corrupt role triggers logout, logs warning, and denies dashboard access."""
        self.seeker_profile.role = "unauthorized_hacker_role"
        self.seeker_profile.save(validate=False)

        self.client.force_login(self.seeker_user)

        # Accessing dashboard checks middleware
        response = self.client.get(reverse("core:jobseeker_dashboard"))
        
        # Middleware should catch corrupt role, log out user, and redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("users:login"), response.url)

        # Confirm user was logged out
        self.assertNotIn("_auth_user_id", self.client.session)


if __name__ == "__main__":
    unittest.main()
