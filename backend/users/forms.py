from django import forms
from django.contrib.auth import get_user_model

from core.utils import parse_csv, save_uploaded_file
from users.documents import UserProfile
from users.services import get_user_profile


class RegisterForm(forms.Form):
    ROLE_CHOICES = [("jobseeker", "Job Seeker"), ("recruiter", "Recruiter")]

    name = forms.CharField(max_length=120)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20, required=False)
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    skills = forms.CharField(required=False, help_text="Comma-separated, e.g. Python, Django, React")
    company_name = forms.CharField(max_length=150, required=False)
    profile_image = forms.FileField(required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        user_model = get_user_model()
        if user_model.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        if UserProfile.objects(email=email).first():
            raise forms.ValidationError("A profile with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm_password = cleaned.get("confirm_password")
        role = cleaned.get("role")
        company_name = cleaned.get("company_name")

        if password and confirm_password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")
        if role == "recruiter" and not company_name:
            self.add_error("company_name", "Company name is required for recruiters.")
        return cleaned

    def save(self):
        user_model = get_user_model()
        email = self.cleaned_data["email"].lower()
        user = user_model.objects.create_user(
            username=email,
            email=email,
            first_name=self.cleaned_data["name"],
            password=self.cleaned_data["password"],
        )
        profile_image_path = ""
        if self.cleaned_data.get("profile_image"):
            profile_image_path = save_uploaded_file(self.cleaned_data["profile_image"], "profiles")

        profile = UserProfile(
            auth_user_id=user.id,
            name=self.cleaned_data["name"],
            email=email,
            password=user.password,
            role=self.cleaned_data["role"],
            phone=self.cleaned_data.get("phone", ""),
            skills=parse_csv(self.cleaned_data.get("skills", "")),
            company_name=self.cleaned_data.get("company_name", ""),
            company_email=email if self.cleaned_data["role"] == "recruiter" else None,
            profile_image=profile_image_path,
        )
        profile.save()
        return user, profile


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email")
    password = forms.CharField(widget=forms.PasswordInput)


class BaseProfileForm(forms.Form):
    name = forms.CharField(max_length=120)
    phone = forms.CharField(max_length=20)
    skills = forms.CharField(required=False, help_text="Comma-separated values")
    profile_image = forms.FileField(required=False)

    def __init__(self, *args, user=None, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.profile = profile or get_user_profile(user.id)

    def apply_common_fields(self, profile):
        profile.name = self.cleaned_data["name"]
        profile.phone = self.cleaned_data["phone"]
        profile.skills = parse_csv(self.cleaned_data.get("skills", ""))
        if self.cleaned_data.get("profile_image"):
            profile.profile_image = save_uploaded_file(self.cleaned_data["profile_image"], "profiles")

    def sync_user(self, profile):
        self.user.first_name = profile.name
        self.user.email = profile.email
        self.user.username = profile.email
        self.user.save()


class JobSeekerProfileForm(BaseProfileForm):
    location = forms.CharField(max_length=150)
    professional_title = forms.CharField(max_length=150)
    education = forms.CharField(max_length=150)
    college = forms.CharField(max_length=180)
    graduation_year = forms.CharField(max_length=10)
    experience_level = forms.CharField(max_length=120)
    resume = forms.FileField(required=False)
    linkedin_url = forms.URLField()
    github_url = forms.URLField()
    career_objective = forms.CharField(widget=forms.Textarea, max_length=1000)
    certifications = forms.CharField(required=False)
    projects = forms.CharField(required=False)
    preferred_role = forms.CharField(required=False, max_length=150)
    expected_salary = forms.CharField(required=False, max_length=80)

    def __init__(self, *args, user=None, profile=None, **kwargs):
        super().__init__(*args, user=user, profile=profile, **kwargs)
        if self.profile and not self.is_bound:
            self.fields["name"].initial = self.profile.name
            self.fields["phone"].initial = self.profile.phone
            self.fields["skills"].initial = ", ".join(self.profile.skills)
            self.fields["location"].initial = self.profile.location
            self.fields["professional_title"].initial = self.profile.professional_title
            self.fields["education"].initial = self.profile.education
            self.fields["college"].initial = self.profile.college
            self.fields["graduation_year"].initial = self.profile.graduation_year
            self.fields["experience_level"].initial = self.profile.experience_level
            self.fields["linkedin_url"].initial = self.profile.linkedin_url
            self.fields["github_url"].initial = self.profile.github_url
            self.fields["career_objective"].initial = self.profile.career_objective
            self.fields["certifications"].initial = ", ".join(self.profile.certifications)
            self.fields["projects"].initial = ", ".join(self.profile.projects)
            self.fields["preferred_role"].initial = self.profile.preferred_role
            self.fields["expected_salary"].initial = self.profile.expected_salary
        self.fields["skills"].required = True

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("resume") and not (self.profile and self.profile.resume):
            self.add_error("resume", "Resume is required for job seekers.")
        if not cleaned.get("skills"):
            self.add_error("skills", "Please add at least one skill.")
        return cleaned

    def save(self):
        profile = self.profile
        if not profile:
            return None
        self.apply_common_fields(profile)
        profile.location = self.cleaned_data["location"]
        profile.professional_title = self.cleaned_data["professional_title"]
        profile.education = self.cleaned_data["education"]
        profile.college = self.cleaned_data["college"]
        profile.graduation_year = self.cleaned_data["graduation_year"]
        profile.experience_level = self.cleaned_data["experience_level"]
        profile.linkedin_url = self.cleaned_data["linkedin_url"]
        profile.github_url = self.cleaned_data["github_url"]
        profile.career_objective = self.cleaned_data["career_objective"]
        profile.certifications = parse_csv(self.cleaned_data.get("certifications", ""))
        profile.projects = parse_csv(self.cleaned_data.get("projects", ""))
        profile.preferred_role = self.cleaned_data.get("preferred_role", "")
        profile.expected_salary = self.cleaned_data.get("expected_salary", "")
        profile.company_email = None
        if self.cleaned_data.get("resume"):
            profile.resume = save_uploaded_file(self.cleaned_data["resume"], "resumes")
        profile.save()
        self.sync_user(profile)
        return profile


class RecruiterProfileForm(BaseProfileForm):
    company_name = forms.CharField(max_length=150)
    company_email = forms.EmailField()
    company_website = forms.URLField()
    company_location = forms.CharField(max_length=180)
    industry_type = forms.CharField(max_length=140)
    company_description = forms.CharField(widget=forms.Textarea, max_length=1200)
    company_logo = forms.FileField(required=False)
    hiring_role = forms.CharField(max_length=140)
    employees_count = forms.CharField(max_length=80)
    linkedin_company = forms.URLField(required=False)
    culture_description = forms.CharField(widget=forms.Textarea, required=False, max_length=1200)
    headquarters = forms.CharField(required=False, max_length=180)
    founded_year = forms.CharField(required=False, max_length=10)

    def __init__(self, *args, user=None, profile=None, **kwargs):
        super().__init__(*args, user=user, profile=profile, **kwargs)
        if self.profile and not self.is_bound:
            self.fields["name"].initial = self.profile.name
            self.fields["phone"].initial = self.profile.phone
            self.fields["skills"].initial = ", ".join(self.profile.skills)
            self.fields["company_name"].initial = self.profile.company_name
            self.fields["company_email"].initial = self.profile.company_email or self.profile.email
            self.fields["company_website"].initial = self.profile.company_website
            self.fields["company_location"].initial = self.profile.company_location
            self.fields["industry_type"].initial = self.profile.industry_type
            self.fields["company_description"].initial = self.profile.company_description
            self.fields["hiring_role"].initial = self.profile.hiring_role
            self.fields["employees_count"].initial = self.profile.employees_count
            self.fields["linkedin_company"].initial = self.profile.linkedin_company
            self.fields["culture_description"].initial = self.profile.culture_description
            self.fields["headquarters"].initial = self.profile.headquarters
            self.fields["founded_year"].initial = self.profile.founded_year

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("company_logo") and not (self.profile and self.profile.company_logo):
            self.add_error("company_logo", "Company logo is required for recruiters.")
        return cleaned

    def clean_company_email(self):
        value = (self.cleaned_data.get("company_email") or "").strip()
        if not value:
            raise forms.ValidationError("Company email is required for recruiters.")
        return value

    def save(self):
        profile = self.profile
        if not profile:
            return None
        self.apply_common_fields(profile)
        profile.company_name = self.cleaned_data["company_name"]
        profile.company_email = self.cleaned_data["company_email"].strip()
        profile.company_website = self.cleaned_data["company_website"]
        profile.company_location = self.cleaned_data["company_location"]
        profile.industry_type = self.cleaned_data["industry_type"]
        profile.company_description = self.cleaned_data["company_description"]
        profile.hiring_role = self.cleaned_data["hiring_role"]
        profile.employees_count = self.cleaned_data["employees_count"]
        profile.linkedin_company = self.cleaned_data.get("linkedin_company", "")
        profile.culture_description = self.cleaned_data.get("culture_description", "")
        profile.headquarters = self.cleaned_data.get("headquarters", "")
        profile.founded_year = self.cleaned_data.get("founded_year", "")
        if self.cleaned_data.get("company_logo"):
            profile.company_logo = save_uploaded_file(self.cleaned_data["company_logo"], "profiles")
        profile.save()
        self.sync_user(profile)
        return profile
