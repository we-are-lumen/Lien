// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";

/// @title FinancingToken
/// @notice ERC-1155 token representing tokenized invoices or purchase orders.
///         One token id per financing. Holders are the investors that funded
///         the deal. Tokens are transferable (P2P) but not fractional in MVP —
///         supply per id is always 1.
/// @dev    The contract owner is the LIEN FundingPool, which is the only
///         entity allowed to mint and update token metadata.
contract FinancingToken is ERC1155, Ownable {
    using Strings for uint256;

    enum ProductType { Invoice, PO }
    enum Status { Pending, Funded, Repaid, Defaulted }

    struct FinancingData {
        ProductType productType;
        uint8 milestoneCount;       // 3 or 4
        uint8 advanceRate;          // 70..100
        string ipfsUri;             // metadata + AI report
        address issuer;             // supplier
        uint256 nominal;            // face value in USDT0 base units
        uint256 dueDate;            // unix timestamp
        Status status;
    }

    mapping(uint256 => FinancingData) public financings;
    uint256 public nextTokenId = 1;

    event FinancingMinted(uint256 indexed tokenId, address indexed issuer, ProductType productType);
    event StatusUpdated(uint256 indexed tokenId, Status status);

    error NotOwner();
    error InvalidMilestoneCount();
    error InvalidAdvanceRate();
    error UnknownToken(uint256 tokenId);

    constructor(address owner_) ERC1155("") Ownable(owner_) {}

    /// @notice Mint a new financing token to the investor that funded it.
    /// @dev    Restricted to FundingPool. Supply is 1.
    function mint(
        address to,
        ProductType productType,
        uint8 milestoneCount,
        uint8 advanceRate,
        string calldata ipfsUri,
        address issuer,
        uint256 nominal,
        uint256 dueDate
    ) external onlyOwner returns (uint256 tokenId) {
        if (milestoneCount != 3 && milestoneCount != 4) revert InvalidMilestoneCount();
        if (advanceRate < 50 || advanceRate > 100) revert InvalidAdvanceRate();

        tokenId = nextTokenId++;
        financings[tokenId] = FinancingData({
            productType: productType,
            milestoneCount: milestoneCount,
            advanceRate: advanceRate,
            ipfsUri: ipfsUri,
            issuer: issuer,
            nominal: nominal,
            dueDate: dueDate,
            status: Status.Funded
        });
        _mint(to, tokenId, 1, "");
        emit FinancingMinted(tokenId, issuer, productType);
    }

    function setStatus(uint256 tokenId, Status s) external onlyOwner {
        if (financings[tokenId].issuer == address(0)) revert UnknownToken(tokenId);
        financings[tokenId].status = s;
        emit StatusUpdated(tokenId, s);
    }

    function burn(uint256 tokenId, address from) external onlyOwner {
        _burn(from, tokenId, 1);
    }

    function uri(uint256 tokenId) public view override returns (string memory) {
        if (financings[tokenId].issuer == address(0)) revert UnknownToken(tokenId);
        return financings[tokenId].ipfsUri;
    }
}
