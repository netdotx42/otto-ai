// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script, console2} from "forge-std/Script.sol";
import {Agent98Pass} from "../src/Agent98Pass.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Deploys Agent98Pass. Canonical USDC is enforced per chain id.
/// Env: TREASURY_ADDRESS, PASS_OWNER_ADDRESS, PASS_METADATA_URI
/// Usage (Base Sepolia):
///   forge script script/Deploy.s.sol --rpc-url base_sepolia --broadcast --verify --account deployer
contract Deploy is Script {
    address internal constant USDC_BASE = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address internal constant USDC_BASE_SEPOLIA = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;

    function run() external returns (Agent98Pass pass) {
        address usdc;
        if (block.chainid == 8453) usdc = USDC_BASE;
        else if (block.chainid == 84532) usdc = USDC_BASE_SEPOLIA;
        else usdc = vm.envAddress("LOCAL_USDC_ADDRESS"); // anvil / local only
        address treasury = vm.envAddress("TREASURY_ADDRESS");
        address owner = vm.envAddress("PASS_OWNER_ADDRESS");
        string memory uri = vm.envOr("PASS_METADATA_URI", string("https://agent98.xyz/api/pass/{id}.json"));

        vm.startBroadcast();
        pass = new Agent98Pass(IERC20(usdc), treasury, owner, uri);
        vm.stopBroadcast();

        console2.log("Agent98Pass", address(pass));
        console2.log("USDC", usdc);
        console2.log("chainId", block.chainid);
        string memory json = string.concat(
            '{"chainId":', vm.toString(block.chainid), ',"pass":"', vm.toString(address(pass)),
            '","usdc":"', vm.toString(usdc), '","treasury":"', vm.toString(treasury), '","block":',
            vm.toString(block.number), "}"
        );
        vm.writeFile(string.concat("deployments/", vm.toString(block.chainid), ".json"), json);
    }
}
