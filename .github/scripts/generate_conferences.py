#!/usr/bin/env python3
"""Generate the human-readable conference list, calendar feed and web page."""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT / "data" / "conferences.yml"
EVENTS_DIRECTORY = ROOT / "events"
CALENDAR_DIRECTORY = ROOT / "calendar"
PAGE_FILE = CALENDAR_DIRECTORY / "index.html"
EVENT_DIRECTORY = CALENDAR_DIRECTORY / "events"

REPOSITORY_URL = (
    "https://github.com/Nordic-Accessibility-Community-Group/"
    "accessibility-conferences"
)
ADDITION_FORM_URL = f"{REPOSITORY_URL}/issues/new?template=conference-addition.yml"
CORRECTION_FORM_URL = f"{REPOSITORY_URL}/issues/new?template=conference-correction.yml"
UID_DOMAIN = "nordic-accessibility-community-group.github.io"

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
ALLOWED_STATUSES = {"cancelled", "confirmed", "tentative"}
EU_COUNTRY_CODES = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
}
COUNTRY_FILTER_LABELS = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "HR": "Croatia",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DK": "Denmark",
    "EE": "Estonia",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "GR": "Greece",
    "HU": "Hungary",
    "IS": "Iceland",
    "IE": "Ireland",
    "IT": "Italy",
    "LV": "Latvia",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MT": "Malta",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "ES": "Spain",
    "SE": "Sweden",
    "GB": "United Kingdom",
    "US": "United States",
    "online-only": "Online only",
}
COMMON_REQUIRED_EVENT_FIELDS = {
    "description",
    "id",
    "last_verified",
    "name",
    "sequence",
    "status",
    "url",
}
SCHEDULED_REQUIRED_EVENT_FIELDS = {
    "attendance",
    "end_date",
    "location",
    "start_date",
}
SCHEDULED_OPTIONAL_EVENT_FIELDS = {
    "country_code",
    "end_time",
    "start_time",
    "time_zone",
}
UNDATED_REQUIRED_EVENT_FIELDS = {"year"}
UNDATED_OPTIONAL_EVENT_FIELDS = {"expected_timing"}
COMMON_OPTIONAL_EVENT_FIELDS = {"language", "organizer"}
ALL_EVENT_FIELDS = (
    COMMON_REQUIRED_EVENT_FIELDS
    | SCHEDULED_REQUIRED_EVENT_FIELDS
    | SCHEDULED_OPTIONAL_EVENT_FIELDS
    | UNDATED_REQUIRED_EVENT_FIELDS
    | UNDATED_OPTIONAL_EVENT_FIELDS
    | COMMON_OPTIONAL_EVENT_FIELDS
)


class DataError(ValueError):
    """Raised when conference source data is invalid."""


@dataclass(frozen=True)
class CalendarDetails:
    name: str
    description: str
    public_url: str

    def feed_url(self, filename: str) -> str:
        return f"{self.public_url}{filename}"

    def webcal_url(self, filename: str) -> str:
        return self.feed_url(filename).replace("https://", "webcal://", 1)


@dataclass(frozen=True)
class FeedDefinition:
    key: str
    filename: str
    name: str
    description: str


FEED_DEFINITIONS = (
    FeedDefinition(
        key="all",
        filename="conferences.ics",
        name="Everything",
        description="Every conference and event in the calendar.",
    ),
    FeedDefinition(
        key="eu",
        filename="eu.ics",
        name="European Union",
        description="Events with onsite attendance in an EU member country.",
    ),
    FeedDefinition(
        key="us",
        filename="us.ics",
        name="United States",
        description="Events with onsite attendance in the United States.",
    ),
    FeedDefinition(
        key="online",
        filename="online.ics",
        name="Online access",
        description="Events offering online attendance, including hybrid events.",
    ),
)


@dataclass(frozen=True)
class Conference:
    id: str
    name: str
    year: int
    start_date: date | None
    end_date: date | None
    location: str | None
    country_code: str | None
    attendance_onsite: bool | None
    attendance_online: bool | None
    url: str
    description: str
    status: str
    sequence: int
    last_verified: date
    expected_timing: str | None = None
    language: str | None = None
    organizer: str | None = None
    start_time: clock_time | None = None
    end_time: clock_time | None = None
    time_zone: str | None = None

    @property
    def event_feed_url(self) -> str:
        return f"events/{self.id}.ics"

    @property
    def is_undated(self) -> bool:
        return self.start_date is None

    @property
    def format(self) -> str:
        if self.is_undated:
            raise DataError(f"conference {self.id!r}: undated conferences have no format")
        if self.attendance_onsite and self.attendance_online:
            return "Hybrid"
        if self.attendance_onsite:
            return "In person"
        return "Online"


def require_non_empty_string(value: object, field: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def parse_iso_date(value: object, field: str, context: str) -> date:
    text = require_non_empty_string(value, field, context)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise DataError(f"{context}: {field} must use YYYY-MM-DD") from error


def parse_clock_time(value: object, field: str, context: str) -> clock_time:
    text = require_non_empty_string(value, field, context)
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise DataError(f"{context}: {field} must use HH:MM")
    try:
        return clock_time.fromisoformat(text)
    except ValueError as error:
        raise DataError(f"{context}: {field} must use a valid 24-hour time") from error


def validate_https_url(value: object, field: str, context: str) -> str:
    text = require_non_empty_string(value, field, context)
    if any(character in text for character in "\r\n"):
        raise DataError(f"{context}: {field} must not contain line breaks")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise DataError(f"{context}: {field} must be an absolute HTTPS URL")
    return text


def load_data(path: Path = DATA_FILE) -> tuple[CalendarDetails, list[Conference]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise DataError(f"Could not read {path}: {error}") from error

    if not isinstance(raw, dict):
        raise DataError("The data file must contain a mapping")

    calendar_raw = raw.get("calendar")
    if not isinstance(calendar_raw, dict):
        raise DataError("calendar must be a mapping")

    calendar_context = "calendar"
    calendar = CalendarDetails(
        name=require_non_empty_string(
            calendar_raw.get("name"), "name", calendar_context
        ),
        description=require_non_empty_string(
            calendar_raw.get("description"), "description", calendar_context
        ),
        public_url=validate_https_url(
            calendar_raw.get("public_url"), "public_url", calendar_context
        ).rstrip("/")
        + "/",
    )

    events_raw = raw.get("conferences")
    if not isinstance(events_raw, list):
        raise DataError("conferences must be a list")

    conferences: list[Conference] = []
    seen_ids: set[str] = set()
    for index, event_raw in enumerate(events_raw, start=1):
        context = f"conference #{index}"
        if not isinstance(event_raw, dict):
            raise DataError(f"{context} must be a mapping")

        fields = set(event_raw)
        unknown = fields - ALL_EVENT_FIELDS
        if unknown:
            raise DataError(
                f"{context} has unknown fields: {', '.join(sorted(unknown))}"
            )

        event_id = require_non_empty_string(event_raw["id"], "id", context)
        context = f"conference {event_id!r}"
        if not ID_PATTERN.fullmatch(event_id):
            raise DataError(
                f"{context}: id must contain lowercase letters, numbers and hyphens"
            )
        if event_id in seen_ids:
            raise DataError(f"{context}: duplicate id")
        seen_ids.add(event_id)

        has_start_date = "start_date" in fields
        has_end_date = "end_date" in fields
        if has_start_date != has_end_date:
            raise DataError(
                f"{context}: conferences must include both start_date and end_date"
            )
        is_undated = not has_start_date

        required_fields = (
            COMMON_REQUIRED_EVENT_FIELDS
            | (UNDATED_REQUIRED_EVENT_FIELDS if is_undated else SCHEDULED_REQUIRED_EVENT_FIELDS)
        )
        missing = required_fields - fields
        if missing:
            raise DataError(f"{context} is missing: {', '.join(sorted(missing))}")

        if is_undated:
            forbidden = fields & (
                SCHEDULED_REQUIRED_EVENT_FIELDS | SCHEDULED_OPTIONAL_EVENT_FIELDS
            )
            if forbidden:
                raise DataError(
                    f"{context}: undated conferences cannot include {', '.join(sorted(forbidden))}"
                )
        else:
            forbidden = fields & (
                UNDATED_REQUIRED_EVENT_FIELDS | UNDATED_OPTIONAL_EVENT_FIELDS
            )
            if forbidden:
                raise DataError(
                    f"{context}: dated conferences cannot include {', '.join(sorted(forbidden))}"
                )

        status = require_non_empty_string(
            event_raw["status"], "status", context
        ).lower()
        if status not in ALLOWED_STATUSES:
            allowed = ", ".join(sorted(ALLOWED_STATUSES))
            raise DataError(f"{context}: status must be one of {allowed}")
        if is_undated and status != "tentative":
            raise DataError(f"{context}: undated conferences must use tentative status")

        sequence = event_raw["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise DataError(f"{context}: sequence must be a non-negative integer")

        optional_values: dict[str, str | None] = {}
        for field in COMMON_OPTIONAL_EVENT_FIELDS:
            value = event_raw.get(field)
            optional_values[field] = (
                require_non_empty_string(value, field, context)
                if value is not None
                else None
            )

        start_date: date | None = None
        end_date: date | None = None
        location: str | None = None
        country_code: str | None = None
        attendance_onsite: bool | None = None
        attendance_online: bool | None = None
        start_time: clock_time | None = None
        end_time: clock_time | None = None
        time_zone: str | None = None
        expected_timing: str | None = None

        if is_undated:
            year = event_raw["year"]
            if not isinstance(year, int) or isinstance(year, bool) or not 1000 <= year <= 9999:
                raise DataError(f"{context}: year must be a four-digit number")
            expected_timing_raw = event_raw.get("expected_timing")
            expected_timing = (
                require_non_empty_string(
                    expected_timing_raw, "expected_timing", context
                )
                if expected_timing_raw is not None
                else None
            )
        else:
            start_date = parse_iso_date(event_raw["start_date"], "start_date", context)
            end_date = parse_iso_date(event_raw["end_date"], "end_date", context)
            if end_date < start_date:
                raise DataError(f"{context}: end_date cannot be before start_date")
            year = start_date.year

            timed_fields = ("start_time", "end_time", "time_zone")
            timed_values = [event_raw.get(field) for field in timed_fields]
            if any(value is not None for value in timed_values) and not all(
                value is not None for value in timed_values
            ):
                raise DataError(
                    f"{context}: start_time, end_time and time_zone must be used together"
                )
            if all(value is not None for value in timed_values):
                start_time = parse_clock_time(
                    event_raw["start_time"], "start_time", context
                )
                end_time = parse_clock_time(event_raw["end_time"], "end_time", context)
                time_zone = require_non_empty_string(
                    event_raw["time_zone"], "time_zone", context
                )
                try:
                    ZoneInfo(time_zone)
                except ZoneInfoNotFoundError as error:
                    raise DataError(
                        f"{context}: time_zone must be a valid IANA time zone"
                    ) from error
                if start_date == end_date and end_time <= start_time:
                    raise DataError(f"{context}: end_time must be after start_time")

            attendance_raw = event_raw["attendance"]
            if not isinstance(attendance_raw, dict):
                raise DataError(f"{context}: attendance must be a mapping")
            if set(attendance_raw) != {"onsite", "online"}:
                raise DataError(
                    f"{context}: attendance must contain only onsite and online"
                )
            attendance_onsite = attendance_raw["onsite"]
            attendance_online = attendance_raw["online"]
            if not isinstance(attendance_onsite, bool) or not isinstance(
                attendance_online, bool
            ):
                raise DataError(f"{context}: attendance values must be true or false")
            if not attendance_onsite and not attendance_online:
                raise DataError(
                    f"{context}: at least one attendance option must be available"
                )

            country_code_raw = event_raw.get("country_code")
            if country_code_raw is not None:
                country_code = require_non_empty_string(
                    country_code_raw, "country_code", context
                ).upper()
                if not COUNTRY_CODE_PATTERN.fullmatch(country_code):
                    raise DataError(
                        f"{context}: country_code must be a two-letter ISO country code"
                    )
            if attendance_onsite and country_code is None:
                raise DataError(f"{context}: onsite events require country_code")

            location = require_non_empty_string(
                event_raw["location"], "location", context
            )

        conferences.append(
            Conference(
                id=event_id,
                name=require_non_empty_string(event_raw["name"], "name", context),
                year=year,
                start_date=start_date,
                end_date=end_date,
                location=location,
                country_code=country_code,
                attendance_onsite=attendance_onsite,
                attendance_online=attendance_online,
                url=validate_https_url(event_raw["url"], "url", context),
                description=require_non_empty_string(
                    event_raw["description"], "description", context
                ),
                status=status,
                sequence=sequence,
                last_verified=parse_iso_date(
                    event_raw["last_verified"], "last_verified", context
                ),
                expected_timing=expected_timing,
                language=optional_values["language"],
                organizer=optional_values["organizer"],
                start_time=start_time,
                end_time=end_time,
                time_zone=time_zone,
            )
        )

    return calendar, sorted(
        conferences,
        key=lambda event: (
            event.year,
            event.is_undated,
            event.start_date or date.max,
            event.name.casefold(),
        ),
    )


def conferences_for_feed(
    feed: FeedDefinition, conferences: list[Conference]
) -> list[Conference]:
    scheduled_conferences = [event for event in conferences if not event.is_undated]
    if feed.key == "all":
        return scheduled_conferences
    if feed.key == "eu":
        return [
            event
            for event in scheduled_conferences
            if event.attendance_onsite and event.country_code in EU_COUNTRY_CODES
        ]
    if feed.key == "us":
        return [
            event
            for event in scheduled_conferences
            if event.attendance_onsite and event.country_code == "US"
        ]
    if feed.key == "online":
        return [event for event in scheduled_conferences if event.attendance_online]
    raise DataError(f"Unknown feed definition: {feed.key}")


def conferences_for_year(
    conferences: list[Conference], year: int
) -> tuple[list[Conference], list[Conference]]:
    year_conferences = [event for event in conferences if event.year == year]
    dated = sorted(
        (event for event in year_conferences if not event.is_undated),
        key=lambda event: (event.start_date or date.max, event.name.casefold()),
    )
    undated = sorted(
        (event for event in year_conferences if event.is_undated),
        key=lambda event: event.name.casefold(),
    )
    return dated, undated


def format_date_range(start: date, end: date) -> str:
    if start == end:
        return f"{start.day} {start.strftime('%B')} {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {start.strftime('%B')} {start.year}"
    if start.year == end.year:
        return (
            f"{start.day} {start.strftime('%B')} – "
            f"{end.day} {end.strftime('%B')} {start.year}"
        )
    return (
        f"{start.day} {start.strftime('%B')} {start.year} – "
        f"{end.day} {end.strftime('%B')} {end.year}"
    )


def format_event_date(event: Conference) -> str:
    if event.start_date is None or event.end_date is None:
        raise DataError(f"conference {event.id!r}: undated conferences have no date")
    date_text = format_date_range(event.start_date, event.end_date)
    if event.start_time is None or event.end_time is None or event.time_zone is None:
        return date_text

    zone = ZoneInfo(event.time_zone)
    start = datetime.combine(event.start_date, event.start_time, tzinfo=zone)
    zone_label = start.tzname() or event.time_zone
    return (
        f"{date_text}, {event.start_time.strftime('%H:%M')}–"
        f"{event.end_time.strftime('%H:%M')} {zone_label}"
    )


def markdown_escape(value: str) -> str:
    return (
        value.replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", " ")
    )


def event_display_name(event: Conference) -> str:
    linked_name = f"[{markdown_escape(event.name)}]({event.url})"
    if event.status == "cancelled":
        return f"~~{linked_name}~~ (cancelled)"
    if event.status == "tentative":
        return f"{linked_name} (tentative)"
    return linked_name


def render_markdown(
    calendar: CalendarDetails,
    year: int,
    conferences: list[Conference],
    undated_conferences: list[Conference],
) -> str:
    rows = []
    for event in conferences:
        rows.append(
            "| "
            + " | ".join(
                [
                    format_event_date(event),
                    event_display_name(event),
                    markdown_escape(event.format),
                    markdown_escape(event.location),
                    (
                        f"[Add {markdown_escape(event.name)}]"
                        f"(../{CALENDAR_DIRECTORY.name}/{event.event_feed_url})"
                    ),
                ]
            )
            + " |"
        )

    scheduled_section = (
        [
            "| Date | Event | Format | Location | Calendar |",
            "| --- | --- | --- | --- | --- |",
            *rows,
        ]
        if rows
        else ["No events with confirmed dates are currently listed."]
    )

    undated_rows = []
    for event in undated_conferences:
        details = event.description
        if event.organizer:
            details = f"{details} Organizer: {event.organizer}."
        undated_rows.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(event.expected_timing or "Dates to be announced"),
                    event_display_name(event),
                    markdown_escape(details),
                ]
            )
            + " |"
        )

    subscription_rows = [
        "| "
        + " | ".join(
            [
                feed.name,
                feed.description,
                f"[ICS]({calendar.feed_url(feed.filename)})",
                f"`{calendar.feed_url(feed.filename)}`",
            ]
        )
        + " |"
        for feed in FEED_DEFINITIONS
    ]

    return "\n".join(
        [
            "<!-- Generated by .github/scripts/generate_conferences.py. Edit data/conferences.yml instead. -->",
            "",
            f"# {year} events",
            "",
            (
                "This table and its calendar files are generated from "
                "[`data/conferences.yml`](../data/conferences.yml)."
            ),
            "",
            "## Subscribe to the calendar",
            "",
            f"- [Open the calendar page to subscribe]({calendar.public_url})",
            "",
            "| Calendar | Includes | Download | Subscription URL |",
            "| --- | --- | --- | --- |",
            *subscription_rows,
            "",
            (
                "Calendar applications decide how frequently subscriptions are refreshed. "
                "The individual Add links below are one-time downloads and do not receive "
                "later updates."
            ),
            "",
            "## Events",
            "",
            *scheduled_section,
            "",
            *(
                [
                    "## Dates to be announced",
                    "",
                    (
                        "These established annual events do not yet have official dates. "
                        "They are not included in calendar subscriptions."
                    ),
                    "",
                    "| Expected timing | Event | Details |",
                    "| --- | --- | --- |",
                    *undated_rows,
                    "",
                ]
                if undated_rows
                else []
            ),
            "## Suggest an event or correction",
            "",
            (
                "You do not need to edit repository files. Use the guided issue forms to "
                "[suggest a conference or event]"
                f"({ADDITION_FORM_URL}) or [report a correction]({CORRECTION_FORM_URL}). "
                "Maintainers will review the official source before changing the calendar."
            ),
            "",
            (
                "Technical contributors can read "
                "[the conference data guide](../data/README.md) before opening a pull request."
            ),
            "",
        ]
    )


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_ics_line(line: str) -> str:
    """Fold an iCalendar content line at 75 UTF-8 octets."""
    chunks: list[str] = []
    current = ""
    byte_limit = 75
    for character in line:
        candidate = current + character
        if current and len(candidate.encode("utf-8")) > byte_limit:
            if current.endswith((" ", "\t")):
                chunks.append(current[:-1])
                current = current[-1] + character
            else:
                chunks.append(current)
                current = character
            byte_limit = 74
        else:
            current = candidate
    chunks.append(current)
    return "\r\n ".join(chunks)


def ics_timestamp(value: date) -> str:
    timestamp = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def event_ics_lines(event: Conference) -> list[str]:
    if event.start_date is None or event.end_date is None or event.location is None:
        raise DataError(f"conference {event.id!r}: undated conferences cannot be exported to ICS")
    description_parts = [event.description, f"Attendance: {event.format}"]
    if event.organizer:
        description_parts.append(f"Organizer: {event.organizer}")
    if event.language:
        description_parts.append(f"Language: {event.language}")

    if event.start_time is not None and event.end_time is not None and event.time_zone:
        zone = ZoneInfo(event.time_zone)
        start = datetime.combine(event.start_date, event.start_time, tzinfo=zone)
        end = datetime.combine(event.end_date, event.end_time, tzinfo=zone)
        date_lines = [
            f"DTSTART:{start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        ]
    else:
        date_lines = [
            f"DTSTART;VALUE=DATE:{event.start_date.strftime('%Y%m%d')}",
            (
                "DTEND;VALUE=DATE:"
                f"{(event.end_date + timedelta(days=1)).strftime('%Y%m%d')}"
            ),
        ]

    return [
        "BEGIN:VEVENT",
        f"UID:{event.id}@{UID_DOMAIN}",
        f"DTSTAMP:{ics_timestamp(event.last_verified)}",
        f"LAST-MODIFIED:{ics_timestamp(event.last_verified)}",
        f"SEQUENCE:{event.sequence}",
        *date_lines,
        f"SUMMARY:{ics_escape(event.name)}",
        f"DESCRIPTION:{ics_escape(chr(10).join(description_parts))}",
        f"LOCATION:{ics_escape(event.location)}",
        f"URL:{event.url}",
        f"STATUS:{event.status.upper()}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def render_ics(
    calendar: CalendarDetails,
    conferences: list[Conference],
    feed: FeedDefinition | None = None,
) -> str:
    calendar_name = calendar.name
    calendar_description = calendar.description
    if feed is not None:
        if feed.key != "all":
            calendar_name = f"{calendar.name}: {feed.name}"
        calendar_description = f"{calendar.description} {feed.description}"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Nordic Accessibility Community Group//Conference Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
        f"X-WR-CALDESC:{ics_escape(calendar_description)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for event in conferences:
        lines.extend(event_ics_lines(event))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def html_event_name(event: Conference) -> str:
    name = html.escape(event.name)
    if event.status == "cancelled":
        name = f'<s>{name}</s> <span class="status">Cancelled</span>'
    elif event.status == "tentative":
        name = f'{name} <span class="status">Tentative</span>'
    return name


def country_filter_value(event: Conference) -> str:
    return event.country_code or "online-only"


def country_filter_label(country_code: str) -> str:
    return COUNTRY_FILTER_LABELS.get(country_code, country_code)


def render_html(calendar: CalendarDetails, conferences: list[Conference]) -> str:
    scheduled_conferences = [event for event in conferences if not event.is_undated]
    undated_conferences = [event for event in conferences if event.is_undated]
    event_count = len(scheduled_conferences)
    event_word = "event" if event_count == 1 else "events"
    table_caption = f"Accessibility conferences and events: {event_count} {event_word}"
    rows = []
    for event in scheduled_conferences:
        add_label = html.escape(f"Add event: {event.name} to calendar", quote=True)
        rows.append(
            f"""          <tr data-format="{html.escape(event.format, quote=True)}" data-country="{html.escape(country_filter_value(event), quote=True)}" data-end-date="{event.end_date.isoformat()}">
            <td><time datetime="{event.start_date.isoformat()}">{html.escape(format_event_date(event))}</time></td>
            <td>
              <a href="{html.escape(event.url, quote=True)}">{html_event_name(event)}</a>
              <p>{html.escape(event.description)}</p>
            </td>
            <td>{html.escape(event.format)}</td>
            <td>{html.escape(event.location)}</td>
            <td><a href="{html.escape(event.event_feed_url, quote=True)}" aria-label="{add_label}">Add event</a></td>
          </tr>"""
        )

    if not rows:
        rows.append(
            '          <tr><td colspan="5">No events are currently listed.</td></tr>'
        )

    country_filter_options = []
    country_values = sorted(
        {country_filter_value(event) for event in scheduled_conferences},
        key=country_filter_label,
    )
    for country_code in country_values:
        country_filter_options.append(
            f'<label><input type="checkbox" name="country" data-filter="country" '
            f'value="{html.escape(country_code, quote=True)}"> '
            f'{html.escape(country_filter_label(country_code))}</label>'
        )

    undated_items = []
    for event in undated_conferences:
        details = [
            f'<a href="{html.escape(event.url, quote=True)}">{html_event_name(event)}</a>',
            f"<p>{html.escape(event.description)}</p>",
        ]
        if event.expected_timing:
            details.append(
                "<p class=\"undated-meta\"><strong>Expected timing:</strong> "
                f"{html.escape(event.expected_timing)}</p>"
            )
        if event.organizer:
            details.append(
                "<p class=\"undated-meta\"><strong>Organizer:</strong> "
                f"{html.escape(event.organizer)}</p>"
            )
        undated_items.append("          <li>" + "".join(details) + "</li>")

    undated_section = ""
    if undated_items:
        undated_section = f"""

    <section class="panel undated-events" aria-labelledby="undated-events-heading">
      <h2 id="undated-events-heading">Dates to be announced</h2>
      <p>These established annual events do not yet have official dates. They are not included in calendar subscriptions.</p>
      <ul class="undated-list">
{chr(10).join(undated_items)}
      </ul>
    </section>"""

    subscription_cards = []
    for feed in FEED_DEFINITIONS:
        feed_url = html.escape(calendar.feed_url(feed.filename), quote=True)
        webcal_url = html.escape(calendar.webcal_url(feed.filename), quote=True)
        feed_name = html.escape(feed.name)
        subscribe_label = html.escape(
            f"Subscribe to {feed.name} calendar", quote=True
        )
        download_label = html.escape(
            f"Download one-time .ics file for {feed.name}", quote=True
        )
        subscription_cards.append(
            f"""        <article class="subscription-card">
          <h3>{feed_name}</h3>
          <p>{html.escape(feed.description)}</p>
          <div class="actions">
            <a class="button primary" href="{webcal_url}" aria-label="{subscribe_label}">Subscribe for updates</a>
            <a class="button" href="{feed_url}" download aria-label="{download_label}">Download one-time file (.ics)</a>
          </div>
          <label for="{feed.key}-subscription-url">Subscription URL for {feed_name}</label>
          <input id="{feed.key}-subscription-url" type="url" readonly value="{feed_url}" onclick="this.select()">
        </article>"""
        )

    calendar_name = html.escape(calendar.name)
    calendar_description = html.escape(calendar.description)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{calendar_description}">
  <title>{calendar_name}</title>
  <link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Next:wght@400;500;600;700&display=swap" rel="stylesheet" media="(prefers-color-scheme: dark)">
  <style>
    :root {{
      color-scheme: light dark;
      --background: #f7f6f1;
      --surface: #ffffff;
      --surface-raised: color-mix(in srgb, var(--surface), var(--accent) 8%);
      --text: #17221d;
      --muted: #4b5b53;
      --accent: #075c46;
      --accent-hover: #043f30;
      --link: var(--accent);
      --link-hover: var(--accent-hover);
      --action: var(--accent);
      --action-hover: var(--accent-hover);
      --action-text: #ffffff;
      --border: #cbd4cf;
      --focus: #ffbf47;
      --focus-gap: transparent;
      --font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --radius: 0.75rem;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: var(--background);
      color: var(--text);
      font-family: var(--font-sans);
      font-size: 1rem;
      line-height: 1.6;
    }}

    a {{ color: var(--link); text-underline-offset: 0.18em; }}
    a:hover {{ color: var(--link-hover); }}
    a:focus-visible, input:focus-visible, button:focus-visible, summary:focus-visible {{ outline: 0.25rem solid var(--focus); outline-offset: 0.2rem; }}

    header, main, footer {{ width: min(76rem, calc(100% - 2rem)); margin-inline: auto; }}

    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding-block: 1.25rem;
    }}

    header a {{ font-weight: 700; }}

    main {{ padding-block: clamp(2rem, 6vw, 5rem); }}
    h1 {{ max-width: 18ch; margin: 0; font-size: clamp(2.4rem, 7vw, 5rem); line-height: 1.02; letter-spacing: -0.035em; }}
    h2 {{ margin-top: 0; font-size: clamp(1.6rem, 4vw, 2.2rem); line-height: 1.15; }}
    h3 {{ margin-top: 0; font-size: 1.3rem; line-height: 1.2; }}
    .intro {{ max-width: 48rem; margin: 1.5rem 0 3rem; color: var(--muted); font-size: 1.2rem; }}
    .visually-hidden {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}

    section {{ margin-block: 3rem; }}
    .panel {{ padding: clamp(1.25rem, 4vw, 2rem); border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }}
    .subscription-options {{ display: inline-block; margin-block: 1.5rem; }}
    summary {{ cursor: pointer; font-weight: 700; }}
    details[open] > summary {{ margin-bottom: 1.5rem; }}
    .subscription-options > summary {{ list-style: none; }}
    .subscription-options > summary::-webkit-details-marker {{ display: none; }}
    .subscription-options[open] {{ display: block; padding: clamp(1.25rem, 4vw, 2rem); border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }}
    .subscription-options[open] > summary {{ display: inline-block; }}
    .subscription-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr)); gap: 1rem; }}
    .subscription-card {{ padding: 1.25rem; border: 1px solid var(--border); border-radius: calc(var(--radius) * 0.75); background: var(--background); }}
    .subscription-card > p {{ color: var(--muted); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 0.75rem; margin-block: 1.5rem; }}
    .button {{
      display: inline-block;
      padding: 0.7rem 1rem;
      border: 2px solid var(--action);
      border-radius: 0.4rem;
      font-weight: 700;
      text-decoration: none;
    }}
    .button.primary {{ background: var(--action); color: var(--action-text); }}
    .button.primary:hover {{ background: var(--action-hover); border-color: var(--action-hover); color: var(--action-text); }}

    label {{ display: block; margin-bottom: 0.35rem; font-weight: 700; }}
    input {{
      width: 100%;
      padding: 0.7rem;
      border: 1px solid var(--border);
      border-radius: 0.3rem;
      background: var(--background);
      color: var(--text);
      font: inherit;
    }}

    .event-filters {{ margin-bottom: 1.5rem; padding: 1rem; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }}
    .event-filters[open] > summary {{ margin-bottom: 1rem; }}
    .filter-options {{ display: flex; flex-wrap: wrap; gap: 0.75rem 1.25rem; align-items: center; }}
    .filter-groups {{ display: grid; gap: 1rem; }}
    .filter-groups fieldset {{ margin: 0; }}
    .filter-actions {{ display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1rem; }}
    .filter-options label {{ display: flex; gap: 0.5rem; align-items: center; margin: 0; font-weight: 400; }}
    .filter-options input {{ width: 1.25rem; height: 1.25rem; padding: 0; border: 0; border-radius: 0; background: transparent; accent-color: var(--link); }}
    button {{ padding: 0.45rem 0.7rem; border: 1px solid var(--action); border-radius: 0.3rem; background: transparent; color: var(--action); font: inherit; font-weight: 700; cursor: pointer; }}
    button:not(:disabled):hover {{ background: color-mix(in srgb, var(--surface), var(--action) 10%); color: var(--action-hover); }}
    button:disabled {{ cursor: not-allowed; opacity: 0.55; }}
    .filter-status {{ margin: 1rem 0 0; color: var(--muted); }}
    .undated-events > p {{ color: var(--muted); }}
    .undated-list {{ display: grid; gap: 0.75rem; margin: 1.5rem 0 0; padding: 0; list-style: none; }}
    .undated-list li {{ padding: 1rem; border: 1px solid var(--border); border-radius: calc(var(--radius) * 0.75); background: var(--background); }}
    .undated-list li > a {{ font-weight: 700; }}
    .undated-list p {{ margin: 0.5rem 0 0; color: var(--muted); }}
    .undated-list .undated-meta {{ font-size: 0.95rem; }}

    .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }}
    .past-events {{ margin-top: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 1rem; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: var(--surface-raised); }}
    td p {{ min-width: 18rem; margin: 0.35rem 0 0; color: var(--muted); }}
    tr:last-child td {{ border-bottom: 0; }}
    .status {{ display: inline-block; margin-left: 0.35rem; font-size: 0.9rem; font-weight: 700; }}

    footer {{ padding-block: 2rem; border-top: 1px solid var(--border); color: var(--muted); }}

    @media (max-width: 40rem) {{
      header {{ align-items: flex-start; flex-direction: column; }}
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --background: #0b1020;
        --surface: #121a2d;
        --surface-raised: #1a2742;
        --text: #f4f7ff;
        --muted: #b9c2d3;
        --link: #afc8ff;
        --link-hover: #d4e1ff;
        --action: #ff9a7e;
        --action-hover: #ffb29d;
        --action-text: #171b2b;
        --border: #5b6a88;
        --focus: #ffd97a;
        --focus-gap: #0b1020;
        --font-sans: "Atkinson Hyperlegible Next", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}

      body {{ letter-spacing: 0.002em; }}

      header, main, footer {{ width: min(78rem, calc(100% - 2.5rem)); }}
      header {{ padding-block: 1.5rem 1.35rem; border-bottom: 1px solid var(--border); }}
      header a {{ font-weight: 600; text-decoration-thickness: 0.09em; }}
      header a:first-child {{ color: var(--text); text-decoration-color: var(--link); }}

      main {{ padding-block: clamp(2rem, 4vw, 3.5rem); }}
      h1 {{ max-width: 17ch; font-size: clamp(3rem, 7vw, 5.75rem); font-weight: 700; line-height: 0.98; letter-spacing: -0.045em; }}
      h2 {{ font-size: clamp(1.8rem, 4vw, 2.45rem); line-height: 1.08; letter-spacing: -0.025em; }}
      h3 {{ font-size: 1.35rem; line-height: 1.15; letter-spacing: -0.015em; }}
      .intro {{ max-width: 42rem; margin-block: 1.25rem 2rem; font-size: 1.2rem; line-height: 1.55; }}
      section {{ margin-block: clamp(2rem, 3vw, 3rem); }}

      .panel, .subscription-options[open], .event-filters, .table-wrap {{
        border-color: var(--border);
        border-radius: 0.6rem;
        box-shadow: inset 0 1px 0 color-mix(in srgb, var(--text), transparent 92%);
      }}
      .panel {{ background: var(--surface-raised); }}

      summary {{ font-weight: 600; }}
      .subscription-options {{ margin-block: 0; }}
      .subscription-options[open] {{ background: var(--surface); }}
      .subscription-options[open] > summary {{ margin-bottom: 1.75rem; }}
      .subscription-grid {{ gap: 0.75rem; }}
      .subscription-card {{
        padding: clamp(1.25rem, 3vw, 1.75rem);
        border-color: var(--border);
        border-radius: 0.45rem;
        background: var(--surface-raised);
      }}
      .subscription-card > p {{ line-height: 1.55; }}
      .actions {{ gap: 0.75rem; margin-block: 1.5rem; }}
      .button {{
        min-height: 2.75rem;
        padding: 0.6rem 0.95rem;
        border-color: var(--link);
        border-radius: 0.4rem;
        color: var(--link);
        font-weight: 600;
      }}
      .button:hover {{ background: color-mix(in srgb, var(--surface-raised), var(--link) 12%); color: var(--link-hover); }}
      .button.primary {{ border-color: var(--action); }}
      .button.primary:hover {{ background: var(--action-hover); border-color: var(--action-hover); color: var(--action-text); }}

      label {{ font-weight: 600; }}
      input {{ border-radius: 0.4rem; background: var(--background); }}
      .event-filters {{ padding: clamp(1.1rem, 3vw, 1.5rem); background: var(--surface); }}
      .filter-options {{ gap: 0.65rem 1.5rem; }}
      .filter-options label {{ min-height: 2rem; }}
      .filter-actions {{ gap: 0.6rem; }}
      button {{ min-height: 2.5rem; padding-inline: 0.85rem; border-color: var(--link); border-radius: 0.4rem; color: var(--link); font-weight: 600; }}
      button:not(:disabled):hover {{ background: color-mix(in srgb, var(--surface-raised), var(--link) 12%); color: var(--link-hover); }}
      .undated-list li {{ border-color: var(--border); border-radius: 0.45rem; background: var(--surface-raised); }}

      .table-wrap {{ background: var(--surface); }}
      th, td {{ padding: clamp(0.95rem, 2.5vw, 1.25rem); border-color: var(--border); }}
      th {{ background: var(--surface-raised); font-weight: 600; white-space: nowrap; }}
      td {{ line-height: 1.55; }}
      td p {{ line-height: 1.5; }}
      tbody tr:not([hidden]):nth-child(even) td {{ background: color-mix(in srgb, var(--surface), var(--background) 24%); }}
      tbody tr:not([hidden]):hover td, tbody tr:focus-within td {{ background: color-mix(in srgb, var(--surface-raised), var(--link) 10%); }}
      .status {{ font-weight: 600; }}

      footer {{ padding-block: 2.5rem; }}

      a:focus-visible, input:focus-visible, button:focus-visible, summary:focus-visible {{
        outline: 0.18rem solid var(--focus-gap);
        outline-offset: 0.18rem;
        box-shadow: 0 0 0 0.16rem var(--focus);
      }}
    }}

    @media (forced-colors: active) {{
      a:focus-visible, input:focus-visible, button:focus-visible, summary:focus-visible {{
        outline: 2px solid CanvasText;
        outline-offset: 2px;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <a href="{REPOSITORY_URL}">Nordic Accessibility Community Group</a>
    <a href="{REPOSITORY_URL}">View on GitHub</a>
  </header>
  <main>
    <h1>Accessibility conferences and events</h1>
    <p class="intro">{calendar_description}</p>

    <section aria-labelledby="subscribe-heading">
      <h2 class="visually-hidden" id="subscribe-heading">Subscribe to the calendar</h2>
      <details class="subscription-options">
        <summary class="button primary">Subscribe to calendars</summary>
        <p>Subscribe to keep the calendar updated. Download a one-time copy (.ics) instead if you do not need future updates.</p>
        <div class="subscription-grid">
{chr(10).join(subscription_cards)}
        </div>
        <p>For Google Calendar, copy the subscription URL and add it using <strong>Other calendars</strong>, then <strong>From URL</strong>. Calendar applications control how frequently subscriptions refresh.</p>
      </details>
    </section>

    <section aria-labelledby="events-heading">
      <h2 id="events-heading">Events</h2>
      <details class="event-filters">
        <summary>Filter events</summary>
        <p>Select one or more options to narrow the events. Leave a group empty to include all options in that group.</p>
        <div class="filter-groups">
        <fieldset>
          <legend>Format</legend>
          <div class="filter-options">
            <label><input type="checkbox" name="format" data-filter="format" value="In person"> In person</label>
            <label><input type="checkbox" name="format" data-filter="format" value="Hybrid"> Hybrid</label>
            <label><input type="checkbox" name="format" data-filter="format" value="Online"> Online</label>
          </div>
        </fieldset>
        <fieldset>
          <legend>Country or online</legend>
          <div class="filter-options">
{chr(10).join(f'            {option}' for option in country_filter_options)}
          </div>
        </fieldset>
        </div>
        <div class="filter-actions">
          <button type="button" id="clear-filters" disabled>Clear filters</button>
        </div>
        <p class="filter-status" id="filter-status" role="status" aria-live="polite" aria-atomic="true">Showing all {event_count} {event_word}.</p>
      </details>
      <div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable conference table">
        <table>
          <caption class="visually-hidden" id="events-caption">{table_caption}</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Event</th>
              <th scope="col">Format</th>
              <th scope="col">Location</th>
              <th scope="col">Calendar</th>
            </tr>
          </thead>
          <tbody id="current-events">
{chr(10).join(rows)}
          <tr id="no-filter-results" hidden>
            <td colspan="5">No events match the selected filters.</td>
          </tr>
          </tbody>
        </table>
      </div>
      <details class="past-events" id="past-events" hidden>
        <summary id="past-events-summary">Past events</summary>
        <div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable past conference table">
          <table>
            <caption class="visually-hidden" id="past-events-caption">Past accessibility conferences and events</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Event</th>
                <th scope="col">Format</th>
                <th scope="col">Location</th>
                <th scope="col">Calendar</th>
              </tr>
            </thead>
            <tbody id="past-events-body"></tbody>
          </table>
        </div>
      </details>
    </section>
{undated_section}

    <section class="panel" aria-labelledby="suggest-heading">
      <h2 id="suggest-heading">Suggest an event or correction</h2>
      <p>You do not need to know Git or edit data files. A guided form collects the information maintainers need to review the official source.</p>
      <div class="actions">
        <a class="button primary" href="{ADDITION_FORM_URL}">Suggest an event</a>
        <a class="button" href="{CORRECTION_FORM_URL}">Report a correction</a>
      </div>
    </section>
  </main>
  <footer>
    <p>Calendar data is maintained in the <a href="{REPOSITORY_URL}">accessibility conferences repository</a>.</p>
  </footer>
  <script>
    const filters = document.querySelectorAll('input[data-filter]');
    const formatFilters = document.querySelectorAll('input[data-filter="format"]');
    const countryFilters = document.querySelectorAll('input[data-filter="country"]');
    const rows = [...document.querySelectorAll('tbody tr[data-format]')];
    const currentEvents = document.querySelector('#current-events');
    const pastEvents = document.querySelector('#past-events');
    const pastEventsBody = document.querySelector('#past-events-body');
    const pastEventsSummary = document.querySelector('#past-events-summary');
    const pastEventsCaption = document.querySelector('#past-events-caption');
    const status = document.querySelector('#filter-status');
    const caption = document.querySelector('#events-caption');
    const emptyState = document.querySelector('#no-filter-results');
    const emptyStateMessage = emptyState.querySelector('td');
    const clear = document.querySelector('#clear-filters');

    function localDate() {{
      const now = new Date();
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const day = String(now.getDate()).padStart(2, '0');
      return `${{now.getFullYear()}}-${{month}}-${{day}}`;
    }}

    function separatePastEvents() {{
      const today = localDate();
      rows.forEach((row) => {{
        if (row.dataset.endDate < today) {{
          pastEventsBody.append(row);
        }} else {{
          currentEvents.insertBefore(row, emptyState);
        }}
      }});
    }}

    function updateFilters() {{
      const selectedFormats = [...formatFilters].filter((filter) => filter.checked).map((filter) => filter.value);
      const selectedCountries = [...countryFilters].filter((filter) => filter.checked).map((filter) => filter.value);
      let visibleCount = 0;
      let currentVisibleCount = 0;
      let pastVisibleCount = 0;
      rows.forEach((row) => {{
        const formatMatches = selectedFormats.length === 0
          || selectedFormats.includes(row.dataset.format);
        const countryMatches = selectedCountries.length === 0
          || selectedCountries.includes(row.dataset.country);
        const visible = formatMatches && countryMatches;
        row.hidden = !visible;
        if (visible) {{
          visibleCount += 1;
          if (row.parentElement === pastEventsBody) {{
            pastVisibleCount += 1;
          }} else {{
            currentVisibleCount += 1;
          }}
        }}
      }});
      const eventWord = visibleCount === 1 ? 'event' : 'events';
      const currentEventWord = currentVisibleCount === 1 ? 'event' : 'events';
      const pastEventWord = pastVisibleCount === 1 ? 'event' : 'events';
      const noFiltersSelected = selectedFormats.length === 0
        && selectedCountries.length === 0;
      clear.disabled = noFiltersSelected;
      emptyState.hidden = rows.length === 0 || currentVisibleCount !== 0;
      if (!emptyState.hidden) {{
        emptyStateMessage.textContent = noFiltersSelected
          ? 'No current or upcoming events are listed. Past events appear below.'
          : pastVisibleCount > 0
            ? 'No current or upcoming events match the selected filters. Matching past events appear below.'
            : 'No events match the selected filters.';
      }}
      caption.textContent = noFiltersSelected
        ? `Current and upcoming accessibility conferences and events: ${{currentVisibleCount}} ${{currentEventWord}}`
        : `Current and upcoming accessibility conferences and events: ${{currentVisibleCount}} ${{currentEventWord}} shown`;
      status.textContent = noFiltersSelected
        ? `Showing all ${{visibleCount}} ${{eventWord}}.`
        : `Showing ${{visibleCount}} of ${{rows.length}} ${{eventWord}}.`;
      pastEvents.hidden = pastVisibleCount === 0;
      pastEvents.open = !noFiltersSelected && pastVisibleCount > 0;
      pastEventsSummary.textContent = noFiltersSelected
        ? `Past events (${{pastVisibleCount}})`
        : `Past events (${{pastVisibleCount}} matching)`;
      pastEventsCaption.textContent = noFiltersSelected
        ? `Past accessibility conferences and events: ${{pastVisibleCount}} ${{pastEventWord}}`
        : `Past accessibility conferences and events: ${{pastVisibleCount}} matching ${{pastEventWord}}`;
    }}

    filters.forEach((filter) => filter.addEventListener('change', updateFilters));
    clear.addEventListener('click', () => {{
      filters.forEach((filter) => {{ filter.checked = false; }});
      updateFilters();
    }});
    function updateEventList() {{
      separatePastEvents();
      updateFilters();
    }}

    updateEventList();
    window.addEventListener('pageshow', updateEventList);
  </script>
</body>
</html>
"""


def expected_outputs(
    calendar: CalendarDetails, conferences: list[Conference]
) -> dict[Path, str]:
    outputs = {PAGE_FILE: render_html(calendar, conferences)}
    for year in sorted({event.year for event in conferences}):
        dated, undated = conferences_for_year(conferences, year)
        outputs[EVENTS_DIRECTORY / f"{year}.md"] = render_markdown(
            calendar, year, dated, undated
        )
    for feed in FEED_DEFINITIONS:
        outputs[CALENDAR_DIRECTORY / feed.filename] = render_ics(
            calendar, conferences_for_feed(feed, conferences), feed
        )
    for event in conferences:
        if event.is_undated:
            continue
        outputs[EVENT_DIRECTORY / f"{event.id}.ics"] = render_ics(calendar, [event])
    return outputs


def write_outputs(outputs: dict[Path, str]) -> None:
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")

    expected_event_files = {path for path in outputs if path.parent == EVENT_DIRECTORY}
    expected_feed_files = {
        path for path in outputs if path.parent == CALENDAR_DIRECTORY
    }
    if EVENT_DIRECTORY.exists():
        for path in EVENT_DIRECTORY.glob("*.ics"):
            if path not in expected_event_files:
                path.unlink()
    for path in CALENDAR_DIRECTORY.glob("*.ics"):
        if path not in expected_feed_files:
            path.unlink()


def check_outputs(outputs: dict[Path, str]) -> list[Path]:
    stale: list[Path] = []
    for path, expected in outputs.items():
        try:
            with path.open("r", encoding="utf-8", newline="") as generated_file:
                actual = generated_file.read()
        except OSError:
            stale.append(path)
            continue
        if actual != expected:
            stale.append(path)

    expected_event_files = {path for path in outputs if path.parent == EVENT_DIRECTORY}
    expected_feed_files = {
        path for path in outputs if path.parent == CALENDAR_DIRECTORY
    }
    if EVENT_DIRECTORY.exists():
        stale.extend(
            path
            for path in EVENT_DIRECTORY.glob("*.ics")
            if path not in expected_event_files
        )
    stale.extend(
        path
        for path in CALENDAR_DIRECTORY.glob("*.ics")
        if path not in expected_feed_files
    )
    return sorted(set(stale))


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated files do not match the conference data",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        calendar, conferences = load_data()
    except DataError as error:
        print(f"Conference data error: {error}", file=sys.stderr)
        return 1

    outputs = expected_outputs(calendar, conferences)
    if arguments.check:
        stale = check_outputs(outputs)
        if stale:
            print("Generated conference files are out of date:", file=sys.stderr)
            for path in stale:
                print(f"- {path.relative_to(ROOT)}", file=sys.stderr)
            print(
                "Run python3 .github/scripts/generate_conferences.py and commit the results.",
                file=sys.stderr,
            )
            return 1
        print(f"Conference data and {len(outputs)} generated files are up to date.")
        return 0

    write_outputs(outputs)
    print(f"Generated {len(outputs)} files for {len(conferences)} conferences.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
