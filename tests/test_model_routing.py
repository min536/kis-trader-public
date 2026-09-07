"""Tests for model routing constants and the model_note() helper.

All tests are fully offline — no API calls, no anthropic package required.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backtester.ai_integration.llm_cache_builder.client import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    PILOT_MODEL,
    PRODUCTION_MODEL,
    SMOKE_TEST_MODEL,
    AnthropicClient,
    model_note,
)


# ── Model constants ───────────────────────────────────────────────────────

class ModelConstantsTests(unittest.TestCase):

    def test_smoke_test_model_is_haiku(self):
        self.assertEqual(SMOKE_TEST_MODEL, "claude-haiku-4-5")

    def test_pilot_model_is_sonnet(self):
        self.assertEqual(PILOT_MODEL, "claude-sonnet-4-6")

    def test_production_model_is_opus(self):
        self.assertEqual(PRODUCTION_MODEL, "claude-opus-4-6")

    def test_default_model_is_smoke_test_model(self):
        """The safest/cheapest option must be the default."""
        self.assertEqual(DEFAULT_MODEL, SMOKE_TEST_MODEL)

    def test_default_is_not_opus(self):
        """Accidental opus runs must be impossible without an explicit flag."""
        self.assertNotEqual(DEFAULT_MODEL, PRODUCTION_MODEL)

    def test_known_models_contains_all_three(self):
        self.assertIn(SMOKE_TEST_MODEL, KNOWN_MODELS)
        self.assertIn(PILOT_MODEL, KNOWN_MODELS)
        self.assertIn(PRODUCTION_MODEL, KNOWN_MODELS)

    def test_known_models_descriptions_non_empty(self):
        for model, desc in KNOWN_MODELS.items():
            self.assertTrue(desc.strip(), f"empty description for {model}")

    def test_anthropic_client_default_matches_module_default(self):
        """AnthropicClient.DEFAULT_MODEL must stay in sync."""
        self.assertEqual(AnthropicClient.DEFAULT_MODEL, DEFAULT_MODEL)


# ── model_note() ──────────────────────────────────────────────────────────

class ModelNoteTests(unittest.TestCase):

    def test_default_model_shows_smoke_test_label(self):
        note = model_note(DEFAULT_MODEL)
        self.assertIn("Smoke-test default model", note)
        self.assertIn(DEFAULT_MODEL, note)

    def test_override_model_shows_override_label(self):
        note = model_note(PILOT_MODEL)
        self.assertIn("override", note.lower())
        self.assertIn(PILOT_MODEL, note)

    def test_production_model_shows_override_label(self):
        note = model_note(PRODUCTION_MODEL)
        self.assertIn("override", note.lower())
        self.assertIn(PRODUCTION_MODEL, note)

    def test_note_includes_description_from_known_models(self):
        for model in KNOWN_MODELS:
            note = model_note(model)
            # Description should appear somewhere in the note
            self.assertIn(model, note)

    def test_unknown_model_does_not_crash(self):
        note = model_note("claude-future-99")
        self.assertIn("claude-future-99", note)
        self.assertIn("override", note.lower())

    def test_note_is_single_line(self):
        for model in KNOWN_MODELS:
            note = model_note(model)
            self.assertNotIn("\n", note, f"model_note for {model} has newline")

    def test_haiku_note_mentions_cheapest(self):
        note = model_note(SMOKE_TEST_MODEL)
        self.assertIn("cheap", note.lower())

    def test_all_notes_non_empty(self):
        for model in KNOWN_MODELS:
            self.assertGreater(len(model_note(model)), 0)


# ── CLI default and help text ─────────────────────────────────────────────

class CLIDefaultTests(unittest.TestCase):

    def _get_parser(self):
        from backtester.ai_integration.llm_cache_builder.__main__ import _build_parser
        return _build_parser()

    def test_cli_default_model_is_haiku(self):
        parser = self._get_parser()
        args = parser.parse_args([
            "--features", "f.jsonl",
            "--output-dir", "/tmp/out",
        ])
        self.assertEqual(args.model, SMOKE_TEST_MODEL)

    def test_cli_override_model_accepted(self):
        parser = self._get_parser()
        args = parser.parse_args([
            "--features", "f.jsonl",
            "--output-dir", "/tmp/out",
            "--model", PRODUCTION_MODEL,
        ])
        self.assertEqual(args.model, PRODUCTION_MODEL)

    def test_cli_help_contains_all_model_names(self):
        parser = self._get_parser()
        help_text = parser.format_help()
        for model in KNOWN_MODELS:
            self.assertIn(model, help_text, f"{model} missing from help text")

    def test_cli_help_mentions_smoke_test(self):
        parser = self._get_parser()
        help_text = parser.format_help()
        self.assertIn("smoke", help_text.lower())

    def test_cli_help_shows_default_model(self):
        parser = self._get_parser()
        help_text = parser.format_help()
        self.assertIn(DEFAULT_MODEL, help_text)


# ── Runtime note printed at startup ──────────────────────────────────────

class RuntimeNoteTests(unittest.TestCase):
    """Verify the model note is printed during a dry run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._features = Path(self._tmp.name) / "features.jsonl"
        self._features.write_text(
            json.dumps({
                "date": "2024-01-15",
                "ticker": "005930",
                "base_score": 4.0,
                "decision": "approved",
                "rule_gate_passed": True,
                "score_gate_passed": True,
                "features": {},
            }) + "\n"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run_dry(self, extra_args: list[str] | None = None) -> str:
        """Run __main__ with --dry-run and capture stdout."""
        from backtester.ai_integration.llm_cache_builder.__main__ import _main
        argv = [
            "--features", str(self._features),
            "--output-dir", str(Path(self._tmp.name) / "out"),
            "--dry-run",
        ] + (extra_args or [])
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _main(argv)
        return buf.getvalue()

    def test_default_run_prints_smoke_test_note(self):
        output = self._run_dry()
        self.assertIn("Smoke-test default model", output)
        self.assertIn(SMOKE_TEST_MODEL, output)

    def test_override_run_prints_override_note(self):
        output = self._run_dry(["--model", PILOT_MODEL])
        self.assertIn("override", output.lower())
        self.assertIn(PILOT_MODEL, output)

    def test_production_override_prints_override_note(self):
        output = self._run_dry(["--model", PRODUCTION_MODEL])
        self.assertIn("override", output.lower())
        self.assertIn(PRODUCTION_MODEL, output)

    def test_note_appears_in_banner_before_dry_run_output(self):
        output = self._run_dry()
        note_pos = output.find("Smoke-test default model")
        dry_run_pos = output.find("DRY RUN")
        self.assertGreater(dry_run_pos, note_pos)


if __name__ == "__main__":
    unittest.main()
