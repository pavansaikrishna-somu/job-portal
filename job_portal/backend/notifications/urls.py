from django.urls import path
from notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.list_notifications, name="list"),
    path("go/<str:notification_id>/", views.read_and_redirect, name="read_and_redirect"),
    path("api/unread-count/", views.unread_count_api, name="unread_count"),
    path("api/recent/", views.recent_notifications_api, name="recent"),
    path("api/read/<str:notification_id>/", views.mark_as_read_api, name="read"),
    path("api/read-all/", views.mark_all_read_api, name="read_all"),
]
