"""Regression checks for user-visible CozyLife documentation."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def test_documentation_describes_current_transport_and_schedule_behavior() -> None:
    """Keep the public behavior claims aligned with the implementation."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    setup = (ROOT / "docs" / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    coverage = (ROOT / "docs" / "APK_LOCAL_PRODUCT_COVERAGE.md").read_text(
        encoding="utf-8"
    )

    assert "HACS-Default" not in readme
    assert "every 500 ms" not in readme
    assert "every 500 ms" not in setup
    assert "every 10 seconds" in readme
    assert "every 10 seconds" in setup
    assert "250 ms" in readme
    assert "250 ms" in setup
    assert "does not create per-device schedule entities" in readme
    assert "write to/clear the CozyLife app's native DPID 3" in readme
    assert "intentionally leaves those records untouched" in coverage
    for heading in (
        "### Fans",
        "### Sensors and Safety Devices",
        "### Energy Storage and Gateways",
        "### Doorbells, Speakers, and AI Profiles",
    ):
        assert heading in readme


def test_debug_documentation_matches_safe_logging_contract() -> None:
    """Keep report guidance consistent with diagnostic redaction."""
    setup = (ROOT / "docs" / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    strings = json.loads(
        (ROOT / "custom_components" / "cozylife_local" / "strings.json").read_text(
            encoding="utf-8"
        )
    )
    translation = json.loads(
        (
            ROOT
            / "custom_components"
            / "cozylife_local"
            / "translations"
            / "en.json"
        ).read_text(encoding="utf-8")
    )

    description = strings["options"]["step"]["init"]["data_description"][
        "enable_debug"
    ]
    translated_description = translation["options"]["step"]["init"][
        "data_description"
    ]["enable_debug"]

    assert "raw device handshakes" not in setup
    assert "complete local packet handshakes" not in description
    assert "protocol summaries and data point IDs" in description
    assert "Review logs before sharing" in description
    assert description == translated_description


def test_documented_repository_files_exist() -> None:
    """Keep repository-relative Markdown references from silently rotting."""
    documents = [
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "docs" / "SETUP_GUIDE.md",
        ROOT / "docs" / "APK_LOCAL_PRODUCT_COVERAGE.md",
        ROOT / "custom_components" / "cozylife_local" / "info.md",
    ]

    for document in documents:
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            relative_path = target.split("#", 1)[0]
            if relative_path:
                assert (document.parent / relative_path).is_file(), (
                    f"{document.relative_to(ROOT)} references missing {relative_path}"
                )
