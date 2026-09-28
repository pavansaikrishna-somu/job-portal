import json
import urllib.request
import urllib.parse
import urllib.error
import base64
import logging
from decouple import config

logger = logging.getLogger(__name__)


def is_zoom_configured():
    account_id = config("ZOOM_ACCOUNT_ID", default="").strip()
    client_id = config("ZOOM_CLIENT_ID", default="").strip()
    client_secret = config("ZOOM_CLIENT_SECRET", default="").strip()
    return bool(account_id and client_id and client_secret)


def get_zoom_access_token():
    if not is_zoom_configured():
        logger.warning("Zoom is not fully configured in environment variables.")
        return None
    
    client_id = config("ZOOM_CLIENT_ID")
    if client_id == "mock_client":
        return "mock_token"
    
    account_id = config("ZOOM_ACCOUNT_ID")
    client_secret = config("ZOOM_CLIENT_SECRET")
    
    url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={account_id}"
    
    auth_str = f"{client_id}:{client_secret}"
    auth_bytes = auth_str.encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    req = urllib.request.Request(url, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("access_token")
    except Exception as e:
        logger.error("Failed to fetch Zoom access token: %s", str(e))
        return None


def create_zoom_meeting(interview):
    token = get_zoom_access_token()
    if not token:
        logger.warning("Aborted Zoom meeting creation: token fetch failed or Zoom unconfigured.")
        return None
    
    client_id = config("ZOOM_CLIENT_ID")
    if client_id == "mock_client":
        return {
            "id": "1234567890",
            "join_url": "https://zoom.us/j/1234567890",
            "start_url": "https://zoom.us/s/1234567890"
        }
    
    url = "https://api.zoom.us/v2/users/me/meetings"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    local_dt = interview.interview_date
    dt_str = local_dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    body = {
        "topic": f"Interview: {interview.title}",
        "type": 2,  # Scheduled Meeting
        "start_time": dt_str,
        "duration": interview.duration,
        "timezone": interview.timezone,
        "settings": {
            "join_before_host": False,
            "waiting_room": True
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "id": str(data.get("id")),
                "join_url": data.get("join_url"),
                "start_url": data.get("start_url")
            }
    except Exception as e:
        logger.error("Failed to create Zoom meeting for interview %s: %s", str(interview.id), str(e))
        return None


def update_zoom_meeting(interview):
    if not interview.meeting_id or interview.meeting_provider != "zoom":
        return False
    
    token = get_zoom_access_token()
    if not token:
        return False
    
    client_id = config("ZOOM_CLIENT_ID")
    if client_id == "mock_client":
        return True
    
    url = f"https://api.zoom.us/v2/meetings/{interview.meeting_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    local_dt = interview.interview_date
    dt_str = local_dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    body = {
        "topic": f"Interview: {interview.title}",
        "start_time": dt_str,
        "duration": interview.duration,
        "timezone": interview.timezone
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="PATCH"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        logger.error("Failed to update Zoom meeting %s: %s", interview.meeting_id, str(e))
        return False


def delete_zoom_meeting(interview):
    if not interview.meeting_id or interview.meeting_provider != "zoom":
        return False
    
    token = get_zoom_access_token()
    if not token:
        return False
    
    client_id = config("ZOOM_CLIENT_ID")
    if client_id == "mock_client":
        return True
    
    url = f"https://api.zoom.us/v2/meetings/{interview.meeting_id}"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    req = urllib.request.Request(
        url,
        headers=headers,
        method="DELETE"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        logger.error("Failed to delete Zoom meeting %s: %s", interview.meeting_id, str(e))
        return False
