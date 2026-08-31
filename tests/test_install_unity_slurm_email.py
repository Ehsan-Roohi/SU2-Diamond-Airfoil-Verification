import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_unity_slurm_email.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _test_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path]:
    mock_bin = tmp_path / "mock-bin"
    wrapper_dir = tmp_path / "wrapper-bin"
    bashrc = tmp_path / ".bashrc"
    call_log = tmp_path / "calls.log"
    mock_bin.mkdir()
    wrapper_dir.mkdir()
    bashrc.write_text("export PRESERVE_ME=yes\n")

    real_sbatch = mock_bin / "real-sbatch"
    _write_executable(
        real_sbatch,
        "#!/usr/bin/env bash\nprintf 'sbatch:%s\\n' \"$*\" >> \"${MOCK_CALL_LOG}\"\n",
    )
    _write_executable(
        mock_bin / "squeue",
        """#!/usr/bin/env bash
cat <<'EOF'
101|cpu|case-a
102|gpu|case-array
102|gpu|case-array
103|ood-share|sys/dash
104|cpu|sys/desktop
EOF
""",
    )
    _write_executable(
        mock_bin / "scontrol",
        "#!/usr/bin/env bash\nprintf 'scontrol:%s\\n' \"$*\" >> \"${MOCK_CALL_LOG}\"\n",
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "USER": "test-user",
            "PATH": f"{mock_bin}:{env['PATH']}",
            "MOCK_CALL_LOG": str(call_log),
            "UNITY_REAL_SBATCH": str(real_sbatch),
            "UNITY_SLURM_BASHRC": str(bashrc),
            "UNITY_SLURM_WRAPPER_DIR": str(wrapper_dir),
        }
    )
    return env, bashrc, wrapper_dir / "sbatch", call_log


class UnitySlurmEmailInstallerTests(unittest.TestCase):
    def test_installs_idempotent_wrapper_and_updates_active_batch_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, bashrc, wrapper, call_log = _test_environment(Path(tmp))
            command = ["bash", str(INSTALLER), "--mail-user", "researcher@example.edu"]

            first = subprocess.run(command, env=env, check=True, text=True, capture_output=True)
            second = subprocess.run(command, env=env, check=True, text=True, capture_output=True)

            bashrc_text = bashrc.read_text()
            self.assertIn("export PRESERVE_ME=yes", bashrc_text)
            self.assertEqual(bashrc_text.count("# >>> unity-slurm-email >>>"), 1)
            self.assertTrue(wrapper.exists() and os.access(wrapper, os.X_OK))
            self.assertIn("Active batch jobs updated: 2; skipped: 0", first.stdout)
            self.assertIn("Active batch jobs updated: 2; skipped: 0", second.stdout)

            subprocess.run(
                [str(wrapper), "--export=ALL", "job.sh"],
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )
            calls = call_log.read_text()
            self.assertIn("JobId=101", calls)
            self.assertIn("JobId=102", calls)
            self.assertNotIn("JobId=103", calls)
            self.assertNotIn("JobId=104", calls)
            self.assertIn(
                "sbatch:--mail-type=END,FAIL,INVALID_DEPEND,TIME_LIMIT,ARRAY_TASKS "
                "--mail-user=researcher@example.edu --export=ALL job.sh",
                calls,
            )

    def test_refuses_to_overwrite_an_unrecognized_sbatch_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, _, wrapper, _ = _test_environment(Path(tmp))
            wrapper.write_text("user-owned file\n")

            result = subprocess.run(
                ["bash", str(INSTALLER)],
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 3)
            self.assertIn("refusing to replace unrecognized existing file", result.stderr)
            self.assertEqual(wrapper.read_text(), "user-owned file\n")


if __name__ == "__main__":
    unittest.main()
