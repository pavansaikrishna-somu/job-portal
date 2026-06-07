from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from applications.documents import Application
from jobs.documents import Job
from users.documents import UserProfile


class Command(BaseCommand):
    help = "Seed sample users, jobs, and applications into MongoDB Atlas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Reset existing seeded data before inserting new sample data.",
        )

    def handle(self, *args, **options):
        User = get_user_model()

        seed_users = [
            {
                "name": "Aarav Rao",
                "email": "recruiter1@example.com",
                "password": "Password@123",
                "role": "recruiter",
                "phone": "9876543210",
                "skills": ["Hiring", "Leadership", "Communication"],
                "company_name": "NextGen Tech",
            },
            {
                "name": "Sneha Iyer",
                "email": "recruiter2@example.com",
                "password": "Password@123",
                "role": "recruiter",
                "phone": "9898989898",
                "skills": ["Recruitment", "Sourcing"],
                "company_name": "CloudBridge Solutions",
            },
            {
                "name": "Rohan Sharma",
                "email": "jobseeker1@example.com",
                "password": "Password@123",
                "role": "jobseeker",
                "phone": "9123456789",
                "skills": ["Python", "Django", "REST APIs"],
                "company_name": "",
            },
            {
                "name": "Meera Nair",
                "email": "jobseeker2@example.com",
                "password": "Password@123",
                "role": "jobseeker",
                "phone": "9000001122",
                "skills": ["React", "JavaScript", "Tailwind"],
                "company_name": "",
            },
        ]

        if options["reset"]:
            emails = [entry["email"] for entry in seed_users]
            django_users = User.objects.filter(email__in=emails)
            auth_user_ids = list(django_users.values_list("id", flat=True))
            Application.objects.delete()
            Job.objects.delete()
            UserProfile.objects(auth_user_id__in=auth_user_ids).delete()
            django_users.delete()
            self.stdout.write(self.style.WARNING("Existing seeded data reset completed."))

        profiles = {}
        for entry in seed_users:
            user, _ = User.objects.get_or_create(
                username=entry["email"],
                defaults={
                    "email": entry["email"],
                    "first_name": entry["name"],
                },
            )
            user.set_password(entry["password"])
            user.email = entry["email"]
            user.first_name = entry["name"]
            user.save()

            profile = UserProfile.objects(auth_user_id=user.id).first()
            if not profile:
                profile = UserProfile(auth_user_id=user.id)
            profile.name = entry["name"]
            profile.email = entry["email"]
            profile.password = user.password
            profile.role = entry["role"]
            profile.phone = entry["phone"]
            profile.skills = entry["skills"]
            profile.company_name = entry["company_name"]
            profile.save()
            profiles[entry["email"]] = profile

        jobs_payload = [
            {
                "title": "Backend Django Developer",
                "company": "NextGen Tech",
                "location": "Hyderabad",
                "salary": "8-12 LPA",
                "experience": "2-4 years",
                "skills_required": ["Python", "Django", "MongoDB", "REST"],
                "description": "Develop scalable backend APIs and integrate cloud-ready data solutions.",
                "recruiter_email": "recruiter1@example.com",
            },
            {
                "title": "Frontend React Engineer",
                "company": "CloudBridge Solutions",
                "location": "Bangalore",
                "salary": "10-14 LPA",
                "experience": "3-5 years",
                "skills_required": ["React", "TypeScript", "Redux", "Bootstrap"],
                "description": "Build responsive and high-performance web interfaces for enterprise products.",
                "recruiter_email": "recruiter2@example.com",
            },
            {
                "title": "Full Stack Developer",
                "company": "NextGen Tech",
                "location": "Pune",
                "salary": "12-18 LPA",
                "experience": "4-6 years",
                "skills_required": ["Django", "React", "PostgreSQL", "Docker"],
                "description": "Own end-to-end product delivery across frontend and backend modules.",
                "recruiter_email": "recruiter1@example.com",
            },
        ]

        created_jobs = []
        for payload in jobs_payload:
            recruiter_profile = profiles[payload["recruiter_email"]]
            existing = Job.objects(
                title=payload["title"],
                company=payload["company"],
                recruiter_id=recruiter_profile.auth_user_id,
            ).first()
            if existing:
                created_jobs.append(existing)
                continue

            job = Job(
                title=payload["title"],
                company=payload["company"],
                location=payload["location"],
                salary=payload["salary"],
                experience=payload["experience"],
                skills_required=payload["skills_required"],
                description=payload["description"],
                recruiter_id=recruiter_profile.auth_user_id,
                recruiter_name=recruiter_profile.name,
            )
            job.save()
            created_jobs.append(job)

        applications_payload = [
            {
                "applicant_email": "jobseeker1@example.com",
                "job_index": 0,
                "cover_letter": "I bring strong Django and API development experience.",
                "status": "Reviewed",
            },
            {
                "applicant_email": "jobseeker2@example.com",
                "job_index": 1,
                "cover_letter": "I have hands-on expertise in modern React ecosystems.",
                "status": "Pending",
            },
            {
                "applicant_email": "jobseeker1@example.com",
                "job_index": 2,
                "cover_letter": "Excited to contribute to full-stack product ownership.",
                "status": "Shortlisted",
            },
        ]

        for payload in applications_payload:
            applicant = profiles[payload["applicant_email"]]
            job = created_jobs[payload["job_index"]]
            existing = Application.objects(
                applicant_id=applicant.auth_user_id,
                job_id=str(job.id),
            ).first()
            if existing:
                continue
            application = Application(
                applicant_id=applicant.auth_user_id,
                applicant_name=applicant.name,
                applicant_email=applicant.email,
                job_id=str(job.id),
                job_title=job.title,
                recruiter_id=job.recruiter_id,
                resume="resumes/sample_resume.pdf",
                cover_letter=payload["cover_letter"],
                status=payload["status"],
            )
            application.save()

        self.stdout.write(self.style.SUCCESS("Sample data seeded successfully."))
        self.stdout.write(f"Users in MongoDB: {UserProfile.objects.count()}")
        self.stdout.write(f"Jobs in MongoDB: {Job.objects.count()}")
        self.stdout.write(f"Applications in MongoDB: {Application.objects.count()}")
