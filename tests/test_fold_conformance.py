"""Test that the vendored close_call_fold.py produces expected output."""

import json
import subprocess
import sys
from pathlib import Path


def test_fold_conformance():
    """Run vendored fold on sample-season.jsonl and verify output matches expected."""
    project_root = Path(__file__).parent.parent
    vendor_dir = project_root / "vendor" / "close-call"
    fold_script = vendor_dir / "close_call_fold.py"
    sample_input = vendor_dir / "examples" / "sample-season.jsonl"
    expected_output = vendor_dir / "examples" / "sample-season.expected.json"

    assert fold_script.exists(), f"Fold script not found: {fold_script}"
    assert sample_input.exists(), f"Sample input not found: {sample_input}"
    assert expected_output.exists(), f"Expected output not found: {expected_output}"

    # Run the fold script
    result = subprocess.run(
        [sys.executable, str(fold_script), str(sample_input)],
        capture_output=True,
        text=True,
        check=True,
    )

    # Parse outputs
    actual = json.loads(result.stdout)
    with open(expected_output) as f:
        expected = json.load(f)

    # Compare
    assert actual == expected, (
        f"Fold output does not match expected.\n"
        f"Expected: {json.dumps(expected, indent=2)}\n"
        f"Actual: {json.dumps(actual, indent=2)}"
    )
