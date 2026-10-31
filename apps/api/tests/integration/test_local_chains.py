"""Real-chain integration tests (no fakes).

Base:   spins up Anvil, deploys MockUSDC + Agent98Pass with forge, mints with cast, then exercises
        the production EvmRpc client, entitlement reads, supply reads, event indexing, revert
        decoding and the FORENSIC bytecode scan against the real EVM.
Solana: uses a local solana-test-validator that has the agent98_pass program deployed and
        exercised by `contracts/solana` tests (see scripts/integration-local.sh), and checks the
        production SolanaRpc client, config-PDA decoding and Anchor event indexing.

Run: INTEGRATION=1 uv run pytest tests/integration -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("INTEGRATION") != "1", reason="set INTEGRATION=1 to run real-chain tests")

ROOT = Path(__file__).resolve().parents[4]
FOUNDRY = Path(os.environ.get("FOUNDRY_BIN", Path.home() / ".foundry" / "bin"))
ANVIL_PORT = 18545
RPC = f"http://127.0.0.1:{ANVIL_PORT}"
# Anvil default account #0 (publicly known dev key; local chain only).
DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEV_ADDR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
TREASURY = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"


def _bin(name: str) -> str:
    p = FOUNDRY / name
    return str(p) if p.exists() else (shutil.which(name) or name)


def _run(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout


@pytest.fixture(scope="module")
def anvil():
    proc = subprocess.Popen([_bin("anvil"), "--port", str(ANVIL_PORT), "--chain-id", "84532", "--silent"])
    for _ in range(50):
        try:
            httpx.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}, timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    cdir = ROOT / "contracts" / "base"

    def create(contract: str, *ctor: str) -> str:
        args = [_bin("forge"), "create", contract, "--rpc-url", RPC, "--private-key", DEV_KEY, "--broadcast", "--json"]
        if ctor:
            args += ["--constructor-args", *ctor]
        return json.loads(_run(*args, cwd=cdir))["deployedTo"].lower()

    usdc = create("test/mocks/MockUSDC.sol:MockUSDC")
    pass_ = create("src/Agent98Pass.sol:Agent98Pass", usdc, TREASURY, DEV_ADDR, "https://x/{id}.json")
    cast = _bin("cast")
    common = ["--rpc-url", RPC, "--private-key", DEV_KEY]
    _run(cast, "send", usdc, "mint(address,uint256)", DEV_ADDR, "100000000", *common)
    _run(cast, "send", usdc, "approve(address,uint256)", pass_, str(3 * 1_980_000), *common)
    _run(cast, "send", pass_, "mint(uint256)", "3", *common)
    yield {"usdc": usdc, "pass": pass_}
    proc.terminate()


async def test_base_real_rpc_entitlement_supply_index(anvil, monkeypatch):
    from app.chains.evm import EvmRpc
    from app.chains.registry import ChainClients, set_clients
    from app.core.config import get_settings
    from app.db.session import sessionmaker
    from app.services import entitlements as ent
    from app.services import mint as mint_svc

    s = get_settings()
    monkeypatch.setattr(s, "base_pass_address", anvil["pass"])
    monkeypatch.setattr(s, "base_pass_deploy_block", 0)
    evm = EvmRpc(RPC)
    set_clients(ChainClients(evm=evm, solana=None, http=httpx.AsyncClient()))

    n, src = await ent.read_base_passes(DEV_ADDR)
    assert n == 3 and "block" in src
    sup = await mint_svc.base_supply()
    assert sup["available"] and sup["total_minted"] == 3

    # Mine a block so the indexer's 1-block reorg margin includes the mint.
    await evm.request("evm_mine", [])
    async with sessionmaker()() as db:
        assert await mint_svc.index_base_mints(db) == 1
    async with sessionmaker()() as db:
        rows = await mint_svc.mint_history(db, "base", [DEV_ADDR], 10, 0)
    assert rows[0]["quantity"] == 3 and rows[0]["paid_usdc"] == "5.94"
    assert await evm.erc20_balance(anvil["usdc"], TREASURY) == 5_940_000


async def test_base_real_revert_decoding_and_forensic_scan(anvil):
    from app.chains.evm import EvmRpc, RpcError, decode_revert, encode_call
    from app.services.token_risk import scan_base_token

    evm = EvmRpc(RPC)
    with pytest.raises(RpcError) as e:
        await evm.call({"from": TREASURY, "to": anvil["pass"], "data": encode_call("mint(uint256)", ["uint256"], [1])})
    assert "reverted" in decode_revert(e.value.data if isinstance(e.value.data, str) else None)
    with pytest.raises(RpcError):
        await evm.call({"from": DEV_ADDR, "to": anvil["pass"], "data": encode_call("mint(uint256)", ["uint256"], [51])})

    rep = await scan_base_token(evm, anvil["usdc"])
    ids = {f["id"] for f in rep["findings"]}
    assert "fn_mint" in ids  # MockUSDC exposes an unrestricted mint — the scanner must see it
    assert rep["metadata"]["symbol"] == "USDC" and rep["metadata"]["decimals"] == 6


@pytest.fixture(scope="module")
def solana_rpc():
    url = os.environ.get("SOLANA_LOCAL_RPC", "http://127.0.0.1:8899")
    try:
        httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getVersion"}, timeout=2)
    except httpx.HTTPError:
        pytest.skip("local solana-test-validator not running (see scripts/integration-local.sh)")
    return url


async def test_solana_real_rpc_supply_and_event_index(solana_rpc):
    from app.chains.registry import ChainClients, set_clients
    from app.chains.solana import SolanaRpc
    from app.db.session import sessionmaker
    from app.services import mint as mint_svc

    sol = SolanaRpc(solana_rpc)
    set_clients(ChainClients(evm=None, solana=sol, http=httpx.AsyncClient()))
    sup = await mint_svc.solana_supply()
    if not sup["available"]:
        pytest.skip(f"program not initialized on local validator: {sup['reason']}")
    assert sup["total_minted"] > 0
    async with sessionmaker()() as db:
        await mint_svc.index_solana_mints(db, limit=200)
    async with sessionmaker()() as db:
        rows = await mint_svc.mint_history(db, "solana", [], 100, 0)
    assert sum(r["quantity"] for r in rows) == sup["total_minted"]
    assert all(r["paid_usdc"] == f"{r['quantity'] * 1.98:.2f}" for r in rows)
