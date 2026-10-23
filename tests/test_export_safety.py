"""Exports cannot redirect writes through existing filesystem links."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord import files
from fluxrecord.core import Ledger


class ExportSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task("T-1", "Synthetic objective", "Synthetic acceptance", scope=["repo/x"])
        session = self.ledger.start("T-1", "worker")["session_id"]
        self.ledger.checkpoint(session, "Progress", "Evidence", "Review", 1)
        self.ledger.finish(session, "Verified")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_every_output_refuses_existing_and_dangling_symlinks(self):
        outputs = (
            "index.json",
            "tickets/T-1.md",
            "evidence/T-1/checkpoint-1.json",
            "evidence/T-1/finish.json",
        )
        for number, relative in enumerate(outputs):
            for dangling in (False, True):
                with self.subTest(relative=relative, dangling=dangling):
                    project = self.root / f"project-{number}-{dangling}"
                    destination = project / relative
                    destination.parent.mkdir(parents=True)
                    outside = self.root / f"outside-{number}-{dangling}"
                    if not dangling:
                        outside.write_text("preserve me")
                    destination.symlink_to(outside)
                    with self.assertRaises(ValueError):
                        files.export(self.ledger, project)
                    self.assertTrue(destination.is_symlink())
                    if dangling:
                        self.assertFalse(outside.exists())
                    else:
                        self.assertEqual(outside.read_text(), "preserve me")

    def test_scaffold_refuses_a_dangling_index_symlink(self):
        project = self.root / "project"
        project.mkdir()
        outside = self.root / "outside-index"
        (project / "index.json").symlink_to(outside)
        with self.assertRaises(ValueError):
            files.scaffold(project)
        self.assertFalse(outside.exists())

    def test_every_output_directory_refuses_symlinks(self):
        for number, relative in enumerate(("tickets", "evidence", "evidence/T-1")):
            for dangling in (False, True):
                with self.subTest(relative=relative, dangling=dangling):
                    project = self.root / f"project-{number}-{dangling}"
                    destination = project / relative
                    destination.parent.mkdir(parents=True)
                    outside = self.root / f"outside-{number}-{dangling}"
                    if not dangling:
                        outside.mkdir()
                    destination.symlink_to(outside, target_is_directory=True)
                    with self.assertRaises(ValueError):
                        files.export(self.ledger, project)
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(list(outside.iterdir()) if outside.exists() else [], [])

    def test_hardlinked_outputs_do_not_change_the_external_inode(self):
        for number, relative in enumerate(
            (
                "index.json",
                "tickets/T-1.md",
                "evidence/T-1/checkpoint-1.json",
                "evidence/T-1/finish.json",
            )
        ):
            with self.subTest(relative=relative):
                project = self.root / f"project-{number}"
                destination = project / relative
                destination.parent.mkdir(parents=True)
                outside = self.root / f"outside-{number}"
                outside.write_text("preserve me")
                os.link(outside, destination)
                files.export(self.ledger, project)
                self.assertEqual(outside.read_text(), "preserve me")
                self.assertNotEqual(destination.stat().st_ino, outside.stat().st_ino)

    def test_invalid_evidence_path_is_rejected_before_replacing_projection_files(self):
        project = self.root / "project"
        files.export(self.ledger, project)
        ticket = project / "tickets/T-1.md"
        index = project / "index.json"
        ticket.write_text("previous ticket")
        index.write_text("previous index")
        checkpoint = project / "evidence/T-1/checkpoint-1.json"
        checkpoint.rename(checkpoint.with_suffix(".saved"))
        outside = self.root / "outside"
        outside.write_text("preserve me")
        checkpoint.symlink_to(outside)
        with self.assertRaises(ValueError):
            files.export(self.ledger, project)
        self.assertEqual(ticket.read_text(), "previous ticket")
        self.assertEqual(index.read_text(), "previous index")
        self.assertEqual(outside.read_text(), "preserve me")

    def test_failed_replacement_preserves_old_file_and_removes_own_temporary_file(self):
        project = self.root / "project"
        files.export(self.ledger, project)
        ticket = project / "tickets/T-1.md"
        ticket.write_text("previous ticket")
        before = sorted(path.relative_to(project) for path in project.rglob("*"))
        with mock.patch("os.replace", side_effect=OSError("injected replacement failure")):
            with self.assertRaisesRegex(OSError, "injected replacement failure"):
                files.export(self.ledger, project)
        self.assertEqual(ticket.read_text(), "previous ticket")
        self.assertEqual(sorted(path.relative_to(project) for path in project.rglob("*")), before)

    def test_unsupported_safe_writes_fail_before_creating_the_project(self):
        project = self.root / "project"
        with mock.patch.object(files, "_SECURE_WRITES", False, create=True):
            with self.assertRaisesRegex(OSError, "directory-relative"):
                files.scaffold(project)
        self.assertFalse(project.exists())

    def test_failed_text_wrapper_closes_descriptor_and_removes_temporary_file(self):
        project = self.root / "project"
        files.export(self.ledger, project)
        opened = []

        def fail_wrapper(descriptor, *args, **kwargs):
            opened.append(descriptor)
            raise OSError("injected wrapper failure")

        with mock.patch("os.fdopen", side_effect=fail_wrapper):
            with self.assertRaisesRegex(OSError, "injected wrapper failure"):
                files.export(self.ledger, project)
        self.assertEqual(len(opened), 1)
        try:
            with self.assertRaises(OSError):
                os.fstat(opened[0])
        finally:
            try:
                os.close(opened[0])
            except OSError:
                pass
        self.assertEqual(list(project.rglob("*.tmp")), [])

    def test_a_leaf_swapped_for_a_symlink_during_replace_cannot_redirect_content(self):
        project = self.root / "project"
        files.export(self.ledger, project)
        outside = self.root / "outside"
        outside.write_text("preserve me")
        ticket = project / "tickets/T-1.md"
        replace = os.replace
        swapped = []

        def swap_leaf(source, destination, **kwargs):
            if destination == "T-1.md" and not swapped:
                ticket.rename(ticket.with_suffix(".saved"))
                ticket.symlink_to(outside)
                swapped.append(True)
            return replace(source, destination, **kwargs)

        with mock.patch("os.replace", side_effect=swap_leaf):
            files.export(self.ledger, project)
        self.assertEqual(swapped, [True])
        self.assertEqual(outside.read_text(), "preserve me")
        self.assertFalse(ticket.is_symlink())

    def test_a_directory_swapped_for_a_symlink_cannot_redirect_content(self):
        project = self.root / "project"
        files.export(self.ledger, project)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "T-1.md").write_text("preserve me")
        tickets = project / "tickets"
        saved = project / "saved-tickets"
        replace = os.replace
        swapped = []

        def swap_directory(source, destination, **kwargs):
            if destination == "T-1.md" and not swapped:
                tickets.rename(saved)
                tickets.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            return replace(source, destination, **kwargs)

        with mock.patch("os.replace", side_effect=swap_directory):
            files.export(self.ledger, project)
        self.assertEqual(swapped, [True])
        self.assertEqual((outside / "T-1.md").read_text(), "preserve me")
        self.assertTrue((saved / "T-1.md").read_text().startswith("---\n"))
        self.assertEqual(list(outside.iterdir()), [outside / "T-1.md"])

    def test_an_explicitly_selected_project_symlink_is_supported(self):
        target = self.root / "target"
        target.mkdir()
        project = self.root / "project"
        project.symlink_to(target, target_is_directory=True)
        files.export(self.ledger, project)
        self.assertTrue((target / "tickets/T-1.md").is_file())

    def test_repeated_exports_preserve_complete_utf8_files(self):
        self.ledger.add_task("T-2", "Résumé 日本語", "Valid UTF-8", scope=["repo/y"])
        project = self.root / "project"
        files.export(self.ledger, project)
        snapshot = {
            path.relative_to(project): path.read_bytes()
            for path in project.rglob("*")
            if path.is_file()
        }
        files.export(self.ledger, project)
        self.assertEqual(
            snapshot,
            {
                path.relative_to(project): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file()
            },
        )
        self.assertIn("Résumé 日本語", (project / "tickets/T-2.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
