"""LangGraph state machine for Level 3 assisted execution.

USER_REQUEST → PARSE_INTENT → VALIDATE_INTENT → FETCH_CHAIN_DATA → BUILD_UNSIGNED_TRANSACTION
→ SIMULATE_TRANSACTION → RUN_POLICY_CHECKS → GENERATE_EXECUTION_PREVIEW → WAIT_FOR_USER_APPROVAL
→ WALLET_SIGN → BROADCAST → VERIFY_CONFIRMATION → RECORD_ACTIVITY

The graph runs in two phases separated by a hard, code-enforced boundary:

* phase "prepare": runs up to WAIT_FOR_USER_APPROVAL and always STOPS there. Nothing past
  this node can run in the same invocation.
* phase "settle": starts AT WAIT_FOR_USER_APPROVAL, which verifies (in code, against the
  database) a single-use ApprovalRecord bound to the exact transaction digest, created by the
  user via the approve endpoint. Without it, the graph ends with status "awaiting_approval".

The backend never holds keys: WALLET_SIGN only *verifies* what the user's wallet signed.
"""

from __future__ import annotations

import base64
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from solders.transaction import VersionedTransaction
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import user_wallet
from app.core.audit import audit
from app.core.chains import explorer_tx_url
from app.core.config import get_settings
from app.core.errors import APIError
from app.db.models import (
    ApprovalRecord,
    ExecutionStep,
    SimulationRecord,
    SystemFlag,
    TransactionSubmission,
    User,
)
from app.db.models import TransactionIntent as IntentRow
from app.execution import evm as evm_exec
from app.execution import solana as sol_exec
from app.execution.digest import evm_digest, solana_digest
from app.execution.intent import parse_intent, validate_intent
from app.execution.policy import PolicyContext, evaluate
from app.execution.preview import build_preview
from app.execution.types import (
    BuiltStep,
    Chain,
    ExecStatus,
    IntentKind,
    PolicyResult,
    SimulationResult,
    TransactionIntent,
)
from app.execution.valuation import input_usd_value
from app.services.token_risk import scan_base_token, scan_solana_mint


class ExecState(TypedDict, total=False):
    phase: Literal["prepare", "settle"]
    execution_id: str
    step_index: int
    text: str
    chain: str
    wallet: str
    intent: dict[str, Any]
    questions: list[str]
    notes: list[str]
    research: dict[str, Any]
    steps_plan: list[str]
    built: dict[str, Any]
    simulation: dict[str, Any]
    policy: dict[str, Any]
    preview: dict[str, Any]
    approval_id: str
    signed: dict[str, Any]
    submission_id: str
    status: str
    error: str
    trace: list[dict[str, Any]]


def _ctx(config: RunnableConfig) -> tuple[AsyncSession, User, Any]:
    c = config["configurable"]
    return c["db"], c["user"], c["clients"]


def _trace(state: ExecState, node: str, detail: str = "") -> list[dict[str, Any]]:
    return [*state.get("trace", []), {"node": node, "detail": detail, "at": time.time()}]


async def execution_enabled(db: AsyncSession) -> bool:
    flag = await db.get(SystemFlag, "execution_enabled")
    return True if flag is None else bool(flag.enabled)


# ------------------------------------------------------------------------------------------
# Prepare phase
# ------------------------------------------------------------------------------------------


async def parse_intent_node(state: ExecState, config: RunnableConfig) -> ExecState:
    if state.get("intent"):
        return {"trace": _trace(state, "PARSE_INTENT", "reusing stored intent")}
    outcome = await parse_intent(state["text"], Chain(state["chain"]), state["wallet"])
    if outcome.read_only:
        return {"status": ExecStatus.rejected, "error": "Not a transaction request (routed to research modules).", "trace": _trace(state, "PARSE_INTENT", "read-only")}
    if outcome.intent is None:
        return {"status": ExecStatus.needs_clarification, "questions": outcome.questions, "trace": _trace(state, "PARSE_INTENT", "ambiguous")}
    return {"intent": outcome.intent.model_dump(mode="json"), "notes": outcome.notes, "trace": _trace(state, "PARSE_INTENT", outcome.intent.parser)}


async def validate_intent_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, user, _ = _ctx(config)
    intent = TransactionIntent(**state["intent"])
    problems = validate_intent(intent, user.settings or {})
    if not user_wallet(user, intent.chain.value, intent.wallet):
        problems.append("The wallet in this request is not verified for your session.")
    if problems:
        return {"status": ExecStatus.needs_clarification, "questions": problems, "trace": _trace(state, "VALIDATE_INTENT", "invalid")}
    return {"trace": _trace(state, "VALIDATE_INTENT", "ok")}


async def fetch_chain_data_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, user, clients = _ctx(config)
    intent = TransactionIntent(**state["intent"])
    try:
        if intent.chain == Chain.base:
            r = await evm_exec.research(clients.evm, intent)
        else:
            r = await sol_exec.research(clients.solana, clients.http, intent)
    except APIError as e:
        return {"status": ExecStatus.rejected, "error": e.message, "trace": _trace(state, "FETCH_CHAIN_DATA", e.code)}
    # Token risk for any non-allowlisted asset.
    risk = None
    for key in ("output_token", "input_token"):
        tok = r.get(key)
        if tok and not tok.get("allowlisted"):
            risk = await (scan_base_token(clients.evm, tok["address"]) if intent.chain == Chain.base else scan_solana_mint(clients.solana, tok["address"]))
            break
    r["token_risk"] = risk
    usd, src = await input_usd_value(clients.evm, clients.http, r)
    r["input_usd_value"], r["input_usd_source"] = usd, src
    # The step plan is fixed at first research; later steps (e.g. swap after approve) keep it.
    plan = state.get("steps_plan") or (evm_exec.plan_steps(intent, r) if intent.chain == Chain.base else sol_exec.plan_steps(intent, r))
    r["_plan"] = plan
    return {"research": r, "steps_plan": plan, "trace": _trace(state, "FETCH_CHAIN_DATA", f"plan={plan}")}


async def build_tx_node(state: ExecState, config: RunnableConfig) -> ExecState:
    _, _, clients = _ctx(config)
    intent = TransactionIntent(**state["intent"])
    idx = state.get("step_index", 0)
    kind = state["steps_plan"][idx]
    try:
        if intent.chain == Chain.base:
            built = evm_exec.build_step(kind, intent, state["research"])
        else:
            built = await sol_exec.build_step(clients.solana, clients.http, kind, intent, state["research"])
    except APIError as e:
        return {"status": ExecStatus.rejected, "error": e.message, "trace": _trace(state, "BUILD_UNSIGNED_TRANSACTION", e.code)}
    return {"built": built.model_dump(mode="json"), "trace": _trace(state, "BUILD_UNSIGNED_TRANSACTION", f"{kind} {built.digest[:12]}")}


async def simulate_node(state: ExecState, config: RunnableConfig) -> ExecState:
    _, _, clients = _ctx(config)
    built = BuiltStep(**state["built"])
    if state["chain"] == "base":
        sim = await evm_exec.simulate(clients.evm, built, state["research"])
    else:
        sim = await sol_exec.simulate(clients.solana, built, state["research"])
    return {"simulation": sim.model_dump(mode="json"), "trace": _trace(state, "SIMULATE_TRANSACTION", "success" if sim.success else "failed")}


async def spent_today_usd(db: AsyncSession, user_id: uuid.UUID) -> float:
    since = datetime.now(UTC) - timedelta(hours=24)
    rows = (
        await db.execute(
            select(IntentRow.research).where(
                IntentRow.user_id == user_id, IntentRow.created_at >= since,
                IntentRow.status.in_([ExecStatus.submitted, ExecStatus.confirmed, ExecStatus.approved]),
            )
        )
    ).scalars().all()
    return float(sum((r or {}).get("input_usd_value") or 0 for r in rows))


async def policy_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, user, _ = _ctx(config)
    intent = TransactionIntent(**state["intent"])
    built = BuiltStep(**state["built"])
    sim = SimulationResult(**state["simulation"]) if state.get("simulation") else None
    res = evaluate(
        PolicyContext(
            intent=intent, research=state["research"], step=built, simulation=sim, user_limits=user.settings or {},
            execution_enabled=await execution_enabled(db), wallet_verified=user_wallet(user, intent.chain.value, intent.wallet),
            spent_today_usd=await spent_today_usd(db, user.id), token_risk=state["research"].get("token_risk"),
        )
    )
    return {"policy": res.model_dump(mode="json"), "trace": _trace(state, "RUN_POLICY_CHECKS", "allowed" if res.allowed else f"blocked:{[c.id for c in res.failures]}")}


async def preview_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, user, _ = _ctx(config)
    intent = TransactionIntent(**state["intent"])
    built = BuiltStep(**state["built"])
    sim = SimulationResult(**state["simulation"]) if state.get("simulation") else None
    pol = PolicyResult(**state["policy"])
    idx = state.get("step_index", 0)
    preview = build_preview(intent, state["research"], built, sim, pol, idx, len(state["steps_plan"]), state["research"].get("token_risk"))
    preview["notes"] = state.get("notes", []) + state["research"].get("notes", [])
    status = ExecStatus.awaiting_approval if pol.allowed else ExecStatus.blocked

    row = await db.get(IntentRow, uuid.UUID(state["execution_id"]))
    assert row is not None
    row.intent, row.research, row.kind, row.chain, row.wallet = state["intent"], state["research"], intent.kind.value, intent.chain.value, intent.wallet
    row.status, row.current_step = status, idx
    row.status_reason = None if pol.allowed else "; ".join(c.message for c in pol.failures)
    step = (await db.execute(select(ExecutionStep).where(ExecutionStep.intent_id == row.id, ExecutionStep.index == idx))).scalar_one_or_none()
    if step is None:
        step = ExecutionStep(intent_id=row.id, index=idx, kind=built.kind, status=status)
        db.add(step)
    else:
        # Transaction changed → every outstanding approval for this step is void.
        if step.digest and step.digest != built.digest:
            await db.execute(update(ApprovalRecord).where(ApprovalRecord.step_id == step.id, ApprovalRecord.used_at.is_(None)).values(invalidated_at=datetime.now(UTC)))
    step.kind, step.unsigned_tx, step.decoded, step.digest = built.kind, built.tx, built.decoded, built.digest
    step.quote = built.quote.model_dump(mode="json") if built.quote else {}
    step.policy, step.preview, step.status = state["policy"], preview, status
    await db.flush()
    if sim:
        db.add(SimulationRecord(
            step_id=step.id, digest=sim.digest, success=sim.success, block_or_slot=sim.block_or_slot, gas_or_units=sim.gas_or_units,
            fee_estimate=sim.fee_estimate, balance_changes=[c.model_dump() for c in sim.balance_changes], error=sim.error,
            logs=sim.logs, limitations=sim.limitations,
        ))
    await audit(db, "execution.preview", user_id=user.id, subject=str(row.id), data={"step": idx, "digest": built.digest, "allowed": pol.allowed})
    await db.commit()
    preview["execution_id"] = str(row.id)
    preview["step_id"] = str(step.id)
    return {"preview": preview, "status": status, "trace": _trace(state, "GENERATE_EXECUTION_PREVIEW", status)}


# ------------------------------------------------------------------------------------------
# Approval boundary + settle phase
# ------------------------------------------------------------------------------------------


async def wait_for_approval_node(state: ExecState, config: RunnableConfig) -> ExecState:
    """Code-enforced gate. In 'prepare' it always stops. In 'settle' it consumes a valid approval."""
    if state.get("phase") != "settle":
        return {"trace": _trace(state, "WAIT_FOR_USER_APPROVAL", "awaiting explicit user confirmation")}
    db, user, _ = _ctx(config)
    appr = await db.get(ApprovalRecord, uuid.UUID(state["approval_id"])) if state.get("approval_id") else None
    now = datetime.now(UTC)
    if appr is None or appr.user_id != user.id:
        return {"status": ExecStatus.awaiting_approval, "error": "No approval record for this user.", "trace": _trace(state, "WAIT_FOR_USER_APPROVAL", "missing")}
    step = await db.get(ExecutionStep, appr.step_id)
    if step is None or step.digest != appr.digest:
        return {"status": ExecStatus.awaiting_approval, "error": "Transaction changed since approval; re-confirm required.", "trace": _trace(state, "WAIT_FOR_USER_APPROVAL", "digest_mismatch")}
    if appr.invalidated_at is not None or appr.expires_at < now:
        return {"status": ExecStatus.expired, "error": "Approval expired or was invalidated. Refresh and confirm again.", "trace": _trace(state, "WAIT_FOR_USER_APPROVAL", "expired")}
    # Single use: atomic compare-and-set on used_at prevents approval replay.
    used = (await db.execute(update(ApprovalRecord).where(ApprovalRecord.id == appr.id, ApprovalRecord.used_at.is_(None)).values(used_at=now).returning(ApprovalRecord.id))).scalar()
    if used is None:
        return {"status": ExecStatus.rejected, "error": "Approval already used (replay blocked).", "trace": _trace(state, "WAIT_FOR_USER_APPROVAL", "replay")}
    await db.commit()
    return {"execution_id": str(step.intent_id), "step_index": step.index, "trace": _trace(state, "WAIT_FOR_USER_APPROVAL", "approved")}


async def wallet_sign_node(state: ExecState, config: RunnableConfig) -> ExecState:
    """Verifies the artifact produced by the USER's wallet. The server never signs."""
    db, user, clients = _ctx(config)
    appr = await db.get(ApprovalRecord, uuid.UUID(state["approval_id"]))
    assert appr is not None
    step = await db.get(ExecutionStep, appr.step_id)
    assert step is not None
    signed = state.get("signed") or {}
    tx = step.unsigned_tx
    if state["chain"] == "base":
        h = str(signed.get("tx_hash", ""))
        if not (h.startswith("0x") and len(h) == 66):
            return {"status": ExecStatus.failed, "error": "Malformed transaction hash.", "trace": _trace(state, "WALLET_SIGN", "bad_hash")}
        onchain = None
        for _ in range(5):
            onchain = await clients.evm.get_transaction(h)
            if onchain:
                break
            import asyncio

            await asyncio.sleep(1.0)
        if onchain is None:
            return {"signed": {"tx_hash": h, "verified": False}, "trace": _trace(state, "WALLET_SIGN", "pending_visibility")}
        seen = {"chainId": int(onchain.get("chainId", "0x0"), 16), "from": onchain["from"], "to": onchain.get("to") or "", "data": onchain.get("input", "0x"), "value": str(int(onchain.get("value", "0x0"), 16))}
        if evm_digest(seen) != appr.digest:
            await audit(db, "execution.substitution_detected", user_id=user.id, subject=h, data={"expected": appr.digest, "seen": evm_digest(seen)})
            await db.commit()
            return {"status": ExecStatus.failed, "error": "Broadcast transaction does not match the approved transaction (substitution detected).", "signed": {"tx_hash": h, "mismatch": True}, "trace": _trace(state, "WALLET_SIGN", "mismatch")}
        return {"signed": {"tx_hash": h, "verified": True}, "trace": _trace(state, "WALLET_SIGN", "verified")}
    # Solana: verify signed bytes == approved message and the fee payer signature is valid.
    try:
        vt = VersionedTransaction.from_bytes(base64.b64decode(signed.get("signed_tx", "")))
    except Exception:  # noqa: BLE001
        return {"status": ExecStatus.failed, "error": "Signed transaction could not be decoded.", "trace": _trace(state, "WALLET_SIGN", "decode")}
    from solders.message import to_bytes_versioned

    msg_bytes = to_bytes_versioned(vt.message)
    if solana_digest(base64.b64encode(msg_bytes).decode()) != appr.digest:
        await audit(db, "execution.substitution_detected", user_id=user.id, subject=str(step.id), data={"expected": appr.digest})
        await db.commit()
        return {"status": ExecStatus.failed, "error": "Signed transaction differs from the approved message (substitution detected).", "trace": _trace(state, "WALLET_SIGN", "mismatch")}
    payer = vt.message.account_keys[0]
    sig = vt.signatures[0]
    if str(payer) != tx["fee_payer"] or not sig.verify(payer, msg_bytes):
        return {"status": ExecStatus.failed, "error": "Fee-payer signature invalid.", "trace": _trace(state, "WALLET_SIGN", "bad_sig")}
    return {"signed": {"signed_tx": signed["signed_tx"], "signature": str(sig), "verified": True}, "trace": _trace(state, "WALLET_SIGN", "verified")}


async def broadcast_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, user, clients = _ctx(config)
    appr = await db.get(ApprovalRecord, uuid.UUID(state["approval_id"]))
    assert appr is not None
    signed = state["signed"]
    chain = state["chain"]
    if chain == "base":
        tx_id = signed["tx_hash"]  # broadcast by the user's wallet
    else:
        try:
            tx_id = await clients.solana.send_transaction(signed["signed_tx"])
        except Exception as e:  # noqa: BLE001
            return {"status": ExecStatus.failed, "error": f"Broadcast rejected by RPC: {e}", "trace": _trace(state, "BROADCAST", "rpc_error")}
    # A Base hash the node could not see yet is "unverified": its contents are checked against the
    # approved digest before any receipt is accepted (see check_submission).
    initial = "submitted" if signed.get("verified") else "unverified"
    sub = TransactionSubmission(step_id=appr.step_id, approval_id=appr.id, chain=chain, tx_id=tx_id, status=initial, explorer_url=explorer_tx_url(chain, tx_id))
    db.add(sub)
    step = await db.get(ExecutionStep, appr.step_id)
    assert step is not None
    step.status = ExecStatus.submitted
    row = await db.get(IntentRow, step.intent_id)
    assert row is not None
    row.status = ExecStatus.submitted
    await db.flush()
    await db.commit()
    return {"submission_id": str(sub.id), "status": ExecStatus.submitted, "trace": _trace(state, "BROADCAST", tx_id)}


async def check_submission(db: AsyncSession, clients: Any, sub: TransactionSubmission) -> str:
    """Updates submission status from chain. Never reports success without a final receipt."""
    from app.db.models import ConfirmedReceipt

    if sub.status in ("mismatch", "confirmed", "failed", "dropped"):
        return sub.status
    sub.checks += 1
    sub.last_checked_at = datetime.now(UTC)
    if sub.chain == "base":
        if sub.status == "unverified":
            onchain = await clients.evm.get_transaction(sub.tx_id)
            if onchain is None:
                return sub.status
            appr = await db.get(ApprovalRecord, sub.approval_id)
            seen = {"chainId": int(onchain.get("chainId", "0x0"), 16), "from": onchain["from"], "to": onchain.get("to") or "", "data": onchain.get("input", "0x"), "value": str(int(onchain.get("value", "0x0"), 16))}
            if appr is None or evm_digest(seen) != appr.digest:
                sub.status = "mismatch"
                return sub.status
            sub.status = "submitted"
        rc = await clients.evm.get_receipt(sub.tx_id)
        if rc is None:
            return sub.status
        head = await clients.evm.block_number()
        bn = int(rc["blockNumber"], 16)
        confs = head - bn + 1
        ok = rc.get("status") == "0x1"
        if not ok:
            sub.status = "failed"
        elif confs >= get_settings().base_confirmations:
            sub.status = "confirmed"
        else:
            return sub.status
        fee = int(rc.get("gasUsed", "0x0"), 16) * int(rc.get("effectiveGasPrice", "0x0"), 16)
        db.add(ConfirmedReceipt(submission_id=sub.id, success=ok, block_or_slot=bn, fee_paid=str(fee), confirmation=f"{confs} confirmations", raw={k: rc.get(k) for k in ("status", "gasUsed", "effectiveGasPrice", "blockHash")}))
    else:
        st = (await clients.solana.get_signature_statuses([sub.tx_id]))[0]
        if st is None:
            step = await db.get(ExecutionStep, sub.step_id)
            lvbh = int((step.unsigned_tx if step else {}).get("last_valid_block_height") or 0)
            if lvbh and await clients.solana.get_block_height() > lvbh:
                sub.status = "dropped"
            return sub.status
        if st.get("err") is not None:
            sub.status = "failed"
        elif st.get("confirmationStatus") in ("confirmed", "finalized"):
            sub.status = "confirmed"
        else:
            return sub.status
        db.add(ConfirmedReceipt(submission_id=sub.id, success=sub.status == "confirmed", block_or_slot=int(st.get("slot", 0)), fee_paid=None, confirmation=str(st.get("confirmationStatus")), raw={"err": st.get("err")}))
    return sub.status


async def verify_confirmation_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, _, clients = _ctx(config)
    sub = await db.get(TransactionSubmission, uuid.UUID(state["submission_id"]))
    assert sub is not None
    status = await check_submission(db, clients, sub)
    await db.commit()
    return {"status": {"confirmed": ExecStatus.confirmed, "failed": ExecStatus.failed, "dropped": ExecStatus.failed, "mismatch": ExecStatus.failed}.get(status, ExecStatus.submitted), "trace": _trace(state, "VERIFY_CONFIRMATION", status)}


async def record_activity_node(state: ExecState, config: RunnableConfig) -> ExecState:
    db, user, _ = _ctx(config)
    if state.get("execution_id"):
        row = await db.get(IntentRow, uuid.UUID(state["execution_id"]))
        # Settle-phase success is finalized by service.advance (it may need to prepare the next
        # step), so only failures are written here. Confirmed/submitted stay "submitted".
        settle_terminal = state.get("phase") == "settle" and state.get("status") in (ExecStatus.failed, ExecStatus.rejected, ExecStatus.expired)
        if row is not None and state.get("status") and state["status"] not in (ExecStatus.awaiting_approval, ExecStatus.confirmed, ExecStatus.submitted):
            if settle_terminal or (state.get("phase") != "settle" and row.status in (ExecStatus.preparing,)):
                row.status = state["status"]
                if state.get("error"):
                    row.status_reason = state["error"]
                elif state.get("questions"):
                    row.status_reason = " ".join(state["questions"])
    await audit(db, "execution.activity", user_id=user.id, subject=state.get("execution_id"), data={"phase": state.get("phase"), "status": state.get("status"), "trace": [t["node"] for t in state.get("trace", [])]})
    await db.commit()
    return {"trace": _trace(state, "RECORD_ACTIVITY", str(state.get("status")))}


# ------------------------------------------------------------------------------------------
# Graph wiring
# ------------------------------------------------------------------------------------------


def _halted(state: ExecState) -> bool:
    return state.get("status") in (ExecStatus.rejected, ExecStatus.needs_clarification, ExecStatus.failed, ExecStatus.expired, ExecStatus.awaiting_approval) and bool(state.get("error") or state.get("questions"))


def _route_start(state: ExecState) -> str:
    return "WAIT_FOR_USER_APPROVAL" if state.get("phase") == "settle" else "PARSE_INTENT"


def _next(ok: str):
    def f(state: ExecState) -> str:
        return "RECORD_ACTIVITY" if _halted(state) else ok
    return f


def _after_wait(state: ExecState) -> str:
    if state.get("phase") != "settle":
        return END
    return "RECORD_ACTIVITY" if _halted(state) else "WALLET_SIGN"


def _after_sign(state: ExecState) -> str:
    if _halted(state):
        return "RECORD_ACTIVITY"
    return "BROADCAST" if (state.get("signed") or {}).get("verified") or state["chain"] == "base" else "RECORD_ACTIVITY"


def build_graph():
    g = StateGraph(ExecState)
    g.add_node("PARSE_INTENT", parse_intent_node)
    g.add_node("VALIDATE_INTENT", validate_intent_node)
    g.add_node("FETCH_CHAIN_DATA", fetch_chain_data_node)
    g.add_node("BUILD_UNSIGNED_TRANSACTION", build_tx_node)
    g.add_node("SIMULATE_TRANSACTION", simulate_node)
    g.add_node("RUN_POLICY_CHECKS", policy_node)
    g.add_node("GENERATE_EXECUTION_PREVIEW", preview_node)
    g.add_node("WAIT_FOR_USER_APPROVAL", wait_for_approval_node)
    g.add_node("WALLET_SIGN", wallet_sign_node)
    g.add_node("BROADCAST", broadcast_node)
    g.add_node("VERIFY_CONFIRMATION", verify_confirmation_node)
    g.add_node("RECORD_ACTIVITY", record_activity_node)

    g.add_conditional_edges(START, _route_start, ["PARSE_INTENT", "WAIT_FOR_USER_APPROVAL"])
    g.add_conditional_edges("PARSE_INTENT", _next("VALIDATE_INTENT"), ["VALIDATE_INTENT", "RECORD_ACTIVITY"])
    g.add_conditional_edges("VALIDATE_INTENT", _next("FETCH_CHAIN_DATA"), ["FETCH_CHAIN_DATA", "RECORD_ACTIVITY"])
    g.add_conditional_edges("FETCH_CHAIN_DATA", _next("BUILD_UNSIGNED_TRANSACTION"), ["BUILD_UNSIGNED_TRANSACTION", "RECORD_ACTIVITY"])
    g.add_conditional_edges("BUILD_UNSIGNED_TRANSACTION", _next("SIMULATE_TRANSACTION"), ["SIMULATE_TRANSACTION", "RECORD_ACTIVITY"])
    g.add_edge("SIMULATE_TRANSACTION", "RUN_POLICY_CHECKS")
    g.add_edge("RUN_POLICY_CHECKS", "GENERATE_EXECUTION_PREVIEW")
    g.add_edge("GENERATE_EXECUTION_PREVIEW", "WAIT_FOR_USER_APPROVAL")
    g.add_conditional_edges("WAIT_FOR_USER_APPROVAL", _after_wait, [END, "WALLET_SIGN", "RECORD_ACTIVITY"])
    g.add_conditional_edges("WALLET_SIGN", _after_sign, ["BROADCAST", "RECORD_ACTIVITY"])
    g.add_conditional_edges("BROADCAST", _next("VERIFY_CONFIRMATION"), ["VERIFY_CONFIRMATION", "RECORD_ACTIVITY"])
    g.add_edge("VERIFY_CONFIRMATION", "RECORD_ACTIVITY")
    g.add_edge("RECORD_ACTIVITY", END)
    return g.compile()


GRAPH = build_graph()


async def run(state: ExecState, db: AsyncSession, user: User, clients: Any) -> ExecState:
    return await GRAPH.ainvoke(state, config={"configurable": {"db": db, "user": user, "clients": clients}})


_ = IntentKind  # re-export for type users
