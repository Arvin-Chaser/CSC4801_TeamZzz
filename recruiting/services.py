from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import transaction


APP_TIMEZONE = ZoneInfo("Asia/Shanghai")
VALID_STATUSES = ("Pending", "Interviewing", "Rejected", "Accepted")


@dataclass
class DomainError(Exception):
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_local_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DomainError("Invalid date/time", 400)
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=APP_TIMEZONE)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise DomainError("Invalid date/time", 400) from None


def apply_to_job(conn, candidate_id: int, job_id: int, *, now=None) -> int:
    now = (now or utc_now()).astimezone(timezone.utc)
    try:
        with transaction(conn, immediate=True):
            job = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise DomainError("Job not found", 404)
            existing = conn.execute(
                "SELECT 1 FROM applications WHERE candidate_id = ? AND job_id = ?",
                (candidate_id, job_id),
            ).fetchone()
            if existing is not None:
                raise DomainError("You have already applied to this job", 409)
            cursor = conn.execute(
                """
                INSERT INTO applications(candidate_id, job_id, status, created_at)
                VALUES (?, ?, 'Pending', ?)
                """,
                (candidate_id, job_id, iso_utc(now)),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise DomainError("You have already applied to this job", 409) from exc


def create_slot(conn, employer_id: int, job_id: int, start_at: datetime, end_at: datetime, *, now=None) -> int:
    with transaction(conn, immediate=True):
        job = conn.execute("SELECT id, employer_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise DomainError("Job not found", 404)
        if job["employer_id"] != employer_id:
            raise DomainError("Forbidden", 403)

        now_utc = (now or utc_now()).astimezone(timezone.utc)
        if start_at.tzinfo is None or end_at.tzinfo is None:
            raise DomainError("Timezone-aware times are required", 400)
        try:
            start_utc = start_at.astimezone(timezone.utc)
            end_utc = end_at.astimezone(timezone.utc)
        except OverflowError:
            raise DomainError("Invalid date/time", 400) from None
        if end_utc <= start_utc:
            raise DomainError("End time must be after start time", 400)
        if end_utc - start_utc != timedelta(minutes=30):
            raise DomainError("Interview slots must be exactly 30 minutes", 400)
        start_stored = start_utc.replace(microsecond=0)
        end_stored = end_utc.replace(microsecond=0)
        if end_stored - start_stored != timedelta(minutes=30):
            raise DomainError("Interview slots must be exactly 30 minutes", 400)
        if start_stored <= now_utc:
            raise DomainError("Interview slots must be in the future", 400)

        cursor = conn.execute(
            """
            INSERT INTO interview_slots(job_id, employer_id, start_at, end_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, employer_id, iso_utc(start_stored), iso_utc(end_stored)),
        )
        return int(cursor.lastrowid)


def delete_slot(conn, employer_id: int, slot_id: int) -> None:
    with transaction(conn, immediate=True):
        slot = conn.execute(
            "SELECT id, employer_id FROM interview_slots WHERE id = ?",
            (slot_id,),
        ).fetchone()
        if slot is None:
            raise DomainError("Interview slot not found", 404)
        if slot["employer_id"] != employer_id:
            raise DomainError("Forbidden", 403)
        booked = conn.execute("SELECT 1 FROM bookings WHERE slot_id = ?", (slot_id,)).fetchone()
        if booked:
            raise DomainError("Booked slots cannot be deleted", 409)
        conn.execute("DELETE FROM interview_slots WHERE id = ?", (slot_id,))


def delete_job(conn, employer_id: int, job_id: int) -> None:
    with transaction(conn, immediate=True):
        job = conn.execute("SELECT id, employer_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise DomainError("Job not found", 404)
        if job["employer_id"] != employer_id:
            raise DomainError("Forbidden", 403)

        has_app = conn.execute("SELECT 1 FROM applications WHERE job_id = ? LIMIT 1", (job_id,)).fetchone()
        has_slot = conn.execute("SELECT 1 FROM interview_slots WHERE job_id = ? LIMIT 1", (job_id,)).fetchone()
        if has_app or has_slot:
            raise DomainError("Job has applications or interview slots", 409)

        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def update_application_status(conn, employer_id: int, application_id: int, status: str) -> int:
    with transaction(conn, immediate=True):
        row = conn.execute(
            """
            SELECT a.id, a.job_id, j.employer_id
            FROM applications AS a
            JOIN jobs AS j ON j.id = a.job_id
            WHERE a.id = ?
            """,
            (application_id,),
        ).fetchone()
        if row is None:
            raise DomainError("Application not found", 404)
        if row["employer_id"] != employer_id:
            raise DomainError("Forbidden", 403)
        if status not in VALID_STATUSES:
            raise DomainError("Invalid application status", 400)
        conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, application_id))
        return int(row["job_id"])


def book_slot(conn, candidate_id: int, application_id: int, slot_id: int, *, now=None) -> int:
    now = (now or utc_now()).astimezone(timezone.utc)
    try:
        with transaction(conn, immediate=True):
            application = conn.execute(
                """
                SELECT a.id, a.candidate_id, a.job_id, a.status, j.employer_id
                FROM applications AS a
                JOIN jobs AS j ON j.id = a.job_id
                WHERE a.id = ?
                """,
                (application_id,),
            ).fetchone()
            if application is None:
                raise DomainError("Application not found", 404)
            if application["candidate_id"] != candidate_id:
                raise DomainError("Forbidden", 403)

            slot = conn.execute(
                """
                SELECT id, job_id, employer_id, start_at, end_at
                FROM interview_slots
                WHERE id = ?
                """,
                (slot_id,),
            ).fetchone()
            if slot is None:
                raise DomainError("Interview slot not found", 404)
            if slot["employer_id"] != application["employer_id"]:
                raise DomainError("Slot does not belong to this application", 403)
            if application["status"] != "Interviewing":
                raise DomainError("Application must be Interviewing", 409)
            if datetime.fromisoformat(slot["start_at"]).astimezone(timezone.utc) <= now:
                raise DomainError("Interview slot is no longer in the future", 409)

            existing_application = conn.execute(
                "SELECT 1 FROM bookings WHERE application_id = ?",
                (application_id,),
            ).fetchone()
            if existing_application:
                raise DomainError("Application already has interview booking", 409)
            existing_slot = conn.execute(
                "SELECT 1 FROM bookings WHERE slot_id = ?",
                (slot_id,),
            ).fetchone()
            if existing_slot:
                raise DomainError("Slot already booked", 409)

            cursor = conn.execute(
                "INSERT INTO bookings(application_id, slot_id, booked_at) VALUES (?, ?, ?)",
                (application_id, slot_id, iso_utc(now)),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        if "application_id" in str(exc):
            raise DomainError("Application already has interview booking", 409) from exc
        raise DomainError("Slot already booked", 409) from exc


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_jobs(conn, query: str):
    escaped = _escape_like(query)
    pattern = f"%{escaped}%"
    return conn.execute(
        """
        SELECT j.id, j.title, j.description, j.required_skills_json, c.company_name
        FROM jobs AS j
        JOIN company_profiles AS c ON c.user_id = j.employer_id
        WHERE j.title LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR j.description LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR c.company_name LIKE ? ESCAPE '\\' COLLATE NOCASE
        ORDER BY j.title COLLATE NOCASE, j.id
        """,
        (pattern, pattern, pattern),
    ).fetchall()
