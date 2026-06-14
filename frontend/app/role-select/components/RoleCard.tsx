"use client";

import { Box, Flex, Paper, Text, Title } from "@mantine/core";
import { CheckIcon } from "@phosphor-icons/react";

const RoleCard = ({
  isSelected,
  onSelect,
  title,
  description,
  benefits,
}: {
  isSelected: boolean;
  onSelect: () => void;
  title: string;
  description: string;
  benefits: string[];
}) => {
  return (
    <Paper
      onClick={onSelect}
      bd={isSelected ? "2px solid primary" : "2px solid gray.3"}
      bg={isSelected ? "primary.1" : "transparent"}
      p={20}
      maw={"27rem"}
      className="cursor-pointer transition-all duration-200"
    >
      <Flex align={"center"} gap={10} mb={10}>
        <Title order={3}>{title}</Title>
      </Flex>
      <Text c={"dimmed"}>{description}</Text>
      <Flex mt={20} gap={5} direction={"column"}>
        <Title order={4}>Key Benefits</Title>
        <Flex direction={"column"} gap={5}>
          {benefits.map((item, key) => (
            <Flex key={key} align={"center"} gap={"xs"}>
              <CheckIcon className="shrink-0" />
              <Text>{item}</Text>
            </Flex>
          ))}
        </Flex>
      </Flex>
    </Paper>
  );
};

export default RoleCard;
