from sibling_repos import sibling_repo


def test_legacy_reader_uses_archive_without_workspace_link(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "false")
    workspace = tmp_path / "kodemeio-workspace"
    archive = tmp_path / "kodemeio-archived" / "kodemeio-dsh"
    archive.mkdir(parents=True)
    assert sibling_repo(workspace, "kodemeio-dsh") == archive
    assert sibling_repo(workspace, "kodemeio-next") == workspace / "kodemeio-next"


def test_ci_sibling_checkout_takes_precedence_and_missing_stays_missing(tmp_path):
    workspace = tmp_path / "workspace"
    sibling = workspace / "kodemeio-dsh"
    sibling.mkdir(parents=True)
    (tmp_path / "kodemeio-archived" / "kodemeio-dsh").mkdir(parents=True)
    assert sibling_repo(workspace, "kodemeio-dsh") == sibling
    assert sibling_repo(workspace, "kodemeio-llmlite") == workspace / "kodemeio-llmlite"


def test_ci_cannot_replace_missing_checkout_with_local_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    workspace = tmp_path / "workspace"
    (tmp_path / "kodemeio-archived" / "kodemeio-dsh").mkdir(parents=True)
    assert sibling_repo(workspace, "kodemeio-dsh") == workspace / "kodemeio-dsh"
