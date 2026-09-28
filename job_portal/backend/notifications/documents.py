from mongoengine import Document, IntField, StringField, DateTimeField, BooleanField
from django.utils import timezone

class Notification(Document):
    user_id = IntField(required=True)
    title = StringField(required=True, max_length=255)
    description = StringField(required=True)
    notification_type = StringField(required=True)
    link = StringField(required=True)
    is_read = BooleanField(default=False)
    created_at = DateTimeField(default=timezone.now)
    deduplication_key = StringField(required=False, max_length=255)

    meta = {
        'collection': 'notifications',
        'indexes': [
            'user_id',
            '-created_at',
            {
                'fields': ['user_id', 'deduplication_key'],
                'unique': True,
                'partialFilterExpression': {
                    'deduplication_key': {'$type': 'string'}
                }
            }
        ]
    }

    @classmethod
    def get_or_none(cls, doc_id):
        try:
            return cls.objects.get(id=doc_id)
        except Exception:
            return None
