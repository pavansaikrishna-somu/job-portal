import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden, HttpResponse
from django.contrib import messages
from django.utils import timezone

from core.decorators import role_required
from interviews.documents import Interview
from applications.documents import Application
from jobs.documents import Job
from users.services import get_user_profile, get_dashboard_route
from messages_box.documents import Conversation, Message
from interviews.zoom_service import is_zoom_configured, create_zoom_meeting, delete_zoom_meeting, update_zoom_meeting
from notifications.services import create_notification


@role_required("recruiter")
def schedule_interview(request, application_id):
    user_id = request.user.id

    app = Application.get_or_none(application_id)
    if not app or app.recruiter_id != user_id:
        raise Http404("Application not found.")  # Prevent enumeration of other users' applications

    if app.status != "Shortlisted":
        messages.warning(request, "Only shortlisted candidates should have interviews scheduled.")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        date_str = request.POST.get("date", "").strip()
        time_str = request.POST.get("start_time", "").strip()
        duration_str = request.POST.get("duration", "").strip()
        tz_str = request.POST.get("timezone", "Asia/Kolkata").strip()
        notes = request.POST.get("notes", "").strip()

        # Validations
        if not title or not date_str or not time_str or not duration_str:
            messages.error(request, "All fields except notes are required.")
            return render(request, "interviews/schedule.html", {"application": app})

        try:
            duration = int(duration_str)
            if duration <= 0 or duration > 1440:
                raise ValueError()
        except ValueError:
            messages.error(request, "Please enter a valid duration in minutes (1 - 1440).")
            return render(request, "interviews/schedule.html", {"application": app})

        try:
            # Parse date and time
            dt_str = f"{date_str} {time_str}"
            dt = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.datetime.strptime(dt_str, fmt)
                    break
                except ValueError:
                    continue
            
            if dt is None:
                raise ValueError("Invalid format")
        except ValueError:
            messages.error(request, "Please enter a valid date and time.")
            return render(request, "interviews/schedule.html", {"application": app})

        # Ensure date/time is in the future
        tz_info = timezone.get_current_timezone()
        dt_aware = timezone.make_aware(dt, tz_info)
        if dt_aware < timezone.now():
            messages.error(request, "Interview date and time must be in the future.")
            return render(request, "interviews/schedule.html", {"application": app})

        # Prevent duplicate interviews for same application/date/time
        duplicate = Interview.objects(
            application_id=application_id,
            interview_date=dt_aware,
            status="Scheduled"
        ).first()
        if duplicate:
            messages.error(request, "An active interview is already scheduled for this application at this date/time.")
            return render(request, "interviews/schedule.html", {"application": app})

        # Create Interview
        interview = Interview(
            application_id=application_id,
            job_id=app.job_id,
            recruiter_id=user_id,
            jobseeker_id=app.applicant_id,
            title=title,
            interview_date=dt_aware,
            start_time=time_str,
            duration=duration,
            timezone=tz_str,
            notes=notes,
            status="Scheduled",
            meeting_provider="none",
            meeting_url="",
            meeting_id="",
            meeting_start_url=""
        )
        interview.save()

        # Zoom meeting creation flow
        if is_zoom_configured():
            zoom_res = create_zoom_meeting(interview)
            if zoom_res:
                interview.meeting_provider = "zoom"
                interview.meeting_id = zoom_res["id"]
                interview.meeting_url = zoom_res["join_url"]
                interview.meeting_start_url = zoom_res["start_url"]
                interview.save()
                messages.success(request, f"Interview '{title}' scheduled and Zoom meeting created successfully.")
            else:
                interview.meeting_provider = "failed"
                interview.save()
                messages.warning(request, f"Interview '{title}' scheduled, but Zoom meeting creation failed. You can retry link generation.")
        else:
            messages.success(request, f"Interview '{title}' scheduled successfully. (Zoom is not configured)")

        # Notify recipient
        recipient_id = interview.jobseeker_id if user_id == interview.recruiter_id else interview.recruiter_id
        create_notification(
            user_id=recipient_id,
            title="Interview Scheduled",
            description=f"Your interview '{title}' has been scheduled for {date_str} at {time_str}.",
            notification_type="interview_scheduled",
            link=f"/interviews/detail/{interview.id}/",
            deduplication_key=f"interview_{interview.id}_scheduled"
        )

        # Notify in Message Box
        try:
            conv = Conversation.objects(
                job_id=app.job_id,
                recruiter_id=user_id,
                jobseeker_id=app.applicant_id
            ).first()

            if not conv:
                job = Job.get_or_none(app.job_id)
                conv = Conversation(
                    job_id=app.job_id,
                    job_title=job.title if job else app.job_title,
                    recruiter_id=user_id,
                    jobseeker_id=app.applicant_id
                )
                conv.save()

            sys_msg = Message(
                conversation=conv,
                sender_id=user_id,
                sender_name=request.user.first_name or request.user.username,
                content=f"SYSTEM: Interview '{title}' has been scheduled for {date_str} at {time_str} ({duration} mins).",
                is_read=False,
                created_at=timezone.now()
            )
            sys_msg.save()
            conv.updated_at = timezone.now()
            conv.save()
        except Exception:
            pass

        return redirect("interviews:list_interviews")

    return render(request, "interviews/schedule.html", {"application": app})


@login_required
def list_interviews(request):
    user_id = request.user.id
    profile = get_user_profile(user_id)
    role = profile.role if profile else "jobseeker"

    if role == "recruiter":
        upcoming = Interview.objects(recruiter_id=user_id, status="Scheduled").order_by("interview_date")
        past = Interview.objects(recruiter_id=user_id, status__in=["Completed", "Cancelled"]).order_by("-interview_date")
    else:
        upcoming = Interview.objects(jobseeker_id=user_id, status="Scheduled").order_by("interview_date")
        past = Interview.objects(jobseeker_id=user_id, status__in=["Completed", "Cancelled"]).order_by("-interview_date")

    # Helper function to enrich items
    def enrich(interviews_qs):
        enriched = []
        for item in interviews_qs:
            other_id = item.jobseeker_id if role == "recruiter" else item.recruiter_id
            other_prof = get_user_profile(other_id)
            other_name = other_prof.name if other_prof else "User"
            job = Job.get_or_none(item.job_id)
            enriched.append({
                "interview": item,
                "other_name": other_name,
                "job_title": job.title if job else "Job Position",
                "company": job.company if job else "Company",
                "iso_date": item.interview_date.isoformat()
            })
        return enriched

    return render(request, "interviews/list.html", {
        "upcoming": enrich(upcoming),
        "past": enrich(past),
        "role": role
    })


@login_required
def view_interview(request, interview_id):
    import urllib.parse
    interview = Interview.get_or_none(interview_id)
    if not interview:
        raise Http404("Interview not found.")

    user_id = request.user.id
    if user_id not in (interview.recruiter_id, interview.jobseeker_id):
        raise Http404("Interview not found.")

    profile = get_user_profile(user_id)
    role = profile.role if profile else "jobseeker"

    # Enforce strict role and ownership checks
    if role == "recruiter":
        job = Job.get_or_none(interview.job_id)
        if job and job.recruiter_id != user_id:
            raise Http404("Interview not found.")
    else:
        app = Application.get_or_none(interview.application_id)
        if app and app.applicant_id != user_id:
            raise Http404("Interview not found.")

    other_id = interview.jobseeker_id if role == "recruiter" else interview.recruiter_id
    other_prof = get_user_profile(other_id)
    other_name = other_prof.name if other_prof else "User"

    job = Job.get_or_none(interview.job_id)

    # Role-specific meeting link setup: never pass start_url to candidates
    meeting_access_url = ""
    if interview.meeting_provider == "zoom":
        if role == "recruiter":
            meeting_access_url = interview.meeting_start_url
        else:
            meeting_access_url = interview.meeting_url

    # Calculate timezone-aware timestamps (UTC)
    start_dt = interview.interview_date
    if start_dt.tzinfo is None:
        start_dt = timezone.make_aware(start_dt, datetime.timezone.utc)
    else:
        start_dt = start_dt.astimezone(datetime.timezone.utc)
        
    end_dt = start_dt + datetime.timedelta(minutes=interview.duration)
    join_window_start = start_dt - datetime.timedelta(minutes=10)
    
    now_utc = timezone.now()
    if now_utc.tzinfo is None:
        now_utc = timezone.make_aware(now_utc, datetime.timezone.utc)
    else:
        now_utc = now_utc.astimezone(datetime.timezone.utc)

    # Server-authoritative join-time status computation
    if interview.status == "Cancelled":
        join_time_status = "Cancelled"
    elif interview.status == "Completed":
        join_time_status = "Completed"
    elif now_utc < join_window_start:
        join_time_status = "Upcoming"
    elif join_window_start <= now_utc < start_dt:
        join_time_status = "Ready to join"
    else:
        join_time_status = "In Progress"

    # Google Calendar URL Generator
    dates_str = f"{start_dt.strftime('%Y%m%dT%H%M%SZ')}/{end_dt.strftime('%Y%m%dT%H%M%SZ')}"
    gcal_params = {
        "action": "TEMPLATE",
        "text": interview.title,
        "dates": dates_str,
        "details": f"Interview for CareerConnect.\nDuration: {interview.duration} mins\nMeeting Link: {meeting_access_url}\n\nNotes:\n{interview.notes}",
        "location": meeting_access_url or "Online (Zoom)"
    }
    google_calendar_url = "https://www.google.com/calendar/render?" + urllib.parse.urlencode(gcal_params)

    return render(request, "interviews/detail.html", {
        "interview": interview,
        "other_name": other_name,
        "job": job,
        "role": role,
        "is_zoom_configured": is_zoom_configured(),
        "meeting_access_url": meeting_access_url,
        "join_time_status": join_time_status,
        "google_calendar_url": google_calendar_url,
        "interview_iso_date": start_dt.isoformat(),
        "join_window_start_iso": join_window_start.isoformat(),
        "end_date_iso": end_dt.isoformat()
    })


@role_required("recruiter")
def update_interview_status(request, interview_id, new_status):
    user_id = request.user.id
    interview = Interview.get_or_none(interview_id)
    if not interview or interview.recruiter_id != user_id:
        raise Http404("Interview not found.")

    if new_status not in ("Completed", "Cancelled"):
        messages.error(request, "Invalid status update.")
        return redirect("interviews:list_interviews")

    old_status = interview.status
    interview.status = new_status
    interview.updated_at = timezone.now()
    if new_status == "Cancelled" and old_status != "Cancelled":
        if interview.meeting_provider == "zoom":
            delete_zoom_meeting(interview)
        interview.meeting_url = ""
        interview.meeting_start_url = ""
    interview.save()

    # If Cancelled, notify applicant in chat and cancel Zoom meeting
    if new_status == "Cancelled" and old_status != "Cancelled":
            
        # Notify other participant
        recipient_id = interview.jobseeker_id if user_id == interview.recruiter_id else interview.recruiter_id
        create_notification(
            user_id=recipient_id,
            title="Interview Cancelled",
            description=f"The interview '{interview.title}' has been cancelled.",
            notification_type="interview_cancelled",
            link=f"/interviews/detail/{interview.id}/",
            deduplication_key=f"interview_{interview.id}_cancelled"
        )
            
        try:
            conv = Conversation.objects(
                job_id=interview.job_id,
                recruiter_id=user_id,
                jobseeker_id=interview.jobseeker_id
            ).first()
            if conv:
                sys_msg = Message(
                    conversation=conv,
                    sender_id=user_id,
                    sender_name=request.user.first_name or request.user.username,
                    content=f"SYSTEM: The interview '{interview.title}' has been cancelled.",
                    is_read=False,
                    created_at=timezone.now()
                )
                sys_msg.save()
                conv.updated_at = timezone.now()
                conv.save()
        except Exception:
            pass

    messages.success(request, f"Interview status updated to {new_status}.")
    return redirect("interviews:list_interviews")


@role_required("recruiter")
def retry_zoom(request, interview_id):
    interview = Interview.get_or_none(interview_id)
    if not interview or interview.recruiter_id != request.user.id:
        raise Http404("Interview not found.")

    if interview.status in ("Cancelled", "Completed"):
        messages.error(request, f"Cannot generate a Zoom link for a {interview.status.lower()} interview.")
        return redirect("interviews:view_interview", interview_id=interview_id)

    if not is_zoom_configured():
        messages.error(request, "Zoom integration is not configured.")
        return redirect("interviews:view_interview", interview_id=interview_id)

    zoom_res = create_zoom_meeting(interview)
    if zoom_res:
        interview.meeting_provider = "zoom"
        interview.meeting_id = zoom_res["id"]
        interview.meeting_url = zoom_res["join_url"]
        interview.meeting_start_url = zoom_res["start_url"]
        interview.save()
        messages.success(request, "Zoom meeting link generated successfully.")
    else:
        messages.error(request, "Failed to connect to Zoom. Please check configuration or try again.")

    return redirect("interviews:view_interview", interview_id=interview_id)


@role_required("recruiter")
def reschedule_interview(request, interview_id):
    user_id = request.user.id
    interview = Interview.get_or_none(interview_id)
    if not interview or interview.recruiter_id != request.user.id:
        raise Http404("Interview not found.")

    if interview.status in ("Completed", "Cancelled"):
        messages.error(request, f"Cannot reschedule a {interview.status.lower()} interview.")
        return redirect("interviews:view_interview", interview_id=interview.id)

    app = Application.get_or_none(interview.application_id)
    if not app or app.recruiter_id != request.user.id:
        raise Http404("Application not found.")

    if request.method == "POST":
        date_str = request.POST.get("date", "").strip()
        time_str = request.POST.get("start_time", "").strip()
        duration_str = request.POST.get("duration", "").strip()
        tz_str = request.POST.get("timezone", "Asia/Kolkata").strip()
        notes = request.POST.get("notes", "").strip()

        # Validations
        if not date_str or not time_str or not duration_str:
            messages.error(request, "Date, start time, and duration are required.")
            return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})

        try:
            duration = int(duration_str)
            if duration <= 0 or duration > 1440:
                raise ValueError()
        except ValueError:
            messages.error(request, "Please enter a valid duration in minutes (1 - 1440).")
            return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})

        try:
            dt_str = f"{date_str} {time_str}"
            dt = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.datetime.strptime(dt_str, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                raise ValueError()
        except ValueError:
            messages.error(request, "Please enter a valid date and time.")
            return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})

        # Ensure future date/time
        tz_info = timezone.get_current_timezone()
        dt_aware = timezone.make_aware(dt, tz_info)
        if dt_aware < timezone.now():
            messages.error(request, "Interview date and time must be in the future.")
            return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})

        # Prevent duplicate active interview for same application/date/time
        duplicate = Interview.objects(
            id__ne=interview.id,
            application_id=interview.application_id,
            interview_date=dt_aware,
            status="Scheduled"
        ).first()
        if duplicate:
            messages.error(request, "An active interview is already scheduled for this application at this date/time.")
            return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})

        # Sync with Zoom if configured
        zoom_sync_success = True
        if interview.meeting_provider == "zoom" and interview.meeting_id:
            original_date = interview.interview_date
            original_start = interview.start_time
            original_duration = interview.duration
            original_timezone = interview.timezone
            original_notes = interview.notes
            
            interview.interview_date = dt_aware
            interview.start_time = time_str
            interview.duration = duration
            interview.timezone = tz_str
            interview.notes = notes
            
            zoom_sync_success = update_zoom_meeting(interview)
            if not zoom_sync_success:
                # Rollback locally and show error
                interview.interview_date = original_date
                interview.start_time = original_start
                interview.duration = original_duration
                interview.timezone = original_timezone
                interview.notes = original_notes
                messages.error(request, "Failed to update meeting details on Zoom. Schedule changes have not been saved.")
                return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})
        else:
            interview.interview_date = dt_aware
            interview.start_time = time_str
            interview.duration = duration
            interview.timezone = tz_str
            interview.notes = notes

        interview.updated_at = timezone.now()
        interview.save()
        messages.success(request, "Interview rescheduled successfully.")

        # Notify other participant
        recipient_id = interview.jobseeker_id if user_id == interview.recruiter_id else interview.recruiter_id
        create_notification(
            user_id=recipient_id,
            title="Interview Rescheduled",
            description=f"Your interview '{interview.title}' has been rescheduled to {date_str} at {time_str}.",
            notification_type="interview_rescheduled",
            link=f"/interviews/detail/{interview.id}/",
            deduplication_key=f"interview_{interview.id}_rescheduled_{interview.interview_date.date().isoformat()}_{interview.start_time.replace(':', '_')}"
        )

        # Notify via Message Box
        try:
            conv = Conversation.objects(
                job_id=interview.job_id,
                recruiter_id=user_id,
                jobseeker_id=interview.jobseeker_id
            ).first()
            if conv:
                sys_msg = Message(
                    conversation=conv,
                    sender_id=user_id,
                    sender_name=request.user.first_name or request.user.username,
                    content=f"SYSTEM: The interview '{interview.title}' has been rescheduled to {date_str} at {time_str} ({duration} mins).",
                    is_read=False,
                    created_at=timezone.now()
                )
                sys_msg.save()
                conv.updated_at = timezone.now()
                conv.save()
        except Exception:
            pass

        return redirect("interviews:view_interview", interview_id=interview.id)

    return render(request, "interviews/reschedule.html", {"interview": interview, "application": app})


def escape_ics_text(text):
    if not text:
        return ""
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return text


@login_required
def export_interview_ics(request, interview_id):
    interview = Interview.get_or_none(interview_id)
    if not interview:
        raise Http404("Interview not found.")

    user_id = request.user.id
    if user_id not in (interview.recruiter_id, interview.jobseeker_id):
        raise Http404("Interview not found.")

    start_dt = interview.interview_date
    if start_dt.tzinfo is None:
        start_dt = timezone.make_aware(start_dt, datetime.timezone.utc)
    else:
        start_dt = start_dt.astimezone(datetime.timezone.utc)
        
    end_dt = start_dt + datetime.timedelta(minutes=interview.duration)

    now_utc = timezone.now()
    if now_utc.tzinfo is None:
        now_utc = timezone.make_aware(now_utc, datetime.timezone.utc)
    else:
        now_utc = now_utc.astimezone(datetime.timezone.utc)
        
    dtstamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    dtstart = start_dt.strftime("%Y%m%dT%H%M%SZ")
    dtend = end_dt.strftime("%Y%m%dT%H%M%SZ")

    join_url = interview.meeting_url if interview.meeting_provider == "zoom" else ""

    summary = escape_ics_text(interview.title)
    description = escape_ics_text(
        f"Interview for CareerConnect.\n"
        f"Duration: {interview.duration} mins\n"
        f"Meeting Link: {join_url}\n\n"
        f"Notes:\n{interview.notes}"
    )
    location = escape_ics_text(join_url if join_url else "Online (Zoom)")
    uid = f"interview_{interview.id}@careerconnect.local"

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CareerConnect//Interview Export//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{location}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR"
    ]

    ics_content = "\r\n".join(ics_lines) + "\r\n"

    response = HttpResponse(ics_content, content_type="text/calendar")
    response["Content-Disposition"] = f'attachment; filename="interview_{interview.id}.ics"'
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
