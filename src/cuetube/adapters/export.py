"""Schedule plan exporters for offline consumption (JSON and iCalendar RFC 5545)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cuetube.domain.models import SchedulePlan


def _format_seconds(sec: int) -> str:
    """Format integer seconds into MM:SS or HH:MM:SS string."""
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_ics_datetime(dt: datetime) -> str:
    """Format datetime into UTC iCalendar format (YYYYMMDDTHHMMSSZ)."""
    if dt.tzinfo is None:
        utc_dt = dt.replace(tzinfo=UTC)
    else:
        utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def _escape_ics_text(text: str) -> str:
    """Escape text for RFC 5545 iCalendar values."""
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace(";", "\\;")
    escaped = escaped.replace(",", "\\,")
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    return escaped


def schedule_plan_to_dict(plan: SchedulePlan) -> dict[str, Any]:
    """Serialize SchedulePlan into a standard JSON-compatible dictionary."""
    slots_data = []
    for s in plan.slots:
        chapters_data = [
            {
                "title": c.title,
                "start_seconds": c.start_seconds,
                "end_seconds": c.end_seconds,
            }
            for c in s.chapters
        ]
        slots_data.append(
            {
                "slot_id": s.slot_id,
                "start_time": s.window.start_time.isoformat(),
                "end_time": s.window.end_time.isoformat(),
                "energy_level": s.window.energy_level.value,
                "video_id": s.video_id,
                "title": s.title,
                "video_duration_minutes": s.video_duration_minutes,
                "buffer_minutes": s.buffer_minutes,
                "target_playlist": s.target_playlist,
                "summary": s.summary,
                "video_url": s.video_url or f"https://youtube.com/watch?v={s.video_id}",
                "chapters": chapters_data,
            }
        )

    unassigned_data = [
        {
            "video_id": tv.item.video_id,
            "title": tv.item.title,
            "duration_seconds": tv.item.duration_seconds,
            "target_playlist": tv.triage.target_playlist,
            "cognitive_demand": tv.triage.cognitive_demand.value,
            "category": tv.triage.category,
        }
        for tv in plan.unassigned_videos
    ]

    return {
        "total_slots": len(slots_data),
        "total_unassigned": len(unassigned_data),
        "slots": slots_data,
        "unassigned_videos": unassigned_data,
    }


def export_schedule_to_json(
    plan: SchedulePlan,
    output_path: Path | str | None = None,
    indent: int = 2,
) -> str:
    """Export SchedulePlan to formatted JSON string and optionally save to file."""
    data = schedule_plan_to_dict(plan)
    json_str = json.dumps(data, indent=indent)

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json_str, encoding="utf-8")

    return json_str


def export_schedule_to_ics(
    plan: SchedulePlan,
    output_path: Path | str | None = None,
    calendar_name: str = "CueTube Schedule",
) -> str:
    """Export SchedulePlan to RFC 5545 iCalendar (.ics) string and optionally save to file."""
    now_stamp = _format_ics_datetime(datetime.now(UTC))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CueTube//Autonomous Video Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_ics_text(calendar_name)}",
    ]

    for s in plan.slots:
        video_url = s.video_url or f"https://youtube.com/watch?v={s.video_id}"
        desc_lines = [
            f"Video: {video_url}",
            f"Playlist: {s.target_playlist}",
            f"Energy Tier: {s.window.energy_level.value}",
            f"Watch Duration: {s.video_duration_minutes}m",
            f"Buffer Padding: {s.buffer_minutes}m",
        ]
        if s.summary and s.summary.strip():
            desc_lines.extend(["", "Summary:", s.summary.strip()])
        if s.chapters:
            desc_lines.extend(["", "Chapters:"])
            for ch in s.chapters:
                desc_lines.append(f"- {_format_seconds(ch.start_seconds)} {ch.title}")

        description_raw = "\n".join(desc_lines)
        escaped_desc = _escape_ics_text(description_raw)
        escaped_title = _escape_ics_text(f"CueTube: {s.title}")
        escaped_cat = _escape_ics_text(s.target_playlist)

        dt_start = _format_ics_datetime(s.window.start_time)
        dt_end = _format_ics_datetime(s.window.end_time)

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{s.slot_id}@cuetube",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART:{dt_start}",
                f"DTEND:{dt_end}",
                f"SUMMARY:{escaped_title}",
                f"DESCRIPTION:{escaped_desc}",
                f"URL:{video_url}",
                f"CATEGORIES:{escaped_cat}",
                "STATUS:CONFIRMED",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    lines.append("")  # Trailing newline

    ics_content = "\r\n".join(lines)

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ics_content, encoding="utf-8")

    return ics_content


def export_schedule_plan(plan: SchedulePlan, target_path: Path | str) -> str:
    """Auto-detect format by extension (.ics or .json) and export schedule plan."""
    p = Path(target_path)
    if p.suffix.lower() == ".ics":
        return export_schedule_to_ics(plan, output_path=p)
    return export_schedule_to_json(plan, output_path=p)
