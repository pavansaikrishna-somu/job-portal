from django.utils import timezone
from mongoengine import DateTimeField, Document, EmailField, IntField, ListField, StringField


class UserProfile(Document):
    auth_user_id = IntField(required=True, unique=True)
    name = StringField(required=True, max_length=120)
    email = EmailField(required=True, unique=True)
    password = StringField(required=True)
    role = StringField(required=True, choices=("jobseeker", "recruiter"))
    phone = StringField(max_length=20, default="")
    skills = ListField(StringField(max_length=100), default=list)
    company_name = StringField(max_length=150, default="")
    profile_image = StringField(default="")
    location = StringField(max_length=150, default="")
    professional_title = StringField(max_length=150, default="")
    education = StringField(max_length=150, default="")
    college = StringField(max_length=180, default="")
    graduation_year = StringField(max_length=10, default="")
    experience_level = StringField(max_length=120, default="")
    resume = StringField(default="")
    linkedin_url = StringField(max_length=220, default="")
    github_url = StringField(max_length=220, default="")
    career_objective = StringField(max_length=1000, default="")
    certifications = ListField(StringField(max_length=140), default=list)
    projects = ListField(StringField(max_length=160), default=list)
    preferred_role = StringField(max_length=150, default="")
    expected_salary = StringField(max_length=80, default="")
    company_email = EmailField(null=True, default=None)
    company_website = StringField(max_length=220, default="")
    company_location = StringField(max_length=180, default="")
    industry_type = StringField(max_length=140, default="")
    company_description = StringField(max_length=1200, default="")
    company_logo = StringField(default="")
    hiring_role = StringField(max_length=140, default="")
    employees_count = StringField(max_length=80, default="")
    linkedin_company = StringField(max_length=220, default="")
    culture_description = StringField(max_length=1200, default="")
    headquarters = StringField(max_length=180, default="")
    founded_year = StringField(max_length=10, default="")
    created_at = DateTimeField(default=timezone.now)

    meta = {
        "collection": "user_profiles",
        "indexes": ["email", "role", "auth_user_id"],
    }
