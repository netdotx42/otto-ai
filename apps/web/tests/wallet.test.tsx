import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { createConfig, http, useAccount, useConnect, useDisconnect, useSwitchChain, WagmiProvider } from "wagmi";
import { base, baseSepolia } from "wagmi/chains";
import { mock } from "wagmi/connectors";

const ACCT = "0xd2135CfB216b74109775236E36d4b433F1DF507B";

function wrap(children: ReactNode) {
  const config = createConfig({
    chains: [baseSepolia, base],
    connectors: [mock({ accounts: [ACCT], features: { reconnect: false } })],
    transports: { [baseSepolia.id]: http(), [base.id]: http() },
  });
  return (
    <WagmiProvider config={config}>
      <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
    </WagmiProvider>
  );
}

function Harness() {
  const { address, chainId, isConnected } = useAccount();
  const { connect, connectors } = useConnect();
  const { disconnect } = useDisconnect();
  const { switchChain } = useSwitchChain();
  return (
    <div>
      <div data-testid="status">{isConnected ? `connected:${address}:${chainId}` : "disconnected"}</div>
      <button onClick={() => connect({ connector: connectors[0] })}>connect</button>
      <button onClick={() => switchChain({ chainId: base.id })}>switch</button>
      <button onClick={() => disconnect()}>disconnect</button>
    </div>
  );
}

describe("wallet connection (wagmi mock connector)", () => {
  it("connects, switches chain and disconnects", async () => {
    const user = userEvent.setup();
    render(wrap(<Harness />));
    expect(screen.getByTestId("status")).toHaveTextContent("disconnected");
    await act(() => user.click(screen.getByText("connect")));
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent(`connected:${ACCT}:${baseSepolia.id}`));
    await act(() => user.click(screen.getByText("switch")));
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent(`:${base.id}`));
    await act(() => user.click(screen.getByText("disconnect")));
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("disconnected"));
  });
});
