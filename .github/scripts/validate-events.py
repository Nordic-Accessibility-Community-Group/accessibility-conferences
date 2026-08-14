#!/usr/bin/env python3

from __future__ import annotations

import calendar
import re
import sys
from datetime import date
from pathlib import Path


MONTHS = {
    name: number
    for number, name in enumerate(calendar.month_name)
    if name
}
MONTH_PATTERN = "|".join(MONTHS)
MONTH_FIRST = re.compile(
    rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<day>\d{{1,2}})\b",
    re.IGNORECASE,
)
DAY_FIRST = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:\s*[-–]\s*\d{{1,2}})?\s+"
    rf"(?P<month>{MONTH_PATTERN})\b",
    re.IGNORECASE,
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)")
YEAR_HEADING = re.compile(r"^#\s+(\d{4})\s+events\s*$", re.IGNORECASE)

ALLOWED_HEADERS = {
    ("Dates", "Event", "Focus", "Location"),
    ("Dates", "Event", "Focus"),
    ("Dates", "List", "Focus"),
    ("Dates", "Event", "Location"),
}


def split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in cells
    )


def first_event_date(value: str, year: int) -> date | None:
    match = MONTH_FIRST.search(value) or DAY_FIRST.search(value)
    if not match:
        return None

    month_name = match.group("month").capitalize()
    month = MONTHS[month_name]
    day = int(match.group("day"))

    try:
        return date(year, month, day)
    except ValueError:
        return None


def validate_file(path: Path) -> tuple[int, int]:
    errors = 0
    rows_checked = 0
    lines = path.read_text(encoding="utf-8").splitlines()

    try:
        year = int(path.stem)
    except ValueError:
        print(f"{path}: filename must be a four-digit year.")
        return 1, 0

    heading_year = None
    for line in lines:
        heading_match = YEAR_HEADING.match(line)
        if heading_match:
            heading_year = int(heading_match.group(1))
            break

    if heading_year != year:
        print(f"{path}: H1 year must match filename year {year}.")
        errors += 1

    index = 0
    while index < len(lines) - 1:
        header = split_row(lines[index])
        separator = split_row(lines[index + 1])
        if not header or not is_separator(separator):
            index += 1
            continue

        if tuple(header) not in ALLOWED_HEADERS:
            print(
                f"{path}:{index + 1}: unsupported table columns: "
                + " | ".join(header)
            )
            errors += 1

        if len(separator) != len(header):
            print(f"{path}:{index + 2}: table separator column count is wrong.")
            errors += 1

        previous_date: date | None = None
        index += 2

        while index < len(lines) and lines[index].lstrip().startswith("|"):
            row = split_row(lines[index])
            line_number = index + 1

            if len(row) != len(header):
                print(
                    f"{path}:{line_number}: expected {len(header)} columns, "
                    f"found {len(row)}."
                )
                errors += 1
                index += 1
                continue

            if any(not cell for cell in row):
                print(f"{path}:{line_number}: table cells must not be empty.")
                errors += 1

            event_date = first_event_date(row[0], year)
            if event_date is None:
                print(
                    f"{path}:{line_number}: could not read a valid event date "
                    f"from {row[0]!r}."
                )
                errors += 1
            elif previous_date and event_date < previous_date:
                print(
                    f"{path}:{line_number}: event dates are not chronological "
                    f"within this table."
                )
                errors += 1
            else:
                previous_date = event_date

            if path.parts[0] == "events" and not MARKDOWN_LINK.search(row[1]):
                print(
                    f"{path}:{line_number}: current event names must link to "
                    "an event source."
                )
                errors += 1

            rows_checked += 1
            index += 1

    if rows_checked == 0:
        print(f"{path}: no event table rows found.")
        errors += 1

    return errors, rows_checked


def main() -> int:
    paths = sorted(Path("events").glob("*.md"))
    paths.extend(sorted(Path("archive").glob("*.md")))

    if not paths:
        print("No event or archive files found.")
        return 1

    total_errors = 0
    total_rows = 0

    for path in paths:
        errors, rows = validate_file(path)
        total_errors += errors
        total_rows += rows

    if total_errors:
        print(f"Event validation found {total_errors} error(s).")
        return 1

    print(f"Validated {total_rows} event row(s) across {len(paths)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
