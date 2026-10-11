from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar

from montreal_poetry_calendar import build_calendar, fetch_librairie_resonance, merge_state, parse_jays_list, parse_posts


class FakeResponse:
    def __init__(self, text): self.text = text
    def raise_for_status(self): pass


class FakeClient:
    def __init__(self, text): self.text = text
    def get(self, url): return FakeResponse(self.text)

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
    assert item.decoded("DTSTART").tzinfo.key == "America/Toronto"


def test_parse_jays_list():
    post = {
        "id": 7,
        "post_date": "2026-10-01T12:00:00Z",
        "canonical_url": "https://example.test/jays-list",
        "body_html": """<h4>17 OCTOBER (SAT)</h4><p><strong>LEIGH KOTSILIDIS: </strong><em><strong>SOME OF US MAY LIVE</strong></em><strong> LAUNCH<br>What:</strong> Poetry launch.<br><strong>Where:</strong> Rocket Science Room.<br><strong>When:</strong> 7pm.<br><a href='https://example.test/event'>Event link</a></p>""",
    }
    event = parse_jays_list(post, NOW)[0]
    assert event.title == "LEIGH KOTSILIDIS: SOME OF US MAY LIVE LAUNCH"
    assert event.start == "2026-10-17T19:00:00-04:00"
    assert event.venue == "Rocket Science Room."


def test_librairie_resonance_keeps_literary_events_only():
    page = '''
      <script type="application/ld+json">{"@type":"Event","name":"Rencontre avec une autrice","startDate":"2026-11-10T19:00","url":"https://librairieresonance.com/evenements/autrice","description":"Discussion autour de son livre.","location":{"name":"Librairie Résonance","address":{"streetAddress":"40 Beaubien East","addressLocality":"Montréal"}}}</script>
      <script type="application/ld+json">{"@type":"Event","name":"Album listening party","startDate":"2026-11-11T19:00","url":"https://librairieresonance.com/evenements/album","description":"Listen to a record."}</script>'''
    got = fetch_librairie_resonance(NOW, FakeClient(page))
    assert [event.title for event in got] == ["Rencontre avec une autrice"]
    assert got[0].source == "librairie-resonance"
