import re

from youtube_transcript_api import YouTubeTranscriptApi

_VIDEO_ID_PATTERNS = [
    re.compile(r"(?:v=|/)([0-9A-Za-z_-]{11})(?:[?&/]|$)"),
]


def extract_video_id(url: str) -> str:
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract a YouTube video ID from: {url}")


def extract_youtube_transcript(url: str) -> tuple[str, str]:
    """Returns (text, video_id)."""
    video_id = extract_video_id(url)
    transcript = YouTubeTranscriptApi().fetch(video_id, languages=("en",))
    text = " ".join(snippet.text for snippet in transcript).strip()
    if not text:
        raise ValueError(f"No transcript available for video: {video_id}")
    return text, video_id
