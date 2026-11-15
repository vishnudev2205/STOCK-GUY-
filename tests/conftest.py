import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockguy.config import Config          # noqa: E402
from stockguy.synthetic import SyntheticData  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return Config()


@pytest.fixture(scope="session")
def market(cfg):
    """Made-up market: growers, laggards, bankruptcies, changing index membership."""
    return SyntheticData(cfg, seed=7)
