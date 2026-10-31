import { getDefaultConfig } from "@rainbow-me/rainbowkit";
import { createConfig, http, type Config } from "wagmi";
import { coinbaseWallet, injected } from "wagmi/connectors";
import { BASE_CHAIN } from "./chains";

const projectId = process.env.NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID;
const rpc = process.env.NEXT_PUBLIC_BASE_RPC_URL;
const transports = { [BASE_CHAIN.id]: http(rpc) } as Record<typeof BASE_CHAIN.id, ReturnType<typeof http>>;

// WalletConnect requires a project id (credential). Without one, injected + Coinbase Wallet only.
export const wagmiConfig: Config = projectId
  ? getDefaultConfig({ appName: "AGENT//98", projectId, chains: [BASE_CHAIN], transports, ssr: true })
  : createConfig({ chains: [BASE_CHAIN], connectors: [injected(), coinbaseWallet({ appName: "AGENT//98" })], transports, ssr: true });
