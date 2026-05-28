from django.utils import timezone
from mongoengine import DateTimeField, Document, IntField, ListField, StringField
from mongoengine.errors import ValidationError


class Job(Document):
    title = StringField(required=True, max_length=150)
    company = StringField(required=True, max_length=150)
    location = StringField(required=True, max_length=120)
    salary = StringField(required=True, max_length=100)
    experience = StringField(required=True, max_length=80)
    skills_required = ListField(StringField(max_length=80), default=list)
    description = StringField(required=True)
    recruiter_id = IntField(required=True)
    recruiter_name = StringField(required=True, max_length=120)
    created_at = DateTimeField(default=timezone.now)

    meta = {
        "collection": "jobs",
        "indexes": ["title", "company", "location", "recruiter_id", "-created_at"],
    }

    @classmethod
    def get_or_none(cls, job_id):
        try:
            return cls.objects.get(id=job_id)
        except (cls.DoesNotExist, ValidationError):
            return None
