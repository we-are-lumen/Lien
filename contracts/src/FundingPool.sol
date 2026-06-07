// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import {FinancingToken} from "./FinancingToken.sol";

/// @title FundingPool
/// @notice Escrow + milestone-gated disbursement for tokenized financings.
///         Investors fund a financing in USDT0; M1 is auto-released at fund
///         time. Subsequent milestones are released by the AI Verifier role
///         after off-chain document verification.
/// @dev    Designed for MVP simplicity. Roles:
///           owner          — admin (deployer)
///           aiVerifier     — allowed to call releaseMilestone()
///           treasury       — receives origination and performance fees
contract FundingPool is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable usdt;
    FinancingToken public immutable token;
    address public treasury;
    address public aiVerifier;

    uint16 public constant ORIGINATION_FEE_BPS = 150;   // 1.5%
    uint16 public constant PERFORMANCE_FEE_BPS = 1000;  // 10%
    uint16 public constant BPS_DENOM = 10_000;

    struct Deal {
        address investor;
        address supplier;
        uint256 fundedAmount;       // gross USDT0 deposited
        uint256 nominal;            // face value to be repaid by buyer
        uint256 totalRepayment;     // funded + yield
        uint8 milestoneCount;
        uint8 milestonesReleased;   // 0..milestoneCount
        uint8[4] milestoneSplitBps; // percentages * 100 for up to 4 milestones
        bool repaid;
        bool defaulted;
        uint256 createdAt;
    }

    mapping(uint256 => Deal) public deals;        // tokenId -> deal
    mapping(uint256 => mapping(uint8 => bool)) public milestoneReleased;

    event Funded(uint256 indexed tokenId, address indexed investor, uint256 amount);
    event MilestoneReleased(uint256 indexed tokenId, uint8 indexed milestoneIdx, uint256 amount);
    event Repaid(uint256 indexed tokenId, uint256 totalPaid, uint256 toInvestor);
    event Defaulted(uint256 indexed tokenId);
    event AiVerifierUpdated(address indexed newAi);
    event TreasuryUpdated(address indexed newTreasury);
    /// @notice Supplier signals that off-chain proof for a milestone has been
    ///         uploaded to IPFS. AI agent picks this up via subgraph and runs
    ///         verification autonomously. Pure signaling - no state change.
    event ProofSubmitted(
        uint256 indexed tokenId,
        uint8 indexed milestoneIdx,
        string ipfsCid,
        address indexed submittedBy
    );

    error NotAiVerifier();
    error AlreadyFunded();
    error UnknownDeal();
    error MilestoneOutOfRange();
    error MilestoneAlreadyReleased();
    error PriorMilestoneNotReleased();
    error TokenSupplyMissing();
    error AlreadySettled();
    error InsufficientPayment();
    error InvalidMilestoneSplit();
    error NotSupplier();
    error CannotSubmitM1();

    modifier onlyAi() {
        if (msg.sender != aiVerifier) revert NotAiVerifier();
        _;
    }

    constructor(
        address owner_,
        IERC20 usdt_,
        FinancingToken token_,
        address treasury_,
        address aiVerifier_
    ) Ownable(owner_) {
        usdt = usdt_;
        token = token_;
        treasury = treasury_;
        aiVerifier = aiVerifier_;
    }

    // --- Admin ---------------------------------------------------------------

    function setAiVerifier(address newAi) external onlyOwner {
        aiVerifier = newAi;
        emit AiVerifierUpdated(newAi);
    }

    function setTreasury(address newTreasury) external onlyOwner {
        treasury = newTreasury;
        emit TreasuryUpdated(newTreasury);
    }

    // --- Funding -------------------------------------------------------------

    /// @notice Investor funds a published financing. M1 auto-released to supplier.
    /// @param  tokenId         Existing FinancingToken id (minted by this pool)
    /// @param  fundedAmount    Total USDT0 the investor commits
    /// @param  totalRepayment  Funded + expected yield (computed off-chain)
    /// @param  supplier        Recipient of milestone payouts
    /// @param  milestoneSplitBps Percentage * 100 per milestone, zero-padded to 4.
    function fund(
        uint256 tokenId,
        uint256 fundedAmount,
        uint256 totalRepayment,
        address supplier,
        uint8 milestoneCount,
        uint8[4] calldata milestoneSplitBps,
        uint256 nominal
    ) external nonReentrant {
        if (deals[tokenId].fundedAmount > 0) revert AlreadyFunded();
        if (milestoneCount != 3 && milestoneCount != 4) revert MilestoneOutOfRange();

        uint256 sum;
        for (uint8 i = 0; i < milestoneCount; i++) sum += milestoneSplitBps[i];
        if (sum != 100) revert InvalidMilestoneSplit();

        usdt.safeTransferFrom(msg.sender, address(this), fundedAmount);

        // Take origination fee from funded amount upfront.
        uint256 origination = (fundedAmount * ORIGINATION_FEE_BPS) / BPS_DENOM;
        if (origination > 0) usdt.safeTransfer(treasury, origination);

        deals[tokenId] = Deal({
            investor: msg.sender,
            supplier: supplier,
            fundedAmount: fundedAmount,
            nominal: nominal,
            totalRepayment: totalRepayment,
            milestoneCount: milestoneCount,
            milestonesReleased: 0,
            milestoneSplitBps: milestoneSplitBps,
            repaid: false,
            defaulted: false,
            createdAt: block.timestamp
        });

        emit Funded(tokenId, msg.sender, fundedAmount);

        // Auto-release M1.
        _releaseMilestone(tokenId, 1);
    }

    // --- Milestone release ----------------------------------------------------

    /// @notice AI Verifier releases a milestone after off-chain verification.
    function releaseMilestone(uint256 tokenId, uint8 milestoneIdx) external onlyAi nonReentrant {
        _releaseMilestone(tokenId, milestoneIdx);
    }

    // --- Proof submission (off-chain signal) --------------------------------

    /// @notice Supplier emits proof-of-completion for a milestone. Pure
    ///         signaling - no state change. The AI agent watches this event
    ///         via subgraph and runs the verification + release flow.
    ///         M1 cannot be submitted: it auto-releases at fund() time.
    function submitProof(
        uint256 tokenId,
        uint8 milestoneIdx,
        string calldata ipfsCid
    ) external {
        Deal storage d = deals[tokenId];
        if (d.fundedAmount == 0) revert UnknownDeal();
        if (milestoneIdx == 1) revert CannotSubmitM1();
        if (milestoneIdx == 0 || milestoneIdx > d.milestoneCount) revert MilestoneOutOfRange();
        if (milestoneReleased[tokenId][milestoneIdx]) revert MilestoneAlreadyReleased();
        if (msg.sender != d.supplier) revert NotSupplier();
        emit ProofSubmitted(tokenId, milestoneIdx, ipfsCid, msg.sender);
    }

    function _releaseMilestone(uint256 tokenId, uint8 milestoneIdx) internal {
        Deal storage d = deals[tokenId];
        if (d.fundedAmount == 0) revert UnknownDeal();
        if (milestoneIdx == 0 || milestoneIdx > d.milestoneCount) revert MilestoneOutOfRange();
        if (milestoneReleased[tokenId][milestoneIdx]) revert MilestoneAlreadyReleased();
        if (milestoneIdx > 1 && !milestoneReleased[tokenId][milestoneIdx - 1]) {
            revert PriorMilestoneNotReleased();
        }

        // Compute payout from funded amount (post-origination-fee) using bps split.
        uint256 origination = (d.fundedAmount * ORIGINATION_FEE_BPS) / BPS_DENOM;
        uint256 disbursable = d.fundedAmount - origination;
        uint256 payout = (disbursable * d.milestoneSplitBps[milestoneIdx - 1]) / 100;

        milestoneReleased[tokenId][milestoneIdx] = true;
        d.milestonesReleased += 1;
        usdt.safeTransfer(d.supplier, payout);
        emit MilestoneReleased(tokenId, milestoneIdx, payout);
    }

    // --- Repayment -----------------------------------------------------------

    /// @notice Supplier repays the financing. Yield + principal pushed to investor;
    ///         performance fee to treasury. Burns the financing token.
    function repay(uint256 tokenId) external nonReentrant {
        Deal storage d = deals[tokenId];
        if (d.fundedAmount == 0) revert UnknownDeal();
        if (d.repaid || d.defaulted) revert AlreadySettled();

        uint256 total = d.totalRepayment;
        usdt.safeTransferFrom(msg.sender, address(this), total);

        uint256 yieldAmount = total - d.fundedAmount;
        uint256 performance = (yieldAmount * PERFORMANCE_FEE_BPS) / BPS_DENOM;
        uint256 toInvestor = total - performance;

        if (performance > 0) usdt.safeTransfer(treasury, performance);
        usdt.safeTransfer(d.investor, toInvestor);

        d.repaid = true;
        token.setStatus(tokenId, FinancingToken.Status.Repaid);
        token.burn(tokenId, d.investor);
        emit Repaid(tokenId, total, toInvestor);
    }

    // --- Default ------------------------------------------------------------

    /// @notice Investor or owner marks a deal as defaulted after due + grace.
    function markDefaulted(uint256 tokenId) external {
        Deal storage d = deals[tokenId];
        if (d.fundedAmount == 0) revert UnknownDeal();
        if (d.repaid || d.defaulted) revert AlreadySettled();
        // Anyone with the investor token, or owner, can call.
        bool isInvestor = token.balanceOf(msg.sender, tokenId) > 0;
        if (!isInvestor && msg.sender != owner()) revert NotAiVerifier();
        d.defaulted = true;
        token.setStatus(tokenId, FinancingToken.Status.Defaulted);
        emit Defaulted(tokenId);
    }
}
