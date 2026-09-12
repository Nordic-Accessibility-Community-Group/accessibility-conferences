# Conference data guide

The calendar uses [`conferences.yml`](conferences.yml) as its single source of truth. The generated Markdown, web page and ICS files should not be edited directly.

People who do not want to edit repository files can use the guided issue forms to [suggest a conference or event](https://github.com/Nordic-Accessibility-Community-Group/accessibility-conferences/issues/new?template=conference-addition.yml) or [report a correction](https://github.com/Nordic-Accessibility-Community-Group/accessibility-conferences/issues/new?template=conference-correction.yml).

## Add or update an event

1. Check the dates and details against the official event website.
2. Add or edit the entry in `conferences.yml`.
3. For a dated event, use inclusive `start_date` and `end_date` values in `YYYY-MM-DD` format.
4. For a dated event with specific times, add `start_time`, `end_time` and the IANA `time_zone`. Omit all three for an all-day event.
5. For a dated event, set `attendance.onsite` and `attendance.online` independently. At least one must be `true`.
6. For a dated onsite event, add the two-letter ISO `country_code` in quotation marks, such as `"FI"` or `"US"`.
7. Keep the `id` stable after publication. Calendar applications use it to identify updates.
8. Increase `sequence` whenever a published event changes.
9. Set `last_verified` to the date on which the official source was checked.
10. Regenerate the published files.

For a cancellation, keep the event in the data, set `status` to `cancelled` and increase `sequence`. Removing it immediately could leave the old event in subscribers' calendars.

The human-readable format is derived from the attendance fields: both options produce `Hybrid`, onsite only produces `In person`, and online only produces `Online`. Set `online` to `true` when the official event offers live online attendance or streaming. Recordings published only after the event do not count.

Allowed `status` values are `confirmed`, `tentative` and `cancelled`.

## Events with dates to be announced

For an established recurring event that has no official date yet, use an undated
entry instead of guessing a calendar date. It must include `year`, use
`status: tentative`, and omit `start_date`, `end_date`, times, location,
country and attendance. Add `expected_timing` only when an established pattern
supports plain language such as `Usually held in November`.

Undated entries appear in a separate **Dates to be announced** section in the
year list and on the calendar page. They are not included in ICS subscriptions
or individual calendar downloads. When the organiser publishes details, keep
the same `id`, replace `year` and `expected_timing` with the normal dated
fields, and increase `sequence`.

## Subscription filters

The generator publishes four independent subscriptions:

- `conferences.ics` contains everything.
- `eu.ics` contains events with onsite attendance in an EU member country.
- `us.ics` contains events with onsite attendance in the United States.
- `online.ics` contains every event with `attendance.online` set to `true`, including hybrid and online-only events.

An online-only event is included in the online subscription but not a geographic subscription. Iceland, Norway and other European countries outside the European Union are not included in the EU subscription.

## Generate the files

Install the generator dependency:

```sh
python3 -m pip install -r .github/scripts/requirements-conferences.txt
```

Generate the Markdown list, web page, filtered subscriptions and individual event files:

```sh
python3 .github/scripts/generate_conferences.py
```

Check that committed output matches the data without changing files:

```sh
python3 .github/scripts/generate_conferences.py --check
```

The conference calendar workflow runs the same check on pull requests. After a change reaches `main`, it publishes the contents of `calendar/` through GitHub Pages.

## Enable publishing

GitHub Pages must be enabled once by a repository administrator, with **GitHub Actions** selected as the source. A pull request cannot change that repository setting. After it is enabled, the workflow publishes the landing page and subscription feeds at the `public_url` configured in `conferences.yml`.
