"""P5-T1: install.sh (dry runs, so CI can check it without network) and install.ps1 basics."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import support

SH = support.ROOT / "install.sh"
PS1 = support.ROOT / "install.ps1"
MARK = "# toknix: put ~/.local/bin on PATH (added by install.sh)"
EXPORT = 'export PATH="$HOME/.local/bin:$PATH"'
# A uv that "installs" toknix the way uv does: an executable in ~/.local/bin
FAKE_UV = r"""#!/bin/sh
if [ "$1" = tool ] && [ "$2" = install ]; then
    mkdir -p "$HOME/.local/bin"
    printf '#!/bin/sh\necho "fake toknix $*"\n' > "$HOME/.local/bin/toknix"
    chmod +x "$HOME/.local/bin/toknix"
fi
exit 0
"""


@unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "POSIX sh")
class TestInstallSh(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.bin = self.home / "fakebin"  # a PATH with python3 and, optionally, uv
        self.bin.mkdir()
        os.symlink(shutil.which("python3") or shutil.which("python"), self.bin / "python3")

    def sh(self, *args: str, uv: bool = True, uv_body: str = "#!/bin/sh\nexit 0\n", **env) -> subprocess.CompletedProcess:
        if uv:
            (self.bin / "uv").write_text(uv_body)
            (self.bin / "uv").chmod(0o755)
        # SHELL is set: bash would otherwise fill it in from the login shell of whoever runs the tests
        environ = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "SHELL": "/bin/sh", **env}
        return subprocess.run(["sh", str(SH), *args], capture_output=True, text=True, env=environ, timeout=60)

    def plan(self, p: subprocess.CompletedProcess) -> list[str]:
        return [l[2:] for l in p.stdout.splitlines() if l.startswith("+ ")]

    def test_dry_run_with_uv(self):
        p = self.sh("--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.plan(p), ["uv tool install --force --reinstall git+https://github.com/lephus/toknix@main",
                                        f"add {self.home}/.local/bin to PATH in {self.home}/.profile",
                                        "toknix install", "toknix doctor"])
        self.assertFalse((self.home / ".toknix").exists())  # a dry run changes nothing

    def test_dry_run_without_uv_uses_a_private_venv(self):
        p = self.sh("--dry-run", uv=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        venv = self.home / ".toknix" / "venv"
        steps = self.plan(p)
        self.assertTrue(steps[0].endswith(f"-m venv {venv}"))
        self.assertIn(f"{venv}/bin/python -m pip install --quiet --upgrade --force-reinstall --no-deps git+https://github.com/lephus/toknix@main",
                      steps)
        self.assertIn(f"ln -sf {venv}/bin/toknix {self.home}/.local/bin/toknix", steps)

    def test_from_ref_repo_and_no_hooks(self):
        self.assertIn("uv tool install --force --reinstall /src/toknix", self.plan(self.sh("--dry-run", "--from", "/src/toknix")))
        p = self.sh("--dry-run", TOKNIX_REPO="me/fork", TOKNIX_REF="v0.1.0")
        self.assertIn("uv tool install --force --reinstall git+https://github.com/me/fork@v0.1.0", self.plan(p))
        self.assertNotIn("toknix install", self.plan(self.sh("--dry-run", "--no-hooks")))

    # ~/.local/bin is not on a fresh macOS PATH: the install succeeded, then `toknix` was "command not found"
    def test_adds_local_bin_to_the_shell_profile(self):
        p = self.sh("--no-hooks", uv_body=FAKE_UV, SHELL="/bin/zsh")
        self.assertEqual(p.returncode, 0, p.stderr)
        zshrc = self.home / ".zshrc"
        self.assertEqual(zshrc.read_text(), f"{MARK}\n{EXPORT}\n")
        self.assertIn(f"added {self.home}/.local/bin to PATH in {zshrc}", p.stdout)
        tail = p.stdout.rstrip().splitlines()[-2:]  # where nobody misses it
        self.assertIn("open a new terminal", tail[0])
        self.assertEqual(tail[1].strip(), EXPORT)
        self.assertEqual(self.sh("--no-hooks", uv_body=FAKE_UV, SHELL="/bin/zsh").returncode, 0)
        self.assertEqual(zshrc.read_text().count(MARK), 1)  # a re-run adds nothing

    def test_profile_of_each_shell(self):
        bash = ".bash_profile" if platform.system() == "Darwin" else ".bashrc"
        for shell, profile in (("/bin/bash", bash), ("/usr/local/bin/fish", ".config/fish/config.fish"),
                               ("/bin/dash", ".profile")):
            with self.subTest(shell=shell):
                self.assertIn(f"add {self.home}/.local/bin to PATH in {self.home}/{profile}",
                              self.plan(self.sh("--dry-run", SHELL=shell)))
        self.sh("--no-hooks", uv_body=FAKE_UV, SHELL="/usr/local/bin/fish")
        self.assertIn('set -gx PATH "$HOME/.local/bin" $PATH', (self.home / ".config/fish/config.fish").read_text())

    def test_no_modify_path_and_already_on_path(self):
        for args, env in ((("--no-modify-path",), {}), ((), {"TOKNIX_NO_MODIFY_PATH": "1"})):
            with self.subTest(args=args, env=env):
                p = self.sh("--no-hooks", *args, uv_body=FAKE_UV, SHELL="/bin/zsh", **env)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertFalse((self.home / ".zshrc").exists())
                self.assertIn("add that line to your shell profile", p.stdout)
        on_path = f"{self.bin}:{self.home}/.local/bin:/usr/bin:/bin"
        p = self.sh("--no-hooks", uv_body=FAKE_UV, SHELL="/bin/zsh", PATH=on_path)
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertNotIn("open a new terminal", p.stdout)

    def test_uninstall_removes_only_its_profile_lines(self):
        before = "export EDITOR=vim\n"
        (self.home / ".zshrc").write_text(before)
        self.sh("--no-hooks", uv_body=FAKE_UV, SHELL="/bin/zsh")
        self.assertIn(MARK, (self.home / ".zshrc").read_text())
        self.assertIn(f"remove the toknix PATH lines from {self.home}/.zshrc",
                      self.plan(self.sh("--dry-run", "--uninstall")))
        p = self.sh("--uninstall")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.home / ".zshrc").read_text(), before)
        self.sh("--no-hooks", uv_body=FAKE_UV, SHELL="/bin/bash")  # no profile yet: install creates it
        self.sh("--uninstall")
        self.assertEqual(sorted(p.name for p in self.home.iterdir() if p.name.startswith(".bash")), [])

    def test_uninstall_dry_run(self):
        p = self.sh("--dry-run", "--uninstall")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("toknix command not found", p.stdout)

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
                       "[switch]$Uninstall", "TOKNIX_DRY_RUN", "TOKNIX_UNINSTALL", "TOKNIX_FROM", "\"tool\", \"install\", \"--force\", \"--reinstall\""):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
