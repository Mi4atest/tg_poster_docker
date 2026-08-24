"""Тесты pull-модели обновления (флаги, lock, без docker.sock)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProjectUpdateServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backups = Path(self.tmp.name) / "backups"
        self.backups.mkdir()
        self.host = Path(self.tmp.name) / "repo"
        self.host.mkdir()
        (self.host / "scripts").mkdir()
        (self.host / "scripts" / "update.sh").write_text("#!/bin/bash\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_paths(self):
        return patch.multiple(
            "app.services.project_update_service",
            HOST_PROJECT=self.host,
            BACKUPS_DIR=self.backups,
            UPDATE_SCRIPT=self.host / "scripts" / "update.sh",
            UPDATE_LOG=self.backups / "last_update.log",
            UPDATE_META=self.backups / "last_update_meta.json",
            UPDATE_FLAG=self.backups / "update_requested.json",
            UPDATE_LOCK=self.backups / "update.lock",
            PRUNE_FLAG=self.backups / "prune_requested.json",
            PRUNE_META=self.backups / "last_prune_meta.json",
        )

    def test_start_project_update_writes_flag(self):
        with self._patch_paths():
            from app.services import project_update_service as svc

            ok, msg = asyncio.run(
                svc.start_project_update(force=True, requested_by=42)
            )
            self.assertTrue(ok)
            self.assertIn("принят", msg.lower())
            data = json.loads(self.backups.joinpath("update_requested.json").read_text())
            self.assertTrue(data["force"])
            self.assertEqual(data["requested_by"], 42)

    def test_start_project_update_rejects_when_lock(self):
        with self._patch_paths():
            from app.services import project_update_service as svc

            self.backups.joinpath("update.lock").write_text("")
            ok, _ = asyncio.run(svc.start_project_update(force=True))
            self.assertFalse(ok)

    def test_start_project_update_rejects_when_pending(self):
        with self._patch_paths():
            from app.services import project_update_service as svc

            self.backups.joinpath("update_requested.json").write_text("{}")
            ok, _ = asyncio.run(svc.start_project_update(force=True))
            self.assertFalse(ok)

    def test_free_docker_disk_space_writes_prune_flag(self):
        with self._patch_paths():
            from app.services import project_update_service as svc

            ok, msg = asyncio.run(svc.free_docker_disk_space(requested_by=7))
            self.assertTrue(ok)
            self.assertIn("принят", msg.lower())
            data = json.loads(self.backups.joinpath("prune_requested.json").read_text())
            self.assertEqual(data["requested_by"], 7)

    def test_is_update_running_by_lock(self):
        with self._patch_paths():
            from app.services import project_update_service as svc

            self.assertFalse(asyncio.run(svc.is_update_running()))
            self.backups.joinpath("update.lock").write_text("")
            self.assertTrue(asyncio.run(svc.is_update_running()))


if __name__ == "__main__":
    unittest.main()
