import { Group } from "@mantine/core";
import { XCircleIcon } from "@phosphor-icons/react";

const PainPointListItem = ({ text }: { text: string }) => {
  return (
    <Group gap={"sm"} align="center">
      <XCircleIcon size={"1rem"} weight="fill" />
      <p>{text}</p>
    </Group>
  );
};

export default PainPointListItem;
