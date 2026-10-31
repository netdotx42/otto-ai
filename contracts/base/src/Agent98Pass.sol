// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Permit} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Permit.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title AGENT//98 Utility Pass (Base)
/// @notice ERC-1155 utility pass. Each pass costs exactly 1.98 USDC. There is NO aggregate
///         supply cap. `MAX_BATCH` exists only to bound the size of a single transaction.
/// @dev    The ONLY way to create passes is by paying USDC through `mint` / `mintWithPermit`.
///         There is no owner mint, no free mint, and no allowlist mint.
///         Pausing is an emergency control only; it does not change price or supply rules.
contract Agent98Pass is ERC1155, Ownable2Step, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice Token id of the utility pass.
    uint256 public constant PASS_ID = 1;
    /// @notice Price per pass in USDC base units (USDC has 6 decimals): 1.98 USDC.
    uint256 public constant PRICE = 1_980_000;
    /// @notice Per-transaction quantity bound (transaction safety, NOT supply scarcity).
    uint256 public constant MAX_BATCH = 50;

    /// @notice Canonical USDC token for this deployment (immutable).
    IERC20 public immutable usdc;
    /// @notice Treasury that receives every payment.
    address public treasury;
    /// @notice Total passes ever minted (monotonic, unbounded).
    uint256 public totalMinted;

    event PassMinted(address indexed buyer, uint256 quantity, uint256 paid, uint256 totalMintedAfter);
    event TreasuryUpdated(address indexed previous, address indexed current);
    event BaseURIUpdated(string uri);

    error InvalidQuantity(uint256 quantity);
    error ZeroAddress();

    constructor(IERC20 usdc_, address treasury_, address owner_, string memory uri_)
        ERC1155(uri_)
        Ownable(owner_)
    {
        if (address(usdc_) == address(0) || treasury_ == address(0)) revert ZeroAddress();
        usdc = usdc_;
        treasury = treasury_;
        emit TreasuryUpdated(address(0), treasury_);
    }

    // ---------------------------------------------------------------------------------------
    // Minting
    // ---------------------------------------------------------------------------------------

    /// @notice Mint `quantity` passes to the caller, paying `quantity * PRICE` USDC.
    /// @dev Requires a prior exact-amount USDC approval to this contract.
    function mint(uint256 quantity) external nonReentrant whenNotPaused returns (uint256 paid) {
        paid = _collectAndMint(msg.sender, quantity);
    }

    /// @notice Same as `mint` but uses an EIP-2612 permit so no separate approval tx is needed.
    /// @dev The permit is wrapped in try/catch so a front-run permit cannot grief the mint;
    ///      the subsequent transferFrom still enforces that sufficient allowance exists.
    function mintWithPermit(uint256 quantity, uint256 deadline, uint8 v, bytes32 r, bytes32 s)
        external
        nonReentrant
        whenNotPaused
        returns (uint256 paid)
    {
        uint256 cost = _cost(quantity);
        try IERC20Permit(address(usdc)).permit(msg.sender, address(this), cost, deadline, v, r, s) {} catch {}
        paid = _collectAndMint(msg.sender, quantity);
    }

    /// @notice Quote the exact USDC cost for `quantity` passes.
    function quote(uint256 quantity) external pure returns (uint256) {
        return _cost(quantity);
    }

    function _cost(uint256 quantity) internal pure returns (uint256) {
        if (quantity == 0 || quantity > MAX_BATCH) revert InvalidQuantity(quantity);
        return quantity * PRICE; // bounded: 50 * 1_980_000 cannot overflow
    }

    function _collectAndMint(address buyer, uint256 quantity) internal returns (uint256 cost) {
        cost = _cost(quantity);
        // Effects before interactions: supply accounting first.
        uint256 newTotal = totalMinted + quantity;
        totalMinted = newTotal;
        // Interaction 1: pull payment straight to treasury (no funds ever held by this contract).
        usdc.safeTransferFrom(buyer, treasury, cost);
        // Interaction 2: issue passes (may call onERC1155Received on contract buyers; guarded).
        _mint(buyer, PASS_ID, quantity, "");
        emit PassMinted(buyer, quantity, cost, newTotal);
    }

    // ---------------------------------------------------------------------------------------
    // Admin (no ability to mint, change price or cap supply)
    // ---------------------------------------------------------------------------------------

    function setTreasury(address treasury_) external onlyOwner {
        if (treasury_ == address(0)) revert ZeroAddress();
        emit TreasuryUpdated(treasury, treasury_);
        treasury = treasury_;
    }

    function setURI(string calldata uri_) external onlyOwner {
        _setURI(uri_);
        emit BaseURIUpdated(uri_);
    }

    /// @notice Emergency stop for minting. Transfers of existing passes are unaffected.
    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    /// @dev Ownership can never be renounced to avoid locking the emergency switch.
    function renounceOwnership() public pure override {
        revert("renounce disabled");
    }
}
