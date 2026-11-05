from django.utils import timezone
from mongoengine import Document, StringField, IntField, DateTimeField, ListField, DictField, BooleanField
from mongoengine.errors import ValidationError


class AIResumeAnalysis(Document):
    user_id = IntField(required=True)
    resume_path = StringField(required=True)
    resume_hash = StringField(required=True)
    
    # Target Job Context (if supplied)
    target_job_id = StringField(default="")
    target_job_title = StringField(default="")
    target_job_desc_hash = StringField(default="")
    has_target_job = BooleanField(default=False)
    
    # Cache identity (composite of resume_hash + target_job inputs + prompt_version)
    cache_key = StringField(default="")
    prompt_version = StringField(default="v2_evidence_rubric")

    # Documented Rubric Scores (Internal Estimates)
    overall_score = IntField(default=0)
    ats_score = IntField(default=0)
    rubric_breakdown = DictField(default=dict)

    # Evidence-based Structured Fields
    explicit_skills = ListField(DictField(), default=list)
    evidence_strengths = ListField(DictField(), default=list)
    improvement_areas = ListField(DictField(), default=list)
    actionable_suggestions = ListField(StringField(), default=list)
    suggested_roles = ListField(StringField(), default=list)

    # Job Matching Evaluation (when target job is provided)
    matched_requirements = ListField(DictField(), default=list)
    missing_requirements = ListField(DictField(), default=list)
    job_fit_summary = StringField(default="")

    # Standard flat fields for backwards compatibility with tests & legacy consumers
    strengths = ListField(StringField(), default=list)
    weaknesses = ListField(StringField(), default=list)
    missing_skills = ListField(StringField(), default=list)
    suggestions = ListField(StringField(), default=list)
    recommended_roles = ListField(StringField(), default=list)

    raw_response = StringField(default="")
    created_at = DateTimeField(default=timezone.now)
    updated_at = DateTimeField(default=timezone.now)

    meta = {
        "collection": "ai_resume_analyses",
        "indexes": [
            "user_id",
            "resume_hash",
            "cache_key",
            ("user_id", "-created_at"),
            ("user_id", "cache_key"),
        ],
    }

    @classmethod
    def get_or_none(cls, analysis_id):
        try:
            return cls.objects.get(id=analysis_id)
        except (cls.DoesNotExist, ValidationError):
            return None
