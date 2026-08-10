import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from codex_enrichment import (
    CodexSettings,
    enrich_one,
    pipeline_job,
    _run_codex,
)


def scraped_job():
    return {
        "title": "Firmware Engineer Intern",
        "company": "Example Corp",
        "location": "Andover, MA, United States",
        "date": "2026-08-01",
        "salary": "N/A",
        "hire_time": "N/A",
        "grad_time": "N/A",
        "qualifications": "Embedded C experience",
        "apply_link": "https://jobright.ai/jobs/info/123?utm_source=test",
    }


def valid_response():
    return {
        "verification_status": "verified",
        "verification_notes": "Found the current official employer posting.",
        "verified_source_url": "https://example.com/careers/firmware?utm_campaign=test",
        "source_type": "official_employer",
        "salary_annualized": "N/A",
        "normalized": {
            "title": "Firmware Engineer Intern",
            "company": "Example Corp",
            "location": "Andover, MA, United States",
            "date": "2026-08-01",
            "salary": "$25/hour",
            "hire_time": "Summer 2027",
            "grad_time": "N/A",
            "qualifications": "Embedded C experience",
            "company_description": "Example Corp builds embedded systems.",
            "position_description": "Build and test firmware for embedded products.",
        },
    }


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        response = self.responses.pop(0)
        output_path.write_text(response, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


class EnrichmentTests(unittest.TestCase):
    def settings(self, directory):
        schema = Path(__file__).parents[1] / "codex_job_schema.json"
        return CodexSettings(
            binary="codex",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            concurrency=1,
            timeout_seconds=5,
            retries=0,
            schema_path=schema,
        )

    def test_web_only_command_and_normalized_record(self):
        runner = FakeRunner([json.dumps(valid_response())])
        with tempfile.TemporaryDirectory() as directory:
            record = enrich_one(scraped_job(), self.settings(directory), runner=runner)

        command = runner.commands[0]
        self.assertIn("--search", command)
        self.assertIn("--disable", command)
        self.assertIn("shell_tool", command)
        self.assertIn("unified_exec", command)
        self.assertEqual(
            record["sources"]["original_apply_link"],
            "https://jobright.ai/jobs/info/123?utm_source=test",
        )
        self.assertEqual(
            record["sources"]["verified_source_url"],
            "https://example.com/careers/firmware",
        )
        self.assertIn("salary", record["changed_fields"])
        self.assertEqual(pipeline_job(record)["hire_time"], "Summer 2027")
        self.assertEqual(
            pipeline_job(record)["apply_link"], "https://example.com/careers/firmware"
        )

    def test_invalid_json_is_retried_once_then_fails_open(self):
        runner = FakeRunner(["not json", "still not json"])
        with tempfile.TemporaryDirectory() as directory:
            record = enrich_one(scraped_job(), self.settings(directory), runner=runner)

        self.assertEqual(len(runner.commands), 2)
        self.assertEqual(record["verification_status"], "lookup_failed")
        self.assertEqual(record["normalized"]["salary"], "N/A")
        self.assertIn("valid JSON", record["verification_notes"])

    def test_nonzero_process_fails_open_after_retry_budget(self):
        calls = []

        def failing_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, "", "network unavailable")

        with tempfile.TemporaryDirectory() as directory:
            record = enrich_one(scraped_job(), self.settings(directory), runner=failing_runner)

        self.assertEqual(len(calls), 1)
        self.assertEqual(record["verification_status"], "lookup_failed")
        self.assertIn("network unavailable", record["verification_notes"])


if __name__ == "__main__":
    unittest.main()
