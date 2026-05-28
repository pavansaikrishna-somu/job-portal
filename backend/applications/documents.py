from django.utils import timezone
from mongoengine import DateTimeField, Document, IntField, StringField
from mongoengine.errors import ValidationError


class Application(Document):
    applicant_id = IntField(required=True)
    applicant_name = StringField(required=True, max_length=120)
    applicant_email = StringField(required=True, max_length=200, default="")
    job_id = StringField(required=True)
    job_title = StringField(required=True, max_length=150)
    recruiter_id = IntField(required=True)
    resume = StringField(required=True)
    cover_letter = StringField(required=True)
    status = StringField(choices=("Pending", "Reviewed", "Shortlisted", "Rejected"), default="Pending")
    applied_at = DateTimeField(default=timezone.now)

    meta = {
        "collection": "applications",
        "indexes": ["applicant_id", "recruiter_id", "job_id", "status", "-applied_at"],
    }

    @classmethod
    def get_or_none(cls, application_id):
        try:
            return cls.objects.get(id=application_id)
        except (cls.DoesNotExist, ValidationError):
            return None
