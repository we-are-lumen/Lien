import { Center, Flex, Text, Title } from "@mantine/core";
import RoleCard from "./components/RoleCard";

const RoleSelectPage = () => {
  return (
    <Center h={"100dvh"}>
      <Flex direction={"column"} align={"center"}>
        <Title ta={"center"}>Welcome to Lien</Title>
        <Text size="lg" ta={"center"}>
          How would you like to use the platform? Select your role to set up
          your dashboard.
        </Text>
        <Flex mt={20} gap={20}>
          <RoleCard
            title="I am a Supplier"
            description="Turn your unpaid invoices and Purchase Orders into instant liquidity."
            benefits={[
              "Get funded within 24 hours.",
              "Financing based on your documents.",
              "Low, flat origination fee with no hidden costs.",
            ]}
          />
          <RoleCard
            title="I am a Investor"
            description="Fund AI-verified real-world businesses and earn uncorrelated yields."
            benefits={[
              "Earn 6% to 18% APR.",
              "Lock-up periods strictly under 120 days.",
              "View full risk reports and scores before you fund.",
            ]}
          />
        </Flex>
      </Flex>
    </Center>
  );
};

export default RoleSelectPage;
