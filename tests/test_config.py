from __future__ import annotations

import yaml
from app.config import load_wallets


def test_placeholder_wallet_is_ignored(tmp_path):
    path = tmp_path / "wallets.yaml"
    path.write_text("wallets:\n  - label: placeholder\n    address: 0x0000000000000000000000000000000000000000\n", encoding="utf-8")
    assert load_wallets(path) == []


def test_real_wallets_load_from_yaml(tmp_path):
    path = tmp_path / "wallets.yaml"
    path.write_text(
        "wallets:\n"
        "  - label: test\n"
        "    source: GMGN\n"
        "    address: '0x5f72eac252211a8ccfde64cc15e2155468139220'\n"
        "    chains: [bsc]\n",
        encoding="utf-8",
    )
    wallets = load_wallets(path)
    assert len(wallets) == 1
    assert wallets[0].address.lower().startswith("0x5f72")
    assert wallets[0].source == "GMGN"
