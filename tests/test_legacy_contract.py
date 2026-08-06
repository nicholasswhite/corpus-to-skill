"""Characterization tests for the protected pre-corpus book-to-skill surface.

These tests intentionally exercise the public entry points in subprocesses. In
particular, ``BOOK_SKILL_WORKDIR`` is read when ``book_to_skill.config`` is
imported, so changing the environment after an in-process import would not
faithfully characterize the installed CLI.

The suite uses only ``unittest`` so it can run in the dependency-free smoke-test
environment. Pytest also discovers standard-library test cases in the full CI
suite.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import book_to_skill


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_TEXT = (
    "Table of Contents\n\n"
    "Chapter 1: Foundations\n"
    "Alpha beta gamma.\n\n"
    "Chapter 2: Practice\n"
    "Delta epsilon zeta.\n"
)

ENTRYPOINTS = {
    "legacy-shim": [sys.executable, str(REPO_ROOT / "scripts" / "extract.py")],
    "python-module": [sys.executable, "-m", "book_to_skill"],
}

TOP_LEVEL_METADATA_KEYS = {
    "source_file",
    "filename",
    "format",
    "extraction_method",
    "extraction_mode",
    "file_size_mb",
    "pages",
    "chars",
    "words",
    "estimated_tokens",
    "estimated_tokens_human",
    "output_text",
    "total_sources",
    "sources",
    "chapters_detected",
    "chapter_headings_sample",
    "has_toc",
}

SOURCE_METADATA_KEYS = {
    "source_file",
    "filename",
    "format",
    "extraction_method",
    "file_size_mb",
    "pages",
    "pages_label",
    "chars",
    "words",
    "estimated_tokens",
    "chapters_detected",
    "has_toc",
}

SINGLE_FILE_API_KEYS = {
    "source_file",
    "filename",
    "format",
    "extraction_method",
    "file_size_mb",
    "sections",
    "pages_label",
    "pages",
    "chars",
    "words",
    "estimated_tokens",
    "text",
    "chapters_detected",
    "chapter_headings_sample",
    "has_toc",
}


def _run_cli(
    entrypoint: list[str],
    output_dir: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "BOOK_SKILL_WORKDIR": str(output_dir),
            "BOOK_SKILL_INSTALL_MISSING": "no",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return subprocess.run(
        [*entrypoint, *arguments],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _read_artifacts(output_dir: Path) -> tuple[str, dict]:
    full_text = (output_dir / "full_text.txt").read_text(encoding="utf-8")
    metadata = json.loads(
        (output_dir / "metadata.json").read_text(encoding="utf-8")
    )
    return full_text, metadata


class LegacyCliContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary_directory = tempfile.TemporaryDirectory(
            prefix="book-to-skill-legacy-contract-"
        )
        cls.base = Path(cls._temporary_directory.name)
        cls.source = cls.base / "legacy-book.txt"
        # Write exact LF bytes so Windows text-mode newline expansion does not
        # become part of the fixture or get expanded a second time on output.
        cls.source.write_bytes(SOURCE_TEXT.encode("utf-8"))

        cls.runs = {}
        for name, entrypoint in ENTRYPOINTS.items():
            output_dir = cls.base / name
            result = _run_cli(
                entrypoint,
                output_dir,
                [
                    str(cls.source),
                    "--mode",
                    "text",
                    "--install-missing",
                    "no",
                ],
            )
            cls.runs[name] = {
                "output_dir": output_dir,
                "result": result,
                "artifacts": (
                    _read_artifacts(output_dir) if result.returncode == 0 else None
                ),
            }

    @classmethod
    def tearDownClass(cls):
        cls._temporary_directory.cleanup()

    def test_legacy_shim_and_python_module_are_artifact_equivalent(self):
        for name, run in self.runs.items():
            with self.subTest(entrypoint=name):
                result = run["result"]
                output_dir = run["output_dir"]
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Extraction complete:", result.stdout)
                self.assertIn(
                    f"Text -> {output_dir / 'full_text.txt'}", result.stdout
                )
                self.assertIn(
                    f"Meta -> {output_dir / 'metadata.json'}", result.stdout
                )
                self.assertIn("book-to-skill", result.stderr)
                self.assertEqual(
                    sorted(path.name for path in output_dir.iterdir()),
                    ["full_text.txt", "metadata.json"],
                )

        shim_text, shim_metadata = self.runs["legacy-shim"]["artifacts"]
        module_text, module_metadata = self.runs["python-module"]["artifacts"]
        self.assertEqual(shim_text, module_text)

        # Each run correctly reports its own work directory; all other metadata
        # must remain equivalent across the backward-compatible entry points.
        shim_metadata = {**shim_metadata, "output_text": "<WORKDIR>/full_text.txt"}
        module_metadata = {
            **module_metadata,
            "output_text": "<WORKDIR>/full_text.txt",
        }
        self.assertEqual(shim_metadata, module_metadata)

    def test_single_book_artifact_shape_and_relationships_are_stable(self):
        output_dir = self.runs["legacy-shim"]["output_dir"]
        full_text, metadata = self.runs["legacy-shim"]["artifacts"]

        boundary = "=" * 80
        expected_text = (
            f"{boundary}\n"
            f"SOURCE: {self.source.name} (Path: {self.source.resolve()})\n"
            f"{boundary}\n\n"
            f"{SOURCE_TEXT.strip()}"
        )
        self.assertEqual(full_text, expected_text)

        self.assertEqual(set(metadata), TOP_LEVEL_METADATA_KEYS)
        self.assertEqual(metadata["source_file"], str(self.source.resolve()))
        self.assertEqual(metadata["filename"], self.source.name)
        self.assertEqual(metadata["format"], "txt")
        self.assertEqual(metadata["extraction_method"], "plain-text")
        self.assertEqual(metadata["extraction_mode"], "text")
        self.assertEqual(
            metadata["output_text"], str(output_dir / "full_text.txt")
        )
        self.assertEqual(metadata["total_sources"], 1)
        self.assertEqual(metadata["pages"], 0)
        self.assertEqual(metadata["chars"], len(full_text))
        self.assertEqual(metadata["words"], len(full_text.split()))
        self.assertEqual(
            metadata["estimated_tokens_human"],
            f"~{metadata['estimated_tokens'] // 1000}K",
        )
        self.assertEqual(metadata["chapters_detected"], 2)
        self.assertEqual(
            metadata["chapter_headings_sample"],
            ["Chapter 1: Foundations", "Chapter 2: Practice"],
        )
        self.assertIs(metadata["has_toc"], True)

        self.assertIs(type(metadata["file_size_mb"]), float)
        for key in ("pages", "chars", "words", "estimated_tokens", "total_sources"):
            with self.subTest(top_level_type=key):
                self.assertIs(type(metadata[key]), int)
        self.assertIs(type(metadata["has_toc"]), bool)
        self.assertIs(type(metadata["chapter_headings_sample"]), list)
        self.assertIs(type(metadata["sources"]), list)

        self.assertEqual(len(metadata["sources"]), 1)
        source_metadata = metadata["sources"][0]
        self.assertEqual(set(source_metadata), SOURCE_METADATA_KEYS)
        self.assertEqual(source_metadata["source_file"], str(self.source.resolve()))
        self.assertEqual(source_metadata["filename"], self.source.name)
        self.assertEqual(source_metadata["format"], "txt")
        self.assertEqual(source_metadata["extraction_method"], "plain-text")
        self.assertEqual(source_metadata["pages"], 0)
        self.assertEqual(source_metadata["pages_label"], "sections")
        self.assertEqual(source_metadata["chars"], len(SOURCE_TEXT))
        self.assertEqual(source_metadata["words"], len(SOURCE_TEXT.split()))
        self.assertEqual(source_metadata["chapters_detected"], 2)
        self.assertIs(source_metadata["has_toc"], True)
        self.assertGreaterEqual(
            metadata["estimated_tokens"], source_metadata["estimated_tokens"]
        )

        self.assertIs(type(source_metadata["file_size_mb"]), float)
        for key in (
            "pages",
            "chars",
            "words",
            "estimated_tokens",
            "chapters_detected",
        ):
            with self.subTest(source_type=key):
                self.assertIs(type(source_metadata[key]), int)
        self.assertIs(type(source_metadata["has_toc"]), bool)

    def test_help_check_and_no_argument_exit_contract(self):
        for name, entrypoint in ENTRYPOINTS.items():
            with self.subTest(entrypoint=name, invocation="help"):
                help_output = self.base / f"{name}-help"
                help_result = _run_cli(entrypoint, help_output, ["--help"])
                self.assertEqual(help_result.returncode, 0)
                self.assertIn("Usage: book-to-skill", help_result.stderr)
                self.assertFalse(help_output.exists())

            with self.subTest(entrypoint=name, invocation="dependency-check"):
                check_output = self.base / f"{name}-check"
                check_result = _run_cli(entrypoint, check_output, ["--check"])
                self.assertEqual(check_result.returncode, 0)
                self.assertIn("book-to-skill — dependency check", check_result.stdout)
                self.assertFalse(check_output.exists())

            with self.subTest(entrypoint=name, invocation="no-arguments"):
                no_arg_output = self.base / f"{name}-no-args"
                no_arg_result = _run_cli(entrypoint, no_arg_output, [])
                self.assertEqual(no_arg_result.returncode, 1)
                self.assertIn("Usage: book-to-skill", no_arg_result.stderr)
                self.assertFalse(no_arg_output.exists())

    def test_all_sources_failing_exits_one_without_artifacts(self):
        output_dir = self.base / "all-failed-output"
        missing_source = self.base / "missing-book.txt"

        result = _run_cli(
            ENTRYPOINTS["python-module"],
            output_dir,
            [str(missing_source), "--install-missing", "no"],
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: All 1 source(s) failed extraction", result.stderr)
        self.assertTrue(output_dir.is_dir())
        self.assertFalse((output_dir / "full_text.txt").exists())
        self.assertFalse((output_dir / "metadata.json").exists())

    def test_unknown_flag_warns_but_preserves_successful_extraction(self):
        source = self.base / "unknown-flag.txt"
        source.write_bytes(b"Chapter 1\nStill extracted.\n")
        output_dir = self.base / "unknown-flag-output"

        result = _run_cli(
            ENTRYPOINTS["python-module"],
            output_dir,
            [str(source), "--definitely-unknown", "--install-missing", "no"],
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn(
            "WARNING: Unknown flag '--definitely-unknown'", result.stderr
        )
        self.assertTrue((output_dir / "full_text.txt").exists())
        self.assertTrue((output_dir / "metadata.json").exists())


class PublicApiContractTests(unittest.TestCase):
    def test_public_exports_signatures_and_single_file_result(self):
        self.assertEqual(
            book_to_skill.__all__,
            [
                "resolve_input_files",
                "extract_single_file",
                "main",
                "ExtractionError",
            ],
        )
        self.assertEqual(book_to_skill.main.__module__, "book_to_skill.utils")
        self.assertEqual(
            book_to_skill.extract_single_file.__module__, "book_to_skill.utils"
        )
        self.assertEqual(
            book_to_skill.resolve_input_files.__module__, "book_to_skill.utils"
        )
        self.assertTrue(issubclass(book_to_skill.ExtractionError, Exception))
        self.assertFalse(issubclass(book_to_skill.ExtractionError, SystemExit))

        self.assertEqual(
            list(inspect.signature(book_to_skill.resolve_input_files).parameters),
            ["paths"],
        )
        self.assertEqual(
            list(inspect.signature(book_to_skill.extract_single_file).parameters),
            ["input_path", "extraction_mode", "install_mode"],
        )
        self.assertEqual(
            list(inspect.signature(book_to_skill.main).parameters), []
        )
        for function in (
            book_to_skill.resolve_input_files,
            book_to_skill.extract_single_file,
            book_to_skill.main,
        ):
            with self.subTest(function=function.__name__):
                self.assertTrue(
                    all(
                        parameter.default is inspect.Parameter.empty
                        for parameter in inspect.signature(function).parameters.values()
                    )
                )

        with tempfile.TemporaryDirectory(
            prefix="book-to-skill-public-api-"
        ) as temporary_directory:
            source = Path(temporary_directory) / "api-book.txt"
            source.write_bytes(SOURCE_TEXT.encode("utf-8"))
            self.assertEqual(
                book_to_skill.resolve_input_files([str(source)]), [source.resolve()]
            )

            result = book_to_skill.extract_single_file(source, "text", "no")
            self.assertEqual(set(result), SINGLE_FILE_API_KEYS)
            self.assertEqual(result["source_file"], str(source.resolve()))
            self.assertEqual(result["filename"], source.name)
            self.assertEqual(result["format"], "txt")
            self.assertEqual(result["extraction_method"], "plain-text")
            self.assertEqual(result["pages_label"], "sections")
            self.assertEqual(result["sections"], 0)
            self.assertEqual(result["pages"], 0)
            self.assertEqual(result["text"], SOURCE_TEXT)
            self.assertEqual(result["chars"], len(SOURCE_TEXT))
            self.assertEqual(result["words"], len(SOURCE_TEXT.split()))
            self.assertEqual(result["chapters_detected"], 2)
            self.assertEqual(
                result["chapter_headings_sample"],
                ["Chapter 1: Foundations", "Chapter 2: Practice"],
            )
            self.assertIs(result["has_toc"], True)


if __name__ == "__main__":
    unittest.main()
