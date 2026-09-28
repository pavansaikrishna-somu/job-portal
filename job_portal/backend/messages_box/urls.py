from django.urls import path
from messages_box import views, api

app_name = "messages_box"

urlpatterns = [
    # HTML Views
    path("", views.inbox, name="inbox"),
    path("chat/<str:conversation_id>/", views.chat_window, name="chat_window"),
    path("start/<str:job_id>/", views.start_conversation, name="start_conversation"),
    path("start-recruiter/<str:application_id>/", views.start_conversation_recruiter, name="start_conversation_recruiter"),
    path("attachments/<str:message_id>/<str:mode>/", views.message_attachment_access, name="message_attachment_access"),
    
    # API endpoints under /messages/api/
    path("api/unread-count/", api.unread_count, name="api_unread_count_nested"),
    path("api/<str:conversation_id>/", api.get_messages, name="api_get_messages_nested"),
    path("api/<str:conversation_id>/send/", api.send_message, name="api_send_message_nested"),

    # Alternative API routes under /messages/api/messages/ to be highly compatible
    path("api/messages/unread-count/", api.unread_count, name="api_unread_count"),
    path("api/messages/<str:conversation_id>/", api.get_messages, name="api_get_messages"),
    path("api/messages/<str:conversation_id>/send/", api.send_message, name="api_send_message"),
]
