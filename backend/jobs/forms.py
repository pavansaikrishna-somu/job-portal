from django import forms

from core.utils import parse_csv
from jobs.documents import Job


class JobForm(forms.Form):
    title = forms.CharField(max_length=150)
    company = forms.CharField(max_length=150)
    location = forms.CharField(max_length=120)
    salary = forms.CharField(max_length=100)
    experience = forms.CharField(max_length=80)
    skills_required = forms.CharField(help_text="Comma-separated, e.g. Python, Django, AWS")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}))

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance and not self.is_bound:
            self.fields["title"].initial = instance.title
            self.fields["company"].initial = instance.company
            self.fields["location"].initial = instance.location
            self.fields["salary"].initial = instance.salary
            self.fields["experience"].initial = instance.experience
            self.fields["skills_required"].initial = ", ".join(instance.skills_required)
            self.fields["description"].initial = instance.description

    def save(self, recruiter_profile, recruiter_user):
        payload = {
            "title": self.cleaned_data["title"],
            "company": self.cleaned_data["company"],
            "location": self.cleaned_data["location"],
            "salary": self.cleaned_data["salary"],
            "experience": self.cleaned_data["experience"],
            "skills_required": parse_csv(self.cleaned_data["skills_required"]),
            "description": self.cleaned_data["description"],
            "recruiter_id": recruiter_user.id,
            "recruiter_name": recruiter_profile.name,
        }
        if self.instance:
            for key, value in payload.items():
                setattr(self.instance, key, value)
            self.instance.save()
            return self.instance

        job = Job(**payload)
        job.save()
        return job
