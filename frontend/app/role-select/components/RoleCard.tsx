"use client";

import { Box, Flex, Paper, Text, Title } from "@mantine/core";
import { CheckIcon } from "@phosphor-icons/react";

const RoleCard = ({
  title,
  description,
  benefits,
}: {
  title: string;
  description: string;
  benefits: string[];
}) => {
  return (
    <Paper
      bd={"1px solid black"}
      p={20}
      maw={"27rem"}
      className="cursor-pointer hover:bg-neutral-100!"
    >
      <Flex align={"center"} gap={10} mb={10}>
        <Box w={16} h={16} bd={"2px solid black"} bdrs={99}></Box>
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
