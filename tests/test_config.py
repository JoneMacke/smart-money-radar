from app.config import load_wallets


def test_placeholder_wallet_is_ignored(tmp_path):
    path = tmp_path / "wallets.yaml"
    path.write_text("wallets:\n  - label: placeholder\n    address: 0x0000000000000000000000000000000000000000\n", encoding="utf-8")
    assert load_wallets(path) == []
