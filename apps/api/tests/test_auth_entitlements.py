from __future__ import annotations

from tests.conftest import EvmUser, SolUser, login_evm, login_sol
from tests.fakes import PASS


async def test_siwe_login_and_session(client):
    u = await login_evm(client)
    r = await client.get("/v1/auth/session")
    assert r.status_code == 200
    assert r.json()["wallets"][0]["address"] == u.address


async def test_siwe_message_is_server_constructed(client):
    u = EvmUser()
    n = (await client.post("/v1/auth/nonce", json={"chain": "base", "address": u.address})).json()
    assert "Chain ID: 84532" in n["message"]
    assert "does not authorize any transaction" in n["message"]
    assert n["nonce"] in n["message"]


async def test_siwe_wrong_signer_rejected(client):
    u, other = EvmUser(), EvmUser()
    n = (await client.post("/v1/auth/nonce", json={"chain": "base", "address": u.address})).json()
    r = await client.post("/v1/auth/verify", json={"chain": "base", "address": u.address, "nonce": n["nonce"], "signature": other.sign(n["message"])})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_signature"


async def test_nonce_single_use(client):
    u = EvmUser()
    n = (await client.post("/v1/auth/nonce", json={"chain": "base", "address": u.address})).json()
    body = {"chain": "base", "address": u.address, "nonce": n["nonce"], "signature": u.sign(n["message"])}
    assert (await client.post("/v1/auth/verify", json=body)).status_code == 200
    r = await client.post("/v1/auth/verify", json=body)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_nonce"


async def test_nonce_bound_to_address(client):
    u, other = EvmUser(), EvmUser()
    n = (await client.post("/v1/auth/nonce", json={"chain": "base", "address": u.address})).json()
    r = await client.post("/v1/auth/verify", json={"chain": "base", "address": other.address, "nonce": n["nonce"], "signature": other.sign(n["message"])})
    assert r.status_code == 401


async def test_siws_login_and_link_second_wallet(client):
    e = await login_evm(client)
    s = await login_sol(client)  # same cookie jar => linked to same user
    r = await client.get("/v1/auth/session")
    chains = sorted(w["chain"] for w in r.json()["wallets"])
    assert chains == ["base", "solana"]
    assert e.address and s.address


async def test_siws_bad_signature(client):
    s, other = SolUser(), SolUser()
    n = (await client.post("/v1/auth/nonce", json={"chain": "solana", "address": s.address})).json()
    r = await client.post("/v1/auth/verify", json={"chain": "solana", "address": s.address, "nonce": n["nonce"], "signature": other.sign(n["message"])})
    assert r.status_code == 401


async def test_malformed_address_rejected(client):
    r = await client.post("/v1/auth/nonce", json={"chain": "base", "address": "0x1234567890123456789012345678901234567890zz"})
    assert r.status_code in (400, 422)


async def test_logout_revokes_session(client):
    await login_evm(client)
    assert (await client.post("/v1/auth/logout")).status_code == 200
    assert (await client.get("/v1/auth/session")).status_code == 401


async def test_unauthenticated_module_rejected(client):
    r = await client.post("/v1/modules/forensic/run", json={"chain": "base", "target": PASS})
    assert r.status_code == 401


async def test_module_requires_pass(client, chain):
    await login_evm(client)
    r = await client.post("/v1/modules/forensic/run", json={"chain": "base", "target": PASS})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "pass_required"


async def test_entitlement_from_chain_state_and_credits(client, chain):
    u = await login_evm(client)
    chain.evm.passes[u.address] = 2
    r = await client.get("/v1/me/entitlements?refresh=true")
    body = r.json()
    assert body["passes"] == 2 and body["has_access"]
    assert body["credits_granted"] == 1000
    assert "base eth_call balanceOf" in body["by_wallet"][0]["source"]
    r = await client.post("/v1/modules/forensic/run", json={"chain": "base", "target": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"})
    assert r.status_code == 200, r.text
    assert r.json()["credits"]["charged"] == 3
    assert (await client.get("/v1/me/entitlements")).json()["credits_used"] == 3


async def test_fair_use_cap_on_credits(client, chain):
    u = await login_evm(client)
    chain.evm.passes[u.address] = 500  # unlimited passes allowed …
    body = (await client.get("/v1/me/entitlements?refresh=true")).json()
    assert body["passes"] == 500
    assert body["credits_granted"] == 20 * 500  # … but credits are capped (fair use)


async def test_credits_exhaustion_returns_402(client, chain):
    from sqlalchemy import select

    from app.db.models import UsageLedger, User
    from app.db.session import sessionmaker
    from app.services.entitlements import current_period

    u = await login_evm(client)
    chain.evm.passes[u.address] = 1
    await client.get("/v1/me/entitlements?refresh=true")
    async with sessionmaker()() as db:
        user = (await db.execute(select(User))).scalar_one()
        db.add(UsageLedger(user_id=user.id, period=current_period()[0], delta=-499, reason="test"))
        await db.commit()
    r = await client.post("/v1/modules/forensic/run", json={"chain": "base", "target": PASS})
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "credits_exhausted"


async def test_solana_pass_entitlement(client, chain):
    from app.services.entitlements import solana_pass_mint

    s = await login_sol(client)
    chain.solana.token_accounts[s.address] = [{"account": {"data": {"parsed": {"info": {"mint": solana_pass_mint(), "tokenAmount": {"amount": "3", "decimals": 0}}}}}}]
    body = (await client.get("/v1/me/entitlements?refresh=true")).json()
    assert body["passes"] == 3 and body["has_access"]


async def test_client_cannot_self_report_passes(client, chain):
    await login_evm(client)
    r = await client.get("/v1/me/entitlements?refresh=true&passes=99")
    assert r.json()["passes"] == 0


async def test_rate_limit_auth(client):
    from app.core.config import get_settings

    lim = get_settings().rate_limit_auth
    codes = [(await client.post("/v1/auth/nonce", json={"chain": "base", "address": EvmUser().address})).status_code for _ in range(lim + 2)]
    assert 429 in codes


async def test_bad_origin_blocked(client):
    r = await client.post("/v1/auth/nonce", json={"chain": "base", "address": EvmUser().address}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
