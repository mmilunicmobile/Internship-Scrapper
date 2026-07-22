import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from airtable_scraper import AirtableScraper
import yaml


PROJECT_DIR = Path(__file__).resolve().parent
TERMS_FILE = PROJECT_DIR / "terms.yaml"


def load_seen_jobs():
    """Load the set of already-seen job IDs from disk."""
    SEEN_JOBS_FILE = "seen_jobs.txt"
    if not os.path.exists(SEEN_JOBS_FILE):
        open(SEEN_JOBS_FILE, 'w').close()
    with open(SEEN_JOBS_FILE, 'r') as f:
        return set(line.strip() for line in f)


def scrape_internship():
    AIRTABLE_URL = "https://airtable.com/app17F0kkWQZhC6HB/shrOTtndhc6HSgnYb/tblp8wxvfYam5sD04?"
    MAX_ROWS_TO_SCRAPE = 200

    table = AirtableScraper(url=AIRTABLE_URL)
    if table.status != "success":
        raise RuntimeError(f"Failed to scrape Airtable (status: {table.status})")

    records = table.to_dict(orient="records") or []
    internships = []
    for record in records[:MAX_ROWS_TO_SCRAPE]:
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
    """Denylist-first split into matches / needs-review / dropped.

    A job is denied (dropped entirely) if its hire_time or title mentions
    any non-2027, non-Summer signal: "2026", a non-summer season (Fall,
    Spring, Winter), or a month outside April-July. Denial checks title
    too, since a job can't be Summer 2027 if its own title says "Fall".

    Anything not denied is a match if it mentions "2027", or mentions
    Summer / an April-July month (either signal alone is enough, since
    the denylist already ruled out wrong years and wrong seasons/months);
    otherwise it's ambiguous (blank hire_time, no signal at all) and goes
    to needs-review instead of being silently dropped.
    """
    with TERMS_FILE.open() as terms_file:
        terms = yaml.safe_load(terms_file) or {}

    deny_terms = terms.get("deny_terms", [])
    good_terms = terms.get("good_terms", [])

    if not isinstance(deny_terms, list) or not isinstance(good_terms, list):
        raise ValueError("terms.yaml must define deny_terms and good_terms as lists")

    matches = []
    needs_review = []

    for job in internships:
        combined = f"{job['hire_time']} {job['title']}".lower()

        if any(term.lower() in combined for term in deny_terms):
            continue

        if any(term.lower() in combined for term in good_terms):
            matches.append(job)
        else:
            needs_review.append(job)

    return matches, needs_review


def get_new_jobs(jobs, seen_jobs):
    SEEN_JOBS_FILE = "seen_jobs.txt"
    new_jobs = []
    for job in jobs:
        job_id = f"{job.get('title')}-{job.get('company')}"
        if job_id not in seen_jobs:
            new_jobs.append(job)
    with open(SEEN_JOBS_FILE, 'a') as f:
        for job in new_jobs:
            job_id = f"{job.get('title')}-{job.get('company')}"
            f.write(job_id + "\n")
    return new_jobs


def _render_job_html(job):
    html = f"<p><b>Title:</b> {job['title']}<br>"
    html += f"<b>Company:</b> {job['company']}<br>"
    html += f"<b>Location:</b> {job['location']}<br>"
    html += f"<b>Hire Time:</b> {job['hire_time']}<br>"
    html += f"<b>Grad Time:</b> {job['grad_time']}<br>"
    html += f"<b>Salary:</b> {job['salary']}<br>"
    html += f'<a href="{job["apply_link"]}"><b>Apply Here</b></a></p><hr>'
    return html


def send_email(matches, unspecified_jobs):
    if not matches and not unspecified_jobs:
        print("No new jobs to notify about")
        return

    sender_email = os.environ.get("MY_EMAIL_ADDRESS")
    sender_password = os.environ.get("MY_EMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        print("Email credentials not set. Skipping email.")
        return

    subject = f"Found {len(matches)} new internships that match your description!"
    if unspecified_jobs:
        subject += f" ({len(unspecified_jobs)} more need review)"

    html_body = "<html><body>"
    html_body += "<h2>Here are the new internships that match your criteria:</h2>"
    for job in matches:
        html_body += _render_job_html(job)

    if unspecified_jobs:
        html_body += "<h2>Needs Review (no hire time / season info found)</h2>"
        for job in unspecified_jobs:
            html_body += _render_job_html(job)

    html_body += "</body></html>"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = sender_email

    msg.set_content("Please enable HTML to view this email.")
    msg.add_alternative(html_body, subtype='html')

    total = len(matches) + len(unspecified_jobs)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print(f"Successfully sent email with {total} new jobs!")
    except smtplib.SMTPException as e:
        print(f"Failed to send email: {e}")


# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    seen_jobs = load_seen_jobs()
    all_internships = scrape_internship()
    new_jobs = get_new_jobs(all_internships, seen_jobs)
    my_matches, unspecified_jobs = filter_for_matches(new_jobs)

    print(f"\nMatches ({len(my_matches)}):")
    for job in my_matches:
        print(f"  {job['title']!r} | hire_time={job['hire_time']!r}")

    print(f"\nNeeds review ({len(unspecified_jobs)}):")
    for job in unspecified_jobs:
        print(f"  {job['title']!r} | hire_time={job['hire_time']!r}")

    send_email(my_matches, unspecified_jobs)
    print("\nScript finished.")
