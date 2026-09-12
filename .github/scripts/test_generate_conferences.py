"""Regression tests for the conference calendar generator."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

import generate_conferences as generator


class ConferenceGeneratorTests(unittest.TestCase):
    def write_data(self, conferences: list[str]) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        data_path = Path(temporary_directory.name) / "conferences.yml"
        data_path.write_text(
            "\n".join(
                [
                    "calendar:",
                    "  name: Test calendar",
                    "  description: Test description.",
                    "  public_url: https://example.test/calendar/",
                    "",
                    "conferences:",
                    *conferences,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return data_path

    def scheduled_conference(self, event_id: str = "dated-2027") -> str:
        return f'''  - id: {event_id}
    name: Dated conference
    start_date: "2027-03-10"
    end_date: "2027-03-12"
    location: Copenhagen, Denmark
    country_code: "DK"
    attendance:
      onsite: true
      online: false
    url: https://example.test/dated
    description: A scheduled accessibility conference.
    status: confirmed
    sequence: 0
    last_verified: "2026-09-12"'''

    def tba_conference(self) -> str:
        return '''  - id: tba-2027
    name: Dates to come conference
    year: 2027
    expected_timing: Usually held in November
    organizer: Example organiser
    url: https://example.test/tba
    description: A well-established annual accessibility conference.
    status: tentative
    sequence: 0
    last_verified: "2026-09-12"'''

    def test_undated_conference_renders_without_calendar_files(self) -> None:
        calendar, conferences = generator.load_data(
            self.write_data([self.scheduled_conference(), self.tba_conference()])
        )

        dated, undated = generator.conferences_for_year(conferences, 2027)
        self.assertEqual([event.id for event in dated], ["dated-2027"])
        self.assertEqual([event.id for event in undated], ["tba-2027"])

        markdown = generator.render_markdown(calendar, 2027, dated, undated)
        html = generator.render_html(calendar, conferences)
        feed = generator.render_ics(
            calendar,
            generator.conferences_for_feed(generator.FEED_DEFINITIONS[0], conferences),
            generator.FEED_DEFINITIONS[0],
        )
        outputs = generator.expected_outputs(calendar, conferences)

        self.assertIn("# 2027 events", markdown)
        self.assertIn("## Dates to be announced", markdown)
        self.assertIn("Usually held in November", markdown)
        self.assertIn("Dates to be announced", html)
        self.assertIn("not included in calendar subscriptions", html)
        self.assertNotIn("Dates to come conference", feed)
        self.assertFalse(
            any(path.name == "tba-2027.ics" for path in outputs),
            "Undated conferences must not create individual ICS files.",
        )

    def test_year_output_sorts_dated_and_undated_conferences(self) -> None:
        earlier = self.scheduled_conference("earlier-2027").replace(
            'start_date: "2027-03-10"', 'start_date: "2027-02-10"'
        ).replace('end_date: "2027-03-12"', 'end_date: "2027-02-12"')
        later = self.scheduled_conference("later-2027")
        tba = self.tba_conference().replace("Dates to come conference", "A conference")
        calendar, conferences = generator.load_data(self.write_data([later, tba, earlier]))

        dated, undated = generator.conferences_for_year(conferences, 2027)

        self.assertEqual([event.id for event in dated], ["earlier-2027", "later-2027"])
        self.assertEqual([event.name for event in undated], ["A conference"])
        self.assertIn("Dated conference", generator.render_markdown(calendar, 2027, dated, undated))

    def test_undated_only_year_has_no_placeholder_calendar_row(self) -> None:
        calendar, conferences = generator.load_data(
            self.write_data([self.tba_conference()])
        )
        dated, undated = generator.conferences_for_year(conferences, 2027)

        markdown = generator.render_markdown(calendar, 2027, dated, undated)

        self.assertIn("No events with confirmed dates are currently listed.", markdown)
        self.assertIn("## Dates to be announced", markdown)
        self.assertNotIn("| Date | Event | Format | Location | Calendar |", markdown)

    def test_undated_conference_requires_a_year_and_tentative_status(self) -> None:
        no_year = self.tba_conference().replace("    year: 2027\n", "")
        with self.assertRaisesRegex(generator.DataError, "missing: year"):
            generator.load_data(self.write_data([no_year]))

        confirmed = self.tba_conference().replace("status: tentative", "status: confirmed")
        with self.assertRaisesRegex(generator.DataError, "must use tentative status"):
            generator.load_data(self.write_data([confirmed]))

    def test_conference_cannot_mix_dated_and_undated_fields(self) -> None:
        mixed = self.tba_conference() + '\n    start_date: "2027-11-10"'
        with self.assertRaisesRegex(generator.DataError, "must include both start_date and end_date"):
            generator.load_data(self.write_data([mixed]))

        tba_with_attendance = self.tba_conference() + "\n    attendance:\n      onsite: false\n      online: true"
        with self.assertRaisesRegex(generator.DataError, "undated conferences cannot include attendance"):
            generator.load_data(self.write_data([tba_with_attendance]))


if __name__ == "__main__":
    unittest.main()
