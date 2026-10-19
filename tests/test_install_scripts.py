"""P5-T1: install.sh (dry runs, so CI can check it without network) and install.ps1 basics."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import support

SH = support.ROOT / "install.sh"
PS1 = support.ROOT / "install.ps1"


@unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "POSIX sh")
class TestInstallSh(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.bin = self.home / "fakebin"  # a PATH with python3 and, optionally, uv
        self.bin.mkdir()
        os.symlink(shutil.which("python3") or shutil.which("python"), self.bin / "python3")

    def sh(self, *args: str, uv: bool = True, **env) -> subprocess.CompletedProcess:
        if uv:
            (self.bin / "uv").write_text("#!/bin/sh\nexit 0\n")
            (self.bin / "uv").chmod(0o755)
        environ = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", **env}
        return subprocess.run(["sh", str(SH), *args], capture_output=True, text=True, env=environ, timeout=60)

    def plan(self, p: subprocess.CompletedProcess) -> list[str]:
        return [l[2:] for l in p.stdout.splitlines() if l.startswith("+ ")]

    def test_dry_run_with_uv(self):
        p = self.sh("--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.plan(p), ["uv tool install --force --reinstall git+https://github.com/lephus/toknix@main",
                                        "tokopt install", "tokopt doctor"])
        self.assertFalse((self.home / ".tokopt").exists())  # a dry run changes nothing

    def test_dry_run_without_uv_uses_a_private_venv(self):
        p = self.sh("--dry-run", uv=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        venv = self.home / ".tokopt" / "venv"
        steps = self.plan(p)
        self.assertTrue(steps[0].endswith(f"-m venv {venv}"))
        self.assertIn(f"{venv}/bin/python -m pip install --quiet --upgrade --force-reinstall --no-deps git+https://github.com/lephus/toknix@main",
                      steps)
        self.assertIn(f"ln -sf {venv}/bin/tokopt {self.home}/.local/bin/tokopt", steps)

    def test_from_ref_repo_and_no_hooks(self):
        self.assertIn("uv tool install --force --reinstall /src/tokopt", self.plan(self.sh("--dry-run", "--from", "/src/tokopt")))
        p = self.sh("--dry-run", TOKOPT_REPO="me/fork", TOKOPT_REF="v0.1.0")
        self.assertIn("uv tool install --force --reinstall git+https://github.com/me/fork@v0.1.0", self.plan(p))
        self.assertNotIn("tokopt install", self.plan(self.sh("--dry-run", "--no-hooks")))

    def test_uninstall_dry_run(self):
        p = self.sh("--dry-run", "--uninstall")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("tokopt command not found", p.stdout)

    def test_bad_option_and_missing_python(self):
        p = self.sh("--bogus")
        self.assertEqual(p.returncode, 1)
        self.assertIn("unknown option: --bogus", p.stderr)
        (self.bin / "python3").unlink()
        p = subprocess.run([shutil.which("sh"), str(SH), "--dry-run"], capture_output=True, text=True,
                           env={"HOME": str(self.home), "PATH": str(self.bin)}, timeout=60)  # no python on PATH
        self.assertEqual(p.returncode, 1)
        self.assertIn("Python 3.9 or newer is required", p.stderr)


class TestInstallPs1(unittest.TestCase):
    """No PowerShell here: CI runs PSScriptAnalyzer and a dry run on Windows (P5-T2)."""

    def test_same_source_and_switches_as_install_sh(self):
        text = PS1.read_text(encoding="utf-8")
        for needle in ('"lephus/toknix"', '"main"', "git+https://github.com/$Repo@$Ref", "[switch]$DryRun",
                       "[switch]$Uninstall", "TOKOPT_DRY_RUN", "TOKOPT_UNINSTALL", "TOKOPT_FROM", "\"tool\", \"install\", \"--force\", \"--reinstall\""):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
