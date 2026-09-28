import json
import os
import uuid
import zipfile
import shutil
from PIL import Image

from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings

from mongoengine.queryset.visitor import Q
from messages_box.documents import Conversation, Message


def handle_attachment_upload(uploaded_file, attachment_type):
    # Ensure private root exists
    private_root = os.path.abspath(settings.PRIVATE_ATTACHMENTS_ROOT)
    tmp_dir = os.path.join(private_root, "tmp")
    perm_dir = os.path.join(private_root, "attachments")
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(perm_dir, exist_ok=True)

    # Extract extension and generate a safe UUID name
    orig_name = uploaded_file.name
    ext = os.path.splitext(orig_name.lower())[1]
    unique_id = str(uuid.uuid4())
    temp_filename = f"{unique_id}_tmp{ext}"
    perm_filename = f"{unique_id}{ext}"

    temp_path = os.path.join(tmp_dir, temp_filename)
    perm_path = os.path.join(perm_dir, perm_filename)

    # Write uploaded content atomically to temporary file
    try:
        with open(temp_path, "wb+") as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False, "Failed to write file to temporary location."

    file_size = os.path.getsize(temp_path)

    # Size validations
    if attachment_type == "image":
        if file_size > 5 * 1024 * 1024:
            os.remove(temp_path)
            return False, "Image size exceeds limit of 5 MB."
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            os.remove(temp_path)
            return False, "Unsupported image file extension."
    elif attachment_type == "document":
        if file_size > 10 * 1024 * 1024:
            os.remove(temp_path)
            return False, "Document size exceeds limit of 10 MB."
        if ext not in (".pdf", ".doc", ".docx"):
            os.remove(temp_path)
            return False, "Unsupported document file extension."
    else:
        os.remove(temp_path)
        return False, "Unsupported attachment type."

    # Read magic signature
    try:
        with open(temp_path, "rb") as f:
            header = f.read(16)
    except Exception:
        os.remove(temp_path)
        return False, "Failed to read file signature."

    mime_type = ""
    width, height = None, None

    if attachment_type == "image":
        is_jpeg = header.startswith(b'\xff\xd8\xff')
        is_png = header.startswith(b'\x89PNG\r\n')
        is_webp = header.startswith(b'RIFF') and b'WEBP' in header[8:16]

        if not (is_jpeg or is_png or is_webp):
            os.remove(temp_path)
            return False, "Invalid image content structure."

        if is_jpeg:
            mime_type = "image/jpeg"
        elif is_png:
            mime_type = "image/png"
        else:
            mime_type = "image/webp"

        # Verify image using Pillow
        try:
            with Image.open(temp_path) as img:
                img.verify()
            with Image.open(temp_path) as img:
                width, height = img.size
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False, "Corrupted or invalid image file."

    elif attachment_type == "document":
        is_pdf = header.startswith(b'%PDF')
        is_doc = header.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
        is_docx = header.startswith(b'PK\x03\x04')

        if not (is_pdf or is_doc or is_docx):
            os.remove(temp_path)
            return False, "Invalid document content structure."

        if is_pdf:
            mime_type = "application/pdf"
        elif is_doc:
            mime_type = "application/msword"
        else:
            mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            try:
                if not zipfile.is_zipfile(temp_path):
                    os.remove(temp_path)
                    return False, "Invalid Word Document container structure."
                with zipfile.ZipFile(temp_path) as zf:
                    namelist = zf.namelist()
                    if "[Content_Types].xml" not in namelist or "word/document.xml" not in namelist:
                        os.remove(temp_path)
                        return False, "Word Document container is not a valid DOCX layout."
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return False, "Corrupted Word Document archive."

    # Move atomically to permanent folder
    try:
        shutil.move(temp_path, perm_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False, "Failed to persist attachment to storage."

    return True, {
        "stored_filename": perm_filename,
        "original_filename": orig_name,
        "attachment_path": f"attachments/{perm_filename}",
        "file_size": file_size,
        "mime_type": mime_type,
        "image_width": width,
        "image_height": height,
        "permanent_path": perm_path,
    }


@login_required
def get_messages(request, conversation_id):
    conv = Conversation.get_or_none(conversation_id)
    user_id = request.user.id

    if not conv or user_id not in (conv.recruiter_id, conv.jobseeker_id):
        return JsonResponse({"error": "Conversation not found or unauthorized."}, status=403)

    # Mark incoming messages as read
    Message.objects(
        conversation=conv,
        sender_id__ne=user_id,
        is_read=False
    ).update(is_read=True)

    # Load messages
    messages_list = Message.objects(conversation=conv).order_by("created_at")
    payload = [
        {
            "id": str(msg.id),
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "content": msg.content,
            "is_read": msg.is_read,
            "created_at": msg.created_at.strftime("%I:%M %p | %b %d"),
            "attachment_type": msg.attachment_type,
            "attachment_url": f"/messages/attachments/{msg.id}/preview/" if msg.attachment_type else None,
            "download_url": f"/messages/attachments/{msg.id}/download/" if msg.attachment_type else None,
            "original_filename": msg.original_filename,
            "file_size": msg.file_size,
            "caption": msg.caption,
            "image_width": msg.image_width,
            "image_height": msg.image_height,
        }
        for msg in messages_list
    ]

    return JsonResponse(payload, safe=False)


@login_required
def send_message(request, conversation_id):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    conv = Conversation.get_or_none(conversation_id)
    user_id = request.user.id

    if not conv or user_id not in (conv.recruiter_id, conv.jobseeker_id):
        return JsonResponse({"error": "Conversation not found or unauthorized."}, status=403)

    # Idempotency / Duplicate protection
    request_id = request.POST.get("request_id") or request.headers.get("X-Request-ID")
    if request_id:
        existing_msg = Message.objects(conversation=conv, request_id=request_id).first()
        if existing_msg:
            return JsonResponse({
                "id": str(existing_msg.id),
                "sender_id": existing_msg.sender_id,
                "sender_name": existing_msg.sender_name,
                "content": existing_msg.content,
                "created_at": existing_msg.created_at.strftime("%I:%M %p | %b %d"),
                "attachment_type": existing_msg.attachment_type,
                "attachment_url": f"/messages/attachments/{existing_msg.id}/preview/" if existing_msg.attachment_type else None,
                "download_url": f"/messages/attachments/{existing_msg.id}/download/" if existing_msg.attachment_type else None,
                "original_filename": existing_msg.original_filename,
                "file_size": existing_msg.file_size,
                "caption": existing_msg.caption,
                "image_width": existing_msg.image_width,
                "image_height": existing_msg.image_height,
            }, status=201)

    # Try parsing JSON or form-encoded POST
    content = ""
    caption = ""
    attachment_type = ""
    uploaded_file = None

    if request.content_type == "application/json":
        try:
            data = json.loads(request.body.decode('utf-8'))
            content = data.get("content", "")
            request_id = data.get("request_id") or request_id
        except Exception:
            return JsonResponse({"error": "Invalid JSON request body."}, status=400)
    else:
        content = request.POST.get("content", "")
        caption = request.POST.get("caption", "")
        attachment_type = request.POST.get("attachment_type", "")
        uploaded_file = request.FILES.get("file")

    content = content.strip()
    caption = caption.strip()

    # If attachment is provided, validate it
    attachment_data = None
    if uploaded_file:
        if not attachment_type or attachment_type not in ("image", "document"):
            return JsonResponse({"error": "Invalid or missing attachment type."}, status=400)

        success, result = handle_attachment_upload(uploaded_file, attachment_type)
        if not success:
            return JsonResponse({"error": result}, status=400)
        attachment_data = result

    # Validate limits
    if len(content) > 2000:
        if attachment_data and os.path.exists(attachment_data["permanent_path"]):
            os.remove(attachment_data["permanent_path"])
        return JsonResponse({"error": "Message exceeds limit of 2000 characters."}, status=400)

    if len(caption) > 1000:
        if attachment_data and os.path.exists(attachment_data["permanent_path"]):
            os.remove(attachment_data["permanent_path"])
        return JsonResponse({"error": "Caption exceeds limit of 1000 characters."}, status=400)

    # Message empty check
    if not content and not caption and not attachment_data:
        return JsonResponse({"error": "Message content cannot be completely empty."}, status=400)

    # Get sender profile/name
    sender_name = request.user.first_name or request.user.username

    # Create Message document
    msg = Message(
        conversation=conv,
        sender_id=user_id,
        sender_name=sender_name,
        content=content,
        is_read=False,
        created_at=timezone.now(),
        request_id=request_id
    )

    if attachment_data:
        msg.attachment_type = attachment_type
        msg.attachment_path = attachment_data["attachment_path"]
        msg.original_filename = attachment_data["original_filename"]
        msg.stored_filename = attachment_data["stored_filename"]
        msg.file_size = attachment_data["file_size"]
        msg.mime_type = attachment_data["mime_type"]
        msg.caption = caption
        msg.image_width = attachment_data["image_width"]
        msg.image_height = attachment_data["image_height"]

    try:
        msg.save()
    except Exception as e:
        if attachment_data and os.path.exists(attachment_data["permanent_path"]):
            os.remove(attachment_data["permanent_path"])
        return JsonResponse({"error": "Failed to save message to database."}, status=500)

    # Determine preview content for notification and latest preview
    if attachment_data:
        if attachment_type == "image":
            notif_desc = "📷 Photo"
            if caption:
                notif_desc += f": {caption}"
        else:
            notif_desc = "📄 Document"
            if caption:
                notif_desc += f": {caption}"
    else:
        notif_desc = content

    # Notify recipient
    recipient_id = conv.recruiter_id if user_id == conv.jobseeker_id else conv.jobseeker_id
    from notifications.services import create_notification
    create_notification(
        user_id=recipient_id,
        title=f"New message from {sender_name}",
        description=notif_desc[:100] + ("..." if len(notif_desc) > 100 else ""),
        notification_type="message_new",
        link=f"/messages/chat/{conv.id}/",
        deduplication_key=f"message_{msg.id}"
    )

    # Update conversation updated_at
    conv.updated_at = timezone.now()
    conv.save()

    return JsonResponse({
        "id": str(msg.id),
        "sender_id": msg.sender_id,
        "sender_name": msg.sender_name,
        "content": msg.content,
        "created_at": msg.created_at.strftime("%I:%M %p | %b %d"),
        "attachment_type": msg.attachment_type,
        "attachment_url": f"/messages/attachments/{msg.id}/preview/" if msg.attachment_type else None,
        "download_url": f"/messages/attachments/{msg.id}/download/" if msg.attachment_type else None,
        "original_filename": msg.original_filename,
        "file_size": msg.file_size,
        "caption": msg.caption,
        "image_width": msg.image_width,
        "image_height": msg.image_height,
    }, status=201)


@login_required
def unread_count(request):
    user_id = request.user.id
    # Get all conversations involving this user
    my_convs = list(Conversation.objects(Q(recruiter_id=user_id) | Q(jobseeker_id=user_id)))
    
    if not my_convs:
        return JsonResponse({"unread_count": 0})

    # Count all unread messages in those conversations where current user is NOT the sender
    count = Message.objects(
        conversation__in=my_convs,
        sender_id__ne=user_id,
        is_read=False
    ).count()

    return JsonResponse({"unread_count": count})
