#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIDEO_PATH = ROOT / "outputs" / "thewordlestuff_mvp.mp4"
STORY_PATH = ROOT / "outputs" / "thewordlestuff_storyboard.json"
QUEUE_FULL_MARKER = ROOT / "outputs" / "queue_full"


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_story():
    return json.loads(STORY_PATH.read_text(encoding="utf-8"))


def caption_for_story(story):
    title = f"Wordle #{story['wordle_id']} - {story['date']}"
    tags = "#thewordlestuff #wordle #wordlereels #wordlepuzzle #reels"
    return f"{title}\n\nCould you solve it before the reveal?\n\n{tags}"


def multipart_body(fields, files):
    boundary = f"----thewordlestuff{int(time.time())}"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode())
        chunks.append(b"\r\n")
    for name, path in files.items():
        data = Path(path).read_bytes()
        chunks.append(f"--{boundary}\r\n".encode())
        header = (
            f'Content-Disposition: form-data; name="{name}"; filename="{Path(path).name}"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        )
        chunks.append(header.encode())
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def cloudinary_config():
    cloudinary_url = os.getenv("CLOUDINARY_URL", "")
    if cloudinary_url:
        parsed = urllib.parse.urlparse(cloudinary_url)
        return parsed.hostname, parsed.username, parsed.password
    return os.getenv("CLOUDINARY_CLOUD_NAME"), os.getenv("CLOUDINARY_API_KEY"), os.getenv("CLOUDINARY_API_SECRET")


def cloudinary_upload(video_path):
    cloud_name, api_key, api_secret = cloudinary_config()
    if not cloud_name or not api_key or not api_secret:
        raise RuntimeError("Set PUBLIC_VIDEO_URL or Cloudinary env vars for public MP4 hosting.")

    timestamp = str(int(time.time()))
    public_id = f"thewordlestuff/{timestamp}"
    params = {"public_id": public_id, "timestamp": timestamp, "overwrite": "true"}
    signature_base = "&".join(f"{key}={params[key]}" for key in sorted(params))
    signature = hashlib.sha1(f"{signature_base}{api_secret}".encode()).hexdigest()
    fields = {**params, "api_key": api_key, "signature": signature}
    boundary, body = multipart_body(fields, {"file": video_path})
    req = urllib.request.Request(
        f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["secure_url"], data.get("public_id", public_id)


def cloudinary_destroy(public_id):
    cloud_name, api_key, api_secret = cloudinary_config()
    if not cloud_name or not api_key or not api_secret or not public_id:
        return
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
        response.read()


def public_video_asset(story):
    public_url = os.getenv("PUBLIC_VIDEO_URL")
    if public_url:
        return public_url, None
    video_url, public_id = cloudinary_upload(VIDEO_PATH)
    return video_url, public_id


def gql_string(value):
    return json.dumps(value)


def create_buffer_post(caption, video_url):
    api_key = require_env("BUFFER_API_KEY")
    channel_id = require_env("BUFFER_INSTAGRAM_CHANNEL_ID")
    mutation = f"""
    mutation {{
      createPost(input: {{
        channelId: {gql_string(channel_id)}
        text: {gql_string(caption)}
        metadata: {{
          instagram: {{
            type: reel
            shouldShareToFeed: true
            isAiGenerated: false
          }}
        }}
        schedulingType: automatic
        mode: addToQueue
        assets: [
          {{
            video: {{
              url: {gql_string(video_url)}
            }}
          }}
        ]
      }}) {{
        ... on PostActionSuccess {{
          post {{ id text }}
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    """
    req = urllib.request.Request(
        "https://api.buffer.com/graphql",
        data=json.dumps({"query": mutation}).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Buffer HTTP {exc.code}: {body}") from exc
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    result = data.get("data", {}).get("createPost", {})
    if result.get("message"):
        message = result["message"]
        if os.getenv("IGNORE_QUEUE_FULL") == "1" and is_queue_full_error(message):
            print(json.dumps({"skipped": "queue_full", "message": message}, indent=2))
            return None
        raise RuntimeError(message)
    return result.get("post", {})


def is_queue_full_error(message):
    text = (message or "").lower()
    markers = ["queue", "limit", "maximum", "max", "scheduled posts", "10 posts"]
    return any(marker in text for marker in markers)


def main():
    QUEUE_FULL_MARKER.unlink(missing_ok=True)
    if not VIDEO_PATH.exists():
        raise RuntimeError(f"Missing video: {VIDEO_PATH}")
    story = load_story()
    caption = caption_for_story(story)
    if os.getenv("DRY_RUN") == "1":
        print(json.dumps({"caption": caption}, indent=2))
        return
    video_url, cloudinary_public_id = public_video_asset(story)
    buffer_post = create_buffer_post(caption, video_url)
    if not buffer_post:
        cloudinary_destroy(cloudinary_public_id)
        QUEUE_FULL_MARKER.parent.mkdir(parents=True, exist_ok=True)
        QUEUE_FULL_MARKER.write_text("1\n", encoding="utf-8")
        return
    print(json.dumps({"buffer_post": buffer_post, "video_url": video_url}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"post_to_buffer failed: {exc}", file=sys.stderr)
        raise
