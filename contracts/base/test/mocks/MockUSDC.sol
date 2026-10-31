// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC20Permit} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import {IERC1155Receiver} from "@openzeppelin/contracts/token/ERC1155/IERC1155Receiver.sol";

/// @dev Test-only 6-decimal USDC with EIP-2612 permit. Never deploy to mainnet.
contract MockUSDC is ERC20, ERC20Permit {
    constructor() ERC20("USD Coin", "USDC") ERC20Permit("USD Coin") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @dev Token whose transferFrom returns false instead of reverting (SafeERC20 must catch it).
contract FalseReturnToken {
    function transferFrom(address, address, uint256) external pure returns (bool) {
        return false;
    }
}

interface IPass {
    function mint(uint256 quantity) external returns (uint256);
}

/// @dev Contract buyer that tries to re-enter `mint` from the ERC-1155 receive hook.
contract ReentrantBuyer is IERC1155Receiver {
    IPass public pass;
    bool public attempted;
    bool public reentrySucceeded;

    constructor(IPass pass_) {
        pass = pass_;
    }

    function buy(uint256 q) external {
        pass.mint(q);
    }

    function onERC1155Received(address, address, uint256, uint256, bytes calldata) external returns (bytes4) {
        if (!attempted) {
            attempted = true;
            try pass.mint(1) {
                reentrySucceeded = true;
            } catch {}
        }
        return this.onERC1155Received.selector;
    }

    function onERC1155BatchReceived(address, address, uint256[] calldata, uint256[] calldata, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        return this.onERC1155BatchReceived.selector;
    }

    function supportsInterface(bytes4 id) external pure returns (bool) {
        return id == type(IERC1155Receiver).interfaceId;
    }
}
