from users.documents import UserProfile


def get_user_profile(auth_user_id):
    return UserProfile.objects(auth_user_id=auth_user_id).first()


def get_dashboard_route(profile):
    if not profile:
        return "core:home"
    if profile.role == "recruiter":
        return "core:recruiter_dashboard"
    return "core:jobseeker_dashboard"


def get_profile_completion(profile):
    if not profile:
        return {"percent": 0, "missing": [], "is_complete": False, "badge": "Starting"}

    jobseeker_fields = {
        "name": "Full name",
        "email": "Email",
        "phone": "Phone number",
        "location": "Location",
        "professional_title": "Professional title",
        "skills": "Skills",
        "education": "Education",
        "college": "College/University",
        "graduation_year": "Graduation year",
        "experience_level": "Experience level",
        "resume": "Upload your resume",
        "linkedin_url": "LinkedIn profile",
        "github_url": "GitHub/Portfolio link",
        "career_objective": "Career objective / bio",
    }

    recruiter_fields = {
        "name": "Recruiter name",
        "company_name": "Company name",
        "company_email": "Company email",
        "phone": "Phone number",
        "company_website": "Company website",
        "company_location": "Company location",
        "industry_type": "Industry type",
        "company_description": "Company description",
        "company_logo": "Add company logo",
        "hiring_role": "Hiring role / position",
        "employees_count": "Number of employees",
    }

    field_map = jobseeker_fields if profile.role == "jobseeker" else recruiter_fields
    missing = []
    completed = 0
    for field_key, label in field_map.items():
        value = getattr(profile, field_key, "")
        if isinstance(value, list):
            has_value = len(value) > 0
        else:
            has_value = bool(str(value).strip())
        if has_value:
            completed += 1
        else:
            missing.append(label)

    total = max(len(field_map), 1)
    percent = int(round((completed / total) * 100))
    is_complete = completed == total

    if percent >= 100:
        badge = "Complete"
    elif percent >= 80:
        badge = "Strong"
    elif percent >= 50:
        badge = "Growing"
    else:
        badge = "Starting"

    return {"percent": percent, "missing": missing, "is_complete": is_complete, "badge": badge}
