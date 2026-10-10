from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar

from montreal_poetry_calendar import build_calendar, merge_state, parse_posts

NOW = datetime(2026, 10, 10, 12, tzinfo=ZoneInfo("America/Toronto"))
POSTS = [{
    "id": 42,
    "date": "2026-10-17T19:00:00",
    "link": "https://wherepoetsread.ca/event/",
    "title": {"rendered": "Poetry Reading"},
    "content": {"rendered": '<p>@ The Reading Room</p><p>Readings by A and B.</p><p><a href="https://example.com/tickets">Info</a></p>'},
}]


def test_parse_post():
    event = parse_posts(POSTS, NOW)[0]
    assert event.title == "Poetry Reading"
    assert event.venue == "The Reading Room"
    assert event.info_url == "https://example.com/tickets"
    assert event.start == "2026-10-17T19:00:00-04:00"


def test_stable_ics(tmp_path: Path):
    event = parse_posts(POSTS, NOW)[0]
    merged = merge_state([event], tmp_path / "state.json", NOW)
    calendar = Calendar.from_ical(build_calendar(merged, NOW))
    item = next(component for component in calendar.walk() if component.name == "VEVENT")
    assert str(item["UID"]) == event.uid
    assert str(item["SUMMARY"]) == "Poetry Reading"
