import passAbi from "@agent98/shared/abi/Agent98Pass.json";
import { erc20Abi } from "viem";
import type { Abi } from "viem";

export const PASS_ABI = passAbi as Abi;
export const ERC20_ABI = erc20Abi;
