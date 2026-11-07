import json
import subprocess
import sys
import sysconfig
from pathlib import Path
from zipfile import ZipFile

from prepare_kaggle_bundle import build_bundle


def test_bundle_imports_without_installed_workspace_packages(tmp_path):
    bundle = tmp_path / "code.zip"
    count = build_bundle(bundle)
    unpacked = tmp_path / "notebook-code"
    with ZipFile(bundle) as archive:
        names = archive.namelist()
        assert len(names) == count
        for package in ("embedding", "retrieval", "preprocessing"):
            assert f"packages/{package}/src/visualseek_{package}/contracts.py" in names
        assert not any(
            "__pycache__" in name or ".env" in name or name.startswith("data/") for name in names
        )
        archive.extractall(unpacked)
    probe = """
import importlib
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1])
# -I -S suppresses PYTHONPATH and editable-install .pth hooks. Only third-party
# dependencies are made available; all project imports must come from the ZIP.
sys.path.insert(0, sys.argv[2])
sys.path.insert(0, str(root / 'scripts'))
import _bootstrap
for package in ('embedding', 'retrieval', 'preprocessing'):
    for suffix in ('', '.contracts'):
        module = importlib.import_module('visualseek_' + package + suffix)
        assert Path(module.__file__).is_relative_to(root), module.__file__
sys.argv = [str(root / 'scripts/kaggle_embed.py'), '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
sys.argv = [str(root / 'scripts/kaggle_embed_multi_gpu.py'), '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", probe, str(unpacked), sysconfig.get_path("purelib")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--manifest" in result.stdout


def test_notebook_required_files_include_packaged_contracts():
    # Prevent the notebook's extraction allowlist from dropping modules again.
    import ast
    import re

    document = Path(__file__).resolve().parents[2] / "docs/kaggle-embedding-rag.md"
    cell = next(
        code
        for code in re.findall(r"```python\n(.*?)\n```", document.read_text(), re.S)
        if "code_files =" in code
    )
    assignment = next(
        node
        for node in ast.parse(cell).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "code_files" for target in node.targets
        )
    )
    files = ast.literal_eval(assignment.value)
    for package in ("embedding", "retrieval", "preprocessing"):
        assert f"packages/{package}/src/visualseek_{package}/contracts.py" in files
    assert len(files) == len(set(files)), json.dumps(files)
