import { Box, Button, Flex } from "@mantine/core";
import Link from "next/link";

const LandingNavbar = () => {
  return (
    <Box bg="white" pos={"sticky"} top={0} style={{ zIndex: 99 }}>
      <Flex
        px={100}
        py={20}
        justify={"space-between"}
        align={"center"}
        style={{ borderBottom: "1.5px solid black" }}
      >
        <Link href={"/"}>Lien</Link>
        <Flex gap={50}>
          <div>Products</div>
          <div>How It Works</div>
          <div>Why Lien</div>
        </Flex>
        <Button>Get Started</Button>
      </Flex>
    </Box>
  );
};

export default LandingNavbar;
