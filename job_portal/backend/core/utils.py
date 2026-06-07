import os
import posixpath
import uuid

from django.core.files.storage import default_storage


def parse_csv(value):
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def save_uploaded_file(uploaded_file, subdir):
    extension = os.path.splitext(uploaded_file.name)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{extension}"
    # Ensure stored paths use forward slashes for consistent MEDIA URLs.
    relative_path = posixpath.join(subdir, unique_filename)
    return default_storage.save(relative_path, uploaded_file)
