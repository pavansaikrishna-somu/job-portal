from django.urls import path
from interviews import views

app_name = "interviews"

urlpatterns = [
    path("", views.list_interviews, name="list_interviews"),
    path("schedule/<str:application_id>/", views.schedule_interview, name="schedule_interview"),
    path("detail/<str:interview_id>/", views.view_interview, name="view_interview"),
    path("status/<str:interview_id>/<str:new_status>/", views.update_interview_status, name="update_status"),
    path("retry-zoom/<str:interview_id>/", views.retry_zoom, name="retry_zoom"),
    path("reschedule/<str:interview_id>/", views.reschedule_interview, name="reschedule_interview"),
    path("export/<str:interview_id>/", views.export_interview_ics, name="export_ics"),
]
