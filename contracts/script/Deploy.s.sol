// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {InvoiceRegistry} from "../src/InvoiceRegistry.sol";
import {FinancingToken} from "../src/FinancingToken.sol";
import {FundingPool} from "../src/FundingPool.sol";
import {ReputationOracle} from "../src/ReputationOracle.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Deploys all four contracts and wires them together. The deployer
///         becomes the owner. Treasury and AI verifier addresses come from
///         env vars.
contract Deploy is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address treasury = vm.envAddress("TREASURY_ADDRESS");
        address ai = vm.envAddress("AI_VERIFIER_ADDRESS");
        address usdt = vm.envAddress("USDT_ADDRESS");

        vm.startBroadcast(deployerKey);

        InvoiceRegistry registry = new InvoiceRegistry();
        console.log("InvoiceRegistry:", address(registry));

        address deployer = vm.addr(deployerKey);

        FinancingToken token = new FinancingToken(deployer);
        console.log("FinancingToken:", address(token));

        FundingPool pool = new FundingPool(deployer, IERC20(usdt), token, treasury, ai);
        console.log("FundingPool:", address(pool));

        // Hand token ownership to the pool so only the pool can mint/burn.
        token.transferOwnership(address(pool));

        ReputationOracle oracle = new ReputationOracle(deployer);
        console.log("ReputationOracle:", address(oracle));

        // Allow pool + AI to write reputation.
        oracle.setWriter(address(pool), true);
        oracle.setWriter(ai, true);

        vm.stopBroadcast();
    }
}
