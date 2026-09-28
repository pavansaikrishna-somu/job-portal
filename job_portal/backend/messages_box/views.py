from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.contrib import messages
from django.utils import timezone

from mongoengine.queryset.visitor import Q

from messages_box.documents import Conversation, Message
from users.services import get_user_profile, get_dashboard_route
from jobs.documents import Job
from applications.documents import Application


def get_message_preview(msg):
    if not msg:
        return "No messages yet."
    if msg.attachment_type == "image":
        preview = "📷 Photo"
        if msg.caption:
            preview += f": {msg.caption}"
        return preview
    elif msg.attachment_type == "document":
        preview = "📄 Document"
        if msg.caption:
            preview += f": {msg.caption}"
        return preview
    return msg.content


@login_required
def inbox(request):
    user_id = request.user.id
    # Find all conversations involving this user
    conversations_queryset = Conversation.objects(Q(recruiter_id=user_id) | Q(jobseeker_id=user_id))
    conversations = list(conversations_queryset.order_by("-updated_at"))

    inbox_items = []
    for conv in conversations:
        # Get other participant name and role
        other_id = conv.recruiter_id if user_id == conv.jobseeker_id else conv.jobseeker_id
        other_profile = get_user_profile(other_id)
        other_name = other_profile.name if other_profile else "User"
        other_role = other_profile.role if other_profile else None

        # Get latest message
        latest_msg = Message.objects(conversation=conv).order_by("-created_at").first()
        preview = get_message_preview(latest_msg)
        timestamp = latest_msg.created_at if latest_msg else conv.updated_at

        # Get unread count
        unread_count = Message.objects(
            conversation=conv,
            sender_id__ne=user_id,
            is_read=False
        ).count()

        inbox_items.append({
            "conversation": conv,
            "other_name": other_name,
            "other_role": other_role,
            "preview": preview,
            "timestamp": timestamp,
            "unread_count": unread_count,
        })

    return render(request, "messages/inbox.html", {"inbox_items": inbox_items})


@login_required
def chat_window(request, conversation_id):
    conv = Conversation.get_or_none(conversation_id)
    user_id = request.user.id

    if not conv or user_id not in (conv.recruiter_id, conv.jobseeker_id):
        raise Http404("Conversation not found.")

    # Mark incoming messages as read
    Message.objects(
        conversation=conv,
        sender_id__ne=user_id,
        is_read=False
    ).update(is_read=True)

    # Get message history
    message_list = Message.objects(conversation=conv).order_by("created_at")

    # Get other participant info
    other_id = conv.recruiter_id if user_id == conv.jobseeker_id else conv.jobseeker_id
    other_profile = get_user_profile(other_id)
    other_name = other_profile.name if other_profile else "User"
    other_role = other_profile.role if other_profile else None

    # Get job context
    job = Job.get_or_none(conv.job_id)

    # Get all conversations for the left-hand panel
    conversations_queryset = Conversation.objects(Q(recruiter_id=user_id) | Q(jobseeker_id=user_id))
    conversations = list(conversations_queryset.order_by("-updated_at"))

    inbox_items = []
    for c in conversations:
        c_other_id = c.recruiter_id if user_id == c.jobseeker_id else c.jobseeker_id
        c_other_profile = get_user_profile(c_other_id)
        c_other_name = c_other_profile.name if c_other_profile else "User"
        c_other_role = c_other_profile.role if c_other_profile else None

        c_latest_msg = Message.objects(conversation=c).order_by("-created_at").first()
        c_preview = get_message_preview(c_latest_msg)
        c_timestamp = c_latest_msg.created_at if c_latest_msg else c.updated_at

        c_unread_count = Message.objects(
            conversation=c,
            sender_id__ne=user_id,
            is_read=False
        ).count()

        inbox_items.append({
            "conversation": c,
            "other_name": c_other_name,
            "other_role": c_other_role,
            "preview": c_preview,
            "timestamp": c_timestamp,
            "unread_count": c_unread_count,
        })

    # Get application context
    app = Application.objects(applicant_id=conv.jobseeker_id, job_id=conv.job_id).first()
    
    # Get scheduled interview context if application exists
    interview = None
    if app:
        from interviews.documents import Interview
        interview = Interview.objects(application_id=str(app.id), status="Scheduled").order_by("-interview_date").first()

    user_profile = get_user_profile(user_id)

    context = {
        "conversation": conv,
        "messages": message_list,
        "other_name": other_name,
        "other_role": other_role,
        "job": job,
        "inbox_items": inbox_items,
        "app": app,
        "interview": interview,
        "role": user_profile.role if user_profile else None,
    }
    return render(request, "messages/chat_window.html", context)


@login_required
def start_conversation(request, job_id):
    user_id = request.user.id
    profile = get_user_profile(user_id)

    # A Job Seeker must be authenticated and only they can start messaging recruiters
    if not profile or profile.role != "jobseeker":
        messages.error(request, "Only job seekers can initiate messaging.")
        return redirect(get_dashboard_route(profile))

    # Verify that the Job Seeker has a valid application for that job
    app = Application.objects(applicant_id=user_id, job_id=job_id).first()
    if not app:
        messages.error(request, "You must apply for this job before messaging the recruiter.")
        return redirect("applications:my_applications")

    # Verify that the job exists
    job = Job.get_or_none(job_id)
    if not job:
        raise Http404("Job not found.")

    # Verify recruiter owns the job and matches the application
    if job.recruiter_id != app.recruiter_id:
        messages.error(request, "Invalid job/recruiter relationship.")
        return redirect("applications:my_applications")

    # Find existing conversation or create it
    conv = Conversation.objects(
        job_id=job_id,
        recruiter_id=job.recruiter_id,
        jobseeker_id=user_id
    ).first()

    if not conv:
        conv = Conversation(
            job_id=job_id,
            job_title=job.title,
            recruiter_id=job.recruiter_id,
            jobseeker_id=user_id
        )
        conv.save()

    return redirect("messages_box:chat_window", conversation_id=str(conv.id))


@login_required
def start_conversation_recruiter(request, application_id):
    user_id = request.user.id
    profile = get_user_profile(user_id)

    if not profile or profile.role != "recruiter":
        messages.error(request, "Only recruiters can access this resource.")
        return redirect(get_dashboard_route(profile))

    app = Application.get_or_none(application_id)
    if not app:
        raise Http404("Application not found.")

    # Verify recruiter owns the job/application
    if app.recruiter_id != user_id:
        messages.error(request, "You are not authorized to message this applicant.")
        return redirect("applications:applicants")

    # Find existing conversation or create it
    conv = Conversation.objects(
        job_id=app.job_id,
        recruiter_id=user_id,
        jobseeker_id=app.applicant_id
    ).first()

    if not conv:
        conv = Conversation(
            job_id=app.job_id,
            job_title=app.job_title,
            recruiter_id=user_id,
            jobseeker_id=app.applicant_id
        )
        conv.save()

    return redirect("messages_box:chat_window", conversation_id=str(conv.id))


@login_required
def message_attachment_access(request, message_id, mode):
    import os
    from django.conf import settings
    from django.http import FileResponse, HttpResponseForbidden
    from django.utils.text import get_valid_filename

    if request.method not in ("GET", "HEAD"):
        return HttpResponseForbidden("Method not allowed.")

    if mode not in ("preview", "download"):
        raise Http404("Invalid access mode.")

    msg = Message.get_or_none(message_id)
    if not msg or not msg.attachment_path:
        raise Http404("File not found.")

    # Confirm the authenticated user is one of the conversation's two participants
    conv = msg.conversation
    user_id = request.user.id
    if user_id not in (conv.recruiter_id, conv.jobseeker_id):
        raise Http404("File not found.")

    # Document preview restriction
    if mode == "preview" and msg.attachment_type == "document":
        raise Http404("Preview not available for documents.")

    # Check and block path traversal attempts
    private_root = os.path.abspath(settings.PRIVATE_ATTACHMENTS_ROOT)
    relative_path = msg.attachment_path.lstrip('/')
    full_path = os.path.abspath(os.path.join(private_root, relative_path))

    if not full_path.startswith(private_root):
        return HttpResponseForbidden("Path traversal attempt detected.")

    if not os.path.exists(full_path):
        raise Http404("File not found on disk.")

    # Sanitize filename used in Content-Disposition
    safe_filename = get_valid_filename(msg.original_filename)

    response = FileResponse(open(full_path, "rb"), content_type=msg.mime_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox"
    response["Cache-Control"] = "private, no-store"

    if mode == "preview" and msg.attachment_type == "image":
        response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
    else:
        response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'

    return response
