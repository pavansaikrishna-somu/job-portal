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

from mongoengine import connection
from mongoengine.connection import get_db

from users.documents import UserProfile
from ai.documents import AIResumeAnalysis
from ai.services import analyze_resume, calculate_file_hash


class ResumeAnalyzerTests(TestCase):
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

    @classmethod
    def tearDownClass(cls):
        # Stop overriding media
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
        
        # Clear collections
        UserProfile.objects.delete()
        AIResumeAnalysis.objects.delete()
        User.objects.all().delete()

        # Create dummy jobseeker
        self.seeker_user = User.objects.create_user(
            username="seeker", password="password", email="seeker@test.com"
        )
        self.seeker_profile = UserProfile(
            auth_user_id=self.seeker_user.id,
            name="Seeker Test",
            email="seeker@test.com",
            password="password",
            role="jobseeker",
            resume=""
        )
        self.seeker_profile.save()

        # Create dummy recruiter
        self.recruiter_user = User.objects.create_user(
            username="recruiter", password="password", email="recruiter@test.com"
        )
        self.recruiter_profile = UserProfile(
            auth_user_id=self.recruiter_user.id,
            name="Recruiter Test",
            email="recruiter@test.com",
            password="password",
            role="recruiter"
        )
        self.recruiter_profile.save()

        # Create a dummy pdf file on disk
        self.resumes_dir = os.path.join(settings.MEDIA_ROOT, "resumes")
        os.makedirs(self.resumes_dir, exist_ok=True)
        self.dummy_pdf_name = "test_resume.pdf"
        self.dummy_pdf_rel_path = f"resumes/{self.dummy_pdf_name}"
        self.dummy_pdf_full_path = os.path.join(self.resumes_dir, self.dummy_pdf_name)
        with open(self.dummy_pdf_full_path, "wb") as f:
            f.write(b"%PDF-1.4 dummy pdf content")

        # Mock the PdfReader inside services to avoid binary parsing failures
        self.mock_pdf_reader_patch = patch("ai.services.PdfReader")
        self.mock_pdf_reader = self.mock_pdf_reader_patch.start()
        self.mock_page = MagicMock()
        self.mock_page.extract_text.return_value = "This is a sample resume text with Python and Django experience."
        self.mock_pdf_reader.return_value.pages = [self.mock_page]

        # Setup standard Gemini mock
        self.mock_client_instance = MagicMock()
        self.mock_genai_client_patch = patch("ai.services.genai.Client", return_value=self.mock_client_instance)
        self.mock_genai_client = self.mock_genai_client_patch.start()

        self.mock_response = MagicMock()
        self.mock_response.text = (
            '{"overall_score": 85, "ats_score": 80, '
            '"strengths": ["Python", "Django"], "weaknesses": ["HTML"], '
            '"missing_skills": ["React"], "suggestions": ["Format better"], '
            '"recommended_roles": ["Backend Developer"]}'
        )
        self.mock_client_instance.models.generate_content.return_value = self.mock_response

    def tearDown(self):
        self.mock_pdf_reader_patch.stop()
        self.mock_genai_client_patch.stop()
        super().tearDown()

    def test_missing_resume_handling(self):
        """Triggering analysis when no resume is uploaded should return a 400 Bad Request."""
        self.client.login(username="seeker", password="password")
        response = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("No resume uploaded", response.json()["error"])

    def test_role_restriction(self):
        """Only users with role jobseeker can trigger or access resume analysis."""
        # Unauthenticated user
        response = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response.status_code, 302)  # Redirects to login

        # Recruiter user
        self.client.login(username="recruiter", password="password")
        response = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response.status_code, 403)  # Returns 403 Forbidden under wrong-role access policy

    def test_successful_gemini_analysis_and_persistence(self):
        """Successful analysis saves to MongoEngine and returns structured results."""
        # Set resume path in profile
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()

        self.client.login(username="seeker", password="password")
        response = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["overall_score"], 85)
        self.assertEqual(data["ats_score"], 80)
        self.assertEqual(data["strengths"], ["Python", "Django"])
        self.assertEqual(data["weaknesses"], ["HTML"])
        self.assertEqual(data["missing_skills"], ["React"])
        self.assertEqual(data["suggestions"], ["Format better"])
        self.assertEqual(data["recommended_roles"], ["Backend Developer"])

        # Verify DB persistence
        analysis_db = AIResumeAnalysis.objects(user_id=self.seeker_user.id).first()
        self.assertIsNotNone(analysis_db)
        self.assertEqual(analysis_db.overall_score, 85)
        self.assertEqual(analysis_db.resume_hash, calculate_file_hash(self.dummy_pdf_full_path))

    def test_resume_hash_caching(self):
        """Identical resume hash results in using the cached version without calling Gemini again."""
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()

        self.client.login(username="seeker", password="password")
        
        # First call (hits Gemini)
        response1 = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response1.status_code, 200)
        
        # Second call (should hit cache)
        response2 = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response2.status_code, 200)

        # Gemini Client generate_content should be called exactly once
        self.assertEqual(self.mock_client_instance.models.generate_content.call_count, 1)

    def test_api_key_privacy(self):
        """GEMINI_API_KEY value is not exposed in the API responses."""
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()

        self.client.login(username="seeker", password="password")
        response = self.client.post(reverse("ai:trigger_analysis"))
        self.assertEqual(response.status_code, 200)

        # Assert API key is not leaked in keys or values of response
        api_key_val = getattr(settings, "GEMINI_API_KEY", "")
        response_str = str(response.json())
        if api_key_val:
            self.assertNotIn(api_key_val, response_str)
        self.assertNotIn("GEMINI_API_KEY", response_str)

    def test_gemini_failure_fallback(self):
        """If Gemini service throws an exception, it is caught gracefully and doesn't crash or leak key."""
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()

        # Simulate exception in Gemini
        self.mock_client_instance.models.generate_content.side_effect = Exception("API key expired or invalid connection.")

        self.client.login(username="seeker", password="password")
        response = self.client.post(reverse("ai:trigger_analysis"))
        
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["error"], "Failed to analyze resume. Please try again later.")

    def test_resume_ownership_and_security(self):
        """A user cannot access another user's resume analysis details."""
        # Create an analysis for the seeker user
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()
        
        self.client.login(username="seeker", password="password")
        response_trigger = self.client.post(reverse("ai:trigger_analysis"))
        analysis_id = response_trigger.json()["id"]

        # Create another seeker user
        other_seeker = User.objects.create_user(
            username="other_seeker", password="password", email="other@test.com"
        )
        other_profile = UserProfile(
            auth_user_id=other_seeker.id,
            name="Other Seeker",
            email="other@test.com",
            password="password",
            role="jobseeker"
        )
        other_profile.save()

        # Try to retrieve the analysis using other seeker
        self.client.login(username="other_seeker", password="password")
        response_get = self.client.get(reverse("ai:get_analysis", kwargs={"analysis_id": analysis_id}))
        
        self.assertEqual(response_get.status_code, 403)
        self.assertIn("Access denied", response_get.json()["error"])

    def test_analyzer_page_requires_auth(self):
        """Accessing the analyzer page redirects unauthenticated users or recruiters."""
        # Unauthenticated user
        response = self.client.get(reverse("ai:analyzer_page"))
        self.assertEqual(response.status_code, 302)
        
        # Recruiter user
        self.client.login(username="recruiter", password="password")
        response = self.client.get(reverse("ai:analyzer_page"))
        self.assertEqual(response.status_code, 403)

    def test_analyzer_page_success_renders_data(self):
        """Analyzer page loads successfully for jobseeker and displays correct analysis fields."""
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()

        analysis = AIResumeAnalysis(
            user_id=self.seeker_user.id,
            resume_path=self.dummy_pdf_rel_path,
            resume_hash=calculate_file_hash(self.dummy_pdf_full_path),
            overall_score=85,
            ats_score=80,
            strengths=["Mock Strength 1", "Mock Strength 2"],
            weaknesses=["Mock Weakness 1"],
            missing_skills=["Pydantic"],
            suggestions=["Improve details"],
            recommended_roles=["Backend Architect"]
        )
        analysis.save()

        self.client.login(username="seeker", password="password")
        response = self.client.get(reverse("ai:analyzer_page"))
        self.assertEqual(response.status_code, 200)

        html = response.content.decode("utf-8")
        self.assertIn("85", html)
        self.assertIn("80%", html)
        self.assertIn("Mock Strength 1", html)
        self.assertIn("Mock Weakness 1", html)
        self.assertIn("Pydantic", html)
        self.assertIn("Backend Architect", html)
        self.assertIn("Improve details", html)

    def test_analyzer_page_empty_state(self):
        """If no analysis exists, the page renders an empty state with prompt button."""
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()

        self.client.login(username="seeker", password="password")
        response = self.client.get(reverse("ai:analyzer_page"))
        self.assertEqual(response.status_code, 200)
        
        html = response.content.decode("utf-8")
        self.assertIn("Analyze Your Resume", html)
        self.assertIn("btn-analyze-empty", html)

    def test_profile_page_buttons_exist_only_with_resume(self):
        """Analyze button appears on jobseeker profile page only if resume exists."""
        self.client.login(username="seeker", password="password")
        
        # Profile has NO resume
        self.seeker_profile.resume = ""
        self.seeker_profile.save()
        response = self.client.get(reverse("users:profile"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertNotIn("btn-analyze-resume", html)

        # Profile HAS resume
        self.seeker_profile.resume = self.dummy_pdf_rel_path
        self.seeker_profile.save()
        response = self.client.get(reverse("users:profile"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("btn-analyze-resume", html)
        self.assertIn("btn-view-analysis", html)

    def test_analyzer_page_ui_elements(self):
        """Analyzer page contains the loading container and error container tags."""
        self.client.login(username="seeker", password="password")
        response = self.client.get(reverse("ai:analyzer_page"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('id="ai-loading-container"', html)
        self.assertIn('id="ai-error-container"', html)


if __name__ == "__main__":
    unittest.main()
