// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ReputationOracle} from "../src/ReputationOracle.sol";

contract ReputationOracleTest is Test {
    ReputationOracle oracle;
    address pool = makeAddr("pool");
    address supplier = makeAddr("supplier");
    address verifier = makeAddr("verifier");

    function setUp() public {
        oracle = new ReputationOracle(address(this));
        oracle.setWriter(pool, true);
    }

    function test_OnlyWriterCanRecord() public {
        vm.prank(makeAddr("rando"));
        vm.expectRevert(ReputationOracle.NotWriter.selector);
        oracle.recordSubmission(supplier);
    }

    function test_OwnerCanAlsoRecord() public {
        oracle.recordSubmission(supplier);
        (uint256 submitted,,,,,,) = oracle.supplierStats(supplier);
        assertEq(submitted, 1);
    }

    function test_RecordFlow() public {
        vm.startPrank(pool);
        oracle.recordSubmission(supplier);
        oracle.recordFunding(supplier, 10_000);
        oracle.recordRepayment(supplier);
        vm.stopPrank();

        (
            uint256 submitted,
            uint256 funded,
            uint256 repaid,
            uint256 defaults,
            uint256 vol,
            ,
        ) = oracle.supplierStats(supplier);
        assertEq(submitted, 1);
        assertEq(funded, 1);
        assertEq(repaid, 1);
        assertEq(defaults, 0);
        assertEq(vol, 10_000);
    }

    function test_DefaultMarksBlacklisted() public {
        vm.prank(pool);
        oracle.recordDefault(supplier);
        (, , , , , , ReputationOracle.Status status_) = oracle.supplierStats(supplier);
        assertEq(uint8(status_), uint8(ReputationOracle.Status.Blacklisted));
    }

    function test_ScoreFormula() public {
        vm.startPrank(pool);
        // 2 funded, 2 repaid -> 100%
        oracle.recordFunding(supplier, 1);
        oracle.recordFunding(supplier, 1);
        oracle.recordRepayment(supplier);
        oracle.recordRepayment(supplier);
        vm.stopPrank();
        assertEq(oracle.supplierScore(supplier), 100);
    }

    function test_ScorePenaltyOnDefault() public {
        vm.startPrank(pool);
        // 5 funded, 4 repaid, 1 default -> (80) - (20) = 60
        for (uint256 i = 0; i < 5; i++) oracle.recordFunding(supplier, 1);
        for (uint256 i = 0; i < 4; i++) oracle.recordRepayment(supplier);
        oracle.recordDefault(supplier);
        vm.stopPrank();
        assertEq(oracle.supplierScore(supplier), 60);
    }

    function test_VerifierStats() public {
        vm.startPrank(pool);
        oracle.recordVerification(verifier);
        oracle.recordVerification(verifier);
        oracle.recordFalsePositive(verifier);
        vm.stopPrank();
        (uint256 runs, uint256 fp, uint256 fn) = oracle.verifierStats(verifier);
        assertEq(runs, 2);
        assertEq(fp, 1);
        assertEq(fn, 0);
    }
}
