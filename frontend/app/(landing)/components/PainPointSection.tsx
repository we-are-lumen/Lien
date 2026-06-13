"use client";

import { Center, Flex, Paper, Text, Title } from "@mantine/core";
import PainPointListItem from "./PainPointListItem";

const PainPointSection = () => {
  return (
    <Center pb={50} h={"80dvh"}>
      <Flex direction={"column"} align={"center"} gap={10} px={100}>
        <Title className="tracking-tighter">The Trade Finance Problem</Title>
        <Text ta={"center"}>
          SMEs across Southeast Asia face a $45 billion funding gap. <br />{" "}
          Traditional options are slow, expensive, or inaccessible.
        </Text>
        <Flex mt={20} gap={20}>
          <Paper bd={"1.5px solid black"} p={20}>
            <Title order={3} mb={10}>
              For Suppliers
            </Title>
            <Flex gap={5} direction={"column"}>
              <PainPointListItem text="Banks require collateral, won't fund SMEs" />
              <PainPointListItem text="60+ day payment terms drain working capital" />
              <PainPointListItem text="Alternative lenders charge 10–15% per month" />
            </Flex>
          </Paper>
          <Paper bd={"1.5px solid black"} p={20}>
            <Title order={3} mb={10}>
              For Investors
            </Title>
            <Flex gap={5} direction={"column"}>
              <PainPointListItem text="T-bills and bonds yield 3–4% in current environment" />
              <PainPointListItem text="RWA platforms require minimums of $250K or more" />
              <PainPointListItem text="Limited visibility into underlying assets and risks" />
            </Flex>
          </Paper>
        </Flex>
      </Flex>
    </Center>
  );
};

export default PainPointSection;
