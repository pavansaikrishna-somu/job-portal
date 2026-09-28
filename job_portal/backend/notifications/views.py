from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden, Http404
from django.views.decorators.http import require_POST
from django.utils.timesince import timesince
from django.core.paginator import Paginator
from django.utils.http import url_has_allowed_host_and_scheme
from notifications.documents import Notification

@login_required
def list_notifications(request):
    user_id = request.user.id
    notifications_qs = Notification.objects(user_id=user_id).order_by("-created_at")
    
    paginator = Paginator(notifications_qs, 15)  # 15 notifications per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Enrich with humanized time
    enriched = []
    for n in page_obj:
        enriched.append({
            "id": str(n.id),
            "title": n.title,
            "description": n.description,
            "notification_type": n.notification_type,
            "link": n.link,
            "is_read": n.is_read,
            "time_ago": timesince(n.created_at).split(",")[0] + " ago"
        })
        
    unread_count = Notification.objects(user_id=user_id, is_read=False).count()
    return render(request, "notifications/list.html", {
        "notifications": enriched,
        "page_obj": page_obj,
        "unread_count": unread_count
    })

@login_required
def read_and_redirect(request, notification_id):
    notification = Notification.get_or_none(notification_id)
    if not notification or notification.user_id != request.user.id:
        raise Http404("Notification not found")
        
    notification.is_read = True
    notification.save()
    
    next_url = notification.link
    if not url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = "/"
    return redirect(next_url)

@login_required
def unread_count_api(request):
    user_id = request.user.id
    count = Notification.objects(user_id=user_id, is_read=False).count()
    return JsonResponse({"unread_count": count})

@login_required
def recent_notifications_api(request):
    user_id = request.user.id
    recent = Notification.objects(user_id=user_id).order_by("-created_at")[:5]
    
    serialized = []
    for n in recent:
        serialized.append({
            "id": str(n.id),
            "title": n.title,
            "description": n.description,
            "notification_type": n.notification_type,
            "link": n.link,
            "is_read": n.is_read,
            "time_ago": timesince(n.created_at).split(",")[0] + " ago"
        })
    return JsonResponse({"notifications": serialized})

@login_required
@require_POST
def mark_as_read_api(request, notification_id):
    notification = Notification.get_or_none(notification_id)
    if not notification or notification.user_id != request.user.id:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.META.get('HTTP_ACCEPT') == 'application/json':
            return JsonResponse({"error": "Notification not found"}, status=404)
        raise Http404("Notification not found")
        
    notification.is_read = True
    notification.save()
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.META.get('HTTP_ACCEPT') == 'application/json':
        return JsonResponse({"success": True})
    return redirect(request.META.get('HTTP_REFERER', 'notifications:list'))

@login_required
@require_POST
def mark_all_read_api(request):
    user_id = request.user.id
    Notification.objects(user_id=user_id, is_read=False).update(is_read=True)
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.META.get('HTTP_ACCEPT') == 'application/json':
        return JsonResponse({"success": True})
    return redirect(request.META.get('HTTP_REFERER', 'notifications:list'))
