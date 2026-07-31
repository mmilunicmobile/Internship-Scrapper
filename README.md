# Internship Scraper

This script scrapes internship listings, filters them using the rules in
[`terms.py`](terms.py), emails new matches, and records processed jobs in
`seen_jobs.txt`.

GitHub Actions is not used. The scraper is intended to run on a private VPS
with cron. Each run commits changes to `seen_jobs.txt` locally. A separate,
less frequent cron job can push those commits back to the repository.

## VPS setup

Clone the repository and install the locked dependencies:

```bash
git clone <repository-url> /opt/internship-scraper
cd /opt/internship-scraper
uv sync --locked
```

Create the environment file:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` with the Gmail account and app password used for notifications.
`FROM_EMAIL` and `TO_EMAIL` are optional; they default to
`MY_EMAIL_ADDRESS` when omitted. The Gmail account must have an app password
enabled.

Configure Git identity on the VPS so the scraper can create local commits:

```bash
git config user.name "Internship Scraper"
git config user.email "scraper@example.com"
```

The VPS also needs push authentication for the repository. SSH deploy keys are
recommended. For an HTTPS remote, configure a Git credential helper or another
non-interactive authentication method; do not put Git credentials in `.env`.

## Running manually

Run from any directory—the script resolves its state and configuration files
relative to `main.py`:

```bash
cd /opt/internship-scraper
uv run main.py
```

The run commits only `seen_jobs.txt`. It does not push automatically.

## Cron setup

Edit the crontab for the user that owns the checkout:

```bash
crontab -e
```

Example configuration, assuming the checkout is `/opt/internship-scraper` and
the branch is `self-hosted`:

```cron
# Scrape twice daily. The VPS timezone determines when these run.
30 8,19 * * * cd /opt/internship-scraper && flock -n /tmp/internship-scraper.lock uv run main.py >> /var/log/internship-scraper.log 2>&1

# Push locally-created seen_jobs.txt commits every six hours.
0 */6 * * * cd /opt/internship-scraper && git push origin self-hosted >> /var/log/internship-scraper-push.log 2>&1
```

Replace `/opt/internship-scraper` and `self-hosted` with the actual checkout
path and branch. If the VPS uses UTC, adjust the scraper times accordingly.

The push job assumes the remote is named `origin` and that Git authentication
works without prompting. Test it manually before enabling cron:

```bash
cd /opt/internship-scraper
git push origin self-hosted
```

Make sure the cron user can write the log locations, or change the log paths to
a directory it owns, such as `/opt/internship-scraper/logs/`.

## Configuration reference

See [`.env.example`](.env.example) for the supported variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `MY_EMAIL_ADDRESS` | Yes | Gmail login address and default sender/recipient |
| `MY_EMAIL_APP_PASSWORD` | Yes | Gmail app password |
| `FROM_EMAIL` | No | Email sender override |
| `TO_EMAIL` | No | Email recipient override |

