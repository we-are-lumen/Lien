// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title InvoiceRegistry
/// @notice On-chain registry of document hashes to prevent double-financing.
///         The hash is `keccak256(buyer || nominal || due_date || doc_number)`
///         computed identically off-chain by the LIEN backend.
/// @dev    Anyone may register; first writer wins. Permissioned by design —
///         only the LIEN backend signer is expected to call register().
contract InvoiceRegistry {
    mapping(bytes32 => bool) public registeredHashes;
    mapping(bytes32 => address) public hashToIssuer;
    mapping(bytes32 => uint256) public hashToTimestamp;

    event InvoiceRegistered(bytes32 indexed hash, address indexed issuer, uint256 timestamp);
    event InvoiceRejected(bytes32 indexed hash, address indexed attemptedIssuer);

    error AlreadyRegistered(bytes32 hash, address existingIssuer);
    error EmptyHash();

    /// @notice Register a new document hash. Reverts if already present.
    /// @param hash The document hash to register.
    function register(bytes32 hash) external returns (bool) {
        if (hash == bytes32(0)) revert EmptyHash();
        if (registeredHashes[hash]) {
            emit InvoiceRejected(hash, msg.sender);
            revert AlreadyRegistered(hash, hashToIssuer[hash]);
        }
        registeredHashes[hash] = true;
        hashToIssuer[hash] = msg.sender;
        hashToTimestamp[hash] = block.timestamp;
        emit InvoiceRegistered(hash, msg.sender, block.timestamp);
        return true;
    }

    function isRegistered(bytes32 hash) external view returns (bool) {
        return registeredHashes[hash];
    }
}
