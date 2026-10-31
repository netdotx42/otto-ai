// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test, stdStorage, StdStorage} from "forge-std/Test.sol";
import {Agent98Pass} from "../src/Agent98Pass.sol";
import {MockUSDC, FalseReturnToken, ReentrantBuyer, IPass} from "./mocks/MockUSDC.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

contract Agent98PassTest is Test {
    using stdStorage for StdStorage;

    Agent98Pass internal pass;
    MockUSDC internal usdc;

    address internal owner = makeAddr("owner");
    address internal treasury = makeAddr("treasury");
    address internal alice;
    uint256 internal alicePk;
    address internal bob = makeAddr("bob");

    event PassMinted(address indexed buyer, uint256 quantity, uint256 paid, uint256 totalMintedAfter);

    function setUp() public {
        (alice, alicePk) = makeAddrAndKey("alice");
        usdc = new MockUSDC();
        pass = new Agent98Pass(IERC20(address(usdc)), treasury, owner, "https://agent98.xyz/api/pass/{id}.json");
        usdc.mint(alice, 1_000_000e6);
        usdc.mint(bob, 1_000_000e6);
    }

    function _approveAndMint(address who, uint256 q) internal {
        vm.startPrank(who);
        usdc.approve(address(pass), q * pass.PRICE()); // exact approval
        pass.mint(q);
        vm.stopPrank();
    }

    // --- price ------------------------------------------------------------------------------

    function test_PriceIsExactly1_98USDC() public view {
        assertEq(pass.PRICE(), 1_980_000);
        assertEq(pass.quote(1), 1_980_000);
        assertEq(pass.quote(7), 13_860_000);
    }

    function test_MintChargesExactPaymentToTreasury() public {
        uint256 aliceBefore = usdc.balanceOf(alice);
        vm.startPrank(alice);
        usdc.approve(address(pass), 3 * pass.PRICE());
        vm.expectEmit(true, false, false, true, address(pass));
        emit PassMinted(alice, 3, 5_940_000, 3);
        pass.mint(3);
        vm.stopPrank();
        assertEq(usdc.balanceOf(treasury), 5_940_000, "treasury routing");
        assertEq(aliceBefore - usdc.balanceOf(alice), 5_940_000, "exact debit");
        assertEq(usdc.balanceOf(address(pass)), 0, "contract never holds funds");
        assertEq(pass.balanceOf(alice, 1), 3);
        assertEq(pass.totalMinted(), 3);
        assertEq(usdc.allowance(alice, address(pass)), 0, "exact approval fully consumed");
    }

    function test_RevertWhen_InsufficientAllowance() public {
        vm.startPrank(alice);
        usdc.approve(address(pass), 1_979_999);
        vm.expectRevert();
        pass.mint(1);
        vm.stopPrank();
        assertEq(pass.totalMinted(), 0);
    }

    function test_RevertWhen_InsufficientBalance() public {
        address poor = makeAddr("poor");
        usdc.mint(poor, 1_000_000);
        vm.startPrank(poor);
        usdc.approve(address(pass), 1_980_000);
        vm.expectRevert();
        pass.mint(1);
        vm.stopPrank();
    }

    // --- quantity / overflow ---------------------------------------------------------------

    function test_RevertWhen_QuantityZero() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Agent98Pass.InvalidQuantity.selector, 0));
        pass.mint(0);
    }

    function test_RevertWhen_QuantityAboveBatchLimit() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Agent98Pass.InvalidQuantity.selector, 51));
        pass.mint(51);
    }

    function test_RevertWhen_QuantityHuge_NoOverflow() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Agent98Pass.InvalidQuantity.selector, type(uint256).max));
        pass.mint(type(uint256).max);
        vm.expectRevert(abi.encodeWithSelector(Agent98Pass.InvalidQuantity.selector, type(uint256).max));
        pass.quote(type(uint256).max);
    }

    function testFuzz_MintAnyValidQuantity(uint256 q) public {
        q = bound(q, 1, 50);
        _approveAndMint(alice, q);
        assertEq(pass.balanceOf(alice, 1), q);
        assertEq(usdc.balanceOf(treasury), q * 1_980_000);
    }

    // --- unlimited aggregate issuance -------------------------------------------------------

    function test_UnlimitedAggregateIssuance_MultipleMintsPerWallet() public {
        // 40 batches of 50 = 2,000 passes in one wallet: no cap is ever hit.
        for (uint256 i; i < 40; ++i) {
            _approveAndMint(alice, 50);
        }
        assertEq(pass.balanceOf(alice, 1), 2_000);
        assertEq(pass.totalMinted(), 2_000);
    }

    function test_UnlimitedAggregate_TotalMintedAtLargeValue() public {
        // Simulate a very large historical supply and prove minting continues without any cap.
        stdstore.target(address(pass)).sig("totalMinted()").checked_write(uint256(10_000_000_000));
        assertEq(pass.totalMinted(), 10_000_000_000);
        _approveAndMint(bob, 50);
        assertEq(pass.totalMinted(), 10_000_000_050);
    }

    // --- unauthorized mint ------------------------------------------------------------------

    function test_NoFreeMintPath_OwnerCannotMintWithoutPaying() public {
        vm.startPrank(owner);
        vm.expectRevert(); // owner has no USDC/allowance; there is no privileged mint function
        pass.mint(1);
        vm.stopPrank();
        // Low-level probe for common privileged-mint selectors: none exist.
        bytes4[3] memory sels = [bytes4(keccak256("ownerMint(address,uint256)")), bytes4(keccak256("mintTo(address,uint256)")), bytes4(keccak256("airdrop(address[],uint256)"))];
        for (uint256 i; i < sels.length; ++i) {
            vm.prank(owner);
            (bool ok,) = address(pass).call(abi.encodeWithSelector(sels[i], owner, 1));
            assertFalse(ok);
        }
        assertEq(pass.totalMinted(), 0);
    }

    function test_CannotSpendSomeoneElsesAllowance() public {
        vm.prank(alice);
        usdc.approve(address(pass), 10 * 1_980_000);
        // bob tries to mint; payment is always pulled from msg.sender, so alice's allowance is unusable.
        vm.prank(bob);
        vm.expectRevert();
        pass.mint(1);
        assertEq(pass.balanceOf(bob, 1), 0);
        assertEq(usdc.allowance(alice, address(pass)), 10 * 1_980_000);
    }

    function test_SafeERC20_RejectsFalseReturningToken() public {
        FalseReturnToken bad = new FalseReturnToken();
        Agent98Pass p = new Agent98Pass(IERC20(address(bad)), treasury, owner, "");
        vm.prank(alice);
        vm.expectRevert();
        p.mint(1);
        assertEq(p.totalMinted(), 0);
    }

    function test_ReentrancyBlocked() public {
        ReentrantBuyer attacker = new ReentrantBuyer(IPass(address(pass)));
        usdc.mint(address(attacker), 100e6);
        vm.prank(address(attacker));
        usdc.approve(address(pass), type(uint256).max);
        attacker.buy(1);
        assertTrue(attacker.attempted());
        assertFalse(attacker.reentrySucceeded());
        assertEq(pass.balanceOf(address(attacker), 1), 1);
        assertEq(pass.totalMinted(), 1);
    }

    // --- permit -----------------------------------------------------------------------------

    function test_MintWithPermit() public {
        uint256 cost = 2 * 1_980_000;
        uint256 deadline = block.timestamp + 600;
        bytes32 digest = _permitDigest(alice, address(pass), cost, usdc.nonces(alice), deadline);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(alicePk, digest);
        vm.prank(alice);
        pass.mintWithPermit(2, deadline, v, r, s);
        assertEq(pass.balanceOf(alice, 1), 2);
        assertEq(usdc.balanceOf(treasury), cost);
    }

    function test_MintWithPermit_FrontRunPermitDoesNotGrief() public {
        uint256 cost = 1_980_000;
        uint256 deadline = block.timestamp + 600;
        bytes32 digest = _permitDigest(alice, address(pass), cost, usdc.nonces(alice), deadline);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(alicePk, digest);
        // Attacker submits the permit first.
        usdc.permit(alice, address(pass), cost, deadline, v, r, s);
        vm.prank(alice);
        pass.mintWithPermit(1, deadline, v, r, s);
        assertEq(pass.balanceOf(alice, 1), 1);
    }

    function _permitDigest(address o, address sp, uint256 val, uint256 nonce, uint256 deadline)
        internal
        view
        returns (bytes32)
    {
        bytes32 structHash = keccak256(
            abi.encode(
                keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)"),
                o,
                sp,
                val,
                nonce,
                deadline
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", usdc.DOMAIN_SEPARATOR(), structHash));
    }

    // --- admin ------------------------------------------------------------------------------

    function test_PauseBlocksMint_UnpauseRestores() public {
        vm.prank(owner);
        pass.pause();
        vm.startPrank(alice);
        usdc.approve(address(pass), 1_980_000);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        pass.mint(1);
        vm.stopPrank();
        vm.prank(owner);
        pass.unpause();
        vm.prank(alice);
        pass.mint(1);
        assertEq(pass.totalMinted(), 1);
    }

    function test_OnlyOwnerAdmin() public {
        vm.startPrank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        pass.setTreasury(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        pass.pause();
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        pass.setURI("x");
        vm.stopPrank();
    }

    function test_TreasuryRotationRoutesNewPayments() public {
        address t2 = makeAddr("treasury2");
        vm.prank(owner);
        pass.setTreasury(t2);
        _approveAndMint(alice, 1);
        assertEq(usdc.balanceOf(t2), 1_980_000);
        assertEq(usdc.balanceOf(treasury), 0);
    }

    function test_RevertWhen_ZeroTreasury() public {
        vm.prank(owner);
        vm.expectRevert(Agent98Pass.ZeroAddress.selector);
        pass.setTreasury(address(0));
        vm.expectRevert(Agent98Pass.ZeroAddress.selector);
        new Agent98Pass(IERC20(address(usdc)), address(0), owner, "");
    }

    function test_RenounceDisabled() public {
        vm.prank(owner);
        vm.expectRevert(bytes("renounce disabled"));
        pass.renounceOwnership();
    }

    function test_PassesAreTransferable() public {
        _approveAndMint(alice, 2);
        vm.prank(alice);
        pass.safeTransferFrom(alice, bob, 1, 1, "");
        assertEq(pass.balanceOf(bob, 1), 1);
    }
}
