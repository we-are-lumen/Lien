"use client";

import { Button, Center, Flex, Text, Title } from "@mantine/core";
import RoleCard from "./components/RoleCard";
import { useState } from "react";

const RoleSelectPage = () => {
  const [selectedRole, setselectedRole] = useState<string>();

  return (
    <Center h={"100dvh"}>
      <Flex direction={"column"} align={"center"}>
        <Title ta={"center"} className="tracking-tighter">
          Welcome to Lien
        </Title>
        <Text size="lg" ta={"center"}>
          How would you like to use the platform? Select your role to set up
          your dashboard.
        </Text>
        <Flex mt={20} gap={20}>
          <RoleCard
            isSelected={selectedRole === "supplier"}
            onSelect={() => setselectedRole("supplier")}
            title="I am a Supplier"
            description="Turn your unpaid invoices and Purchase Orders into instant liquidity."
            benefits={[
              "Get funded within 24 hours.",
              "Financing based on your documents.",
              "Low, flat origination fee with no hidden costs.",
            ]}
          />
          <RoleCard
            isSelected={selectedRole === "investor"}
            onSelect={() => setselectedRole("investor")}
            title="I am an Investor"
            description="Fund AI-verified real-world businesses and earn uncorrelated yields."
            benefits={[
              "Earn 6% to 18% APR.",
              "Lock-up periods strictly under 120 days.",
              "View full risk reports and scores before you fund.",
            ]}
          />
        </Flex>
        {selectedRole && (
          <Button mt={50}>
            <p>
              Continue as <span className="capitalize"> {selectedRole}</span>
            </p>
          </Button>
        )}
      </Flex>
    </Center>
  );
};

export default RoleSelectPage;
