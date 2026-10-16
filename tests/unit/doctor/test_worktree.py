# SPDX-License-Identifier: Apache-2.0
"""The doctor's worktree check: a git worktree whose scope has none of the settings its main checkout's has is named,
with the command that copies them -- and the doctor itself writes nothing."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.build.checkouts import Checkout, main_of
from sim_mirror.doctor.report import CheckResult
from sim_mirror.doctor.worktree import NAME, check_worktree_settings, copy_command
from sim_mirror.scope import Scope
from sim_mirror.testing.checkouts import linked_worktree, main_checkout, separate_main_checkout


def layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A repository whose app is in ``ios/``, a worktree of it beside it, and where config.toml goes."""
    tmp_path = tmp_path.resolve()
    repo = main_checkout(tmp_path / "repo")
    worktree = linked_worktree(repo, tmp_path / "repo-feature")
    for top in (repo, worktree):
        (top / "ios").mkdir()
    return repo, worktree, tmp_path / "config.toml"


def table(config: Path, folder: Path, body: str) -> None:
    with config.open("a", encoding="utf-8") as file:
        file.write(f'[scopes."{Scope.for_folder(folder).id}"]\n{body}\n')


def test_a_worktree_whose_scope_lacks_its_main_checkouts_settings_is_told_how_to_copy_them(tmp_path: Path) -> None:
    repo, worktree, config = layout(tmp_path)
    table(config, repo / "ios", 'build.tools = true\nbuild.configuration = "Staging"\nserver.port = 7500')
    before = config.read_text()
    here, there = Scope.for_folder(worktree / "ios"), Scope.for_folder(repo / "ios")
    result = check_worktree_settings(worktree / "ios", config)
    command = f"sim-mirror config copy --from {there.id} --scope {here.id}"
    assert result == CheckResult(
        NAME,
        "warn",
        f"{worktree / 'ios'} is a git worktree of {repo}, whose scope {there.id} sets build.tools, "
        f"build.configuration; this folder's scope {here.id} sets none of them, so SimMirror started here runs "
        "without them",
        f"`{command}` copies them",
    )
    assert copy_command(there, here) == command
    # Checking writes nothing.
    assert config.read_text() == before


def test_a_long_list_of_settings_is_cut_short_with_how_many_more(tmp_path: Path) -> None:
    repo, worktree, config = layout(tmp_path)
    body = "\n".join(f"{name} = {value}" for name, value in (
        ("agent.cursor", "false"), ("agent.cursor_lead_ms", 100), ("agent.cursor_linger_s", 5),
        ("agent.screenshot_width", 300), ("agent.snapshot_max_elements", 60), ("agent.tools", "true"),
        ("stream.fps", 20),
    ))  # fmt: skip
    table(config, repo, body)
    result = check_worktree_settings(worktree, config)
    assert result.status == "warn" and " and 1 more;" in result.detail


def test_a_worktree_with_settings_of_its_own_or_with_none_to_miss_is_fine(tmp_path: Path) -> None:
    repo, worktree, config = layout(tmp_path)
    assert check_worktree_settings(worktree, config) == CheckResult(
        NAME, "ok", f"{worktree} is a git worktree of {repo}; neither it nor {repo} has settings of its own, so both "
        "use the file's",
    )  # fmt: skip
    table(config, repo, "build.tools = true")
    table(config, worktree, "agent.cursor = false")
    result = check_worktree_settings(worktree, config)
    own = Scope.for_folder(worktree).id
    assert result.status == "ok" and f"its scope {own} has settings of its own" in result.detail


def test_what_is_not_a_worktree_is_not_checked(tmp_path: Path) -> None:
    repo, _worktree, config = layout(tmp_path)
    assert check_worktree_settings(None, config).status == "skip"
    assert check_worktree_settings(repo, None).status == "skip"
    assert check_worktree_settings(repo / "ios", config) == CheckResult(
        NAME, "skip", f"{repo / 'ios'} is in the main checkout of its repository, not a git worktree"
    )
    (tmp_path / "plain").mkdir()
    assert check_worktree_settings(tmp_path / "plain", config).detail.endswith("is not in a git checkout")


def test_a_worktree_of_a_repository_with_no_main_checkout_has_nothing_to_copy_from(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    bare = tmp_path / "app.git"
    (bare / "worktrees").mkdir(parents=True)
    own = bare / "worktrees" / "wt"
    own.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {own}\n", encoding="utf-8")
    (own / "gitdir").write_text(f"{worktree / '.git'}\n", encoding="utf-8")
    (own / "commondir").write_text("../..\n", encoding="utf-8")
    (bare / "config").write_text("[core]\n\trepositoryformatversion = 0\n\tbare = true\n", encoding="utf-8")
    assert main_of(Checkout(worktree, bare)) is None
    result = check_worktree_settings(worktree, tmp_path / "config.toml")
    assert result == CheckResult(NAME, "skip", f"{worktree} belongs to a repository with no main checkout")


def test_a_config_file_that_cannot_be_read_is_left_to_config_validate(tmp_path: Path) -> None:
    _repo, worktree, config = layout(tmp_path)
    config.write_text("[scopes\n", encoding="utf-8")
    result = check_worktree_settings(worktree, config)
    assert result.status == "skip" and "config validate" in result.fix


def separate_layout(tmp_path: Path, *, named_back: bool) -> tuple[Path, Path, Path]:
    """A main checkout made with ``--separate-git-dir``, a worktree of it, and where config.toml goes."""
    tmp_path = tmp_path.resolve()
    repo = separate_main_checkout(tmp_path / "repo", tmp_path / "store" / "repo.git", named_back=named_back)
    worktree = linked_worktree(repo, tmp_path / "repo-feature")
    for top in (repo, worktree):
        (top / "ios").mkdir()
    return repo, worktree, tmp_path / "config.toml"


def test_a_worktree_finds_a_separate_git_dir_main_checkout_its_git_folder_names_back(tmp_path: Path) -> None:
    """The check found no main checkout for a ``--separate-git-dir`` layout, so it could not offer to copy its
    settings; it now finds the one the git folder names as ``core.worktree``, as builds do."""
    repo, worktree, config = separate_layout(tmp_path, named_back=True)
    table(config, repo / "ios", "build.tools = true")
    result = check_worktree_settings(worktree / "ios", config)
    here, there = Scope.for_folder(worktree / "ios"), Scope.for_folder(repo / "ios")
    assert result.status == "warn" and f"is a git worktree of {repo}, whose scope {there.id}" in result.detail
    assert result.fix == f"`{copy_command(there, here)}` copies them"


def test_a_separate_git_dir_main_checkout_named_nowhere_is_said_how_to_name(tmp_path: Path) -> None:
    repo, worktree, config = separate_layout(tmp_path, named_back=False)
    git_dir = tmp_path.resolve() / "store" / "repo.git"
    assert check_worktree_settings(worktree, config) == CheckResult(
        NAME,
        "skip",
        f"{worktree} is a git worktree whose repository keeps its git folder apart, in {git_dir} "
        "(--separate-git-dir), and that folder does not say where its main checkout is",
        '`git config core.worktree "$PWD"`, run once at the top of the main checkout, lets SimMirror find it',
    )
    # Run in the main checkout itself, the doctor knows it as builds know their own build folder's.
    assert check_worktree_settings(repo / "ios", config) == CheckResult(
        NAME, "skip", f"{repo / 'ios'} is in the main checkout of its repository, not a git worktree"
    )
