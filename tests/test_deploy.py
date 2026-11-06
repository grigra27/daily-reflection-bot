"""Exercise the actual shell script with Docker stubbed, never production."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", ["none", "pull", "up", "wrong-image"])
def test_deploy_fails_closed_and_checks_target_image(tmp_path, failure):
    source = Path(__file__).resolve().parents[1] / "deploy/deploy_remote.sh"
    shutil.copy(source, tmp_path / "deploy_remote.sh")
    (tmp_path / "deploy.env").write_text("IMAGE_REF=old\n")
    (tmp_path / "running-image").write_text("old\n")
    docker = tmp_path / "docker"
    docker.write_text("""#!/usr/bin/env bash
set -eu
source deploy.env
if [[ "$1" == "inspect" ]]; then
  case "$3" in
    *State.Status*) echo running ;;
    *RestartCount*) echo 0 ;;
    *Config.Image*) cat running-image ;;
  esac
  exit 0
fi
action="$6"
echo "$IMAGE_REF:$action" >> actions
if [[ "$IMAGE_REF" == "new" && "$action" == "$FAILURE" ]]; then exit 1; fi
if [[ "$action" == "up" ]]; then
  if [[ "$FAILURE" != "wrong-image" || "$IMAGE_REF" == "old" ]]; then
    echo "$IMAGE_REF" > running-image
  fi
fi
""")
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", "deploy_remote.sh", "new"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "FAILURE": failure,
            "STABLE_SECONDS": "0",
            "POLL_SECONDS": "0",
            "VERIFY_MAX_TICKS": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if failure == "none":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "deployment success: new" in result.stdout
    else:
        assert result.returncode != 0
        assert "deployment success" not in result.stdout
        assert "rollback success" in result.stdout
        assert (tmp_path / "running-image").read_text().strip() == "old"
        if failure == "pull":
            assert "new:up" not in (tmp_path / "actions").read_text()
