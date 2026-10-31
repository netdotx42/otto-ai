"""Solana Level 3 lifecycle: build (solders) → simulate → approve → user-signed → broadcast → confirm."""

from __future__ import annotations

import base64

from solders.message import from_bytes_versioned, to_bytes_versioned
from solders.transaction import VersionedTransaction

from tests.conftest import login_sol


def sign(user, unsigned_b64: str) -> str:
    vt = VersionedTransaction.from_bytes(base64.b64decode(unsigned_b64))
    signed = VersionedTransaction(vt.message, [user.kp])
    return base64.b64encode(bytes(signed)).decode()


async def _setup(client, chain):
    from app.services.entitlements import solana_pass_mint

    u = await login_sol(client)
    chain.solana.lamports[u.address] = 5 * 10**9
    chain.solana.token_accounts[u.address] = [{"account": {"data": {"parsed": {"info": {"mint": solana_pass_mint(), "tokenAmount": {"amount": "1", "decimals": 0}}}}}}]
    chain.http.prices["So11111111111111111111111111111111111111112"] = 150.0
    return u


DEST = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"


async def _start(client, u, text):
    r = await client.post("/v1/executions", json={"request": text, "chain": "solana", "wallet": u.address})
    assert r.status_code == 200, r.text
    return r.json()


async def test_sol_transfer_lifecycle(client, chain):
    u = await _setup(client, chain)
    ex = await _start(client, u, f"send 0.5 SOL to {DEST}")
    assert ex["status"] == "awaiting_approval", ex
    step = ex["steps"][0]
    p = step["preview"]
    assert p["action"].startswith("Transfer 0.5 SOL")
    ixs = p["decoded"]["instructions"]
    assert [i["program_name"] for i in ixs] == ["System Program"]
    assert ixs[0]["lamports"] == 500_000_000
    sol_delta = next(c for c in p["simulation"]["balance_changes"] if c["symbol"] == "SOL")
    assert sol_delta["delta"] == "-0.500005"
    appr = (await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": True})).json()
    signed = sign(u, appr["unsigned_tx"]["transaction"])
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "signed_tx": signed})).json()
    assert ex["status"] == "confirmed", ex
    sub = ex["steps"][0]["submissions"][0]
    assert sub["receipt"]["confirmation"] == "confirmed"
    assert "cluster=devnet" in sub["explorer_url"]


async def test_sol_signed_message_substitution_rejected(client, chain):
    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    u = await _setup(client, chain)
    ex = await _start(client, u, f"send 0.5 SOL to {DEST}")
    step = ex["steps"][0]
    appr = (await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": True})).json()
    attacker = Pubkey.new_unique()
    msg = MessageV0.try_compile(u.kp.pubkey(), [transfer(TransferParams(from_pubkey=u.kp.pubkey(), to_pubkey=attacker, lamports=4 * 10**9))], [], Hash.from_string(chain.solana.blockhash))
    evil = base64.b64encode(bytes(VersionedTransaction(msg, [u.kp]))).decode()
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "signed_tx": evil})).json()
    assert ex["status"] == "failed"
    assert "substitution" in ex["error"].lower()
    assert chain.solana.sent == []  # never broadcast


async def test_sol_unsigned_or_forged_signature_rejected(client, chain):
    u = await _setup(client, chain)
    ex = await _start(client, u, f"send 0.5 SOL to {DEST}")
    step = ex["steps"][0]
    appr = (await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": True})).json()
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "signed_tx": appr["unsigned_tx"]["transaction"]})).json()
    assert ex["status"] == "failed"
    assert chain.solana.sent == []


async def test_wallet_disconnect_during_signing_expires(client, chain, monkeypatch):
    """User approved, wallet disconnected; the approval lapses and cannot be used later."""
    from datetime import UTC, datetime, timedelta

    import app.execution.graph as g

    u = await _setup(client, chain)
    ex = await _start(client, u, f"send 0.5 SOL to {DEST}")
    step = ex["steps"][0]
    appr = (await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": True})).json()

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(minutes=10)

    monkeypatch.setattr(g, "datetime", Later)
    signed = sign(u, appr["unsigned_tx"]["transaction"])
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "signed_tx": signed})).json()
    assert ex["status"] == "expired"
    assert chain.solana.sent == []


async def test_sol_simulation_program_error_blocks(client, chain):
    u = await _setup(client, chain)
    chain.solana.sim_err = {"InstructionError": [0, {"Custom": 1}]}
    ex = await _start(client, u, f"send 0.5 SOL to {DEST}")
    assert ex["status"] == "blocked"
    assert "program error" in ex["steps"][0]["preview"]["simulation"]["error"]


async def test_sol_swap_unavailable_on_devnet(client, chain):
    u = await _setup(client, chain)
    ex = await _start(client, u, "Simulate selling 25% of my SOL position into USDC")
    assert ex["status"] == "rejected"
    assert "jupiter" in (ex["error"] or ex["status_reason"] or "").lower()


async def test_sol_dropped_after_expiry_not_success(client, chain):
    u = await _setup(client, chain)
    chain.solana.auto_confirm = False
    ex = await _start(client, u, f"send 0.5 SOL to {DEST}")
    step = ex["steps"][0]
    appr = (await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": True})).json()
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "signed_tx": sign(u, appr["unsigned_tx"]["transaction"])})).json()
    assert ex["status"] == "submitted"
    chain.solana.height += 1000  # blockhash expired, never landed
    ex = (await client.get(f"/v1/executions/{ex['id']}")).json()
    assert ex["status"] == "failed"
    assert "expired" in ex["status_reason"].lower()


def test_message_roundtrip_digest_stable():
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.system_program import TransferParams, transfer

    from app.execution.digest import solana_digest

    kp = Keypair()
    msg = MessageV0.try_compile(kp.pubkey(), [transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=kp.pubkey(), lamports=1))], [], Hash.new_unique())
    b = to_bytes_versioned(msg)
    assert to_bytes_versioned(from_bytes_versioned(b)) == b
    assert solana_digest(base64.b64encode(b).decode()).startswith("0x")
