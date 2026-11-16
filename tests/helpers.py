"""Test helpers — every test gets a mock client bound to the frozen fixture set.

fixtures/test/ is a small frozen set chosen for stability, never breadth.
Nothing in it feeds the demo path. A test whose meaning changes with the
weather is not a test.
"""

import json
import pathlib
from unittest.mock import MagicMock

from src.atlas import cache as response_cache
from src.atlas.client import AtlasHTTPError


# Project root — two levels up from tests/
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The frozen test fixture directory — never regenerated, never the demo set.
TEST_FIXTURES = PROJECT_ROOT / "fixtures" / "test"

# A config dict that satisfies AtlasClient without needing .env on disk.
TEST_CONFIG = {
    "ATLAS_BASE_URL": "https://sandbox.atriptech.com",
    "ATLAS_CLIENT_ID": "test-id",
    "ATLAS_CLIENT_SECRET": "test-secret",
}


def load_fixture(key):
    """Load a test fixture file directly and return parsed JSON.

    Does NOT populate the response cache. Use when you need raw fixture
    data without cache side-effects.
    """
    path = TEST_FIXTURES / (key.replace(":", "/") + ".json")
    if not path.is_file():
        raise FileNotFoundError("No fixture for key: %s" % key)
    with open(path) as f:
        return json.load(f)


def _fixture_post(endpoint, payload, fixture_key=None, allow_error=False):
    """Serve a test fixture — used as mock side_effect for AtlasClient.post.

    Reads from fixtures/test/, populates the response cache, and raises
    AtlasHTTPError on missing keys (unless allow_error=True).
    """
    if not fixture_key:
        return None
    path = TEST_FIXTURES / (fixture_key.replace(":", "/") + ".json")
    if not path.is_file():
        if allow_error:
            return None
        raise AtlasHTTPError(
            404,
            "No fixture for key: %s" % fixture_key,
            str(path))
    with open(path) as f:
        data = json.load(f)
    response_cache.put(fixture_key, data)
    return data


def make_client():
    """Return a MagicMock whose .post() serves fixtures/test/.

    Every test uses this. No test constructs a client by hand and drifts
    onto the demo fixtures. The mock has the same .post() signature as
    AtlasClient, so production code works unchanged.
    """
    client = MagicMock()
    client.post = MagicMock(side_effect=_fixture_post)
    return client
