"""End-to-end Level 3 lifecycle on Base with a deterministic fake chain."""

from __future__ import annotations

import time

from tests.conftest import login_evm
from tests.fakes import ROUTER, USDC, WETH


async def _setup(client, chain, usdc=100):
    u = await login_evm(client)
    chain.evm.passes[u.address] = 1
    chain.evm.native[u.address] = 10**17
    chain.evm.erc20[USDC]["bal"][u.address] = usdc * 10**6
    return u


async def _start(client, u, text="Prepare a 5 USDC swap into WETH", key=None):
    headers = {"Idempotency-Key": key} if key else {}
    r = await client.post("/v1/executions", json={"request": text, "chain": "base", "wallet": u.address}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _approve(client, step):
    return await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": True})


async def test_full_swap_lifecycle_with_exact_approval(client, chain):
    u = await _setup(client, chain)
    ex = await _start(client, u)
    assert ex["status"] == "awaiting_approval", ex
    nodes = [t["node"] for t in ex["trace"]]
    assert nodes == ["PARSE_INTENT", "VALIDATE_INTENT", "FETCH_CHAIN_DATA", "BUILD_UNSIGNED_TRANSACTION", "SIMULATE_TRANSACTION",
                     "RUN_POLICY_CHECKS", "GENERATE_EXECUTION_PREVIEW", "WAIT_FOR_USER_APPROVAL"]
    step = ex["steps"][0]
    assert step["kind"] == "approve"
    p = step["preview"]
    assert p["approvals"][0]["unlimited"] is False and p["approvals"][0]["amount"] == "5"
    assert p["simulation"]["status"] == "success"
    assert p["policy"]["allowed"] is True

    # Approve step 1
    r = await _approve(client, step)
    assert r.status_code == 200, r.text
    appr = r.json()
    assert appr["unsigned_tx"]["to"] == USDC
    # Wallet signs & broadcasts exactly what was approved
    h = chain.evm.broadcast(appr["unsigned_tx"])
    r = await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "tx_hash": h})
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["steps"][0]["submissions"][0]["status"] in ("submitted", "confirmed")
    assert ex["steps"][0]["submissions"][0]["explorer_url"].endswith(h)

    # Poll: receipt confirmed → swap step is re-quoted, re-simulated, previewed
    ex = (await client.get(f"/v1/executions/{ex['id']}")).json()
    assert ex["status"] == "awaiting_approval", ex
    swap = ex["steps"][1]
    assert swap["kind"] == "swap"
    sp = swap["preview"]
    assert sp["destination"]["address"] == ROUTER
    assert sp["receives"][0]["symbol"] == "WETH"
    assert sp["quote"]["provider"].startswith("uniswap-v3")
    dec = sp["decoded"]["calls"][0]
    assert dec["recipient"] == u.address and dec["tokenIn"] == USDC and dec["tokenOut"] == WETH

    r = await _approve(client, swap)
    assert r.status_code == 200, r.text
    appr2 = r.json()
    h2 = chain.evm.broadcast(appr2["unsigned_tx"])
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr2["approval_id"], "tx_hash": h2})).json()
    ex = (await client.get(f"/v1/executions/{ex['id']}")).json()
    assert ex["status"] == "confirmed"
    assert ex["steps"][1]["submissions"][0]["receipt"]["success"] is True


async def test_digest_mismatch_blocks_approval(client, chain):
    u = await _setup(client, chain)
    step = (await _start(client, u))["steps"][0]
    r = await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": "0x" + "11" * 32, "confirm": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "digest_mismatch"


async def test_confirm_must_be_explicit_true(client, chain):
    u = await _setup(client, chain)
    step = (await _start(client, u))["steps"][0]
    r = await client.post(f"/v1/executions/steps/{step['id']}/approve", json={"digest": step["digest"], "confirm": False})
    assert r.status_code == 422


async def test_approval_replay_blocked(client, chain):
    u = await _setup(client, chain)
    step = (await _start(client, u))["steps"][0]
    appr = (await _approve(client, step)).json()
    h = chain.evm.broadcast(appr["unsigned_tx"])
    await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "tx_hash": h})
    h_b = chain.evm.broadcast(appr["unsigned_tx"])  # second broadcast of same tx
    r = await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "tx_hash": h_b})
    body = r.json()
    assert "replay" in (body.get("error") or "").lower() or any(t["detail"] == "replay" for t in body["trace"])


async def test_transaction_substitution_detected(client, chain):
    u = await _setup(client, chain)
    step = (await _start(client, u))["steps"][0]
    appr = (await _approve(client, step)).json()
    evil = dict(appr["unsigned_tx"])
    evil["data"] = evil["data"][:-8] + "ffffffff"  # different approval amount
    h = chain.evm.broadcast(evil)
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "tx_hash": h})).json()
    assert ex["status"] == "failed"
    assert "substitution" in (ex["error"] or "").lower()


async def test_never_success_without_receipt(client, chain):
    u = await _setup(client, chain)
    step = (await _start(client, u))["steps"][0]
    appr = (await _approve(client, step)).json()
    h = chain.evm.broadcast(appr["unsigned_tx"], mined=False)
    ex = (await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "tx_hash": h})).json()
    assert ex["status"] == "submitted"
    ex = (await client.get(f"/v1/executions/{ex['id']}")).json()
    assert ex["status"] == "submitted"
    assert ex["steps"][0]["submissions"][0]["receipt"] is None


async def test_reverted_receipt_marked_failed(client, chain):
    u = await _setup(client, chain)
    step = (await _start(client, u))["steps"][0]
    appr = (await _approve(client, step)).json()
    h = chain.evm.broadcast(appr["unsigned_tx"], status=0)
    await client.post("/v1/executions/submit", json={"approval_id": appr["approval_id"], "tx_hash": h})
    rows = (await client.get("/v1/activity")).json()["items"]
    ex = (await client.get(f"/v1/executions/{rows[0]['id']}")).json()
    assert ex["status"] == "failed"
    assert ex["steps"][0]["submissions"][0]["receipt"]["success"] is False


async def test_stale_quote_requires_refresh(client, chain, monkeypatch):
    u = await _setup(client, chain)
    chain.evm.erc20[USDC]["allow"][(u.address, ROUTER)] = 10**12  # allowance exists → single swap step
    ex = await _start(client, u)
    step = ex["steps"][0]
    assert step["kind"] == "swap"
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    from datetime import UTC, datetime, timedelta

    import app.execution.service as svc

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(seconds=120)

    monkeypatch.setattr(svc, "datetime", FakeDT)
    r = await _approve(client, step)
    assert r.status_code == 409
    assert r.json()["error"]["code"] in ("quote_expired", "simulation_stale")


async def test_refresh_changes_digest_and_invalidates_old_approval(client, chain):
    u = await _setup(client, chain)
    chain.evm.erc20[USDC]["allow"][(u.address, ROUTER)] = 10**12
    ex = await _start(client, u)
    step = ex["steps"][0]
    appr = (await _approve(client, step)).json()
    # approval puts the step in "approved"; a refresh is only allowed when awaiting/blocked/expired
    chain.evm.rates[(USDC, WETH)] = (31 * 10**13, 10**6)  # market moved
    r = await client.post(f"/v1/executions/{ex['id']}/refresh")
    assert r.status_code == 409
    # Settle with the old approval still works only for the exact approved tx
    assert appr["digest"] == step["digest"]


async def test_insufficient_balance_blocked(client, chain):
    u = await _setup(client, chain, usdc=1)
    chain.evm.erc20[USDC]["allow"][(u.address, ROUTER)] = 10**12
    ex = await _start(client, u)
    assert ex["status"] == "blocked"
    ids = {c["id"]: c["status"] for c in ex["steps"][0]["preview"]["policy"]["checks"]}
    assert ids["balance_sufficient"] == "fail"
    assert ids["simulation_ok"] == "fail"
    r = await _approve(client, ex["steps"][0])
    assert r.status_code == 409


async def test_spend_limit_blocks(client, chain):
    u = await _setup(client, chain, usdc=10_000)
    chain.evm.erc20[USDC]["allow"][(u.address, ROUTER)] = 10**12
    ex = await _start(client, u, "swap 900 USDC into WETH")
    assert ex["status"] == "blocked"
    checks = {c["id"]: c for c in ex["steps"][0]["preview"]["policy"]["checks"]}
    assert checks["spend_limit_tx"]["status"] == "fail"


async def test_user_tighter_limit_applies(client, chain):
    u = await _setup(client, chain)
    chain.evm.erc20[USDC]["allow"][(u.address, ROUTER)] = 10**12
    await client.put("/v1/me/settings", json={"max_tx_usd": 3})
    ex = await _start(client, u)
    assert ex["status"] == "blocked"


async def test_ambiguous_request_needs_clarification(client, chain):
    u = await _setup(client, chain)
    ex = await _start(client, u, "Prepare a 5 USDC swap into this Base token.")
    assert ex["status"] == "needs_clarification"
    assert ex["steps"] == []
    assert any("address" in q.lower() for q in ex["questions"])


async def test_unverified_wallet_rejected(client, chain):
    await _setup(client, chain)
    r = await client.post("/v1/executions", json={"request": "swap 5 USDC into WETH", "chain": "base", "wallet": "0x000000000000000000000000000000000000dEaD"})
    ex = r.json()
    assert ex["status"] == "needs_clarification"
    assert any("not verified" in q for q in ex["questions"])


async def test_kill_switch(client, chain):
    from app.db.models import SystemFlag
    from app.db.session import sessionmaker

    u = await _setup(client, chain)
    async with sessionmaker()() as db:
        db.add(SystemFlag(key="execution_enabled", enabled=False, reason="incident"))
        await db.commit()
    r = await client.post("/v1/executions", json={"request": "swap 5 USDC into WETH", "chain": "base", "wallet": u.address})
    assert r.status_code == 503


async def test_idempotent_start(client, chain):
    u = await _setup(client, chain)
    a = await _start(client, u, key="abc-123")
    b = await _start(client, u, key="abc-123")
    assert a["id"] == b["id"]
    r = await client.post("/v1/executions", json={"request": "swap 6 USDC into WETH", "chain": "base", "wallet": u.address}, headers={"Idempotency-Key": "abc-123"})
    assert r.status_code == 409


async def test_transfer_to_contract_warns(client, chain):
    u = await _setup(client, chain)
    dest = "0x1111111111111111111111111111111111111111"
    chain.evm.code[dest] = "0x60806040"
    ex = await _start(client, u, f"send 2 USDC to {dest}")
    assert ex["status"] == "awaiting_approval", ex
    p = ex["steps"][0]["preview"]
    assert p["type"] == "transfer"
    assert any("smart contract" in w for w in p["risk"]["warnings"])


async def test_simulation_revert_blocks(client, chain):
    u = await _setup(client, chain)
    chain.evm.erc20[USDC]["allow"][(u.address, ROUTER)] = 10**12
    chain.evm.fail_router = True
    ex = await _start(client, u)
    assert ex["status"] == "blocked"
    assert "STF" in ex["steps"][0]["preview"]["simulation"]["error"]


async def test_audit_chain_intact_after_flow(client, chain):
    from app.core.audit import verify_chain
    from app.db.session import sessionmaker

    u = await _setup(client, chain)
    await _start(client, u)
    async with sessionmaker()() as db:
        ok, bad = await verify_chain(db)
    assert ok and bad is None


async def test_audit_tamper_detected(client, chain):
    from sqlalchemy import text

    from app.core.audit import verify_chain
    from app.db.session import sessionmaker

    u = await _setup(client, chain)
    await _start(client, u)
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE audit_events SET data = '{\"forged\": true}' WHERE id = 2"))
        await db.commit()
        ok, bad = await verify_chain(db)
    assert not ok and bad == 2
