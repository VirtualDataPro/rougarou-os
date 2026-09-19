"""Rootless Docker setup's account/configuration preservation contracts."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "rootfs/usr/local/bin/rougarou-docker"
loader = importlib.machinery.SourceFileLoader("rougarou_docker", str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
docker = importlib.util.module_from_spec(spec)
loader.exec_module(docker)


class DockerSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)

    def result(self, output):
        return subprocess.CompletedProcess([], 0, output, "")

    def test_root_is_rejected_before_any_setup(self):
        with patch.object(docker.os, "getuid", return_value=0):
            with self.assertRaisesRegex(docker.SetupError, "without sudo"):
                docker.user_paths()

    def test_subordinate_ids_need_sufficient_range_for_exact_account(self):
        path = self.home / "subuid"
        path.write_text("someone:100000:65536\nowner:200000:100\n1000:300000:65536\n")
        self.assertTrue(docker.subordinate_range(path, "owner", 1000))
        self.assertFalse(docker.subordinate_range(path, "own", 1001))

    def test_context_selected_only_for_fresh_configuration_or_explicit_flag(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(docker, "run", return_value=self.result("default\n")):
            self.assertEqual(docker.context_plan(self.home, "unix:///test.sock", False), (True, True))
            (self.home / ".docker").mkdir()
            (self.home / ".docker/config.json").write_text('{"currentContext":"default","auths":{}}')
            original = (self.home / ".docker/config.json").read_bytes()
            self.assertEqual(docker.context_plan(self.home, "unix:///test.sock", False), (True, False))
            self.assertEqual(docker.context_plan(self.home, "unix:///test.sock", True), (True, True))
            self.assertEqual((self.home / ".docker/config.json").read_bytes(), original)

    def test_existing_context_collision_is_refused(self):
        existing = [{"Endpoints": {"docker": {"Host": "ssh://existing-server"}}}]
        with patch.object(docker, "run", side_effect=[self.result("default\nrougarou-rootless\n"), self.result(json.dumps(existing))]):
            with self.assertRaisesRegex(docker.SetupError, "points elsewhere"):
                docker.context_plan(self.home, "unix:///local.sock", True)

    def test_config_created_private_but_existing_content_never_replaced(self):
        docker.daemon_config(self.home)
        path = self.home / ".config/rougarou/docker/daemon.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_text('{"debug":true}\n')
        docker.daemon_config(self.home)
        self.assertEqual(path.read_text(), '{"debug":true}\n')
        path.write_text('{"data-root":"/some/existing/data"}\n')
        with self.assertRaises(docker.SetupError):
            docker.daemon_config(self.home)
        self.assertEqual(path.read_text(), '{"data-root":"/some/existing/data"}\n')

    def test_remote_environment_cannot_redirect_internal_checks(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://remote", "DOCKER_CONTEXT": "production"}), patch.object(docker.subprocess, "run", return_value=self.result("")) as call:
            docker.run(["docker", "context", "ls"])
            environment = call.call_args.kwargs["env"]
            self.assertNotIn("DOCKER_HOST", environment)
            self.assertNotIn("DOCKER_CONTEXT", environment)

    def test_rootful_response_is_not_accepted_as_success(self):
        with patch.object(docker, "run", return_value=self.result('{"SecurityOptions":["name=seccomp"]}')):
            with self.assertRaisesRegex(docker.SetupError, "did not report rootless"):
                docker.daemon_info("unix:///socket")


if __name__ == "__main__":
    unittest.main()
