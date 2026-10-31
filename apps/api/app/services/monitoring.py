"""SENTINEL / AUTOPILOT background rule evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from eth_utils import keccak
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alert, MonitoringRule, User, Workflow
from app.modules.research import TRANSFER_TOPIC, _topic_addr
from app.services.workflows import run_actions

MAX_SPAN = 2_000


async def evaluate_rule(db: AsyncSession, clients: Any, rule: MonitoringRule) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    p = rule.params or {}
    if rule.chain == "base" and rule.kind == "wallet_large_transfer" and p.get("token"):
        evm = clients.evm
        head = await evm.block_number()
        start = int(rule.cursor) if rule.cursor else head - 50
        start = max(start, head - MAX_SPAN)
        meta = await evm.erc20_metadata(p["token"])
        dec = int(meta.get("decimals") or 18)
        thr = int(Decimal(p.get("min_amount") or p.get("threshold") or "0") * (Decimal(10) ** dec))
        for topics in ([TRANSFER_TOPIC, _topic_addr(rule.target)], [TRANSFER_TOPIC, None, _topic_addr(rule.target)]):
            logs = await evm.get_logs({"address": p["token"], "topics": topics, "fromBlock": hex(start), "toBlock": hex(head)})
            for lg in logs:
                v = int(lg["data"], 16) if lg["data"] not in ("0x", "") else 0
                if v >= thr:
                    direction = "out" if topics[1] else "in"
                    events.append({"title": f"Large transfer {direction}: {Decimal(v) / (Decimal(10) ** dec)} tokens", "tx": lg["transactionHash"], "token": p["token"], "amount_raw": str(v), "block": int(lg["blockNumber"], 16)})
        rule.cursor = str(head + 1)
    elif rule.chain == "solana" and rule.kind == "wallet_large_transfer":
        sol = clients.solana
        sigs = await sol.get_signatures_for_address(rule.target, limit=20, until=rule.cursor)
        thr = Decimal(p.get("min_amount") or p.get("threshold") or "0")
        for s in reversed(sigs):
            tx = await sol.get_transaction(s["signature"])
            if not tx or not tx.get("meta"):
                continue
            pre = {(b["accountIndex"], b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in tx["meta"].get("preTokenBalances", []) if b.get("owner") == rule.target}
            for b in tx["meta"].get("postTokenBalances", []):
                if b.get("owner") != rule.target or (p.get("token") and b["mint"] != p["token"]):
                    continue
                k = (b["accountIndex"], b["mint"])
                delta = Decimal(int(b["uiTokenAmount"]["amount"]) - pre.get(k, 0)) / (Decimal(10) ** int(b["uiTokenAmount"]["decimals"]))
                if abs(delta) >= thr:
                    events.append({"title": f"Large token change {delta}", "tx": s["signature"], "token": b["mint"], "delta": str(delta)})
        if sigs:
            rule.cursor = sigs[0]["signature"]
    elif rule.kind == "balance_below":
        thr = Decimal(p.get("threshold") or "0")
        if rule.chain == "base":
            raw = await clients.evm.get_balance(rule.target) if p.get("token", "native") == "native" else await clients.evm.erc20_balance(p["token"], rule.target)
            dec = 18 if p.get("token", "native") == "native" else int((await clients.evm.erc20_metadata(p["token"])).get("decimals") or 18)
        else:
            raw, _ = await clients.solana.get_balance(rule.target)
            dec = 9
        bal = Decimal(raw) / (Decimal(10) ** dec)
        below = bal < thr
        was_below = rule.cursor == "below"
        if below and not was_below:
            events.append({"title": f"Balance {bal} fell below {thr}", "balance": str(bal)})
        rule.cursor = "below" if below else "ok"
    elif rule.kind == "contract_code_change" and rule.chain == "base":
        code = await clients.evm.get_code(rule.target)
        h = "0x" + keccak(hexstr=code).hex() if code and code != "0x" else "none"
        impl = await clients.evm.get_storage_at(rule.target, "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc")
        fp = f"{h}:{impl}"
        if rule.cursor and rule.cursor != fp:
            events.append({"title": "Contract code or proxy implementation changed", "before": rule.cursor, "after": fp})
        rule.cursor = fp
    rule.last_checked_at = datetime.now(UTC)
    return events


async def evaluate_all(db: AsyncSession, clients: Any, limit: int = 200) -> int:
    rules = (await db.execute(select(MonitoringRule).where(MonitoringRule.enabled.is_(True)).order_by(MonitoringRule.last_checked_at.nullsfirst()).limit(limit))).scalars().all()
    fired = 0
    for rule in rules:
        try:
            events = await evaluate_rule(db, clients, rule)
        except Exception as e:  # noqa: BLE001 - one bad rule must not stop the loop
            rule.last_checked_at = datetime.now(UTC)
            db.add(Alert(user_id=rule.user_id, rule_id=rule.id, severity="warning", title=f"Monitoring check failed: {type(e).__name__}", body={"error": str(e)[:300]}))
            await db.commit()
            continue
        await db.commit()
        for ev in events[:10]:
            fired += 1
            user = await db.get(User, rule.user_id)
            wf = await db.get(Workflow, rule.workflow_id) if rule.workflow_id else None
            if wf and wf.status == "active" and user:
                await run_actions(db, user, clients, wf, rule, ev)
            else:
                db.add(Alert(user_id=rule.user_id, rule_id=rule.id, severity="info", title=ev["title"][:200], body=ev))
                await db.commit()
    return fired
