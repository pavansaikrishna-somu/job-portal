import os
import shutil
import tempfile
import datetime
import unittest
from unittest.mock import patch, MagicMock

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jobportal.settings")
django.setup()

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.conf import settings
from django.utils import timezone

from mongoengine import connection
from mongoengine.connection import get_db

from users.documents import UserProfile
from jobs.documents import Job
from applications.documents import Application
from interviews.documents import Interview
from notifications.documents import Notification
from messages_box.documents import Conversation, Message


class InterviewZoomFlowTests(TestCase):
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
        Job.objects.delete()
        Application.objects.delete()
        Interview.objects.delete()
        Notification.objects.delete()
        Conversation.objects.delete()
        Message.objects.delete()
        User.objects.all().delete()

        # Recruiter
        self.recruiter_user = User.objects.create_user(
            username="recruiter@test.com", password="Password123!", email="recruiter@test.com"
        )
        self.recruiter_profile = UserProfile(
            auth_user_id=self.recruiter_user.id,
            name="Recruiter One",
            email="recruiter@test.com",
            password="Password123!",
            role="recruiter",
            company_name="TechCorp"
        )
        self.recruiter_profile.save()

        # Jobseeker
        self.seeker_user = User.objects.create_user(
            username="seeker@test.com", password="Password123!", email="seeker@test.com"
        )
        self.seeker_profile = UserProfile(
            auth_user_id=self.seeker_user.id,
            name="Job Seeker",
            email="seeker@test.com",
            password="Password123!",
            role="jobseeker"
        )
        self.seeker_profile.save()

        # Third-party user
        self.other_user = User.objects.create_user(
            username="other@test.com", password="Password123!", email="other@test.com"
        )
        self.other_profile = UserProfile(
            auth_user_id=self.other_user.id,
            name="Other User",
            email="other@test.com",
            password="Password123!",
            role="jobseeker"
        )
        self.other_profile.save()

        # Job
        self.job = Job(
            title="Senior Python Engineer",
            description="Build scalable distributed systems with Django.",
            company="TechCorp",
            location="Remote",
            salary="$120,000",
            experience="5+ years",
            recruiter_id=self.recruiter_user.id,
            recruiter_name="Recruiter One"
        )
        self.job.save()

        # Shortlisted Application
        self.app = Application(
            job_id=str(self.job.id),
            job_title=self.job.title,
            applicant_id=self.seeker_user.id,
            applicant_name=self.seeker_profile.name,
            applicant_email=self.seeker_profile.email,
            recruiter_id=self.recruiter_user.id,
            resume="resumes/test_resume.pdf",
            cover_letter="I am very excited about this opportunity.",
            status="Shortlisted"
        )
        self.app.save()

    def test_schedule_interview_with_mock_zoom_success(self):
        """Recruiter schedules an interview and Zoom meeting details are generated and saved."""
        self.client.login(username="recruiter@test.com", password="Password123!")

        future_date = (timezone.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        response = self.client.post(
            reverse("interviews:schedule_interview", kwargs={"application_id": str(self.app.id)}),
            {
                "title": "System Architecture Round",
                "date": future_date,
                "start_time": "14:00",
                "duration": "60",
                "timezone": "Asia/Kolkata",
                "notes": "Please prepare a diagram of your past projects."
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)

        # Check interview created in DB
        interview = Interview.objects(application_id=str(self.app.id)).first()
        self.assertIsNotNone(interview)
        self.assertEqual(interview.title, "System Architecture Round")
        self.assertEqual(interview.duration, 60)
        self.assertEqual(interview.status, "Scheduled")
        self.assertEqual(interview.meeting_provider, "zoom")
        self.assertEqual(interview.meeting_id, "1234567890")
        self.assertEqual(interview.meeting_url, "https://zoom.us/j/1234567890")
        self.assertEqual(interview.meeting_start_url, "https://zoom.us/s/1234567890")
        self.assertEqual(interview.meeting_password, "CcPass321")

        # Candidate received notification
        notif = Notification.objects(user_id=self.seeker_user.id, notification_type="interview_scheduled").first()
        self.assertIsNotNone(notif)
        self.assertIn("System Architecture Round", notif.description)

        # Conversation message created
        msg = Message.objects.first()
        self.assertIsNotNone(msg)
        self.assertIn("System Architecture Round", msg.content)

    def test_schedule_interview_past_date_rejected(self):
        """Scheduling an interview in the past is rejected."""
        self.client.login(username="recruiter@test.com", password="Password123!")

        past_date = (timezone.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        response = self.client.post(
            reverse("interviews:schedule_interview", kwargs={"application_id": str(self.app.id)}),
            {
                "title": "Past Interview",
                "date": past_date,
                "start_time": "10:00",
                "duration": "30",
                "timezone": "Asia/Kolkata",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Interview.objects.count(), 0)

    def test_meeting_start_url_privacy_and_role_access(self):
        """Candidate can view meeting join_url but host start_url is strictly hidden from candidate."""
        future_dt = timezone.now() + datetime.timedelta(days=1)
        interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Frontend Interview",
            interview_date=future_dt,
            start_time="11:00",
            duration=45,
            timezone="Asia/Kolkata",
            status="Scheduled",
            meeting_provider="zoom",
            meeting_id="999888777",
            meeting_url="https://zoom.us/j/999888777",
            meeting_start_url="https://zoom.us/s/SECRET_HOST_TOKEN",
            meeting_password="SecretPass123"
        )
        interview.save()

        # 1. Candidate views page
        self.client.login(username="seeker@test.com", password="Password123!")
        res_seeker = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(interview.id)}))
        self.assertEqual(res_seeker.status_code, 200)
        html_seeker = res_seeker.content.decode("utf-8")
        self.assertIn("https://zoom.us/j/999888777", html_seeker)
        self.assertNotIn("SECRET_HOST_TOKEN", html_seeker)
        self.assertIn("SecretPass123", html_seeker)

        # 2. Recruiter views page
        self.client.login(username="recruiter@test.com", password="Password123!")
        res_recruiter = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(interview.id)}))
        self.assertEqual(res_recruiter.status_code, 200)
        html_recruiter = res_recruiter.content.decode("utf-8")
        self.assertIn("SECRET_HOST_TOKEN", html_recruiter)

        # 3. Third party views page -> 404
        self.client.login(username="other@test.com", password="Password123!")
        res_other = self.client.get(reverse("interviews:view_interview", kwargs={"interview_id": str(interview.id)}))
        self.assertEqual(res_other.status_code, 404)

    def test_reschedule_interview_flow(self):
        """Recruiter reschedules interview, updates Zoom, and notifies candidate."""
        future_dt = timezone.now() + datetime.timedelta(days=1)
        interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Tech Screen",
            interview_date=future_dt,
            start_time="10:00",
            duration=30,
            timezone="Asia/Kolkata",
            status="Scheduled",
            meeting_provider="zoom",
            meeting_id="1234567890",
            meeting_url="https://zoom.us/j/1234567890",
            meeting_start_url="https://zoom.us/s/1234567890"
        )
        interview.save()

        self.client.login(username="recruiter@test.com", password="Password123!")
        new_date = (timezone.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        response = self.client.post(
            reverse("interviews:reschedule_interview", kwargs={"interview_id": str(interview.id)}),
            {
                "date": new_date,
                "start_time": "15:00",
                "duration": "45",
                "timezone": "Asia/Kolkata",
                "notes": "Updated time per candidate request."
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)

        interview.reload()
        self.assertEqual(interview.start_time, "15:00")
        self.assertEqual(interview.duration, 45)
        self.assertEqual(interview.notes, "Updated time per candidate request.")

        notif = Notification.objects(user_id=self.seeker_user.id, notification_type="interview_rescheduled").first()
        self.assertIsNotNone(notif)
        self.assertIn("15:00", notif.description)

    def test_cancel_interview_flow(self):
        """Cancelling an interview deletes Zoom meeting, clears meeting links and notifies candidate."""
        future_dt = timezone.now() + datetime.timedelta(days=2)
        interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Final Round",
            interview_date=future_dt,
            start_time="14:00",
            duration=60,
            timezone="Asia/Kolkata",
            status="Scheduled",
            meeting_provider="zoom",
            meeting_id="1234567890",
            meeting_url="https://zoom.us/j/1234567890",
            meeting_start_url="https://zoom.us/s/1234567890",
            meeting_password="CcPass321"
        )
        interview.save()

        self.client.login(username="recruiter@test.com", password="Password123!")
        response = self.client.get(
            reverse("interviews:update_status", kwargs={"interview_id": str(interview.id), "new_status": "Cancelled"}),
            follow=True
        )
        self.assertEqual(response.status_code, 200)

        interview.reload()
        self.assertEqual(interview.status, "Cancelled")
        self.assertEqual(interview.meeting_url, "")
        self.assertEqual(interview.meeting_start_url, "")
        self.assertEqual(interview.meeting_password, "")

        notif = Notification.objects(user_id=self.seeker_user.id, notification_type="interview_cancelled").first()
        self.assertIsNotNone(notif)
        self.assertIn("cancelled", notif.description.lower())

    def test_export_ics_calendar(self):
        """Exporting ICS returns valid iCalendar file."""
        future_dt = timezone.now() + datetime.timedelta(days=1)
        interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Culture Fit Interview",
            interview_date=future_dt,
            start_time="16:00",
            duration=45,
            timezone="Asia/Kolkata",
            status="Scheduled",
            meeting_provider="zoom",
            meeting_id="1234567890",
            meeting_url="https://zoom.us/j/1234567890",
            meeting_start_url="https://zoom.us/s/1234567890"
        )
        interview.save()

        self.client.login(username="seeker@test.com", password="Password123!")
        response = self.client.get(reverse("interviews:export_ics", kwargs={"interview_id": str(interview.id)}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar")
        content = response.content.decode("utf-8")
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertIn("SUMMARY:Culture Fit Interview", content)
        self.assertIn("https://zoom.us/j/1234567890", content)
        self.assertIn("END:VCALENDAR", content)

    def test_retry_zoom_success(self):
        """Recruiter can retry/generate Zoom link for an interview that previously lacked one."""
        future_dt = timezone.now() + datetime.timedelta(days=2)
        interview = Interview(
            application_id=str(self.app.id),
            job_id=str(self.job.id),
            recruiter_id=self.recruiter_user.id,
            jobseeker_id=self.seeker_user.id,
            title="Follow-up Interview",
            interview_date=future_dt,
            start_time="11:00",
            duration=30,
            timezone="Asia/Kolkata",
            status="Scheduled",
            meeting_provider="failed",
            meeting_id="",
            meeting_url="",
            meeting_start_url=""
        )
        interview.save()

        self.client.login(username="recruiter@test.com", password="Password123!")
        response = self.client.post(
            reverse("interviews:retry_zoom", kwargs={"interview_id": str(interview.id)}),
            follow=True
        )
        self.assertEqual(response.status_code, 200)

        interview.reload()
        self.assertEqual(interview.meeting_provider, "zoom")
        self.assertEqual(interview.meeting_id, "1234567890")
        self.assertEqual(interview.meeting_url, "https://zoom.us/j/1234567890")
        self.assertEqual(interview.meeting_password, "CcPass321")

    def test_schedule_interview_zoom_unconfigured(self):
        """When Zoom is unconfigured, scheduling still works cleanly with meeting_provider='none'."""
        with patch("interviews.views.is_zoom_configured", return_value=False):
            self.client.login(username="recruiter@test.com", password="Password123!")
            future_date = (timezone.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
            response = self.client.post(
                reverse("interviews:schedule_interview", kwargs={"application_id": str(self.app.id)}),
                {
                    "title": "On-site Interview",
                    "date": future_date,
                    "start_time": "12:00",
                    "duration": "45",
                    "timezone": "Asia/Kolkata",
                    "notes": "Office room 302"
                },
                follow=True
            )
            self.assertEqual(response.status_code, 200)

            interview = Interview.objects(title="On-site Interview").first()
            self.assertIsNotNone(interview)
            self.assertEqual(interview.meeting_provider, "none")
            self.assertEqual(interview.meeting_url, "")

    def test_duplicate_interview_prevention(self):
        """Scheduling two interviews for the same application at the exact same date/time is blocked."""
        self.client.login(username="recruiter@test.com", password="Password123!")
        future_date = (timezone.now() + datetime.timedelta(days=4)).strftime("%Y-%m-%d")
        payload = {
            "title": "Initial Round",
            "date": future_date,
            "start_time": "10:00",
            "duration": "30",
            "timezone": "Asia/Kolkata",
        }
        res1 = self.client.post(
            reverse("interviews:schedule_interview", kwargs={"application_id": str(self.app.id)}),
            payload,
            follow=True
        )
        self.assertEqual(res1.status_code, 200)

        # Second attempt for same application and date/time
        res2 = self.client.post(
            reverse("interviews:schedule_interview", kwargs={"application_id": str(self.app.id)}),
            payload,
            follow=True
        )
        self.assertContains(res2, "already scheduled")
        self.assertEqual(Interview.objects(application_id=str(self.app.id)).count(), 1)


if __name__ == "__main__":
    unittest.main()

