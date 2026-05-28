from django.urls import path

from jobs import views

app_name = "jobs"

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("post/", views.post_job, name="post_job"),
    path("manage/", views.manage_jobs, name="manage_jobs"),
    path("edit/<str:job_id>/", views.edit_job, name="edit_job"),
    path("delete/<str:job_id>/", views.delete_job, name="delete_job"),
    path("<str:job_id>/", views.job_detail, name="job_detail"),
]
