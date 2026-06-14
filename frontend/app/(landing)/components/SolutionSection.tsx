"use client";

import { Center, Flex, Text, Title } from "@mantine/core";
import { DiamondsFourIcon, ExcludeSquareIcon } from "@phosphor-icons/react";
import SolutionCard from "./SolutionCard";

const SolutionSection = () => {
  return (
    <Center my={120}>
      <Flex direction={"column"} align={"center"} gap={10} px={100}>
        <Title className="tracking-tighter">Two Financing Solutions</Title>
        <Text ta={"center"}>
          Purpose-built for invoice and purchase order financing. <br /> Each
          delivers verified liquidity in 24 hours.
        </Text>
        <Flex mt={20} gap={20}>
          <SolutionCard
            icon={<DiamondsFourIcon weight="fill" size={"1.3rem"} />}
            title="Invoice Financing"
            description="Delivered goods or services but waiting for payment? Get paid in 24 hours instead of 60+ days."
            checkItems={[
              "100% advance on invoice value",
              "30–90 day payment tenor",
              "6–12% APR for investors",
              "1–2% platform fee",
            ]}
            idealFor="Suppliers with proven payment history needing immediate working capital"
          />
          <SolutionCard
            icon={<ExcludeSquareIcon weight="fill" size={"1.3rem"} />}
            title="PO Financing"
            description="Secured a PO but lack capital to produce? Get funded upfront to fulfill orders and scale."
            checkItems={[
              "70–80% advance on PO value",
              "60–120 day tenor (includes production cycle)",
              "10–18% APR for investors",
              "Milestone-gated fund releases",
            ]}
            idealFor="Growing manufacturers and suppliers scaling production without external capital"
          />
        </Flex>
      </Flex>
    </Center>
  );
};

export default SolutionSection;
