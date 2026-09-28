from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth_views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from applications.api import MyApplicationsAPIView
from jobs.api import JobListAPIView
from users import views as users_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("users/", include("users.urls")),
    path("jobs/", include("jobs.urls")),
    path("applications/", include("applications.urls")),
    path("messages/", include("messages_box.urls")),
    path("interviews/", include("interviews.urls")),
    path("notifications/", include("notifications.urls")),
    path("ai/", include("ai.urls")),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/jobs/", JobListAPIView.as_view(), name="api_jobs"),
    path("api/my-applications/", MyApplicationsAPIView.as_view(), name="api_my_applications"),
    
    # Password Reset Flow
    path("password-reset/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),

    # Google Sign-In & allauth
    path("accounts/logout/", users_views.logout_view, name="account_logout"),
    path("accounts/", include("allauth.urls")),
]

handler404 = "core.views.custom_404"
handler403 = "core.views.custom_403"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
