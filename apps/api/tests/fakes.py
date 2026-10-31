"""Deterministic in-memory chain providers for CI. They implement the same async interface as
EvmRpc / SolanaRpc so production code paths run unmodified."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak
from solders.hash import Hash

from app.chains.evm import RpcError, selector
from app.core.chains import base_cfg

USDC = base_cfg()["usdc"].lower()
WETH = base_cfg()["weth"].lower()
ROUTER = base_cfg()["uniswapV3"]["swapRouter02"].lower()
QUOTER = base_cfg()["uniswapV3"]["quoterV2"].lower()
ORACLE = "0x420000000000000000000000000000000000000f"
PASS = "0x9898989898989898989898989898989898989898"

S = {name: "0x" + selector(sig).hex() for name, sig in {
    "balanceOf": "balanceOf(address)", "allowance": "allowance(address,address)", "decimals": "decimals()",
    "symbol": "symbol()", "name": "name()", "totalSupply": "totalSupply()", "owner": "owner()",
    "q_single": "quoteExactInputSingle((address,address,uint256,uint24,uint160))", "q_path": "quoteExactInput(bytes,uint256)",
    "multicall": "multicall(uint256,bytes[])", "exactSingle": "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))",
    "pass_balance": "balanceOf(address,uint256)", "totalMinted": "totalMinted()", "l1fee": "getL1FeeUpperBound(uint256)",
    "isValidSignature": "isValidSignature(bytes32,bytes)",
}.items()}

STF = "0x" + (selector("Error(string)") + encode(["string"], ["STF"])).hex()


def _hx(n: int) -> str:
    return hex(n)


class FakeEvm:
    def __init__(self) -> None:
        self.block = 1_000_000
        self.native: dict[str, int] = {}
        self.erc20: dict[str, dict[str, Any]] = {
            USDC: {"decimals": 6, "symbol": "USDC", "name": "USD Coin", "supply": 10**15, "bal": {}, "allow": {}},
            WETH: {"decimals": 18, "symbol": "WETH", "name": "Wrapped Ether", "supply": 10**24, "bal": {}, "allow": {}},
        }
        self.code: dict[str, str] = {USDC: "0x6080", WETH: "0x6080", ROUTER: "0x6080", QUOTER: "0x6080", PASS: "0x6080"}
        self.storage: dict[tuple[str, str], str] = {}
        self.owner: dict[str, str] = {}
        # price: out per 1 unit in (as rational numerator/denominator in base units)
        self.rates: dict[tuple[str, str], tuple[int, int]] = {(USDC, WETH): (3 * 10**14, 10**6), (WETH, USDC): (3300 * 10**6, 10**18)}
        self.fee_tiers = {500}
        self.passes: dict[str, int] = {}
        self.total_minted = 0
        self.txs: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.logs: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.fail_router = False

    async def chain_id(self) -> int:
        return 84532

    async def block_number(self) -> int:
        return self.block

    async def get_balance(self, address: str, block: str = "latest") -> int:
        return self.native.get(address.lower(), 0)

    async def get_code(self, address: str, block: str = "latest") -> str:
        return self.code.get(address.lower(), "0x")

    async def get_storage_at(self, address: str, slot: str, block: str = "latest") -> str:
        return self.storage.get((address.lower(), slot), "0x" + "0" * 64)

    async def get_block(self, number: str = "latest") -> dict[str, Any]:
        return {"number": _hx(self.block), "baseFeePerGas": _hx(10_000_000)}

    async def max_priority_fee(self) -> int:
        return 1_000_000

    async def gas_price(self) -> int:
        return 11_000_000

    async def estimate_gas(self, tx: dict[str, Any]) -> int:
        await self.call(tx)
        return 150_000 if tx["to"].lower() == ROUTER else 50_000

    async def get_logs(self, flt: dict[str, Any]) -> list[dict[str, Any]]:
        frm, to = int(flt["fromBlock"], 16), int(flt["toBlock"], 16)
        out = []
        for lg in self.logs:
            bn = int(lg["blockNumber"], 16)
            if lg["address"].lower() != flt["address"].lower() or not (frm <= bn <= to):
                continue
            ok = True
            for i, t in enumerate(flt.get("topics", [])):
                if t is not None and (i >= len(lg["topics"]) or lg["topics"][i].lower() != t.lower()):
                    ok = False
            if ok:
                out.append(lg)
        return out

    async def get_transaction(self, h: str) -> dict[str, Any] | None:
        return self.txs.get(h)

    async def get_receipt(self, h: str) -> dict[str, Any] | None:
        return self.receipts.get(h)

    async def erc20_balance(self, token: str, owner: str) -> int:
        return self.erc20[token.lower()]["bal"].get(owner.lower(), 0)

    async def erc20_allowance(self, token: str, owner: str, spender: str) -> int:
        return self.erc20[token.lower()]["allow"].get((owner.lower(), spender.lower()), 0)

    async def erc20_metadata(self, token: str) -> dict[str, Any]:
        t = self.erc20.get(token.lower())
        if not t:
            return {"name": None, "symbol": None, "decimals": None, "totalSupply": None}
        return {"name": t["name"], "symbol": t["symbol"], "decimals": t["decimals"], "totalSupply": t["supply"]}

    def _quote(self, tin: str, tout: str, amt: int) -> int:
        r = self.rates.get((tin.lower(), tout.lower()))
        if r is None:
            raise RpcError("execution reverted")
        return amt * r[0] // r[1]

    async def call(self, tx: dict[str, Any], block: str = "latest") -> str:
        self.calls.append(tx)
        to = tx["to"].lower()
        data = tx.get("data", "0x")
        sel, body = data[:10], bytes.fromhex(data[10:])
        if to in self.erc20:
            t = self.erc20[to]
            if sel == S["balanceOf"]:
                (o,) = decode(["address"], body)
                return "0x" + encode(["uint256"], [t["bal"].get(o.lower(), 0)]).hex()
            if sel == S["allowance"]:
                o, sp = decode(["address", "address"], body)
                return "0x" + encode(["uint256"], [t["allow"].get((o.lower(), sp.lower()), 0)]).hex()
            if sel == S["decimals"]:
                return "0x" + encode(["uint8"], [t["decimals"]]).hex()
            if sel == S["symbol"]:
                return "0x" + encode(["string"], [t["symbol"]]).hex()
            if sel == S["name"]:
                return "0x" + encode(["string"], [t["name"]]).hex()
            if sel == S["totalSupply"]:
                return "0x" + encode(["uint256"], [t["supply"]]).hex()
            if sel == S["owner"] and to in self.owner:
                return "0x" + encode(["address"], [self.owner[to]]).hex()
            if data[:10] == "0x" + selector("approve(address,uint256)").hex() or data[:10] == "0x" + selector("transfer(address,uint256)").hex():
                return "0x" + encode(["bool"], [True]).hex()
            raise RpcError("execution reverted")
        if to == QUOTER:
            if sel == S["q_single"]:
                ((tin, tout, amt, fee, _),) = decode(["(address,address,uint256,uint24,uint160)"], body)
                if fee not in self.fee_tiers:
                    raise RpcError("execution reverted")
                out = self._quote(tin, tout, amt)
                return "0x" + encode(["uint256", "uint160", "uint32", "uint256"], [out, 0, 1, 90_000]).hex()
            raise RpcError("execution reverted")
        if to == ROUTER and sel == S["multicall"]:
            if self.fail_router:
                raise RpcError("execution reverted", STF)
            deadline, calls = decode(["uint256", "bytes[]"], body)
            c = calls[0]
            ((tin, tout, fee, rcpt, amt, minout, _),) = decode(["(address,address,uint24,address,uint256,uint256,uint160)"], c[4:])
            frm = tx["from"].lower()
            if int(tx.get("value", "0x0"), 16) == 0:
                if self.erc20[tin.lower()]["allow"].get((frm, ROUTER), 0) < amt or self.erc20[tin.lower()]["bal"].get(frm, 0) < amt:
                    raise RpcError("execution reverted", STF)
            out = self._quote(tin, tout, amt)
            if out < minout:
                raise RpcError("execution reverted", "0x" + (selector("Error(string)") + encode(["string"], ["Too little received"])).hex())
            return "0x" + encode(["bytes[]"], [[encode(["uint256"], [out])]]).hex()
        if to == ORACLE and sel == S["l1fee"]:
            return "0x" + encode(["uint256"], [2_000_000_000]).hex()
        if to == PASS:
            if sel == S["pass_balance"]:
                o, _id = decode(["address", "uint256"], body)
                return "0x" + encode(["uint256"], [self.passes.get(o.lower(), 0)]).hex()
            if sel == S["totalMinted"]:
                return "0x" + encode(["uint256"], [self.total_minted]).hex()
        if self.code.get(to, "0x") == "0x":
            return "0x"
        raise RpcError("execution reverted")

    # helpers for tests -------------------------------------------------------------------
    def broadcast(self, tx: dict[str, Any], *, status: int = 1, mined: bool = True) -> str:
        h = "0x" + keccak(text=f"{tx}{len(self.txs)}").hex()
        self.txs[h] = {"hash": h, "chainId": hex(int(tx["chainId"])), "from": tx["from"], "to": tx["to"], "input": tx["data"], "value": hex(int(tx["value"]))}
        if mined:
            self.mine(h, status)
        return h

    def mine(self, h: str, status: int = 1) -> None:
        tx = self.txs[h]
        self.receipts[h] = {"status": hex(status), "blockNumber": hex(self.block + 1), "gasUsed": hex(100_000), "effectiveGasPrice": hex(11_000_000), "blockHash": "0x" + "ab" * 32}
        if status == 1:
            data = tx["input"]
            sel = data[:10]
            frm = tx["from"].lower()
            if sel == "0x" + selector("approve(address,uint256)").hex():
                sp, amt = decode(["address", "uint256"], bytes.fromhex(data[10:]))
                self.erc20[tx["to"].lower()]["allow"][(frm, sp.lower())] = amt
        self.block += 3


def _b58_sig(seed: str) -> str:
    import base58

    return base58.b58encode(hashlib.sha512(seed.encode()).digest()).decode()


class FakeSolana:
    def __init__(self) -> None:
        self.slot = 300_000_000
        self.height = 280_000_000
        self.lamports: dict[str, int] = {}
        self.accounts: dict[str, dict[str, Any]] = {}
        self.token_accounts: dict[str, list[dict[str, Any]]] = {}
        self.blockhash = str(Hash.new_unique())
        self.sent: list[str] = []
        self.statuses: dict[str, dict[str, Any] | None] = {}
        self.sim_err: Any = None
        self.signatures: dict[str, list[dict[str, Any]]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.auto_confirm = True

    async def get_slot(self, commitment: str = "confirmed") -> int:
        return self.slot

    async def get_balance(self, address: str) -> tuple[int, int]:
        return self.lamports.get(address, 0), self.slot

    async def get_account_info(self, address: str, encoding: str = "jsonParsed") -> tuple[dict[str, Any] | None, int]:
        return self.accounts.get(address), self.slot

    async def get_token_accounts_by_owner(self, owner: str, *, mint: str | None = None, program: str | None = None):
        accs = self.token_accounts.get(owner, [])
        if mint:
            accs = [a for a in accs if a["account"]["data"]["parsed"]["info"]["mint"] == mint]
        return accs, self.slot

    async def get_latest_blockhash(self) -> tuple[str, int, int]:
        return self.blockhash, self.height + 150, self.slot

    async def get_block_height(self) -> int:
        return self.height

    async def simulate_transaction(self, tx_b64: str, account_addresses: list[str] | None = None) -> dict[str, Any]:
        from solders.message import from_bytes_versioned
        from solders.transaction import VersionedTransaction

        vt = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        msg = vt.message
        keys = [str(k) for k in msg.account_keys]
        spent = 5000
        for ix in msg.instructions:
            if keys[ix.program_id_index] == "11111111111111111111111111111111":
                data = bytes(ix.data)
                if data[:4] == (2).to_bytes(4, "little"):
                    spent += int.from_bytes(data[4:12], "little")
        _ = from_bytes_versioned
        accounts = []
        for a in account_addresses or []:
            if a == keys[0]:
                accounts.append({"lamports": self.lamports.get(a, 0) - spent, "owner": "11111111111111111111111111111111"})
            else:
                accounts.append(self.accounts.get(a))
        return {"context": {"slot": self.slot}, "value": {"err": self.sim_err, "logs": ["Program log: ok"], "unitsConsumed": 450, "accounts": accounts}}

    async def send_transaction(self, tx_b64: str) -> str:
        from solders.transaction import VersionedTransaction

        vt = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        sig = str(vt.signatures[0])
        self.sent.append(tx_b64)
        self.statuses[sig] = {"slot": self.slot + 2, "err": None, "confirmationStatus": "confirmed"} if self.auto_confirm else None
        return sig

    async def get_signature_statuses(self, sigs: list[str]) -> list[dict[str, Any] | None]:
        return [self.statuses.get(s) for s in sigs]

    async def get_signatures_for_address(self, address: str, *, limit: int = 100, until: str | None = None, before: str | None = None):
        return self.signatures.get(address, [])[:limit]

    async def get_transaction(self, sig: str) -> dict[str, Any] | None:
        return self.transactions.get(sig)


class FakeHttp:
    """Stands in for httpx.AsyncClient against Jupiter in tests."""

    def __init__(self) -> None:
        self.prices: dict[str, float] = {}

    async def get(self, url: str, params: dict[str, Any] | None = None):
        class R:
            def __init__(self, code: int, body: Any) -> None:
                self.status_code, self._b = code, body

            def json(self) -> Any:
                return self._b

        if url.endswith("/price/v3"):
            mint = (params or {}).get("ids")
            if mint in self.prices:
                return R(200, {mint: {"usdPrice": self.prices[mint]}})
            return R(200, {})
        return R(404, {})

    async def post(self, url: str, json: Any = None):
        class R:
            status_code = 503

            def json(self) -> Any:
                return {}

        return R()
