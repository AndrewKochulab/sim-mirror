# SPDX-License-Identifier: Apache-2.0
"""Which folder a build runs in: inside the build folder, or the same folder of another checkout of its repository --
and nowhere a folder only claims to be one."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.build import checkouts
from sim_mirror.build.checkouts import POINTER_MAX, Checkout, build_folder_for, checkout_of
from sim_mirror.testing.checkouts import linked_worktree, main_checkout, separate_main_checkout


def layout(tmp_path: Path) -> tuple[Path, Path]:
    """A repository whose app is in ``ios/``, and a worktree of it beside it."""
    tmp_path = tmp_path.resolve()
    repo = main_checkout(tmp_path / "repo")
    worktree = linked_worktree(repo, tmp_path / "repo-feature")
    for top in (repo, worktree):
        (top / "ios" / "Sub").mkdir(parents=True)
        (top / "docs").mkdir()
    return repo, worktree


def test_a_checkout_is_known_by_its_git_folder_or_a_worktree_its_repository_names_back(tmp_path: Path) -> None:
    repo, worktree = layout(tmp_path)
    assert checkout_of(repo / "ios" / "Sub") == Checkout(repo, repo / ".git")
    assert checkout_of(worktree / "ios") == Checkout(worktree, repo / ".git")
    assert checkout_of(tmp_path / "elsewhere") is None


def test_the_same_folder_of_another_checkout_is_built_and_only_that(tmp_path: Path) -> None:
    repo, worktree = layout(tmp_path)
    home = repo / "ios"
    # Where the agent works: anywhere in the worktree builds its ios folder.
    for asked in (worktree, worktree / "docs", worktree / "ios" / "Sub"):
        assert build_folder_for(asked, home, exact=False) == worktree / "ios"
    # A folder it names: kept inside the worktree's ios folder, taken there from above it, refused beside it.
    assert build_folder_for(worktree / "ios" / "Sub", home, exact=True) == worktree / "ios" / "Sub"
    assert build_folder_for(worktree, home, exact=True) == worktree / "ios"
    assert build_folder_for(worktree / "docs", home, exact=True) is None
    # And back: a build folder in the worktree reaches the main checkout's.
    assert build_folder_for(repo, worktree / "ios", exact=False) == repo / "ios"


def test_in_its_own_checkout_a_build_stays_in_the_build_folder_or_inside_it(tmp_path: Path) -> None:
    repo, _ = layout(tmp_path)
    home = repo / "ios"
    assert build_folder_for(repo / "ios" / "Sub", home, exact=True) == repo / "ios" / "Sub"
    assert build_folder_for(repo / "ios" / "Sub", home, exact=False) == home
    assert build_folder_for(repo, home, exact=True) == home
    assert build_folder_for(repo / "docs", home, exact=False) == home
    assert build_folder_for(repo / "docs", home, exact=True) is None


def test_a_worktree_inside_the_build_folder_is_a_checkout_of_its_own(tmp_path: Path) -> None:
    repo = main_checkout(tmp_path.resolve() / "repo")
    nested = linked_worktree(repo, repo / ".worktrees" / "fix", name="fix")
    assert build_folder_for(nested / "Sources", repo, exact=False) == nested
    assert build_folder_for(nested / "Sources", repo, exact=True) == nested / "Sources"


def test_outside_any_repository_only_the_build_folder_and_what_is_inside_it_build(tmp_path: Path) -> None:
    home = (tmp_path / "app").resolve()
    (home / "Sub").mkdir(parents=True)
    assert build_folder_for(home / "Sub", home, exact=True) == home / "Sub"
    assert build_folder_for(home / "Sub", home, exact=False) == home
    # A client working above the build folder still builds it; a folder named above it does not.
    assert build_folder_for(tmp_path, home, exact=False) == home
    assert build_folder_for(tmp_path, home, exact=True) is None
    assert build_folder_for(tmp_path / "other", home, exact=False) is None


def test_another_repository_is_not_built_even_inside_the_build_folder_it_stays_where_it_was(tmp_path: Path) -> None:
    repo, _ = layout(tmp_path)
    other = main_checkout(tmp_path.resolve() / "other")
    assert build_folder_for(other, repo, exact=False) is None
    assert build_folder_for(other, repo, exact=True) is None
    vendored = main_checkout(repo / "Vendor" / "Lib")
    assert build_folder_for(vendored, repo, exact=True) == vendored
    assert build_folder_for(vendored, repo, exact=False) == repo


def test_a_folder_that_only_claims_to_be_a_worktree_is_not_one(tmp_path: Path) -> None:
    repo, worktree = layout(tmp_path)
    home = repo / "ios"
    base = tmp_path.resolve()
    # A copy of the worktree's .git file: the repository names the worktree back, not the copy.
    copied = base / "copied"
    copied.mkdir()
    (copied / ".git").write_text((worktree / ".git").read_text())
    # A link to the worktree's .git file, or to the repository's own git folder.
    linked = base / "linked"
    linked.mkdir()
    (linked / ".git").symlink_to(worktree / ".git")
    disguised = base / "disguised"
    disguised.mkdir()
    (disguised / ".git").symlink_to(repo / ".git")
    for forged in (copied, linked, disguised):
        assert checkout_of(forged) is None
        assert build_folder_for(forged, home, exact=False) is None
        assert build_folder_for(forged, home, exact=True) is None


def test_a_git_file_that_is_not_a_worktrees_is_no_checkout(tmp_path: Path) -> None:
    repo = main_checkout(tmp_path.resolve() / "repo")
    folder = tmp_path.resolve() / "odd"
    folder.mkdir()
    pointer = folder / ".git"
    module = repo / ".git" / "modules" / "odd"
    module.mkdir(parents=True)
    (module / "gitdir").write_text(f"{pointer}\n")
    (module / "commondir").write_text("../..\n")
    for said in (
        "ref: refs/heads/main\n",
        "gitdir: " + "x" * POINTER_MAX,
        f"gitdir: {repo / '.git' / 'worktrees' / 'missing'}\n",
        f"gitdir: {module}\n",
    ):
        pointer.write_text(said)
        assert checkout_of(folder) is None, said
    pointer.write_bytes(b"gitdir: \xff\n")
    assert checkout_of(folder) is None
    pointer.write_text("")
    assert checkout_of(folder) is None


def test_a_link_that_leads_nowhere_or_a_path_that_cannot_be_read_is_passed_over(tmp_path: Path) -> None:
    repo = main_checkout(tmp_path.resolve() / "repo")
    inner = repo / "inner"
    inner.mkdir()
    (inner / ".git").symlink_to(tmp_path / "gone")
    assert checkout_of(inner) == Checkout(repo, repo / ".git")
    assert checkouts._kind(Path("/no\x00where")) is None


def separate_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A main checkout made with ``--separate-git-dir``, its git folder elsewhere, and a worktree of it."""
    base = tmp_path.resolve()
    git_dir = base / "store" / "repo.git"
    repo = separate_main_checkout(base / "repo", git_dir)
    worktree = linked_worktree(repo, base / "repo-feature")
    for top in (repo, worktree):
        (top / "ios").mkdir()
    return repo, worktree, git_dir


def test_a_main_checkout_whose_git_folder_is_elsewhere_builds_its_worktrees(tmp_path: Path) -> None:
    repo, worktree, git_dir = separate_layout(tmp_path)
    home = repo / "ios"
    assert checkouts.home_checkout(home) == Checkout(repo, git_dir)
    assert checkout_of(worktree / "ios") == Checkout(worktree, git_dir)
    assert build_folder_for(worktree, home, exact=False) == worktree / "ios"
    assert build_folder_for(worktree, home, exact=True) == worktree / "ios"
    assert build_folder_for(repo, home, exact=False) == home
    assert build_folder_for(repo / "ios", home, exact=True) == home


def test_a_git_file_naming_a_repositorys_git_folder_is_trusted_only_as_the_build_folders_own(tmp_path: Path) -> None:
    repo, worktree, _ = separate_layout(tmp_path)
    home = repo / "ios"
    # Nothing names a separate-git-dir checkout back, so a copy of its .git file is no checkout of it.
    copied = tmp_path.resolve() / "copied"
    (copied / "ios").mkdir(parents=True)
    (copied / ".git").write_text((repo / ".git").read_text())
    assert checkout_of(copied) is None
    assert build_folder_for(copied, home, exact=False) is None
    assert build_folder_for(copied / "ios", home, exact=True) is None
    # A worktree still has to be named back by the repository.
    forged = tmp_path.resolve() / "forged"
    forged.mkdir()
    (forged / ".git").write_text((worktree / ".git").read_text())
    assert build_folder_for(forged, home, exact=False) is None
    # Nor is the main checkout known from one of its worktrees: its git folder does not say where it is.
    assert build_folder_for(repo, worktree / "ios", exact=False) is None


def test_a_git_file_that_names_no_repositorys_own_git_folder_is_no_build_folder_checkout(tmp_path: Path) -> None:
    base = tmp_path.resolve()
    _, worktree, git_dir = separate_layout(tmp_path)
    home = base / "odd"
    home.mkdir()
    pointer = home / ".git"
    for said in (
        "ref: refs/heads/main\n",
        f"gitdir: {base / 'missing'}\n",
        f"gitdir: {base / 'store'}\n",
        f"gitdir: {git_dir / 'worktrees' / worktree.name}\n",
    ):
        pointer.write_text(said)
        assert checkouts.home_checkout(home) is None, said
    pointer.unlink()
    assert checkouts.home_checkout(home) is None
    plain = main_checkout(base / "plain")
    (plain / "Sub").mkdir()
    (plain / "Sub" / ".git").symlink_to(plain / ".git")
    assert checkouts.home_checkout(plain / "Sub") is None
