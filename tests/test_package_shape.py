from pathlib import Path


def test_runtime_package_lives_at_repo_root() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "domain").is_dir()
    assert (root / "runtime").is_dir()
    assert (root / "adapters").is_dir()
    assert (root / "observability").is_dir()
    assert (root / "strategies").is_dir()
    assert (root / "cli.py").is_file()
    assert not any((path / "runtime").exists() for path in root.glob("prod_*"))
    assert (root / "runtime" / "context.py").is_file()
    assert not (root / "runtime" / "context_builder.py").exists()
