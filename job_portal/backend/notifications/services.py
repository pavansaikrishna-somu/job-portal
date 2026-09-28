from django.utils import timezone
from notifications.documents import Notification

def create_notification(user_id, title, description, notification_type, link, deduplication_key=None):
    """
    Helper to cleanly create or update a notification for a target user with optional deduplication.
    """
    try:
        if deduplication_key:
            existing = Notification.objects(
                user_id=int(user_id),
                deduplication_key=deduplication_key
            ).first()
            if existing:
                existing.title = title
                existing.description = description
                existing.link = link
                existing.notification_type = notification_type
                existing.is_read = False
                existing.created_at = timezone.now()
                existing.save()
                return existing

        notification = Notification(
            user_id=int(user_id),
            title=title,
            description=description,
            notification_type=notification_type,
            link=link,
            is_read=False,
            created_at=timezone.now(),
            deduplication_key=deduplication_key
        )
        notification.save()
        return notification
    except Exception as e:
        # Fail silently or log error to prevent breaking host view logic
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Failed to create notification: %s", str(e))
        return None
