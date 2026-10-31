"""SENTINEL, FORENSIC, WHALE, ALPHA, PORTFOLIO — read-only intelligence modules."""

from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import Decimal
from typing import Any, Literal

from eth_utils import keccak
from pydantic import BaseModel, Field, field_validator

from app.auth.messages import normalize_address
from app.chains.evm import RpcError
from app.core.chains import base_cfg, solana_cfg, tokens
from app.core.config import get_settings
from app.core.errors import APIError
from app.db.models import MonitoringRule, TokenScan
from app.modules.base import Evidence, Module, ModuleContext, ModuleOutput
from app.providers import uniswap_v3 as uni
from app.services.token_risk import scan_base_token, scan_solana_mint

TRANSFER_TOPIC = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
MAX_LOG_SPAN = 2_000


class ChainTarget(BaseModel):
    chain: Literal["base", "solana"]
    target: str = Field(min_length=20, max_length=64)

    @field_validator("target")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    def normalized(self) -> str:
        try:
            return normalize_address(self.chain, self.target)
        except ValueError as e:
            raise APIError(422, "invalid_address", str(e)) from e


def _topic_addr(a: str) -> str:
    return "0x" + "0" * 24 + a.lower()[2:]


# --------------------------------------------------------------------------------------------
# FORENSIC
# --------------------------------------------------------------------------------------------


class ForensicInput(ChainTarget):
    pass


class Forensic(Module):
    name, title = "forensic", "FORENSIC"
    description = "Token and smart-contract risk analysis from raw chain state (bytecode, proxies, authorities, Token-2022 extensions)."
    credit_cost = 3
    tools = ("eth_getCode", "eth_getStorageAt", "eth_call", "getAccountInfo")
    Input = ForensicInput

    async def run(self, ctx: ModuleContext, inp: ForensicInput) -> ModuleOutput:  # type: ignore[override]
        addr = inp.normalized()
        rep = await (scan_base_token(ctx.clients.evm, addr) if inp.chain == "base" else scan_solana_mint(ctx.clients.solana, addr))
        ctx.db.add(TokenScan(user_id=ctx.user.id, chain=inp.chain, token=addr, risk_level=rep["risk_level"], findings=rep["findings"], report=rep))
        await ctx.db.commit()
        ev = [Evidence(source=f"{inp.chain}-rpc", method=e.get("method", ""), ref=str(e.get("block") or e.get("slot"))) for e in rep["evidence"]]
        return ModuleOutput(module=self.name, summary=f"Risk {rep['risk_level'].upper()}: {rep['summary']}", data=rep, evidence=ev, limitations=rep.get("limitations", []))


# --------------------------------------------------------------------------------------------
# SENTINEL
# --------------------------------------------------------------------------------------------


class SentinelInput(ChainTarget):
    kind: Literal["wallet", "contract", "token_liquidity"] = "wallet"
    create_rule: bool = False
    threshold: str | None = Field(default=None, description="For wallet: min transfer size (token units) that alerts")
    token: str | None = None


class Sentinel(Module):
    name, title = "sentinel", "SENTINEL"
    description = "Wallet, liquidity and contract monitoring: snapshot now, optionally arm a background rule."
    credit_cost = 2
    tools = ("eth_getBalance", "eth_getCode", "eth_call", "getBalance", "getAccountInfo", "create_monitoring_rule")
    Input = SentinelInput

    async def run(self, ctx: ModuleContext, inp: SentinelInput) -> ModuleOutput:  # type: ignore[override]
        addr = inp.normalized()
        ev: list[Evidence] = []
        data: dict[str, Any] = {"target": addr, "kind": inp.kind, "chain": inp.chain}
        if inp.chain == "base":
            block = await ctx.clients.evm.block_number()
            if inp.kind == "wallet":
                data["native_balance_wei"] = str(await ctx.clients.evm.get_balance(addr))
                ev.append(Evidence(source="base-rpc", method="eth_getBalance", ref=f"block {block}"))
            elif inp.kind == "contract":
                code = await ctx.clients.evm.get_code(addr)
                data["code_hash"] = "0x" + keccak(hexstr=code).hex() if code and code != "0x" else None
                data["has_code"] = bool(code and code != "0x")
                ev.append(Evidence(source="base-rpc", method="eth_getCode", ref=f"block {block}"))
            else:
                usdc = base_cfg()["usdc"].lower()
                probe = 100 * 10**6
                try:
                    q = await uni.quote(ctx.clients.evm, usdc, addr, probe, 50)
                    data["liquidity_probe"] = {"usdc_in": 100, "amount_out": str(q.amount_out), "price_impact_bps": q.price_impact_bps, "route": q.route, "fees": q.raw.get("fees")}
                except APIError as e:
                    data["liquidity_probe"] = {"error": e.message}
                ev.append(Evidence(source="uniswap-v3-quoter", method="eth_call quoteExactInput*", ref=f"block {block}"))
        else:
            if inp.kind == "wallet":
                lamports, slot = await ctx.clients.solana.get_balance(addr)
                data["native_balance_lamports"] = lamports
                ev.append(Evidence(source="solana-rpc", method="getBalance", ref=f"slot {slot}"))
            else:
                acc, slot = await ctx.clients.solana.get_account_info(addr)
                data["exists"] = acc is not None
                data["owner_program"] = acc.get("owner") if acc else None
                ev.append(Evidence(source="solana-rpc", method="getAccountInfo", ref=f"slot {slot}"))
        if inp.create_rule:
            kind = {"wallet": "wallet_large_transfer", "contract": "contract_code_change", "token_liquidity": "liquidity_drop"}[inp.kind]
            rule = MonitoringRule(user_id=ctx.user.id, kind=kind, chain=inp.chain, target=addr, params={"threshold": inp.threshold, "token": inp.token, "baseline": data.get("code_hash")})
            ctx.db.add(rule)
            await ctx.db.commit()
            data["rule_id"] = str(rule.id)
        return ModuleOutput(module=self.name, summary=f"Snapshot of {inp.kind} {addr} on {inp.chain}" + (" — monitoring armed." if inp.create_rule else "."), data=data, evidence=ev)


# --------------------------------------------------------------------------------------------
# WHALE
# --------------------------------------------------------------------------------------------


class WhaleInput(BaseModel):
    chain: Literal["base", "solana"]
    token: str | None = Field(default=None, description="Base: ERC-20 contract to watch")
    wallet: str | None = Field(default=None, description="Solana: wallet to inspect")
    min_amount: Decimal = Field(default=Decimal("10000"), gt=0)
    lookback_blocks: int = Field(default=1_000, ge=10, le=MAX_LOG_SPAN)


class Whale(Module):
    name, title = "whale", "WHALE"
    description = "Large transfer detection and counterparty clustering from Transfer logs / parsed transactions."
    credit_cost = 4
    tools = ("eth_getLogs", "eth_call", "getSignaturesForAddress", "getTransaction")
    Input = WhaleInput

    async def run(self, ctx: ModuleContext, inp: WhaleInput) -> ModuleOutput:  # type: ignore[override]
        if inp.chain == "base":
            if not inp.token:
                raise APIError(422, "token_required", "Provide an ERC-20 token address for Base whale tracking.")
            token = normalize_address("base", inp.token)
            evm = ctx.clients.evm
            meta = await evm.erc20_metadata(token)
            dec = int(meta.get("decimals") or 18)
            head = await evm.block_number()
            frm = max(0, head - inp.lookback_blocks)
            logs = await evm.get_logs({"address": token, "topics": [TRANSFER_TOPIC], "fromBlock": hex(frm), "toBlock": hex(head)})
            thr = int(inp.min_amount * (Decimal(10) ** dec))
            big = []
            cluster: dict[str, dict[str, int]] = defaultdict(lambda: {"in": 0, "out": 0, "count": 0})
            for lg in logs:
                if len(lg.get("topics", [])) < 3:
                    continue
                v = int(lg["data"], 16) if lg["data"] not in ("0x", "") else 0
                src, dst = "0x" + lg["topics"][1][-40:], "0x" + lg["topics"][2][-40:]
                if v >= thr:
                    big.append({"tx": lg["transactionHash"], "block": int(lg["blockNumber"], 16), "from": src, "to": dst, "amount": str(Decimal(v) / (Decimal(10) ** dec))})
                    cluster[src]["out"] += v
                    cluster[src]["count"] += 1
                    cluster[dst]["in"] += v
                    cluster[dst]["count"] += 1
            top = sorted(cluster.items(), key=lambda kv: kv[1]["in"] + kv[1]["out"], reverse=True)[:10]
            data = {
                "token": token, "block_range": [frm, head], "transfers_scanned": len(logs), "large_transfers": big[-50:],
                "clusters": [{"address": a, "inflow": str(Decimal(c["in"]) / (Decimal(10) ** dec)), "outflow": str(Decimal(c["out"]) / (Decimal(10) ** dec)), "count": c["count"]} for a, c in top],
            }
            return ModuleOutput(
                module=self.name, summary=f"{len(big)} transfer(s) ≥ {inp.min_amount} in blocks {frm}–{head}.", data=data,
                evidence=[Evidence(source="base-rpc", method="eth_getLogs Transfer", ref=f"blocks {frm}-{head}")],
                limitations=["Clusters group counterparties by co-occurrence only; they do not establish common ownership."],
            )
        if not inp.wallet:
            raise APIError(422, "wallet_required", "Provide a Solana wallet to inspect.")
        wallet = normalize_address("solana", inp.wallet)
        sol = ctx.clients.solana
        sigs = await sol.get_signatures_for_address(wallet, limit=25)
        big = []
        for s in sigs:
            tx = await sol.get_transaction(s["signature"])
            if not tx or not tx.get("meta"):
                continue
            pre = {(b["accountIndex"], b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in tx["meta"].get("preTokenBalances", []) if b.get("owner") == wallet}
            post = {(b["accountIndex"], b["mint"]): (int(b["uiTokenAmount"]["amount"]), int(b["uiTokenAmount"]["decimals"])) for b in tx["meta"].get("postTokenBalances", []) if b.get("owner") == wallet}
            for k, (amt, dec) in post.items():
                delta = Decimal(amt - pre.get(k, 0)) / (Decimal(10) ** dec)
                if abs(delta) >= inp.min_amount:
                    big.append({"signature": s["signature"], "slot": tx["slot"], "mint": k[1], "delta": str(delta)})
        return ModuleOutput(
            module=self.name, summary=f"{len(big)} large token balance change(s) in the last {len(sigs)} transactions.",
            data={"wallet": wallet, "large_changes": big, "transactions_scanned": len(sigs)},
            evidence=[Evidence(source="solana-rpc", method="getSignaturesForAddress+getTransaction", ref=f"{len(sigs)} sigs")],
        )


# --------------------------------------------------------------------------------------------
# ALPHA
# --------------------------------------------------------------------------------------------


class AlphaInput(BaseModel):
    chain: Literal["base"] = "base"
    token: str
    lookback_blocks: int = Field(default=1_800, ge=100, le=MAX_LOG_SPAN)
    buckets: int = Field(default=12, ge=4, le=48)
    z_threshold: float = Field(default=2.5, ge=1.0, le=10.0)


class Alpha(Module):
    name, title = "alpha", "ALPHA"
    description = "On-chain anomaly research: z-score of transfer count/volume per block window vs the recent baseline."
    credit_cost = 4
    tools = ("eth_getLogs", "eth_call")
    Input = AlphaInput

    async def run(self, ctx: ModuleContext, inp: AlphaInput) -> ModuleOutput:  # type: ignore[override]
        token = normalize_address("base", inp.token)
        evm = ctx.clients.evm
        head = await evm.block_number()
        frm = head - inp.lookback_blocks
        logs = await evm.get_logs({"address": token, "topics": [TRANSFER_TOPIC], "fromBlock": hex(frm), "toBlock": hex(head)})
        width = inp.lookback_blocks // inp.buckets
        counts = [0] * inp.buckets
        vols = [0] * inp.buckets
        senders: list[set[str]] = [set() for _ in range(inp.buckets)]
        for lg in logs:
            b = min(inp.buckets - 1, (int(lg["blockNumber"], 16) - frm) // max(1, width))
            counts[b] += 1
            vols[b] += int(lg["data"], 16) if lg["data"] not in ("0x", "") else 0
            if len(lg.get("topics", [])) > 1:
                senders[b].add(lg["topics"][1])

        def z(series: list[int]) -> float | None:
            base = series[:-1]
            if len(base) < 3:
                return None
            sd = statistics.pstdev(base)
            mean = statistics.mean(base)
            if sd == 0:
                # Perfectly flat baseline: any deviation is maximal; report a capped score.
                return 0.0 if series[-1] == mean else (99.0 if series[-1] > mean else -99.0)
            return max(-99.0, min(99.0, (series[-1] - mean) / sd))

        zc, zv, zs = z(counts), z(vols), z([len(s) for s in senders])
        anomalies = [name for name, val in (("transfer_count", zc), ("volume", zv), ("unique_senders", zs)) if val is not None and abs(val) >= inp.z_threshold]
        return ModuleOutput(
            module=self.name,
            summary=("Anomalies in latest window: " + ", ".join(anomalies)) if anomalies else "No statistically significant anomaly in the latest window.",
            data={"token": token, "block_range": [frm, head], "bucket_width_blocks": width, "counts": counts, "volumes_raw": [str(v) for v in vols], "unique_senders": [len(s) for s in senders], "z_scores": {"count": zc, "volume": zv, "senders": zs}, "anomalies": anomalies},
            evidence=[Evidence(source="base-rpc", method="eth_getLogs Transfer", ref=f"blocks {frm}-{head}")],
            limitations=["Statistical signal only; not a prediction or recommendation."],
        )


# --------------------------------------------------------------------------------------------
# PORTFOLIO
# --------------------------------------------------------------------------------------------


class PortfolioInput(BaseModel):
    addresses: list[ChainTarget] = Field(default_factory=list, description="Defaults to your verified wallets")
    include_prices: bool = True


class Portfolio(Module):
    name, title = "portfolio", "PORTFOLIO"
    description = "Unified Base + Solana balances with trusted-source USD valuation (missing prices are shown as unavailable)."
    credit_cost = 2
    tools = ("eth_getBalance", "eth_call", "getBalance", "getTokenAccountsByOwner", "uniswap_quote", "jupiter_price")
    Input = PortfolioInput

    async def run(self, ctx: ModuleContext, inp: PortfolioInput) -> ModuleOutput:  # type: ignore[override]
        targets = [(t.chain, t.normalized()) for t in inp.addresses] or [(w.chain, w.address) for w in ctx.user.wallets]
        holdings: list[dict[str, Any]] = []
        ev: list[Evidence] = []
        warnings: list[str] = []
        for chain, addr in targets:
            if chain == "base":
                evm = ctx.clients.evm
                block = await evm.block_number()
                holdings.append({"chain": "base", "wallet": addr, "symbol": "ETH", "token": "native", "decimals": 18, "raw": str(await evm.get_balance(addr))})
                for t in tokens("base"):
                    try:
                        bal = await evm.erc20_balance(t.address, addr)
                    except RpcError:
                        continue
                    if bal:
                        holdings.append({"chain": "base", "wallet": addr, "symbol": t.symbol, "token": t.address.lower(), "decimals": t.decimals, "raw": str(bal), "stable": t.stable})
                ev.append(Evidence(source="base-rpc", method="eth_getBalance+balanceOf", ref=f"block {block}"))
            else:
                sol = ctx.clients.solana
                lamports, slot = await sol.get_balance(addr)
                holdings.append({"chain": "solana", "wallet": addr, "symbol": "SOL", "token": "native", "decimals": 9, "raw": str(lamports)})
                for prog in ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"):
                    accs, _ = await sol.get_token_accounts_by_owner(addr, program=prog)
                    for a in accs:
                        info = a["account"]["data"]["parsed"]["info"]
                        amt = int(info["tokenAmount"]["amount"])
                        if amt == 0:
                            continue
                        known = next((t for t in tokens("solana") if t.address == info["mint"]), None)
                        holdings.append({"chain": "solana", "wallet": addr, "symbol": known.symbol if known else info["mint"][:4] + "…", "token": info["mint"], "decimals": int(info["tokenAmount"]["decimals"]), "raw": str(amt), "stable": bool(known and known.stable)})
                ev.append(Evidence(source="solana-rpc", method="getBalance+getTokenAccountsByOwner", ref=f"slot {slot}"))
        total = Decimal(0)
        priced = 0
        for h in holdings:
            h["amount"] = str(Decimal(int(h["raw"])) / (Decimal(10) ** h["decimals"]))
            h["usd"] = None
            h["price_source"] = None
            if not inp.include_prices:
                continue
            if h.get("stable"):
                h["usd"], h["price_source"] = float(h["amount"]), "stablecoin par"
            elif h["chain"] == "base" and int(h["raw"]) > 0:
                tin = base_cfg()["weth"].lower() if h["token"] == "native" else h["token"]
                try:
                    q = await uni.quote(ctx.clients.evm, tin, base_cfg()["usdc"].lower(), int(h["raw"]), 50)
                    h["usd"], h["price_source"] = float(Decimal(q.amount_out) / Decimal(10**6)), "Uniswap V3 quote → USDC (executable value)"
                except APIError:
                    pass
            elif h["chain"] == "solana" and int(h["raw"]) > 0 and solana_cfg().get("jupiterAvailable"):
                mint = "So11111111111111111111111111111111111111112" if h["token"] == "native" else h["token"]
                try:
                    r = await ctx.clients.http.get(f"{get_settings().jupiter_base()}/price/v3", params={"ids": mint}, headers=get_settings().jupiter_headers())
                    p = r.json().get(mint, {}).get("usdPrice") if r.status_code == 200 else None
                    if p is not None:
                        h["usd"], h["price_source"] = float(Decimal(str(p)) * Decimal(h["amount"])), "Jupiter Price API v3"
                except Exception:  # noqa: BLE001
                    pass
            if h["usd"] is not None:
                total += Decimal(str(h["usd"]))
                priced += 1
        unpriced = [h["symbol"] for h in holdings if h["usd"] is None and int(h["raw"]) > 0]
        if unpriced:
            warnings.append(f"No trusted price for: {', '.join(unpriced)} (excluded from total).")
        return ModuleOutput(
            module=self.name, summary=f"{len(holdings)} holding(s) across {len(targets)} wallet(s); priced value ${total:,.2f}.",
            data={"holdings": holdings, "total_usd_priced": float(total), "priced_count": priced}, evidence=ev, warnings=warnings,
            limitations=["Base balances cover native ETH plus allowlisted ERC-20s; add tokens by address via SENTINEL/FORENSIC."],
        )
