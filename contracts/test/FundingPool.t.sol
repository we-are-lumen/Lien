// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {FundingPool} from "../src/FundingPool.sol";
import {FinancingToken} from "../src/FinancingToken.sol";
import {MockUSDT} from "./mocks/MockUSDT.sol";

contract FundingPoolTest is Test {
    FundingPool pool;
    FinancingToken token;
    MockUSDT usdt;

    address owner = address(this);
    address investor = makeAddr("investor");
    address supplier = makeAddr("supplier");
    address buyer = makeAddr("buyer");
    address treasury = makeAddr("treasury");
    address ai = makeAddr("ai-verifier");

    function setUp() public {
        usdt = new MockUSDT();
        token = new FinancingToken(owner);
        pool = new FundingPool(owner, usdt, token, treasury, ai);
        token.transferOwnership(address(pool));

        // Give actors some USDT.
        usdt.mintTo(investor, 1_000_000 * 10 ** 6);
        usdt.mintTo(supplier, 1_000_000 * 10 ** 6);
        usdt.mintTo(buyer, 1_000_000 * 10 ** 6);
    }

    function _mintToken(address to) internal returns (uint256) {
        // Owner of token is the pool. Use pool's owner key (this contract) to
        // ask pool to mint via a helper. Easier: prank pool to call mint.
        vm.prank(address(pool));
        return token.mint(
            to,
            FinancingToken.ProductType.Invoice,
            3,
            100,
            "ipfs://x",
            supplier,
            10_000 * 10 ** 6,
            block.timestamp + 60 days
        );
    }

    function _fund(uint256 fundedAmount, uint256 yieldAmount) internal returns (uint256 tokenId) {
        tokenId = _mintToken(investor);
        uint8[4] memory split = [30, 50, 20, 0];

        vm.startPrank(investor);
        usdt.approve(address(pool), fundedAmount);
        pool.fund(
            tokenId,
            fundedAmount,
            fundedAmount + yieldAmount,
            supplier,
            3,
            split,
            10_000 * 10 ** 6
        );
        vm.stopPrank();
    }

    function test_FundAutoReleasesM1() public {
        uint256 funded = 10_000 * 10 ** 6;
        uint256 yieldAmt = 700 * 10 ** 6;
        uint256 supplierBefore = usdt.balanceOf(supplier);
        uint256 treasuryBefore = usdt.balanceOf(treasury);

        uint256 tokenId = _fund(funded, yieldAmt);

        // origination = 150 USDT (1.5%)
        uint256 origination = (funded * 150) / 10_000;
        assertEq(usdt.balanceOf(treasury), treasuryBefore + origination);

        // M1 payout = (funded - origination) * 30%
        uint256 disbursable = funded - origination;
        uint256 m1 = (disbursable * 30) / 100;
        assertEq(usdt.balanceOf(supplier), supplierBefore + m1);

        // Subsequent milestones still pending
        assertTrue(pool.milestoneReleased(tokenId, 1));
        assertFalse(pool.milestoneReleased(tokenId, 2));
        assertFalse(pool.milestoneReleased(tokenId, 3));
    }

    function test_RevertWhen_NonAiReleasesMilestone() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);
        vm.expectRevert(FundingPool.NotAiVerifier.selector);
        pool.releaseMilestone(tokenId, 2);
    }

    function test_AiReleasesMilestonesInOrder() public {
        uint256 funded = 10_000 * 10 ** 6;
        uint256 tokenId = _fund(funded, 700 * 10 ** 6);

        // Release M2
        vm.prank(ai);
        pool.releaseMilestone(tokenId, 2);
        assertTrue(pool.milestoneReleased(tokenId, 2));

        // Release M3
        vm.prank(ai);
        pool.releaseMilestone(tokenId, 3);
        assertTrue(pool.milestoneReleased(tokenId, 3));
    }

    function test_RevertWhen_SkippingMilestone() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);

        vm.prank(ai);
        vm.expectRevert(FundingPool.PriorMilestoneNotReleased.selector);
        pool.releaseMilestone(tokenId, 3);
    }

    function test_RevertWhen_DoubleReleaseMilestone() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);

        vm.prank(ai);
        pool.releaseMilestone(tokenId, 2);
        vm.prank(ai);
        vm.expectRevert(FundingPool.MilestoneAlreadyReleased.selector);
        pool.releaseMilestone(tokenId, 2);
    }

    function test_Repay() public {
        uint256 funded = 10_000 * 10 ** 6;
        uint256 yieldAmt = 700 * 10 ** 6;
        uint256 total = funded + yieldAmt;
        uint256 tokenId = _fund(funded, yieldAmt);

        // Supplier repays.
        vm.startPrank(supplier);
        usdt.approve(address(pool), total);
        uint256 investorBefore = usdt.balanceOf(investor);
        uint256 treasuryBefore = usdt.balanceOf(treasury);
        pool.repay(tokenId);
        vm.stopPrank();

        // Performance fee = 10% of yield = 70 USDT
        uint256 performance = (yieldAmt * 1000) / 10_000;
        assertEq(usdt.balanceOf(treasury), treasuryBefore + performance);

        // Investor gets total - performance fee
        assertEq(usdt.balanceOf(investor), investorBefore + (total - performance));

        // Token burnt
        assertEq(token.balanceOf(investor, tokenId), 0);
    }

    function test_RevertWhen_DoubleFund() public {
        uint256 funded = 10_000 * 10 ** 6;
        uint256 yieldAmt = 700 * 10 ** 6;
        uint256 tokenId = _fund(funded, yieldAmt);

        vm.startPrank(investor);
        usdt.approve(address(pool), funded);
        uint8[4] memory split = [30, 50, 20, 0];
        vm.expectRevert(FundingPool.AlreadyFunded.selector);
        pool.fund(tokenId, funded, funded + yieldAmt, supplier, 3, split, 10_000 * 10 ** 6);
        vm.stopPrank();
    }

    function test_RevertWhen_BadMilestoneSplit() public {
        uint256 tokenId = _mintToken(investor);
        uint8[4] memory bad = [30, 30, 20, 0]; // sums to 80

        vm.startPrank(investor);
        usdt.approve(address(pool), 10_000 * 10 ** 6);
        vm.expectRevert(FundingPool.InvalidMilestoneSplit.selector);
        pool.fund(tokenId, 10_000 * 10 ** 6, 10_700 * 10 ** 6, supplier, 3, bad, 10_000 * 10 ** 6);
        vm.stopPrank();
    }

    function test_MarkDefaulted() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);

        // Investor (token holder) marks as defaulted.
        vm.prank(investor);
        pool.markDefaulted(tokenId);

        // Public getter skips the fixed-size array field; layout: investor,
        // supplier, fundedAmount, nominal, totalRepayment, milestoneCount,
        // milestonesReleased, repaid, defaulted, createdAt.
        (, , , , , , , bool repaid, bool defaulted, ) = pool.deals(tokenId);
        assertFalse(repaid);
        assertTrue(defaulted);
    }

    function test_RevertWhen_RepayingDefaulted() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);
        vm.prank(investor);
        pool.markDefaulted(tokenId);

        vm.startPrank(supplier);
        usdt.approve(address(pool), 10_700 * 10 ** 6);
        vm.expectRevert(FundingPool.AlreadySettled.selector);
        pool.repay(tokenId);
        vm.stopPrank();
    }

    // --- submitProof ---------------------------------------------------------

    event ProofSubmitted(
        uint256 indexed tokenId,
        uint8 indexed milestoneIdx,
        string ipfsCid,
        address indexed submittedBy
    );

    function test_submitProof_emitsEvent() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);
        string memory cid = "bafkreigh2akiscaildc";

        vm.expectEmit(true, true, true, true, address(pool));
        emit ProofSubmitted(tokenId, 2, cid, supplier);

        vm.prank(supplier);
        pool.submitProof(tokenId, 2, cid);
    }

    function test_submitProof_revertsIfNotSupplier() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);

        vm.expectRevert(FundingPool.NotSupplier.selector);
        vm.prank(investor);
        pool.submitProof(tokenId, 2, "bafkreix");
    }

    function test_submitProof_revertsIfM1() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);

        vm.expectRevert(FundingPool.CannotSubmitM1.selector);
        vm.prank(supplier);
        pool.submitProof(tokenId, 1, "bafkreix");
    }

    function test_submitProof_revertsIfAlreadyReleased() public {
        uint256 tokenId = _fund(10_000 * 10 ** 6, 700 * 10 ** 6);

        // Release M2 via the AI verifier.
        vm.prank(ai);
        pool.releaseMilestone(tokenId, 2);

        vm.expectRevert(FundingPool.MilestoneAlreadyReleased.selector);
        vm.prank(supplier);
        pool.submitProof(tokenId, 2, "bafkreix");
    }

    function test_submitProof_revertsIfUnknownDeal() public {
        uint256 unknownTokenId = 9_999_999;

        vm.expectRevert(FundingPool.UnknownDeal.selector);
        vm.prank(supplier);
        pool.submitProof(unknownTokenId, 2, "bafkreix");
    }

    // --- fundWithRef ---------------------------------------------------------

    event FundedWithRef(
        uint256 indexed tokenId,
        address indexed investor,
        bytes32 indexed financingRef,
        uint256 amount
    );

    function test_fundWithRef_emitsRefEvent() public {
        uint256 tokenId = _mintToken(investor);
        uint256 funded = 10_000 * 10 ** 6;
        uint256 yieldAmt = 700 * 10 ** 6;
        uint8[4] memory split = [30, 50, 20, 0];
        bytes32 ref = keccak256(abi.encodePacked("123e4567-e89b-12d3-a456-426614174000"));

        vm.startPrank(investor);
        usdt.approve(address(pool), funded);

        vm.expectEmit(true, true, true, true, address(pool));
        emit FundedWithRef(tokenId, investor, ref, funded);

        pool.fundWithRef(
            tokenId,
            funded,
            funded + yieldAmt,
            supplier,
            3,
            split,
            10_000 * 10 ** 6,
            ref
        );
        vm.stopPrank();
    }

    function test_fundWithRef_sameSideEffectsAsFund() public {
        // Two parallel mints, two parallel funds: one via fund(), one via
        // fundWithRef(). Treasury fee, M1 payout, and deal state must match.
        uint256 funded = 10_000 * 10 ** 6;
        uint256 yieldAmt = 700 * 10 ** 6;
        uint8[4] memory split = [30, 50, 20, 0];

        uint256 supplierBefore = usdt.balanceOf(supplier);
        uint256 treasuryBefore = usdt.balanceOf(treasury);

        uint256 tokenA = _fund(funded, yieldAmt);

        uint256 supplierAfterA = usdt.balanceOf(supplier);
        uint256 treasuryAfterA = usdt.balanceOf(treasury);

        uint256 tokenB = _mintToken(investor);
        vm.startPrank(investor);
        usdt.approve(address(pool), funded);
        pool.fundWithRef(
            tokenB,
            funded,
            funded + yieldAmt,
            supplier,
            3,
            split,
            10_000 * 10 ** 6,
            keccak256(abi.encodePacked("ref-b"))
        );
        vm.stopPrank();

        // Both deals applied the same origination fee and M1 payout.
        assertEq(
            usdt.balanceOf(treasury) - treasuryAfterA,
            treasuryAfterA - treasuryBefore,
            "treasury fee parity"
        );
        assertEq(
            usdt.balanceOf(supplier) - supplierAfterA,
            supplierAfterA - supplierBefore,
            "supplier M1 payout parity"
        );

        // M1 released on both deals.
        assertTrue(pool.milestoneReleased(tokenA, 1));
        assertTrue(pool.milestoneReleased(tokenB, 1));
    }
}
