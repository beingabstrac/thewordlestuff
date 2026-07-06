#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


def cloudinary_config():
    cloudinary_url = os.getenv("CLOUDINARY_URL", "")
    if cloudinary_url:
        parsed = urllib.parse.urlparse(cloudinary_url)
        return parsed.hostname, parsed.username, parsed.password
    return os.getenv("CLOUDINARY_CLOUD_NAME"), os.getenv("CLOUDINARY_API_KEY"), os.getenv("CLOUDINARY_API_SECRET")


def require_config():
    cloud_name, api_key, api_secret = cloudinary_config()
    if not cloud_name or not api_key or not api_secret:
        raise RuntimeError("Missing Cloudinary config")
    return cloud_name, api_key, api_secret


def admin_request(path, api_key, api_secret):
    auth = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    req = urllib.request.Request(path, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def list_videos(cloud_name, api_key, api_secret):
    resources = []
    cursor = ""
    while True:
        params = {"prefix": "thewordlestuff/", "max_results": "100"}
        if cursor:
            params["next_cursor"] = cursor
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/resources/video/upload?{urllib.parse.urlencode(params)}"
        data = admin_request(url, api_key, api_secret)
        resources.extend(data.get("resources", []))
        cursor = data.get("next_cursor") or ""
        if not cursor:
            return resources


def destroy_video(cloud_name, api_key, api_secret, public_id):
    timestamp = str(int(time.time()))
    signature_base = f"public_id={public_id}&timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(signature_base.encode()).hexdigest()
    body = urllib.parse.urlencode(
        {
            "public_id": public_id,
            "timestamp": timestamp,
            "api_key": api_key,
            "signature": signature,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.cloudinary.com/v1_1/{cloud_name}/video/destroy",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_created_at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    cloud_name, api_key, api_secret = require_config()
    retention_days = int(os.getenv("CLOUDINARY_RETENTION_DAYS", "4"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    dry_run = os.getenv("DRY_RUN") == "1"

    deleted = []
    kept = 0
    for item in list_videos(cloud_name, api_key, api_secret):
        public_id = item.get("public_id")
        created_at = item.get("created_at")
        if not public_id or not created_at:
            kept += 1
            continue
        if parse_created_at(created_at) >= cutoff:
            kept += 1
            continue
        if not dry_run:
            destroy_video(cloud_name, api_key, api_secret, public_id)
        deleted.append(public_id)

    print(json.dumps({"deleted": deleted, "kept": kept, "retention_days": retention_days}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"cleanup_cloudinary failed: {exc}", file=sys.stderr)
        raise
