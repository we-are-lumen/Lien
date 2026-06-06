// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {FinancingToken} from "../src/FinancingToken.sol";

contract FinancingTokenTest is Test {
    FinancingToken token;
    address owner = address(this);
    address investor = makeAddr("investor");
    address supplier = makeAddr("supplier");

    function setUp() public {
        token = new FinancingToken(owner);
    }

    function _mintDefault() internal returns (uint256) {
        return token.mint(
            investor,
            FinancingToken.ProductType.Invoice,
            3,
            100,
            "ipfs://meta",
            supplier,
            1_000_000,
            block.timestamp + 60 days
        );
    }

    function test_MintAssignsToken() public {
        uint256 id = _mintDefault();
        assertEq(token.balanceOf(investor, id), 1);
        assertEq(token.uri(id), "ipfs://meta");
    }

    function test_RevertWhen_NonOwnerMints() public {
        vm.prank(supplier);
        vm.expectRevert();
        token.mint(
            investor,
            FinancingToken.ProductType.PO,
            4,
            80,
            "ipfs://x",
            supplier,
            500_000,
            block.timestamp + 90 days
        );
    }

    function test_RevertWhen_BadMilestoneCount() public {
        vm.expectRevert(FinancingToken.InvalidMilestoneCount.selector);
        token.mint(investor, FinancingToken.ProductType.Invoice, 5, 100, "x", supplier, 1, 1);
    }

    function test_RevertWhen_BadAdvanceRate() public {
        vm.expectRevert(FinancingToken.InvalidAdvanceRate.selector);
        token.mint(investor, FinancingToken.ProductType.PO, 4, 40, "x", supplier, 1, 1);
    }

    function test_SetStatus() public {
        uint256 id = _mintDefault();
        token.setStatus(id, FinancingToken.Status.Repaid);
        (, , , , , , , FinancingToken.Status s) = token.financings(id);
        assertEq(uint8(s), uint8(FinancingToken.Status.Repaid));
    }

    function test_Burn() public {
        uint256 id = _mintDefault();
        token.burn(id, investor);
        assertEq(token.balanceOf(investor, id), 0);
    }

    function test_RevertWhen_UnknownTokenUri() public {
        vm.expectRevert();
        token.uri(999);
    }
}
