"""A local working clone as the repos.txt source must keep every branch.

Such a clone holds only the checked-out branch under refs/heads; the rest are
refs/remotes/origin/*. The bundle step promotes them to heads so that commit,
branch and contributor counts cover the whole history, not one branch.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import run

from repo_metadata_cli.fetcher import fetch_bundles
from repo_metadata_cli.vcs.git import GitVCS


def _git(args, cwd):
    run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _commit(path: Path, name: str, message: str) -> None:
    (path / name).write_text(f"{message}\n")
    _git(["add", "."], cwd=path)
    _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message], cwd=path)


def _make_upstream(path: Path) -> None:
    path.mkdir(parents=True)
    _git(["init", "-q", "-b", "master"], cwd=path)
    _commit(path, "a.py", "init")
    _commit(path, "b.py", "second")
    _git(["checkout", "-qb", "develop"], cwd=path)
    _commit(path, "c.py", "dev-1")
    _commit(path, "d.py", "dev-2")
    _commit(path, "e.py", "dev-3")
    _git(["checkout", "-q", "master"], cwd=path)


def test_local_clone_source_keeps_remote_tracking_branches(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    _make_upstream(upstream)
    clone = tmp_path / "clone"
    _git(["clone", "-q", str(upstream), str(clone)], cwd=tmp_path)  # heads: master only
    heads = run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
                cwd=clone, capture_output=True, text=True, check=True).stdout.split()
    assert heads == ["master"]

    repos_file = tmp_path / "repos.txt"
    repos_file.write_text(f"{clone}\n")
    bundles = tmp_path / "bundles"
    fetch_bundles(repos_file, bundles, tmp_path / "mirrors", tmp_path / "ok.txt")
    bundle = next(bundles.glob("*.bundle"))

    vcs = GitVCS()
    repo_dir = vcs.clone(bundle, tmp_path / "work")
    assert repo_dir is not None
    assert vcs.commit_count(repo_dir) == 5
    assert vcs.branch_count(repo_dir) == 2
