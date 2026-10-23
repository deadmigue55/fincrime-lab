from fincrime import cli, doctor
from fincrime.config import Paths, find_project_root, get_paths


def test_project_root_has_pyproject():
    assert (find_project_root() / "pyproject.toml").is_file()


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FCL_DATA_DIR", str(tmp_path))
    paths = get_paths()
    assert paths.data == tmp_path.resolve()
    assert paths.database.name == "fincrime.duckdb"


def test_ensure_creates_folders(tmp_path):
    paths = Paths(root=tmp_path, data=tmp_path / "d").ensure()
    assert paths.raw.is_dir()
    assert paths.processed.is_dir()


def test_engines_actually_work():
    # Used, not just imported: a blocked native library fails here.
    assert doctor.check_duckdb().ok
    assert doctor.check_pyarrow().ok
    assert doctor.check_polars().ok


def test_disk_check_flags_tiny_threshold_and_huge_threshold(tmp_path):
    paths = Paths(root=tmp_path, data=tmp_path)
    assert doctor.check_disk(paths, min_free_gb=0.001).ok
    assert not doctor.check_disk(paths, min_free_gb=10**9).ok


def test_doctor_exit_code(capsys):
    assert cli.main(["doctor"]) == 0
    assert "duckdb" in capsys.readouterr().out
