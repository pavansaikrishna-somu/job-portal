from django.utils import timezone
from mongoengine import Document, StringField, IntField, DateTimeField, ListField
from mongoengine.errors import ValidationError


class AIResumeAnalysis(Document):
    user_id = IntField(required=True)
    resume_path = StringField(required=True)
    resume_hash = StringField(required=True)

    # Structured analysis results
    overall_score = IntField(required=True)
    ats_score = IntField(required=True)
    strengths = ListField(StringField(), default=list)
    weaknesses = ListField(StringField(), default=list)
    missing_skills = ListField(StringField(), default=list)
    suggestions = ListField(StringField(), default=list)
    recommended_roles = ListField(StringField(), default=list)

    raw_response = StringField(default="")
    created_at = DateTimeField(default=timezone.now)

    meta = {
        "collection": "ai_resume_analyses",
        "indexes": [
            "user_id",
            "resume_hash",
            ("user_id", "-created_at"),
        ],
    }

    @classmethod
    def get_or_none(cls, analysis_id):
        try:
            return cls.objects.get(id=analysis_id)
        except (cls.DoesNotExist, ValidationError):
            return None
