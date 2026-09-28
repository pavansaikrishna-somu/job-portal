from django.utils import timezone as django_timezone
from mongoengine import Document, StringField, IntField, DateTimeField
from mongoengine.errors import ValidationError


class Interview(Document):
    application_id = StringField(required=True)
    job_id = StringField(required=True)
    recruiter_id = IntField(required=True)
    jobseeker_id = IntField(required=True)
    
    title = StringField(required=True, max_length=150)
    interview_date = DateTimeField(required=True)
    start_time = StringField(required=True)  # "10:00" or similar
    duration = IntField(required=True)      # in minutes
    timezone = StringField(required=True, default="Asia/Kolkata")
    notes = StringField(default="")
    
    status = StringField(choices=("Scheduled", "Completed", "Cancelled"), default="Scheduled")
    meeting_provider = StringField(default="none")
    meeting_url = StringField(default="")
    meeting_id = StringField(default="")
    meeting_start_url = StringField(default="")
    
    created_at = DateTimeField(default=django_timezone.now)
    updated_at = DateTimeField(default=django_timezone.now)

    meta = {
        "collection": "interviews",
        "indexes": [
            ("recruiter_id", "-interview_date"),
            ("jobseeker_id", "-interview_date"),
            ("application_id", "interview_date", "status"),
        ],
    }

    @classmethod
    def get_or_none(cls, interview_id):
        try:
            return cls.objects.get(id=interview_id)
        except (cls.DoesNotExist, ValidationError):
            return None
