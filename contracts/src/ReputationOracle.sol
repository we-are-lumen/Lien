// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title ReputationOracle
/// @notice Soulbound reputation tracking for suppliers and the AI verifier.
///         Not a full ERC-8004 implementation — keeps the on-chain accounting
///         lean: supplier stats are updated by the FundingPool; AI verifier
///         stats are updated by the backend after outcomes are known.
/// @dev    Soulbound = no transfer. Each address has at most one record per role.
contract ReputationOracle is Ownable {
    struct SupplierStats {
        uint256 invoicesSubmitted;
        uint256 invoicesFunded;
        uint256 invoicesRepaid;
        uint256 defaultCount;
        uint256 totalVolume;
        uint256 freezeCount;
        Status status;
    }

    struct VerifierStats {
        uint256 verificationsRun;
        uint256 falsePositiveCount;
        uint256 falseNegativeCount;
    }

    enum Status { Active, Frozen, Blacklisted, FraudConfirmed }

    mapping(address => SupplierStats) public supplierStats;
    mapping(address => VerifierStats) public verifierStats;
    mapping(address => bool) public writers;          // FundingPool + backend AI

    event SupplierUpdated(address indexed supplier);
    event VerifierUpdated(address indexed verifier);
    event StatusChanged(address indexed supplier, Status status);
    event WriterSet(address indexed writer, bool allowed);

    error NotWriter();

    modifier onlyWriter() {
        if (!writers[msg.sender] && msg.sender != owner()) revert NotWriter();
        _;
    }

    constructor(address owner_) Ownable(owner_) {}

    function setWriter(address writer, bool allowed) external onlyOwner {
        writers[writer] = allowed;
        emit WriterSet(writer, allowed);
    }

    // --- Supplier writes ----------------------------------------------------

    function recordSubmission(address supplier) external onlyWriter {
        supplierStats[supplier].invoicesSubmitted += 1;
        emit SupplierUpdated(supplier);
    }

    function recordFunding(address supplier, uint256 amount) external onlyWriter {
        SupplierStats storage s = supplierStats[supplier];
        s.invoicesFunded += 1;
        s.totalVolume += amount;
        emit SupplierUpdated(supplier);
    }

    function recordRepayment(address supplier) external onlyWriter {
        supplierStats[supplier].invoicesRepaid += 1;
        emit SupplierUpdated(supplier);
    }

    function recordDefault(address supplier) external onlyWriter {
        SupplierStats storage s = supplierStats[supplier];
        s.defaultCount += 1;
        s.status = Status.Blacklisted;
        emit SupplierUpdated(supplier);
        emit StatusChanged(supplier, Status.Blacklisted);
    }

    function recordFreeze(address supplier) external onlyWriter {
        SupplierStats storage s = supplierStats[supplier];
        s.freezeCount += 1;
        s.status = Status.Frozen;
        emit StatusChanged(supplier, Status.Frozen);
    }

    function setStatus(address supplier, Status status_) external onlyWriter {
        supplierStats[supplier].status = status_;
        emit StatusChanged(supplier, status_);
    }

    // --- Verifier writes ---------------------------------------------------

    function recordVerification(address verifier) external onlyWriter {
        verifierStats[verifier].verificationsRun += 1;
        emit VerifierUpdated(verifier);
    }

    function recordFalsePositive(address verifier) external onlyWriter {
        verifierStats[verifier].falsePositiveCount += 1;
        emit VerifierUpdated(verifier);
    }

    function recordFalseNegative(address verifier) external onlyWriter {
        verifierStats[verifier].falseNegativeCount += 1;
        emit VerifierUpdated(verifier);
    }

    // --- Views -------------------------------------------------------------

    /// @notice Score = (repaid / funded) * 100 - (defaults * 20). Capped at [0, 100].
    function supplierScore(address supplier) external view returns (uint256) {
        SupplierStats memory s = supplierStats[supplier];
        if (s.invoicesFunded == 0) return 0;
        uint256 ratio = (s.invoicesRepaid * 100) / s.invoicesFunded;
        uint256 penalty = s.defaultCount * 20;
        if (penalty >= ratio) return 0;
        uint256 score = ratio - penalty;
        return score > 100 ? 100 : score;
    }
}
