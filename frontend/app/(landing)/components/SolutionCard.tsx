"use client";

import { Box, Flex, Group, Paper, Text, Title } from "@mantine/core";
import { CheckIcon } from "@phosphor-icons/react";
import { ReactNode } from "react";

const SolutionCard = ({
  icon,
  title,
  description,
  checkItems,
  idealFor,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  checkItems: string[];
  idealFor: string;
}) => {
  return (
    <Paper bd={"1px solid black"} p={20} maw={"27rem"}>
      <Group gap={"xs"} mb={16}>
        {icon}
        <Title order={3}>{title}</Title>
      </Group>
      <Text c={"dimmed"}>{description}</Text>
      <Flex direction={"column"} my={"md"} gap={5}>
        {checkItems.map((item, index) => (
          <Group key={index} gap={"xs"}>
            <CheckIcon />
            <Text>{item}</Text>
          </Group>
        ))}
      </Flex>
      <Box p={16} bg={"gray.1"}>
        <Text size="sm" c={"dimmed"}>
          <strong>Ideal for: </strong>
          {idealFor}.
        </Text>
      </Box>
    </Paper>
  );
};

export default SolutionCard;
