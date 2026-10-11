from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from icalendar import Calendar, Event

WPR_API = "https://wherepoetsread.ca/wp-json/wp/v2/posts"
WPR_URL = "https://wherepoetsread.ca/"
JAYS_ARCHIVE = "https://www.discordiareview.com/api/v1/archive"
JAYS_URL = "https://www.discordiareview.com/"
CULT_API = "https://cultmtl.com/wp-json/wp/v2/event_listing"
CULT_URL = "https://cultmtl.com/event_listing_category/literary/"
HAS_THINGS_API = "https://montreal.hasthings.com/api/events"
HAS_THINGS_URL = "https://montreal.hasthings.com/"
RESONANCE_URL = "https://librairieresonance.com/en/pages/evenements"
TIMEZONE = ZoneInfo("America/Toronto")
USER_AGENT = "MontrealLiteraryCalendar/2.0 (+https://github.com/k10sj02/montreal-poetry-calendar)"
SOURCE_URLS = {
    "wherepoetsread": WPR_URL,
    "jays-list": JAYS_URL,
    "cult-mtl": CULT_URL,
    "has-things": HAS_THINGS_URL,
    "librairie-resonance": RESONANCE_URL,
}
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
LITERARY_TERMS = re.compile(
    r"po[eé]sie|poetry|po[eè]te|poet\b|litt[eé]raire|literary|club de lecture|book club|"
    r"book launch|lancement (?:de |du |d['’])?(?:livre|recueil|roman|zine)\b|livre|auteur|author|"
    r"[eé]crivain|slam\b|spoken word|lecture publique",
    re.I,
)


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
    source: str = "wherepoetsread"
    category: str = "Poetry & Literature"

    @property
    def uid(self) -> str:
        raw = f"{self.source}:{self.source_id}:{self.start}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24] + "@montreal-poetry-calendar"


def _text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _client() -> httpx.Client:
    return httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": USER_AGENT})


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
        info_url = next((link for link in links if link and "wherepoetsread.ca" not in link), post.get("link", WPR_URL))
        events.append(PoetryEvent(
            source_id=str(post.get("id")),
            title=_text(post.get("title", {}).get("rendered", "Poetry event")),
            start=start.isoformat(),
            end=(start + timedelta(hours=2)).isoformat(),
            venue=venue,
            description="\n\n".join(paragraphs),
            url=str(post.get("link") or WPR_URL),
            info_url=str(info_url),
        ))
    return events


def fetch_where_poets_read(now: datetime, client: httpx.Client) -> list[PoetryEvent]:
    response = client.get(WPR_API, params={"per_page": 100, "orderby": "date", "order": "desc"})
    response.raise_for_status()
    return parse_posts(response.json(), now)


def _parse_clock(value: str) -> tuple[int, int] | None:
    show_time = re.search(r"show is (?:from )?(.+)", value, re.I)
    value = show_time[1] if show_time else value
    matches = list(re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", value, re.I))
    if not matches:
        return None
    match = matches[0]
    suffix = match[3] or next((item[3] for item in reversed(matches) if item[3]), "")
    hour, minute = int(match[1]), int(match[2] or 0)
    suffix = suffix.lower().replace(".", "")
    if suffix == "pm" and hour != 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    return (hour, minute) if hour < 24 else None


def parse_jays_list(post: dict, now: datetime) -> list[PoetryEvent]:
    soup = BeautifulSoup(post.get("body_html") or "", "html.parser")
    year = datetime.fromisoformat(post["post_date"].replace("Z", "+00:00")).year
    events: list[PoetryEvent] = []
    for heading in soup.find_all("h4"):
        match = re.search(r"(\d{1,2})\s+([A-Z]+)", heading.get_text(" ", strip=True), re.I)
        if not match:
            continue
        month = MONTHS.get(match[2][:3].lower())
        if not month:
            continue
        sibling = heading.find_next_sibling()
        while sibling and not (isinstance(sibling, Tag) and sibling.name in {"h2", "h3", "h4"}):
            if isinstance(sibling, Tag) and sibling.name == "p":
                lines = [line.strip() for line in sibling.get_text("\n", strip=True).splitlines() if line.strip()]
                text = " ".join(lines)
                title = re.split(r"\bWhat\s*:", text, maxsplit=1, flags=re.I)[0].strip(" :-")
                where = re.search(r"Where\s*:\s*(.*?)(?=\s+When\s*:|\s+Who\s*:|\s+How\s+Much\s*:|$)", text, re.I)
                when = re.search(r"When\s*:\s*(.*?)(?=\s+Who\s*:|\s+How\s+Much\s*:|$)", text, re.I)
                clock = _parse_clock(when[1]) if when else None
                if title and clock:
                    start = datetime(year, month, int(match[1]), clock[0], clock[1], tzinfo=TIMEZONE)
                    if start >= now - timedelta(days=1):
                        link = next((a.get("href") for a in sibling.find_all("a", href=True)), post.get("canonical_url", JAYS_URL))
                        digest = hashlib.sha256(f"{post.get('id')}:{start}:{title}".encode()).hexdigest()[:16]
                        events.append(PoetryEvent(
                            source_id=digest,
                            title=title,
                            start=start.isoformat(),
                            end=(start + timedelta(hours=2)).isoformat(),
                            venue=where[1].strip() if where else "",
                            description=text,
                            url=post.get("canonical_url", JAYS_URL),
                            info_url=link,
                            source="jays-list",
                        ))
            sibling = sibling.find_next_sibling()
    return events


def fetch_jays_list(now: datetime, client: httpx.Client) -> list[PoetryEvent]:
    response = client.get(JAYS_ARCHIVE, params={"sort": "new", "search": "Jay's List"})
    response.raise_for_status()
    candidates = [item for item in response.json() if "jay's list" in item.get("title", "").lower()]
    candidates.sort(key=lambda item: item.get("post_date", ""), reverse=True)
    for item in candidates[:3]:
        detail = client.get(f"{JAYS_URL}api/v1/posts/{item['slug']}")
        detail.raise_for_status()
        events = parse_jays_list(detail.json(), now)
        if events:
            return events
    return []


def fetch_cult_mtl(now: datetime, client: httpx.Client) -> list[PoetryEvent]:
    response = client.get(CULT_API, params={"per_page": 100, "event_listing_category": 40482})
    response.raise_for_status()
    events: list[PoetryEvent] = []
    for post in response.json():
        try:
            start = datetime.fromisoformat(post["date"]).replace(tzinfo=TIMEZONE)
        except (KeyError, ValueError):
            continue
        if start < now - timedelta(days=1):
            continue
        content = post.get("content", {}).get("rendered", "")
        paragraphs = [_text(str(p)) for p in BeautifulSoup(content, "html.parser").find_all("p")]
        venue = next((p.removeprefix("At ").strip() for p in paragraphs if p.startswith("At ")), "")
        events.append(PoetryEvent(
            source_id=str(post["id"]), title=_text(post.get("title", {}).get("rendered", "Literary event")),
            start=start.isoformat(), end=(start + timedelta(hours=2)).isoformat(), venue=venue,
            description="\n\n".join(paragraphs), url=post.get("link", CULT_URL), info_url=post.get("link", CULT_URL),
            source="cult-mtl",
        ))
    return events


def fetch_has_things(now: datetime, client: httpx.Client) -> list[PoetryEvent]:
    events: list[PoetryEvent] = []
    offset = 0
    until = now + timedelta(days=60)
    while True:
        response = client.get(HAS_THINGS_API, params={"from": now.isoformat(), "to": until.isoformat(), "limit": 500, "offset": offset})
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("items", []):
            tags = " ".join(tag.get("label", "") for tag in item.get("tags", []))
            if not LITERARY_TERMS.search(f"{item.get('title', '')} {tags}"):
                continue
            start = datetime.fromisoformat(item["startsAt"].replace("Z", "+00:00")).astimezone(TIMEZONE)
            end_value = item.get("endsAt")
            end = datetime.fromisoformat(end_value.replace("Z", "+00:00")).astimezone(TIMEZONE) if end_value else start + timedelta(hours=2)
            source_names = ", ".join(item.get("sources", []))
            events.append(PoetryEvent(
                source_id=item["id"], title=item["title"], start=start.isoformat(), end=end.isoformat(),
                venue=item.get("venue", {}).get("name", ""),
                description=f"Listed by {source_names}. Data: Has Things (hasthings.com).",
                url=item.get("url", HAS_THINGS_URL), info_url=item.get("ticketUrl") or item.get("url", HAS_THINGS_URL),
                status="CANCELLED" if item.get("status") == "cancelled" else "CONFIRMED", source="has-things",
            ))
        next_offset = payload.get("nextOffset")
        if next_offset is None:
            break
        offset = int(next_offset)
    return events


def fetch_librairie_resonance(now: datetime, client: httpx.Client) -> list[PoetryEvent]:
    # Shopify occasionally returns a brief 429 to GitHub-hosted runners.
    # Retry at a second locale URL so one transient response does not mark
    # the whole source unavailable.
    response = None
    urls = (RESONANCE_URL, "https://librairieresonance.com/pages/evenements", RESONANCE_URL)
    for attempt, url in enumerate(urls):
        response = client.get(url)
        if getattr(response, "status_code", 200) != 429:
            break
        if attempt < len(urls) - 1:
            time.sleep(2 ** attempt)
    assert response is not None
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    events: list[PoetryEvent] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            item = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict) or item.get("@type") != "Event" or not item.get("startDate"):
            continue
        description = _text(item.get("description", ""))
        if not LITERARY_TERMS.search(f"{item.get('name', '')} {description}"):
            continue
        start = datetime.fromisoformat(item["startDate"]).replace(tzinfo=TIMEZONE)
        if start < now - timedelta(days=1):
            continue
        location = item.get("location") or {}
        address = location.get("address") or {}
        venue_parts = [location.get("name", ""), address.get("streetAddress", ""), address.get("addressLocality", "")]
        url = item.get("url") or RESONANCE_URL
        events.append(PoetryEvent(
            source_id=url.rstrip("/").rsplit("/", 1)[-1], title=_text(item.get("name", "Literary event")),
            start=start.isoformat(), end=(start + timedelta(hours=2)).isoformat(),
            venue=", ".join(part for part in venue_parts if part), description=description,
            url=url, info_url=(item.get("offers") or {}).get("url") or url,
            status="CANCELLED" if "Cancelled" in str(item.get("eventStatus")) else "CONFIRMED",
            source="librairie-resonance",
        ))
    return events


def _dedupe(events: list[PoetryEvent]) -> list[PoetryEvent]:
    def key(event: PoetryEvent) -> tuple[str, str]:
        title = unicodedata.normalize("NFKD", event.title.casefold())
        return "".join(char for char in title if char.isalnum()), event.start[:16]

    unique: dict[tuple[str, str], PoetryEvent] = {}
    for event in events:
        unique.setdefault(key(event), event)
    return sorted(unique.values(), key=lambda event: (event.start, event.title))


def fetch_events(now: datetime) -> tuple[list[PoetryEvent], set[str], dict[str, str]]:
    collected: list[PoetryEvent] = []
    successful: set[str] = set()
    errors: dict[str, str] = {}
    with _client() as client:
        for name, fetcher in (
            ("wherepoetsread", fetch_where_poets_read), ("jays-list", fetch_jays_list),
            ("cult-mtl", fetch_cult_mtl), ("has-things", fetch_has_things),
            ("librairie-resonance", fetch_librairie_resonance),
        ):
            try:
                items = fetcher(now, client)
                collected.extend(items)
                successful.add(name)
                print(f"{name}: {len(items)} events")
            except Exception as exc:
                errors[name] = str(exc)
                print(f"WARNING: {name}: {exc}")
    if not collected:
        raise ValueError("No upcoming literary events found")
    return _dedupe(collected), successful, errors


def merge_state(current: list[PoetryEvent], state_path: Path, now: datetime, successful_sources: set[str] | None = None) -> list[PoetryEvent]:
    successful_sources = successful_sources or {event.source for event in current}
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
        item.setdefault("source", "wherepoetsread")
        item.setdefault("category", "Poetry & Literature")
        try:
            start = datetime.fromisoformat(item["start"])
        except (KeyError, ValueError):
            continue
        missing = int(item.get("missing_runs", 0)) + (item["source"] in successful_sources)
        if start >= now - timedelta(days=1):
            status = "CANCELLED" if missing >= 2 else item.get("status", "CONFIRMED")
            merged[uid] = {**item, "missing_runs": missing, "status": status}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"updated": now.isoformat(), "events": merged}, ensure_ascii=False, indent=2) + "\n")
    return [PoetryEvent(**{key: value for key, value in item.items() if key != "missing_runs"}) for item in merged.values()]


def build_calendar(events: list[PoetryEvent], generated: datetime) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Montréal Poetry & Literary Calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Montréal Poetry & Literature")
    calendar.add("x-wr-timezone", "America/Toronto")
    calendar.add("refresh-interval", timedelta(hours=6), parameters={"VALUE": "DURATION"})
    for item in sorted(events, key=lambda event: (event.start, event.title)):
        event = Event()
        event.add("uid", item.uid)
        event.add("dtstamp", generated)
        event.add("dtstart", datetime.fromisoformat(item.start).astimezone(TIMEZONE))
        event.add("dtend", datetime.fromisoformat(item.end).astimezone(TIMEZONE))
        event.add("summary", item.title)
        if item.venue:
            event.add("location", item.venue)
        details = item.description + ("\n\n" if item.description else "")
        details += f"Listing: {item.url}\nMore information: {item.info_url}"
        event.add("description", details)
        event.add("url", item.info_url)
        event.add("status", item.status)
        event.add("categories", item.category)
        event.add("x-montreal-literary-source", item.source)
        calendar.add_component(event)
    return calendar.to_ical()


def main() -> None:
    root = Path.cwd()
    now = datetime.now(TIMEZONE)
    current, successful, errors = fetch_events(now)
    events = merge_state(current, root / "data/state.json", now, successful)
    public = root / "public"
    public.mkdir(exist_ok=True)
    (public / "montreal-poetry.ics").write_bytes(build_calendar(events, now))
    status = {
        "generated": now.isoformat(), "count": sum(event.status != "CANCELLED" for event in events),
        "cancelled": sum(event.status == "CANCELLED" for event in events),
        "sources_ok": sorted(successful), "errors": errors,
        "sources": [SOURCE_URLS[name] for name in sorted(successful)],
    }
    (public / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    print(f"Published {status['count']} literary events ({status['cancelled']} cancelled).")
