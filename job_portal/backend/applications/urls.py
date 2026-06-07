from django.urls import path

from applications import views

app_name = "applications"

urlpatterns = [
    path("apply/<str:job_id>/", views.apply_job, name="apply_job"),
    path("my/", views.my_applications, name="my_applications"),
    path("applicants/", views.applicants, name="applicants"),
    path("status/<str:application_id>/<str:new_status>/", views.update_status, name="update_status"),
    path("resume/<str:application_id>/<str:mode>/", views.resume_access, name="resume_access"),
]
