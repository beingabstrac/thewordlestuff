#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROGRESS_FILE = ROOT / "data" / "progress.json"
STORY_FILE = ROOT / "outputs" / "thewordlestuff_storyboard.json"


def main():
    progress = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    story = json.loads(STORY_FILE.read_text(encoding="utf-8"))
    current_offset = int(story.get("offset", progress.get("next_offset", 0)))
    progress["next_offset"] = current_offset + 1
    progress["last_wordle_id"] = story.get("wordle_id")
    progress["last_date"] = story.get("date")
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(progress, indent=2))


if __name__ == "__main__":
    main()
