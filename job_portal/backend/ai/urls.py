from django.urls import path

from ai import views

app_name = "ai"

urlpatterns = [
    path("analyze/", views.trigger_analysis_view, name="trigger_analysis"),
    path("analyzer/", views.analyzer_page_view, name="analyzer_page"),
    path("analysis/latest/", views.get_latest_analysis_view, name="get_latest_analysis"),
    path("analysis/<str:analysis_id>/", views.get_analysis_view, name="get_analysis"),
]
