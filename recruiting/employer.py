from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .ai_advisor import generate_employer_insight
from .auth import role_required
from .db import get_db, transaction
from .matching import rank_applicants, skills_from_json, skills_to_json
from .services import (
    DomainError,
    VALID_STATUSES,
    create_slot,
    delete_job,
    delete_slot,
    iso_utc,
    parse_local_datetime,
    update_application_status,
    utc_now,
)

bp = Blueprint("employer", __name__, url_prefix="/employer")


def _owned_job_or_error(job_id: int):
    job = get_db().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        abort(404, description="Job not found")
    if job["employer_id"] != g.user["id"]:
        abort(403, description="Forbidden")
    return job


def _applicant_summary(applicants: list[dict]) -> dict:
    counts = {status: 0 for status in VALID_STATUSES}
    for item in applicants:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    avg_score = round(sum(item["match_score"] for item in applicants) / len(applicants)) if applicants else 0
    return {
        "total": len(applicants),
        "counts": counts,
        "avg_score": avg_score,
    }


@bp.get("/")
@role_required("Employer")
def dashboard():
    db = get_db()
    company = db.execute(
        "SELECT company_name, description FROM company_profiles WHERE user_id = ?",
        (g.user["id"],),
    ).fetchone()
    job_rows = db.execute(
        """
        SELECT j.*,
               (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS application_count,
               (SELECT COUNT(*) FROM interview_slots s WHERE s.job_id = j.id) AS slot_count
        FROM jobs j
        WHERE j.employer_id = ?
        ORDER BY j.title COLLATE NOCASE, j.id
        """,
        (g.user["id"],),
    ).fetchall()
    jobs = []
    for row in job_rows:
        item = dict(row)
        item["required_skills"] = skills_from_json(item["required_skills_json"])
        jobs.append(item)
    slots = db.execute(
        """
        SELECT s.*, j.title,
               b.application_id,
               p.display_name AS booked_candidate_name,
               aj.title AS booked_job_title
        FROM interview_slots s
        JOIN jobs j ON j.id = s.job_id
        LEFT JOIN bookings b ON b.slot_id = s.id
        LEFT JOIN applications a ON a.id = b.application_id
        LEFT JOIN candidate_profiles p ON p.user_id = a.candidate_id
        LEFT JOIN jobs aj ON aj.id = a.job_id
        WHERE s.employer_id = ?
        ORDER BY s.start_at, s.id
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("employer/dashboard.html", company=company, jobs=jobs, slots=slots)


@bp.route("/company", methods=("GET", "POST"))
@role_required("Employer")
def company():
    db = get_db()
    if request.method == "POST":
        company_name = request.form.get("company_name", "").strip()
        description = request.form.get("description", "")
        if not company_name:
            abort(400, description="Company name is required")
        with transaction(db):
            db.execute(
                "UPDATE company_profiles SET company_name = ?, description = ? WHERE user_id = ?",
                (company_name, description, g.user["id"]),
            )
        flash("Company profile updated.", "success")
        return redirect(url_for("employer.company"))

    row = db.execute(
        "SELECT company_name, description FROM company_profiles WHERE user_id = ?",
        (g.user["id"],),
    ).fetchone()
    return render_template("employer/company.html", company=row)


@bp.route("/jobs/new", methods=("GET", "POST"))
@role_required("Employer")
def job_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title or not description:
            abort(400, description="Job title and description are required")
        db = get_db()
        with transaction(db):
            db.execute(
                """
                INSERT INTO jobs(employer_id, title, description, required_skills_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    g.user["id"],
                    title,
                    description,
                    skills_to_json(request.form.get("required_skills", "")),
                    iso_utc(utc_now()),
                ),
            )
        flash("Job created.", "success")
        return redirect(url_for("employer.dashboard"))
    return render_template("employer/job_form.html", job=None, skills="")


@bp.route("/jobs/<int:job_id>/edit", methods=("GET", "POST"))
@role_required("Employer")
def job_edit(job_id: int):
    job = _owned_job_or_error(job_id)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title or not description:
            abort(400, description="Job title and description are required")
        with transaction(get_db()):
            get_db().execute(
                """
                UPDATE jobs
                SET title = ?, description = ?, required_skills_json = ?
                WHERE id = ?
                """,
                (title, description, skills_to_json(request.form.get("required_skills", "")), job_id),
            )
        flash("Job updated.", "success")
        return redirect(url_for("employer.dashboard"))

    return render_template(
        "employer/job_form.html",
        job=job,
        skills=", ".join(skills_from_json(job["required_skills_json"])),
    )


@bp.post("/jobs/<int:job_id>/delete")
@role_required("Employer")
def job_delete(job_id: int):
    try:
        delete_job(get_db(), g.user["id"], job_id)
    except DomainError as exc:
        abort(exc.status_code, description=exc.message)
    flash("Job deleted.", "success")
    return redirect(url_for("employer.dashboard"))


@bp.get("/jobs/<int:job_id>/applicants")
@role_required("Employer")
def applicants(job_id: int):
    job = _owned_job_or_error(job_id)
    ranked = rank_applicants(get_db(), job_id)
    return render_template(
        "employer/applicants.html",
        job=job,
        required_skills=skills_from_json(job["required_skills_json"]),
        applicants=ranked,
        summary=_applicant_summary(ranked),
        statuses=VALID_STATUSES,
        ai_insight=None,
        ai_application_id=None,
    )


@bp.post("/applications/<int:application_id>/ai-insight")
@role_required("Employer")
def application_ai_insight(application_id: int):
    db = get_db()
    row = db.execute(
        """
        SELECT a.id, a.job_id, a.status,
               j.employer_id, j.title, j.description, j.required_skills_json,
               p.display_name, p.skills_json
        FROM applications AS a
        JOIN jobs AS j ON j.id = a.job_id
        JOIN candidate_profiles AS p ON p.user_id = a.candidate_id
        WHERE a.id = ?
        """,
        (application_id,),
    ).fetchone()
    if row is None:
        abort(404, description="Application not found")
    if row["employer_id"] != g.user["id"]:
        abort(403, description="Forbidden")

    job = _owned_job_or_error(row["job_id"])
    ranked = rank_applicants(db, row["job_id"])
    insight = generate_employer_insight(
        config=current_app.config,
        job_title=row["title"],
        job_description=row["description"],
        required_skills=skills_from_json(row["required_skills_json"]),
        candidate_display_name=row["display_name"],
        candidate_skills=skills_from_json(row["skills_json"]),
        application_status=row["status"],
    )
    return render_template(
        "employer/applicants.html",
        job=job,
        required_skills=skills_from_json(job["required_skills_json"]),
        applicants=ranked,
        summary=_applicant_summary(ranked),
        statuses=VALID_STATUSES,
        ai_insight=insight,
        ai_application_id=application_id,
    )


@bp.post("/applications/<int:application_id>/status")
@role_required("Employer")
def application_status(application_id: int):
    try:
        job_id = update_application_status(
            get_db(),
            g.user["id"],
            application_id,
            request.form.get("status", ""),
        )
    except DomainError as exc:
        abort(exc.status_code, description=exc.message)
    flash("Application status updated.", "success")
    return redirect(url_for("employer.applicants", job_id=job_id))


@bp.post("/slots")
@role_required("Employer")
def slot_create():
    try:
        job_id = int(request.form.get("job_id", "0"))
        _owned_job_or_error(job_id)
        create_slot(
            get_db(),
            g.user["id"],
            job_id,
            parse_local_datetime(request.form.get("start_at", "")),
            parse_local_datetime(request.form.get("end_at", "")),
        )
    except (ValueError, DomainError) as exc:
        if isinstance(exc, DomainError):
            abort(exc.status_code, description=exc.message)
        abort(400, description="Invalid job ID")
    flash("Interview slot created.", "success")
    return redirect(url_for("employer.dashboard"))


@bp.post("/slots/<int:slot_id>/delete")
@role_required("Employer")
def slot_delete(slot_id: int):
    try:
        delete_slot(get_db(), g.user["id"], slot_id)
    except DomainError as exc:
        abort(exc.status_code, description=exc.message)
    flash("Interview slot deleted.", "success")
    return redirect(url_for("employer.dashboard"))
