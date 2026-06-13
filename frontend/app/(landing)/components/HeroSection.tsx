import { Button, Center, Flex, Text, Title } from "@mantine/core";

const HeroSection = () => {
  return (
    <Center h={"90dvh"}>
      <Flex direction={"column"} align={"center"} gap={20} px={250}>
        <Title
          size={"3.7rem"}
          ta={"center"}
          fw={900}
          className="tracking-tighter leading-none!"
        >
          Turn Your Invoices and POs <br /> Into Instant Liquidity Within 24
          Hours
        </Title>
        <Text size="lg" ta={"center"}>
          Lien is a blockchain-based financing protocol on the Mantle Network
          that transforms SME invoices and Purchase Orders into instant capital.
          Secure funding without physical collateral, verified by advanced AI,
          and enforced by smart contracts.
        </Text>
        <Flex mt={30} gap={10}>
          <Button size="md">Get Funded</Button>
          <Button size="md" variant="outline">
            Start Investing
          </Button>
        </Flex>
      </Flex>
    </Center>
  );
};

export default HeroSection;
