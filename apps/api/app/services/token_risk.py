"""FORENSIC core: deterministic token / contract risk analysis from raw chain state.

Every finding cites the RPC method and block/slot it came from. No finding is inferred by a
model; token metadata is treated as untrusted data and checked for injection payloads.
"""

from __future__ import annotations

from typing import Any

from eth_utils import keccak

from app.chains.evm import RpcError
from app.chains.solana import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from app.llm.sanitize import looks_like_injection, sanitize_untrusted

SEVERITY = ["info", "low", "medium", "high", "critical"]
EIP1967_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"

RISKY_EVM_FUNCTIONS = {
    "mint(address,uint256)": ("medium", "Privileged mint function present: supply can be inflated."),
    "pause()": ("medium", "Pause function present: transfers can be halted."),
    "blacklist(address)": ("high", "Blacklist function present: holders can be blocked from transferring."),
    "addToBlacklist(address)": ("high", "Blacklist function present: holders can be blocked from transferring."),
    "setBlacklist(address,bool)": ("high", "Blacklist function present."),
    "setFee(uint256)": ("high", "Adjustable fee: transfer tax can be changed by the owner."),
    "setTaxFeePercent(uint256)": ("high", "Adjustable tax percentage."),
    "setMaxTxAmount(uint256)": ("medium", "Owner can limit transaction size (possible sell restriction)."),
    "setTradingEnabled(bool)": ("high", "Owner-controlled trading switch."),
    "enableTrading()": ("medium", "Owner-controlled trading activation."),
    "excludeFromFee(address)": ("low", "Fee exclusion list present."),
    "upgradeTo(address)": ("medium", "Upgrade function present."),
    "selfdestruct()": ("high", "Self-destruct entry point."),
}
_SELECTORS = {keccak(text=sig)[:4]: (sig, sev, msg) for sig, (sev, msg) in RISKY_EVM_FUNCTIONS.items()}


def extract_push4_selectors(code_hex: str) -> set[bytes]:
    """Walk EVM bytecode and collect PUSH4 immediates (function-dispatch selectors)."""
    code = bytes.fromhex(code_hex[2:] if code_hex.startswith("0x") else code_hex)
    out: set[bytes] = set()
    i = 0
    while i < len(code):
        op = code[i]
        if 0x60 <= op <= 0x7F:
            n = op - 0x5F
            if op == 0x63 and i + 5 <= len(code):
                out.add(code[i + 1 : i + 5])
            i += n + 1
        else:
            i += 1
    return out


def _level(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "low"
    return max((f["severity"] for f in findings), key=SEVERITY.index)


def _metadata_findings(fields: dict[str, Any], evidence: str) -> list[dict[str, Any]]:
    out = []
    for k, v in fields.items():
        if isinstance(v, str) and looks_like_injection(v):
            out.append({
                "id": f"metadata_injection_{k}", "severity": "high",
                "message": f"Token {k} contains instruction-like text. It was treated strictly as data and ignored.",
                "evidence": evidence,
            })
    return out


async def scan_base_token(evm, address: str) -> dict[str, Any]:
    addr = address.lower()
    block = await evm.block_number()
    ev = f"base @ block {block}"
    code = await evm.get_code(addr)
    findings: list[dict[str, Any]] = []
    if not code or code == "0x":
        return {
            "chain": "base", "token": addr, "risk_level": "critical", "summary": "Address has no contract code.",
            "findings": [{"id": "no_code", "severity": "critical", "message": "No contract code at this address.", "evidence": f"eth_getCode {ev}"}],
            "metadata": {}, "evidence": [{"method": "eth_getCode", "block": block}], "limitations": [],
        }
    meta_raw = await evm.erc20_metadata(addr)
    findings += _metadata_findings({"name": meta_raw.get("name"), "symbol": meta_raw.get("symbol")}, f"erc20 metadata {ev}")
    meta = {
        "name": sanitize_untrusted(str(meta_raw.get("name") or ""), 64) or None,
        "symbol": sanitize_untrusted(str(meta_raw.get("symbol") or ""), 16) or None,
        "decimals": meta_raw.get("decimals"),
        "total_supply": str(meta_raw["totalSupply"]) if meta_raw.get("totalSupply") is not None else None,
    }
    if meta["decimals"] is None:
        findings.append({"id": "not_erc20", "severity": "high", "message": "decimals() not implemented; not a standard ERC-20.", "evidence": ev})
    elif int(meta["decimals"]) > 18:
        findings.append({"id": "odd_decimals", "severity": "medium", "message": f"Unusual decimals ({meta['decimals']}).", "evidence": ev})
    impl = await evm.get_storage_at(addr, EIP1967_IMPL)
    target_code = code
    if impl and int(impl, 16) != 0:
        impl_addr = "0x" + impl[-40:]
        findings.append({"id": "upgradeable_proxy", "severity": "medium", "message": f"EIP-1967 upgradeable proxy (implementation {impl_addr}); logic can be changed by its admin.", "evidence": f"eth_getStorageAt {ev}"})
        target_code = await evm.get_code(impl_addr)
    try:
        from app.chains.evm import encode_call

        owner_out = await evm.call({"to": addr, "data": encode_call("owner()", [], [])})
        owner = "0x" + owner_out[-40:] if owner_out and len(owner_out) >= 42 else None
        if owner and int(owner, 16) != 0:
            findings.append({"id": "owned", "severity": "low", "message": f"Contract has an owner ({owner}) with privileged functions.", "evidence": f"owner() {ev}"})
            meta["owner"] = owner
        elif owner:
            meta["owner"] = "renounced (zero address)"
    except RpcError:
        meta["owner"] = None
    sels = extract_push4_selectors(target_code)
    for sel, (sig, sev, msg) in _SELECTORS.items():
        if sel in sels:
            findings.append({"id": f"fn_{sig.split('(')[0]}", "severity": sev, "message": f"{msg} [{sig}]", "evidence": f"bytecode selector scan {ev}"})
    level = _level(findings)
    return {
        "chain": "base", "token": addr, "risk_level": level,
        "summary": f"{len(findings)} finding(s); highest severity {level}.", "findings": findings, "metadata": meta,
        "evidence": [{"method": "eth_getCode/eth_getStorageAt/eth_call", "block": block}],
        "limitations": [
            "Static bytecode selector scan can miss obfuscated logic and flag unused code.",
            "Sell-side (honeypot) behaviour is not simulated by this scan; use SIMULATOR for an exact sell simulation.",
        ],
    }


async def scan_solana_mint(sol, mint: str) -> dict[str, Any]:
    acc, slot = await sol.get_account_info(mint)
    ev = f"solana getAccountInfo @ slot {slot}"
    if acc is None:
        return {"chain": "solana", "token": mint, "risk_level": "critical", "summary": "Mint account not found.",
                "findings": [{"id": "not_found", "severity": "critical", "message": "Account does not exist.", "evidence": ev}],
                "metadata": {}, "evidence": [{"method": "getAccountInfo", "slot": slot}], "limitations": []}
    owner = acc.get("owner")
    findings: list[dict[str, Any]] = []
    if owner not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        return {"chain": "solana", "token": mint, "risk_level": "critical", "summary": "Not an SPL token mint.",
                "findings": [{"id": "not_mint", "severity": "critical", "message": f"Owned by {owner}, not an SPL token program.", "evidence": ev}],
                "metadata": {}, "evidence": [{"method": "getAccountInfo", "slot": slot}], "limitations": []}
    info = acc["data"]["parsed"]["info"]
    meta: dict[str, Any] = {
        "decimals": info.get("decimals"), "supply": info.get("supply"), "program": "Token-2022" if owner == TOKEN_2022_PROGRAM else "SPL Token",
        "mint_authority": info.get("mintAuthority"), "freeze_authority": info.get("freezeAuthority"),
    }
    if info.get("mintAuthority"):
        findings.append({"id": "mint_authority", "severity": "medium", "message": f"Mint authority active ({info['mintAuthority']}): supply can be increased.", "evidence": ev})
    if info.get("freezeAuthority"):
        findings.append({"id": "freeze_authority", "severity": "high", "message": f"Freeze authority active ({info['freezeAuthority']}): your token account can be frozen.", "evidence": ev})
    for ext in info.get("extensions", []) or []:
        name, state = ext.get("extension"), ext.get("state", {})
        if name == "transferFeeConfig":
            bps = int((state.get("newerTransferFee") or {}).get("transferFeeBasisPoints", 0))
            sev = "high" if bps >= 1000 else "medium" if bps > 0 else "info"
            findings.append({"id": "transfer_fee", "severity": sev, "message": f"Token-2022 transfer fee {bps / 100:.2f}%.", "evidence": ev})
        elif name == "permanentDelegate":
            findings.append({"id": "permanent_delegate", "severity": "critical", "message": f"Permanent delegate {state.get('delegate')} can move or burn ANY holder's tokens.", "evidence": ev})
        elif name == "transferHook":
            findings.append({"id": "transfer_hook", "severity": "high", "message": f"Transfer hook program {state.get('programId')} runs on every transfer.", "evidence": ev})
        elif name == "nonTransferable":
            findings.append({"id": "non_transferable", "severity": "critical", "message": "Token is non-transferable.", "evidence": ev})
        elif name == "defaultAccountState" and state.get("accountState") == "frozen":
            findings.append({"id": "default_frozen", "severity": "high", "message": "New token accounts start frozen.", "evidence": ev})
        elif name == "mintCloseAuthority":
            findings.append({"id": "mint_close_authority", "severity": "medium", "message": "Mint can be closed by an authority.", "evidence": ev})
        elif name == "tokenMetadata":
            raw = {"name": state.get("name"), "symbol": state.get("symbol"), "uri": state.get("uri")}
            findings += _metadata_findings(raw, ev)
            meta.update({k: sanitize_untrusted(str(v), 64 if k != "uri" else 200) for k, v in raw.items() if v})
    level = _level(findings)
    return {
        "chain": "solana", "token": mint, "risk_level": level, "summary": f"{len(findings)} finding(s); highest severity {level}.",
        "findings": findings, "metadata": meta, "evidence": [{"method": "getAccountInfo(jsonParsed)", "slot": slot}],
        "limitations": ["Liquidity depth and holder concentration require additional data sources (see WHALE/ALPHA)."],
    }
