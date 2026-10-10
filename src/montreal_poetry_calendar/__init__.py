from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

API_URL = "https://wherepoetsread.ca/wp-json/wp/v2/posts"
SOURCE_URL = "https://wherepoetsread.ca/"
TIMEZONE = ZoneInfo("America/Toronto")
USER_AGENT = "MontrealPoetryCalendar/1.0 (+https://github.com/k10sj02/montreal-poetry-calendar)"


@dataclass(frozen=True)
class PoetryEvent:
    source_id: str
    title: str
    start: str
    end: str
    venue: str
    description: str
    url: str
    info_url: str
    status: str = "CONFIRMED"

    @property
    def uid(self) -> str:
        raw = f"wherepoetsread:{self.source_id}:{self.start}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24] + "@montreal-poetry-calendar"


def _text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def parse_posts(posts: list[dict], now: datetime) -> list[PoetryEvent]:
    events: list[PoetryEvent] = []
    cutoff = now - timedelta(days=1)
    for post in posts:
        try:
            start = datetime.fromisoformat(post["date"]).replace(tzinfo=TIMEZONE)
        except (KeyError, ValueError, TypeError):
            continue
        if start < cutoff:
            continue
        content_html = post.get("content", {}).get("rendered", "")
        soup = BeautifulSoup(content_html, "html.parser")
        paragraphs = [_text(str(p)) for p in soup.find_all("p") if _text(str(p))]
        venue = ""
        if paragraphs:
            candidate = paragraphs[0].lstrip("@ ")
            if len(candidate) <= 140 and not any(word in candidate.lower() for word in ("ticket", "info", "register", "zoom")):
                venue = candidate
        links = [a.get("href") for a in soup.find_all("a", href=True)]
        info_url = next((link for link in links if link and "wherepoetsread.ca" not in link), post.get("link", SOURCE_URL))
        description = "\n\n".join(paragraphs)
        events.append(PoetryEvent(
            source_id=str(post.get("id")),
            title=_text(post.get("title", {}).get("rendered", "Poetry event")),
            start=start.isoformat(),
            end=(start + timedelta(hours=2)).isoformat(),
            venue=venue,
            description=description,
            url=str(post.get("link") or SOURCE_URL),
            info_url=str(info_url),
        ))
    unique = {(event.source_id, event.start): event for event in events}
    return sorted(unique.values(), key=lambda event: (event.start, event.title))


def fetch_events(now: datetime) -> list[PoetryEvent]:
    response = httpx.get(API_URL, params={"per_page": 100, "orderby": "date", "order": "desc"}, headers={"User-Agent": USER_AGENT}, timeout=45)
    response.raise_for_status()
    events = parse_posts(response.json(), now)
    if not events:
        raise ValueError("No upcoming poetry events found")
    return events


def merge_state(current: list[PoetryEvent], state_path: Path, now: datetime) -> list[PoetryEvent]:
    old: dict[str, dict] = {}
    if state_path.exists():
        try:
            old = json.loads(state_path.read_text()).get("events", {})
        except (json.JSONDecodeError, OSError):
            pass
    merged = {event.uid: {**asdict(event), "missing_runs": 0} for event in current}
    for uid, item in old.items():
        if uid in merged:
            continue
        try:
            start = datetime.fromisoformat(item["start"])
        except (KeyError, ValueError):
            continue
        missing = int(item.get("missing_runs", 0)) + 1
        if start >= now - timedelta(days=1):
            status = "CANCELLED" if missing >= 2 else item.get("status", "CONFIRMED")
            merged[uid] = {**item, "missing_runs": missing, "status": status}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"updated": now.isoformat(), "events": merged}, ensure_ascii=False, indent=2) + "\n")
    return [PoetryEvent(**{k: value for k, value in item.items() if k != "missing_runs"}) for item in merged.values()]


def build_calendar(events: list[PoetryEvent], generated: datetime) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Montréal Poetry Calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Montréal Poetry")
    calendar.add("x-wr-timezone", "America/Toronto")
    calendar.add("refresh-interval", timedelta(hours=6), parameters={"VALUE": "DURATION"})
    for item in sorted(events, key=lambda event: (event.start, event.title)):
        event = Event()
        event.add("uid", item.uid)
        event.add("dtstamp", generated)
        event.add("dtstart", datetime.fromisoformat(item.start))
        event.add("dtend", datetime.fromisoformat(item.end))
        event.add("summary", item.title)
        if item.venue:
            event.add("location", item.venue)
        details = item.description
        if details:
            details += "\n\n"
        details += f"Listing: {item.url}\nMore information: {item.info_url}"
        event.add("description", details)
        event.add("url", item.info_url)
        event.add("status", item.status)
        event.add("categories", "Poetry")
        calendar.add_component(event)
    return calendar.to_ical()


def main() -> None:
    root = Path.cwd()
    now = datetime.now(TIMEZONE)
    events = merge_state(fetch_events(now), root / "data/state.json", now)
    public = root / "public"
    public.mkdir(exist_ok=True)
    (public / "montreal-poetry.ics").write_bytes(build_calendar(events, now))
    status = {
        "generated": now.isoformat(),
        "count": sum(event.status != "CANCELLED" for event in events),
        "cancelled": sum(event.status == "CANCELLED" for event in events),
        "sources": [SOURCE_URL],
    }
    (public / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    print(f"Published {status['count']} poetry events ({status['cancelled']} cancelled).")
