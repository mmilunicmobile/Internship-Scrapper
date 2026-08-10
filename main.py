import html
import json
import logging
import os
import re
import smtplib
import subprocess
from collections import defaultdict
from email.message import EmailMessage
from pathlib import Path

from airtable_scraper import AirtableScraper
from dotenv import load_dotenv

from codex_enrichment import enrich_jobs, job_id, pipeline_job, settings_from_environment


PROJECT_DIR = Path(__file__).resolve().parent
TERMS_FILE = PROJECT_DIR / "terms.py"
SEEN_JOBS_FILE = PROJECT_DIR / "seen_jobs.txt"
FOUND_JOBS_FILE = PROJECT_DIR / "found_jobs.jsonl"
CODEX_SCHEMA_FILE = PROJECT_DIR / "codex_job_schema.json"

# Cron does not necessarily run with the project directory as its working
# directory, so load configuration and state relative to this file.
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)


def load_seen_jobs():
    """Load the set of already-seen job IDs from disk."""
    if not SEEN_JOBS_FILE.exists():
        SEEN_JOBS_FILE.touch()
    with SEEN_JOBS_FILE.open(encoding="utf-8") as seen_jobs_file:
        return {line.strip() for line in seen_jobs_file if line.strip()}


def scrape_internship():
    airtable_url = "https://airtable.com/app17F0kkWQZhC6HB/shrOTtndhc6HSgnYb/tblp8wxvfYam5sD04?"
    max_rows_to_scrape = 200

    table = AirtableScraper(url=airtable_url)
    if table.status != "success":
        raise RuntimeError(f"Failed to scrape Airtable (status: {table.status})")

    records = table.to_dict(orient="records") or []
    internships = []
    for record in records[:max_rows_to_scrape]:
        values = list(record.values())

        def value_at(index):
            value = values[index] if index < len(values) else None
            return str(value).strip() if value is not None and str(value).strip() else "N/A"

        job_data = {
            "title": value_at(0),
            "date": value_at(1),
            "apply_link": value_at(2),
            "location": value_at(4),
            "company": value_at(5),
            "salary": value_at(6),
            "hire_time": value_at(7),
            "grad_time": value_at(8),
            "qualifications": value_at(11),
        }
        internships.append(job_data)
        print(f"Successfully scraped: {job_data['title']} | hire_time={job_data['hire_time']!r}")

    print(f"Scraped {len(internships)} jobs.")
    return internships


def filter_for_matches(internships):
    """Denylist-first split into matches / needs-review / dropped."""
    with TERMS_FILE.open(encoding="utf-8") as terms_file:
        contents = terms_file.read()

    matches = []
    needs_review = []

    for job in internships:
        seen_terms = defaultdict(bool)
        combined = f"{job['hire_time']} {job['title']}".lower()
        combined_clean = re.sub(r"[^a-z0-9]+", " ", combined).strip()
        for term in combined_clean.split():
            seen_terms[term] = True
            seen_terms["_" + term] = True

        exec(contents, {}, seen_terms)
        result = seen_terms.get("RESULT")

        if result == "MATCH":
            matches.append(job)
        elif result == "REVIEW":
            needs_review.append(job)
        elif result != "DENY":
            needs_review.append(job)

    return matches, needs_review


def get_new_jobs(jobs, seen_jobs):
    """Return unique new jobs without mutating seen-job state."""
    new_jobs = []
    new_ids = set()
    for job in jobs:
        current_job_id = job_id(job)
        if current_job_id not in seen_jobs and current_job_id not in new_ids:
            new_jobs.append(job)
            new_ids.add(current_job_id)
    return new_jobs


def append_found_jobs(records):
    """Append one JSONL audit record per successfully notified new job."""
    if not records:
        return
    existing_ids = set()
    if FOUND_JOBS_FILE.exists():
        with FOUND_JOBS_FILE.open(encoding="utf-8") as found_jobs_file:
            for line in found_jobs_file:
                try:
                    existing_record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(existing_record, dict) and existing_record.get("job_id"):
                    existing_ids.add(existing_record["job_id"])

    with FOUND_JOBS_FILE.open("a", encoding="utf-8") as found_jobs_file:
        for record in records:
            if record.get("job_id") in existing_ids:
                continue
            found_jobs_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing_ids.add(record.get("job_id"))


def mark_jobs_seen(jobs):
    """Mark jobs seen only after their notification has succeeded."""
    if not jobs:
        return
    with SEEN_JOBS_FILE.open("a", encoding="utf-8") as seen_jobs_file:
        for job in jobs:
            seen_jobs_file.write(job_id(job) + "\n")


def commit_state_files():
    """Commit the synchronized JSONL audit and seen-job state files."""
    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            SEEN_JOBS_FILE.name,
            FOUND_JOBS_FILE.name,
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        print("No changes to internship state files to commit.")
        return

    subprocess.run(
        ["git", "add", "--", SEEN_JOBS_FILE.name, FOUND_JOBS_FILE.name],
        cwd=PROJECT_DIR,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Record enriched internships"],
        cwd=PROJECT_DIR,
        check=True,
    )
    print("Committed updated internship state files.")


def _escaped(value):
    return html.escape(str(value if value is not None else "N/A"), quote=True)


def _safe_apply_url(value):
    if not isinstance(value, str) or not re.match(r"^https?://[^\s]+$", value):
        return None
    return _escaped(value)


def _render_job_html(job):
    rendered = f"<p><b>Title:</b> {_escaped(job['title'])}<br>"
    rendered += f"<b>Company:</b> {_escaped(job['company'])}<br>"
    rendered += f"<b>Location:</b> {_escaped(job['location'])}<br>"
    rendered += f"<b>Hire Time:</b> {_escaped(job['hire_time'])}<br>"
    rendered += f"<b>Grad Time:</b> {_escaped(job['grad_time'])}<br>"
    rendered += f"<b>Salary:</b> {_escaped(job['salary'])}<br>"

    if job.get("company_description", "N/A") != "N/A":
        rendered += f"<b>Company Description:</b> {_escaped(job['company_description'])}<br>"
    if job.get("position_description", "N/A") != "N/A":
        rendered += f"<b>Position Description:</b> {_escaped(job['position_description'])}<br>"

    status = job.get("verification_status", "verified")
    if status != "verified":
        rendered += f"<b>Verification Status:</b> {_escaped(status)}<br>"
        rendered += f"<b>Verification Notes:</b> {_escaped(job.get('verification_notes', 'N/A'))}<br>"

    if job.get("salary_annualized") is True:
        rendered += "<b>Salary Note:</b> Source explicitly identifies this compensation as annualized.<br>"

    original_url = _safe_apply_url(job.get("sources", {}).get("original_apply_link", "N/A"))
    verified_url = _safe_apply_url(job.get("sources", {}).get("verified_source_url", "N/A"))
    primary_url = verified_url or original_url
    if primary_url:
        rendered += f'<a href="{primary_url}"><b>Apply Here</b></a>'
    else:
        rendered += "<b>Apply Here:</b> N/A"
    if verified_url and original_url and verified_url != original_url:
        rendered += f' · <a href="{original_url}">Original scraped link</a>'
    return rendered + "</p><hr>"


def send_email(matches, unspecified_jobs, new_job_count=None, scraped_job_count=None):
    if not matches and not unspecified_jobs:
        print("No new jobs to notify about")
        return True

    verified_matches = [
        job for job in matches if job.get("verification_status", "verified") == "verified"
    ]
    verification_review_jobs = [
        job for job in matches if job.get("verification_status", "verified") != "verified"
    ]
    review_jobs = verification_review_jobs + list(unspecified_jobs)

    sender_email = os.environ.get("MY_EMAIL_ADDRESS")
    sender_password = os.environ.get("MY_EMAIL_APP_PASSWORD")
    from_email = os.environ.get("FROM_EMAIL") or sender_email
    to_email = os.environ.get("TO_EMAIL") or sender_email

    if not sender_email or not sender_password:
        print("Email credentials not set. Skipping email.")
        return False

    subject = f"Found {len(verified_matches)} new verified internships that match your description!"
    if review_jobs:
        subject += f" ({len(review_jobs)} more need review)"

    email_summary = ""
    if new_job_count is not None:
        scraped_summary = (
            f" out of {scraped_job_count} scraped"
            if scraped_job_count is not None
            else ""
        )
        email_summary = f"New jobs found this scrape: {new_job_count}{scraped_summary}."

    html_body = "<html><body>"
    if email_summary:
        html_body += f"<p>{_escaped(email_summary)}</p>"
    if verified_matches:
        html_body += "<h2>Verified internships matching your criteria</h2>"
        for job in verified_matches:
            html_body += _render_job_html(job)

    if review_jobs:
        html_body += "<h2>Needs Review / Verification Warnings</h2>"
        for job in review_jobs:
            html_body += _render_job_html(job)
    html_body += "</body></html>"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(
        f"{email_summary}\n\nPlease enable HTML to view the full email."
        if email_summary
        else "Please enable HTML to view this email."
    )
    msg.add_alternative(html_body, subtype="html")

    total = len(verified_matches) + len(review_jobs)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print(f"Successfully sent email with {total} new jobs!")
        return True
    except (OSError, smtplib.SMTPException) as error:
        print(f"Failed to send email: {error}")
        return False


def main():
    seen_jobs = load_seen_jobs()
    all_internships = scrape_internship()
    new_jobs = get_new_jobs(all_internships, seen_jobs)
    print(f"Found {len(new_jobs)} new jobs out of {len(all_internships)} scraped.")

    configured_limit = os.environ.get("CODEX_MAX_NEW_JOBS", "0")
    try:
        max_new_jobs = max(0, int(configured_limit))
    except ValueError:
        raise RuntimeError(f"Invalid CODEX_MAX_NEW_JOBS={configured_limit!r}")
    if max_new_jobs and len(new_jobs) > max_new_jobs:
        raise RuntimeError(
            f"Found {len(new_jobs)} new jobs, exceeding CODEX_MAX_NEW_JOBS={max_new_jobs}; "
            "no jobs were transformed or marked seen."
        )

    if new_jobs:
        settings = settings_from_environment(CODEX_SCHEMA_FILE)
        print(
            f"Enriching {len(new_jobs)} jobs with Codex "
            f"(model={settings.model}, concurrency={settings.concurrency})."
        )
        enriched_records = enrich_jobs(new_jobs, settings)
        enriched_jobs = [pipeline_job(record) for record in enriched_records]
    else:
        enriched_records = []
        enriched_jobs = []

    my_matches, unspecified_jobs = filter_for_matches(enriched_jobs)
    print(f"Matches: {len(my_matches)}")
    print(f"Needs review: {len(unspecified_jobs)}")

    if os.environ.get("CODEX_DRY_RUN", "0").lower() in {"1", "true", "yes"}:
        print("CODEX_DRY_RUN is enabled; skipping email and state commits.")
        return 0

    if not send_email(
        my_matches,
        unspecified_jobs,
        new_job_count=len(new_jobs),
        scraped_job_count=len(all_internships),
    ):
        raise RuntimeError("Email delivery failed; leaving new jobs unmarked for retry.")

    append_found_jobs(enriched_records)
    mark_jobs_seen(new_jobs)
    commit_state_files()
    print("\nScript finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
