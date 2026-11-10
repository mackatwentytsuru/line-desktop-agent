import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


import pytest

from line_agent import parse, sendbook

TEST_CHAT = "一括送信"  # tests' own send target (the real tool has no built-in target)
_real_load_config = sendbook.load_config


@pytest.fixture(autouse=True)
def _test_config(monkeypatch):
    """Tests pass their own config: a test chat is allowed unless the test writes its own send_allowlist."""
    def load(home):
        return {"send_allowlist": [TEST_CHAT], **_real_load_config(home)}
    monkeypatch.setattr(sendbook, "load_config", load)
    parse.set_owner_names(["Me"])
    yield
    parse.set_owner_names(None)
