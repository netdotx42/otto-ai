from __future__ import annotations

import base64
import hashlib
import struct

from eth_abi import encode
from eth_utils import keccak

from tests.conftest import login_evm
from tests.fakes import PASS, USDC, WETH

TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()


def _topic(a: str) -> str:
    return "0x" + "0" * 24 + a[2:].lower()


async def _pass_user(client, chain):
    u = await login_evm(client)
    chain.evm.passes[u.address] = 1
    chain.evm.native[u.address] = 10**18
    return u


async def test_module_catalog_has_ten_typed_modules(client):
    mods = (await client.get("/v1/modules")).json()["modules"]
    assert [m["name"] for m in mods] == ["sentinel", "forensic", "whale", "alpha", "simulator", "portfolio", "autopilot", "builder", "intel", "market"]
    assert all("input_schema" in m and m["tools"] is not None for m in mods)


async def test_whale_detects_large_transfers_and_clusters(client, chain):
    await _pass_user(client, chain)
    a, b = "0x" + "a1" * 20, "0x" + "b2" * 20
    chain.evm.logs = [
        {"address": USDC, "topics": [TRANSFER, _topic(a), _topic(b)], "data": "0x" + encode(["uint256"], [50_000 * 10**6]).hex(), "blockNumber": hex(chain.evm.block - 5), "transactionHash": "0x" + "01" * 32, "logIndex": "0x0"},
        {"address": USDC, "topics": [TRANSFER, _topic(b), _topic(a)], "data": "0x" + encode(["uint256"], [5 * 10**6]).hex(), "blockNumber": hex(chain.evm.block - 4), "transactionHash": "0x" + "02" * 32, "logIndex": "0x0"},
    ]
    r = await client.post("/v1/modules/whale/run", json={"chain": "base", "token": USDC, "min_amount": "10000"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert len(d["large_transfers"]) == 1 and d["large_transfers"][0]["amount"] == "50000"
    assert d["clusters"][0]["address"] in (a, b)
    assert r.json()["evidence"][0]["method"] == "eth_getLogs Transfer"


async def test_alpha_flags_volume_spike(client, chain):
    await _pass_user(client, chain)
    head = chain.evm.block
    logs = []
    for i in range(11):  # steady baseline: 2 transfers per bucket
        for j in range(2):
            logs.append({"address": USDC, "topics": [TRANSFER, _topic("0x" + f"{i:02x}" * 20), _topic("0x" + "99" * 20)], "data": "0x" + encode(["uint256"], [10**6]).hex(), "blockNumber": hex(head - 1800 + i * 150 + j), "transactionHash": "0x" + "03" * 32, "logIndex": hex(j)})
    for j in range(40):  # spike in the last bucket
        logs.append({"address": USDC, "topics": [TRANSFER, _topic("0x" + f"{j + 50:02x}" * 20), _topic("0x" + "99" * 20)], "data": "0x" + encode(["uint256"], [10**9]).hex(), "blockNumber": hex(head - 10), "transactionHash": "0x" + "04" * 32, "logIndex": hex(j)})
    chain.evm.logs = logs
    r = await client.post("/v1/modules/alpha/run", json={"chain": "base", "token": USDC})
    d = r.json()["data"]
    assert "transfer_count" in d["anomalies"] and "volume" in d["anomalies"]


async def test_portfolio_prices_only_from_trusted_sources(client, chain):
    u = await _pass_user(client, chain)
    chain.evm.erc20[USDC]["bal"][u.address] = 25 * 10**6
    out = (await client.post("/v1/modules/portfolio/run", json={})).json()
    hs = {h["symbol"]: h for h in out["data"]["holdings"]}
    assert hs["USDC"]["usd"] == 25.0 and hs["USDC"]["price_source"] == "stablecoin par"
    assert hs["ETH"]["usd"] == 3300.0  # executable Uniswap quote, 1 ETH → 3300 USDC in the fake pool
    assert out["data"]["total_usd_priced"] == 3325.0


async def test_portfolio_unpriced_marked_unavailable(client, chain):
    await _pass_user(client, chain)
    chain.evm.rates.clear()
    out = (await client.post("/v1/modules/portfolio/run", json={})).json()
    eth = next(h for h in out["data"]["holdings"] if h["symbol"] == "ETH")
    assert eth["usd"] is None
    assert any("No trusted price" in w for w in out["warnings"])


async def test_intel_routes_to_forensic_with_evidence(client, chain):
    await _pass_user(client, chain)
    r = await client.post("/v1/modules/intel/run", json={"question": f"Is {WETH} safe to buy?"})
    body = r.json()
    assert body["data"]["plan"] == ["forensic"]
    assert body["evidence"]


async def test_sentinel_creates_rule(client, chain):
    await _pass_user(client, chain)
    r = await client.post("/v1/modules/sentinel/run", json={"chain": "base", "target": WETH, "kind": "contract", "create_rule": True})
    assert r.json()["data"]["rule_id"]


# ----------------------------------------------------------------------------- workflows
def _wf(wallet, **over):
    d = {"name": "Whale watch", "trigger": {"type": "wallet_large_transfer", "chain": "base", "wallet": wallet, "token": USDC, "min_amount": "1000"},
         "actions": [{"type": "forensic_scan"}, {"type": "alert", "severity": "warning"}]}
    d.update(over)
    return d


async def test_workflow_validation_rejects_unapproved_actions(client):
    r = await client.post("/v1/workflows/validate", json={"definition": _wf("0x" + "11" * 20, actions=[{"type": "send_funds", "to": "0x0"}])})
    assert r.json()["valid"] is False


async def test_workflow_versioning_revocation(client, chain):
    u = await _pass_user(client, chain)
    w = (await client.post("/v1/workflows", json={"definition": _wf(u.address)})).json()
    assert w["version"] == 1
    w2 = (await client.put(f"/v1/workflows/{w['id']}", json={"definition": _wf(u.address, name="Whale watch v2")})).json()
    assert w2["version"] == 2
    detail = (await client.get(f"/v1/workflows/{w['id']}")).json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    assert sum(r["enabled"] for r in detail["rules"]) == 1
    r = await client.post(f"/v1/workflows/{w['id']}/status", json={"status": "revoked"})
    assert r.json()["status"] == "revoked"
    assert (await client.put(f"/v1/workflows/{w['id']}", json={"definition": _wf(u.address)})).status_code == 409


async def test_workflow_requires_pass(client, chain):
    u = await login_evm(client)
    r = await client.post("/v1/workflows", json={"definition": _wf(u.address)})
    assert r.status_code == 403


async def test_monitoring_fires_and_prepare_transaction_pauses_for_approval(client, chain):
    from app.db.session import sessionmaker
    from app.services.monitoring import evaluate_all

    u = await _pass_user(client, chain)
    chain.evm.erc20[USDC]["bal"][u.address] = 100 * 10**6
    d = _wf(u.address, actions=[{"type": "prepare_transaction", "request": "swap 5 USDC into WETH", "wallet": u.address}, {"type": "alert", "severity": "info"}])
    w = (await client.post("/v1/workflows", json={"definition": d})).json()
    async with sessionmaker()() as db:
        await evaluate_all(db, chain)  # sets cursor
    chain.evm.logs.append({"address": USDC, "topics": [TRANSFER, _topic(u.address), _topic("0x" + "22" * 20)], "data": "0x" + encode(["uint256"], [5000 * 10**6]).hex(), "blockNumber": hex(chain.evm.block + 1), "transactionHash": "0x" + "05" * 32, "logIndex": "0x0"})
    chain.evm.block += 2
    async with sessionmaker()() as db:
        fired = await evaluate_all(db, chain)
    assert fired == 1
    alerts = (await client.get("/v1/alerts")).json()["items"]
    assert "awaiting your approval" in alerts[0]["title"]
    acts = (await client.get("/v1/activity")).json()["items"]
    assert acts[0]["status"] == "awaiting_approval"  # never auto-signed / submitted
    assert all(s["submissions"] == [] for s in acts[0]["steps"])
    assert w["id"]


async def test_prepare_tx_must_target_verified_wallet(client, chain):
    u = await _pass_user(client, chain)
    d = _wf(u.address, actions=[{"type": "prepare_transaction", "request": "swap 5 USDC into WETH", "wallet": "0x" + "33" * 20}])
    r = await client.post("/v1/workflows", json={"definition": d})
    assert r.status_code == 403


async def test_marketplace_install_pins_hash(client, chain):
    from app.db.models import MarketplaceTemplate
    from app.db.session import sessionmaker

    u = await _pass_user(client, chain)
    async with sessionmaker()() as db:
        db.add(MarketplaceTemplate(slug="t1", name="Template", description="A test template.", module="autopilot", definition=_wf("0x" + "01" * 20), definition_hash="a" * 64, official=True))
        await db.commit()
    t = (await client.get("/v1/marketplace/templates")).json()["items"][0]
    r = await client.post(f"/v1/marketplace/templates/{t['id']}/install", json={"expected_hash": "b" * 64, "overrides": {"wallet": u.address}})
    assert r.status_code == 409
    r = await client.post(f"/v1/marketplace/templates/{t['id']}/install", json={"expected_hash": "a" * 64, "overrides": {"wallet": u.address}})
    assert r.status_code == 200, r.text
    r = await client.post(f"/v1/marketplace/templates/{t['id']}/install", json={"expected_hash": "a" * 64, "overrides": {"actions": []}})
    assert r.status_code == 422


async def test_community_template_cannot_prepare_transactions(client, chain):
    u = await _pass_user(client, chain)
    d = _wf(u.address, actions=[{"type": "prepare_transaction", "request": "swap 5 USDC into WETH", "wallet": u.address}])
    r = await client.post("/v1/marketplace/templates", json={"slug": "evil-tpl", "name": "Evil", "description": "prepares swaps for you", "module": "autopilot", "definition": d})
    assert r.status_code == 422


# ----------------------------------------------------------------------------- mint
async def test_supply_reads_verified_contract_state(client, chain):
    chain.evm.total_minted = 12_345
    body = (await client.get("/v1/mint/supply")).json()
    base = next(c for c in body["chains"] if c["chain"] == "base")
    assert base["total_minted"] == 12_345 and "totalMinted()" in base["source"]
    assert body["aggregate_cap"] is None and body["price_usdc"] == "1.98"
    sol = next(c for c in body["chains"] if c["chain"] == "solana")
    assert sol["available"] is False  # program not initialized on the fake cluster: no invented numbers


async def test_solana_supply_decodes_config_pda(client, chain):
    from app.services.entitlements import solana_config_pda

    raw = b"\x00" * 8 + b"\x01" * 32 * 5 + struct.pack("<Q", 777) + b"\x00\xff\xfe"
    chain.solana.accounts[solana_config_pda()] = {"owner": "ARd3pfR6GHqpLYr7sacHgjNwwagkzN15qmoq2GnHyw2Z", "data": [base64.b64encode(raw).decode(), "base64"]}
    body = (await client.get("/v1/mint/supply")).json()
    sol = next(c for c in body["chains"] if c["chain"] == "solana")
    assert sol["total_minted"] == 777


async def test_solana_supply_rejects_spoofed_owner(client, chain):
    from app.services.entitlements import solana_config_pda

    raw = b"\x00" * 8 + b"\x01" * 160 + struct.pack("<Q", 10**9) + b"\x00\xff\xfe"
    chain.solana.accounts[solana_config_pda()] = {"owner": "11111111111111111111111111111111", "data": [base64.b64encode(raw).decode(), "base64"]}
    sol = next(c for c in (await client.get("/v1/mint/supply")).json()["chains"] if c["chain"] == "solana")
    assert sol["available"] is False


async def test_index_base_mint_events_idempotent(client, chain):
    from app.db.session import sessionmaker
    from app.services.mint import PASS_MINTED_TOPIC, index_base_mints

    buyer = "0x" + "be" * 20
    chain.evm.logs = [{"address": PASS, "topics": [PASS_MINTED_TOPIC, _topic(buyer)], "data": "0x" + encode(["uint256", "uint256", "uint256"], [3, 5_940_000, 3]).hex(), "blockNumber": hex(chain.evm.block - 10), "transactionHash": "0x" + "aa" * 32, "logIndex": "0x1"}]
    from app.core.config import get_settings

    get_settings().base_pass_deploy_block = chain.evm.block - 100
    async with sessionmaker()() as db:
        assert await index_base_mints(db) == 1
    async with sessionmaker()() as db:
        from app.db.models import IndexerCursor

        await db.delete(await db.get(IndexerCursor, "base_mints"))
        await db.commit()
        assert await index_base_mints(db) == 0  # duplicate log ignored
    h = (await client.get("/v1/mint/history")).json()["items"]
    assert h[0]["quantity"] == 3 and h[0]["paid_usdc"] == "5.94" and h[0]["explorer_url"].endswith("aa" * 32)


def test_parse_anchor_event_only_matching_discriminator():
    from solders.pubkey import Pubkey

    from app.services.mint import parse_anchor_pass_minted

    disc = hashlib.sha256(b"event:PassMinted").digest()[:8]
    buyer = Pubkey.new_unique()
    good = base64.b64encode(disc + bytes(buyer) + struct.pack("<QQQ", 2, 3_960_000, 10)).decode()
    fake = base64.b64encode(b"\x00" * 8 + bytes(buyer) + struct.pack("<QQQ", 999, 0, 0)).decode()
    evs = parse_anchor_pass_minted([f"Program data: {good}", f"Program data: {fake}", "Program log: hi"])
    assert evs == [{"buyer": str(buyer), "quantity": 2, "paid": 3_960_000, "total_minted": 10}]


# ----------------------------------------------------------------------------- terminal
async def test_terminal_stream_transaction_preview(client, chain):
    u = await _pass_user(client, chain)
    chain.evm.erc20[USDC]["bal"][u.address] = 100 * 10**6
    c = (await client.post("/v1/terminal/conversations", json={"title": "t"})).json()
    r = await client.post(f"/v1/terminal/conversations/{c['id']}/stream", json={"prompt": "Prepare a 5 USDC swap into WETH", "chain": "base"})
    assert r.status_code == 200
    events = [line.split(": ", 1)[1] for line in r.text.splitlines() if line.startswith("event: ")]
    assert events[0] == "tool_start"
    assert "execution" in events and events[-1] == "done"
    text = "".join(__import__("json").loads(line[6:])["text"] for line in r.text.splitlines() if line.startswith("data: ") and '"text"' in line)
    assert "ACTION: Approve exactly 5 USDC" in text and "STATUS: Awaiting Approval" in text
    msgs = (await client.get(f"/v1/terminal/conversations/{c['id']}/messages")).json()["items"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["payload"]["execution_id"]


async def test_terminal_research_path(client, chain):
    await _pass_user(client, chain)
    c = (await client.post("/v1/terminal/conversations", json={})).json()
    r = await client.post(f"/v1/terminal/conversations/{c['id']}/stream", json={"prompt": f"Analyze this contract {WETH} before I approve anything", "chain": "base"})
    assert "event: evidence" in r.text and "event: detected" in r.text
