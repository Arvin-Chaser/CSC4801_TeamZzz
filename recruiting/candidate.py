from __future__ import annotations

from datetime import timezone

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .ai_advisor import generate_candidate_insight
from .auth import role_required
from .db import get_db, transaction
from .matching import explain_skill_match, rank_jobs, skills_from_json, skills_to_json
from .services import DomainError, apply_to_job, book_slot, iso_utc, search_jobs, utc_now

bp = Blueprint("candidate", __name__, url_prefix="/candidate")


def _candidate_skills(db, user_id: int) -> list[str]:
    row = db.execute(
        "SELECT skills_json FROM candidate_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return skills_from_json(row["skills_json"] if row else "[]")


def _load_job_detail_context(db, candidate_id: int, job_id: int):
    job = db.execute(
        """
        SELECT j.*, c.company_name, c.description AS company_description
        FROM jobs AS j
        JOIN company_profiles AS c ON c.user_id = j.employer_id
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()
    if job is None:
        abort(404, description="Job not found")
    applied = db.execute(
        "SELECT id, status FROM applications WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, job_id),
    ).fetchone()
    required_skills = skills_from_json(job["required_skills_json"])
    candidate_skills = _candidate_skills(db, candidate_id)
    breakdown = explain_skill_match(candidate_skills, required_skills)
    return job, required_skills, candidate_skills, breakdown, applied


@bp.get("/")
@role_required("Candidate")
def dashboard():
    db = get_db()
    jobs = rank_jobs(db, g.user["id"])
    candidate_skills = _candidate_skills(db, g.user["id"])
    applied_rows = db.execute(
        "SELECT job_id, id, status FROM applications WHERE candidate_id = ?",
        (g.user["id"],),
    ).fetchall()
    applied_map = {row["job_id"]: dict(row) for row in applied_rows}
    return render_template(
        "candidate/dashboard.html",
        jobs=jobs,
        candidate_skills=candidate_skills,
        applied_map=applied_map,
    )


@bp.get("/search")
@role_required("Candidate")
def search():
    db = get_db()
    query = request.args.get("q", "")
    candidate_skills = _candidate_skills(db, g.user["id"])
    rows = []
    if query:
        for row in search_jobs(db, query):
            item = dict(row)
            item["required_skills"] = skills_from_json(item.pop("required_skills_json"))
            breakdown = explain_skill_match(candidate_skills, item["required_skills"])
            item["match_score"] = breakdown["score"]
            item["matched_skills"] = breakdown["matched_skills"]
            item["missing_skills"] = breakdown["missing_skills"]
            rows.append(item)
    return render_template("candidate/search.html", query=query, jobs=rows)


@bp.route("/profile", methods=("GET", "POST"))
@role_required("Candidate")
def profile():
    db = get_db()
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            abort(400, description="Display name is required")
        skills_json = skills_to_json(request.form.get("skills", ""))
        with transaction(db):
            db.execute(
                "UPDATE candidate_profiles SET display_name = ?, skills_json = ? WHERE user_id = ?",
                (display_name, skills_json, g.user["id"]),
            )
        flash("Profile updated.", "success")
        return redirect(url_for("candidate.profile"))

    row = db.execute(
        "SELECT display_name, skills_json FROM candidate_profiles WHERE user_id = ?",
        (g.user["id"],),
    ).fetchone()
    skill_list = skills_from_json(row["skills_json"])
    return render_template(
        "candidate/profile.html",
        profile=row,
        skills=", ".join(skill_list),
        skill_list=skill_list,
    )


@bp.route("/resume/<int:candidate_id>", methods=("GET", "POST"))
@role_required("Candidate")
def resume(candidate_id: int):
    if candidate_id != g.user["id"]:
        abort(403, description="Resume is private")
    db = get_db()
    profile = db.execute(
        "SELECT user_id, display_name, resume_text FROM candidate_profiles WHERE user_id = ?",
        (candidate_id,),
    ).fetchone()
    if profile is None:
        abort(404, description="Candidate profile not found")

    if request.method == "POST":
        resume_text = request.form.get("resume_text", "")
        with transaction(db):
            db.execute(
                "UPDATE candidate_profiles SET resume_text = ? WHERE user_id = ?",
                (resume_text, candidate_id),
            )
        flash("Resume updated.", "success")
        return redirect(url_for("candidate.resume", candidate_id=candidate_id))

    return render_template("candidate/resume.html", profile=profile)


@bp.get("/jobs/<int:job_id>")
@role_required("Candidate")
def job_detail(job_id: int):
    db = get_db()
    job, required_skills, _candidate_skills, breakdown, applied = _load_job_detail_context(
        db, g.user["id"], job_id
    )
    return render_template(
        "candidate/job_detail.html",
        job=job,
        required_skills=required_skills,
        breakdown=breakdown,
        applied=applied,
        ai_insight=None,
    )


@bp.post("/jobs/<int:job_id>/ai-insight")
@role_required("Candidate")
def job_ai_insight(job_id: int):
    db = get_db()
    job, required_skills, candidate_skills, breakdown, applied = _load_job_detail_context(
        db, g.user["id"], job_id
    )
    include_resume = request.form.get("include_resume", "").strip().lower() in {"1", "true", "on", "yes"}
    resume_text = ""
    if include_resume:
        row = db.execute(
            "SELECT resume_text FROM candidate_profiles WHERE user_id = ?",
            (g.user["id"],),
        ).fetchone()
        resume_text = row["resume_text"] if row else ""

    ai_insight = generate_candidate_insight(
        config=current_app.config,
        candidate_skills=candidate_skills,
        job_title=job["title"],
        company_name=job["company_name"],
        job_description=job["description"],
        required_skills=required_skills,
        include_resume=include_resume,
        resume_text=resume_text,
    )
    return render_template(
        "candidate/job_detail.html",
        job=job,
        required_skills=required_skills,
        breakdown=breakdown,
        applied=applied,
        ai_insight=ai_insight,
    )


@bp.post("/jobs/<int:job_id>/apply")
@role_required("Candidate")
def apply(job_id: int):
    try:
        apply_to_job(get_db(), g.user["id"], job_id)
    except DomainError as exc:
        abort(exc.status_code, description=exc.message)
    flash("Application submitted with Pending status.", "success")
    return redirect(url_for("candidate.applications"))


@bp.get("/applications")
@role_required("Candidate")
def applications():
    rows = get_db().execute(
        """
        SELECT a.id, a.status, j.id AS job_id, j.title, c.company_name,
               s.start_at, s.end_at
        FROM applications AS a
        JOIN jobs AS j ON j.id = a.job_id
        JOIN company_profiles AS c ON c.user_id = j.employer_id
        LEFT JOIN bookings AS b ON b.application_id = a.id
        LEFT JOIN interview_slots AS s ON s.id = b.slot_id
        WHERE a.candidate_id = ?
        ORDER BY a.id
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("candidate/applications.html", applications=rows)


@bp.get("/applications/<int:application_id>")
@role_required("Candidate")
def application_detail(application_id: int):
    db = get_db()
    application = db.execute(
        """
        SELECT a.id, a.candidate_id, a.status, a.job_id,
               j.title, j.description, j.employer_id, c.company_name,
               s.start_at AS booked_start, s.end_at AS booked_end
        FROM applications AS a
        JOIN jobs AS j ON j.id = a.job_id
        JOIN company_profiles AS c ON c.user_id = j.employer_id
        LEFT JOIN bookings AS b ON b.application_id = a.id
        LEFT JOIN interview_slots AS s ON s.id = b.slot_id
        WHERE a.id = ?
        """,
        (application_id,),
    ).fetchone()
    if application is None:
        abort(404, description="Application not found")
    if application["candidate_id"] != g.user["id"]:
        abort(403, description="Forbidden")

    available = []
    if application["status"] == "Interviewing" and application["booked_start"] is None:
        now_text = iso_utc(utc_now().astimezone(timezone.utc))
        available = db.execute(
            """
            SELECT s.id, s.start_at, s.end_at
            FROM interview_slots AS s
            LEFT JOIN bookings AS b ON b.slot_id = s.id
            WHERE s.employer_id = ?
              AND s.start_at > ?
              AND b.id IS NULL
            ORDER BY s.start_at, s.id
            """,
            (application["employer_id"], now_text),
        ).fetchall()

    return render_template(
        "candidate/application_detail.html",
        application=application,
        available_slots=available,
    )


@bp.post("/applications/<int:application_id>/book/<int:slot_id>")
@role_required("Candidate")
def book(application_id: int, slot_id: int):
    try:
        book_slot(get_db(), g.user["id"], application_id, slot_id)
    except DomainError as exc:
        abort(exc.status_code, description=exc.message)
    flash("Interview slot booked.", "success")
    return redirect(url_for("candidate.application_detail", application_id=application_id))
