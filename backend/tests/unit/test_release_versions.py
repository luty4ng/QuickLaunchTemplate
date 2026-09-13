"""The arithmetic behind an automatic release.

`scripts/check_version.py --next` decides which version tomorrow's 2am release
gets, and `version_key` decides whether a version is allowed to ship at all.
Both are pure functions, so they can be pinned here instead of being discovered
by a release that reaches nobody: electron-updater only accepts a version
strictly HIGHER than the running one, so a wrong bump is silent, not loud.
"""

from __future__ import annotations

import pytest

# scripts/ is on sys.path because the test bootstrap imports app.billing.fake,
# which puts it there - the same import the app itself uses to reach the shared
# provider core.
from check_version import next_version, version_key

pytestmark = pytest.mark.unit


class TestNextVersion:
    def test_patch_bumps_the_last_number(self) -> None:
        assert next_version("1.2.1", "patch") == "1.2.2"

    def test_minor_resets_patch(self) -> None:
        assert next_version("1.2.7", "minor") == "1.3.0"

    def test_major_resets_everything_below(self) -> None:
        assert next_version("1.9.9", "major") == "2.0.0"

    def test_double_digits_are_not_string_maths(self) -> None:
        """1.0.9 -> 1.0.10, not 1.0.91 and not 1.0.10 sorting below 1.0.9."""
        assert next_version("1.0.9", "patch") == "1.0.10"
        assert version_key("1.0.10") > version_key("1.0.9")

    def test_the_first_ever_release_starts_at_0_1_0(self) -> None:
        assert next_version(None, "minor") == "0.1.0"
        assert next_version(None, "patch") == "0.0.1"

    def test_a_prerelease_is_bumped_from_its_numeric_core(self) -> None:
        assert next_version("1.4.0-rc.3", "patch") == "1.4.1"

    def test_a_short_version_is_tolerated(self) -> None:
        assert next_version("2", "patch") == "2.0.1"


class TestVersionKey:
    def test_a_release_outranks_its_own_prerelease(self) -> None:
        assert version_key("1.2.0") > version_key("1.2.0-rc.1")

    def test_ordering_is_numeric_not_lexicographic(self) -> None:
        assert version_key("1.10.0") > version_key("1.9.0")
