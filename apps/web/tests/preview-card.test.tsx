import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PreviewCard } from "@/components/preview-card";
import type { Step } from "@/lib/execution";

const base: Step = {
  id: "s", index: 0, kind: "approve", status: "awaiting_approval", digest: "0x" + "ab".repeat(32), unsigned_tx: {}, submissions: [],
  preview: {
    digest: "0x" + "ab".repeat(32), network: "Base Sepolia", action: "Approve exactly 5 USDC for the Uniswap router", type: "approve", step: { index: 1, of: 2 },
    approvals: [{ token: "0x1", symbol: "USDC", spender: "0x2", spender_label: "Uniswap V3 SwapRouter02 (allowlisted)", amount: "5", unlimited: false }],
    simulation: { status: "success", block_or_slot: 123, balance_changes: [{ symbol: "ETH", delta: "-0.0000015" }], limitations: ["state can change"] },
    risk: { level: "low", warnings: [] },
    policy: { allowed: true, checks: [{ id: "approval_exact", status: "pass", message: "Exact-amount approval." }] },
    decoded: { function: "approve" },
  },
};

describe("PreviewCard", () => {
  it("shows exact approval, simulation and policy", () => {
    render(<PreviewCard step={base} chain="base" />);
    expect(screen.getByText(/Approve exactly 5 USDC/)).toBeInTheDocument();
    expect(screen.getByText(/\(exact\)/)).toBeInTheDocument();
    expect(screen.getByText(/success @ block 123/)).toBeInTheDocument();
    expect(screen.getByText(/1\/1 passed/)).toBeInTheDocument();
    expect(screen.getByLabelText("Risk low")).toBeInTheDocument();
  });

  it("shows blocked risk, simulation failure and warnings", () => {
    const step: Step = { ...base, preview: { ...base.preview, simulation: { status: "failed", error: "reverted: STF" }, risk: { level: "blocked", warnings: ["Insufficient balance"] }, policy: { allowed: false, checks: [{ id: "balance_sufficient", status: "fail", message: "Insufficient balance" }] } } };
    render(<PreviewCard step={step} chain="base" />);
    expect(screen.getByLabelText("Risk blocked")).toBeInTheDocument();
    expect(screen.getByText("reverted: STF")).toBeInTheDocument();
    expect(screen.getAllByText("Insufficient balance").length).toBeGreaterThan(0);
  });

  it("shows submitted-but-unconfirmed honestly", () => {
    const step: Step = { ...base, submissions: [{ tx_id: "0x" + "11".repeat(32), status: "submitted", explorer_url: "https://sepolia.basescan.org/tx/0x11", receipt: null }] };
    render(<PreviewCard step={step} chain="base" />);
    const link = screen.getByRole("link");
    expect(link).toHaveTextContent("submitted");
    expect(link).not.toHaveTextContent("receipt ok");
  });
});
