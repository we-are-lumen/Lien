// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {InvoiceRegistry} from "../src/InvoiceRegistry.sol";

contract InvoiceRegistryTest is Test {
    InvoiceRegistry reg;

    function setUp() public {
        reg = new InvoiceRegistry();
    }

    function test_RegisterFirstTime() public {
        bytes32 h = keccak256("doc-1");
        assertTrue(reg.register(h));
        assertTrue(reg.isRegistered(h));
        assertEq(reg.hashToIssuer(h), address(this));
        assertGt(reg.hashToTimestamp(h), 0);
    }

    function test_RegisterEmitsEvent() public {
        bytes32 h = keccak256("doc-2");
        vm.expectEmit(true, true, false, false);
        emit InvoiceRegistry.InvoiceRegistered(h, address(this), block.timestamp);
        reg.register(h);
    }

    function test_RevertWhen_RegisterDuplicate() public {
        bytes32 h = keccak256("doc-3");
        reg.register(h);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.AlreadyRegistered.selector, h, address(this)));
        reg.register(h);
    }

    function test_RevertWhen_EmptyHash() public {
        vm.expectRevert(InvoiceRegistry.EmptyHash.selector);
        reg.register(bytes32(0));
    }

    function test_DifferentSendersDifferentDocs() public {
        bytes32 h1 = keccak256("a");
        bytes32 h2 = keccak256("b");

        address alice = makeAddr("alice");
        address bob = makeAddr("bob");

        vm.prank(alice);
        reg.register(h1);
        vm.prank(bob);
        reg.register(h2);

        assertEq(reg.hashToIssuer(h1), alice);
        assertEq(reg.hashToIssuer(h2), bob);
    }

    function testFuzz_RegisterAnyHash(bytes32 h) public {
        vm.assume(h != bytes32(0));
        assertTrue(reg.register(h));
        assertTrue(reg.isRegistered(h));
    }
}
