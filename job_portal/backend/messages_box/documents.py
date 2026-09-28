from django.utils import timezone
from mongoengine import Document, IntField, StringField, DateTimeField, BooleanField, ReferenceField, CASCADE
from mongoengine.errors import ValidationError


class Conversation(Document):
    job_id = StringField(required=True)
    job_title = StringField(required=True, max_length=150)
    recruiter_id = IntField(required=True)
    jobseeker_id = IntField(required=True)
    created_at = DateTimeField(default=timezone.now)
    updated_at = DateTimeField(default=timezone.now)

    meta = {
        "collection": "conversations",
        "indexes": [
            ("job_id", "recruiter_id", "jobseeker_id"),
            ("recruiter_id", "-updated_at"),
            ("jobseeker_id", "-updated_at"),
        ],
    }

    @classmethod
    def get_or_none(cls, conversation_id):
        try:
            return cls.objects.get(id=conversation_id)
        except (cls.DoesNotExist, ValidationError):
            return None


    def __str__(self):
        return f"Conversation: {self.job_title} (ID: {self.id})"


class Message(Document):
    conversation = ReferenceField(Conversation, required=True, reverse_delete_rule=CASCADE)
    sender_id = IntField(required=True)
    sender_name = StringField(required=True, max_length=120)
    content = StringField(required=False, default="")
    is_read = BooleanField(default=False)
    created_at = DateTimeField(default=timezone.now)

    # Optional attachments and idempotency metadata
    attachment_type = StringField(choices=("image", "document"), required=False)
    attachment_path = StringField(required=False)
    original_filename = StringField(required=False, max_length=255)
    stored_filename = StringField(required=False, max_length=255)
    file_size = IntField(required=False)
    mime_type = StringField(required=False)
    caption = StringField(required=False, max_length=1000)
    image_width = IntField(required=False)
    image_height = IntField(required=False)
    request_id = StringField(required=False)

    meta = {
        "collection": "messages",
        "indexes": [
            ("conversation", "created_at"),
            ("conversation", "is_read"),
            "request_id",
        ],
    }

    @classmethod
    def get_or_none(cls, message_id):
        try:
            return cls.objects.get(id=message_id)
        except (cls.DoesNotExist, ValidationError):
            return None

    def __str__(self):
        return f"Message by {self.sender_name}: {self.content[:30]}"
