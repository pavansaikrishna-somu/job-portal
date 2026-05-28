import os

from django import forms
from django.conf import settings


class ApplicationForm(forms.Form):
    resume = forms.FileField()
    cover_letter = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))

    def clean_resume(self):
        resume = self.cleaned_data["resume"]
        extension = os.path.splitext(resume.name)[1].lower()
        allowed_extensions = getattr(settings, "ALLOWED_RESUME_EXTENSIONS", [".pdf", ".doc", ".docx"])
        if extension not in allowed_extensions:
            raise forms.ValidationError("Only PDF, DOC, and DOCX files are allowed.")

        max_size = getattr(settings, "MAX_RESUME_UPLOAD_SIZE", 5 * 1024 * 1024)
        if resume.size > max_size:
            raise forms.ValidationError("Resume file is too large. Max 5MB allowed.")
        return resume
