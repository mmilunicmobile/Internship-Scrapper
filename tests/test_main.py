import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class MainTests(unittest.TestCase):
    def test_get_new_jobs_does_not_mutate_seen_file(self):
        jobs = [
            {"title": "A", "company": "B"},
            {"title": "A", "company": "B"},
            {"title": "C", "company": "D"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            seen_file = Path(directory) / "seen_jobs.txt"
            seen_file.write_text("A-B\n", encoding="utf-8")
            with patch.object(main, "SEEN_JOBS_FILE", seen_file):
                new_jobs = main.get_new_jobs(jobs, main.load_seen_jobs())
            self.assertEqual([job["title"] for job in new_jobs], ["C"])
            self.assertEqual(seen_file.read_text(encoding="utf-8"), "A-B\n")

    def test_html_escapes_values_and_keeps_both_links(self):
        job = {
            "title": '<Firmware "Intern">',
            "company": "Example & Corp",
            "location": "Andover, MA",
            "hire_time": "Summer 2027",
            "grad_time": "N/A",
            "salary": "$25/hour",
            "company_description": "Builds <embedded> products.",
            "position_description": "Writes firmware.",
            "verification_status": "verified",
            "sources": {
                "original_apply_link": "https://jobright.ai/jobs/1?utm_source=test",
                "verified_source_url": "https://example.com/jobs/1",
            },
        }
        rendered = main._render_job_html(job)
        self.assertIn("&lt;Firmware &quot;Intern&quot;&gt;", rendered)
        self.assertIn("Example &amp; Corp", rendered)
        self.assertIn("https://jobright.ai/jobs/1?utm_source=test", rendered)
        self.assertIn("https://example.com/jobs/1", rendered)
        self.assertNotIn('<Firmware "Intern">', rendered)

    def test_found_jobs_append_is_idempotent_by_job_id(self):
        record = {"job_id": "A-B", "normalized": {"title": "A"}}
        with tempfile.TemporaryDirectory() as directory:
            found_file = Path(directory) / "found_jobs.jsonl"
            with patch.object(main, "FOUND_JOBS_FILE", found_file):
                main.append_found_jobs([record])
                main.append_found_jobs([record])
            self.assertEqual(len(found_file.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
