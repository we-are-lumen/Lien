"use client";

import { useWeb3Auth } from "@/shared/hooks/useWeb3Auth";
import { Box, Button, Flex } from "@mantine/core";
import Link from "next/link";

const LandingNavbar = () => {
  const { login, isLoginPending } = useWeb3Auth();

  return (
    <Box bg="white" pos={"sticky"} top={0} style={{ zIndex: 99 }}>
      <Flex
        px={100}
        py={20}
        justify={"space-between"}
        align={"center"}
        style={{ borderBottom: "1.5px solid black" }}
      >
        <Link
          href={"/"}
          style={{ fontWeight: "bold", textDecoration: "none", color: "black" }}
        >
          Lien
        </Link>

        <Flex gap={50} align={"center"}>
          <div>Products</div>
          <div>How It Works</div>
          <div>Why Lien</div>
        </Flex>

        <Flex gap={20} align={"center"}>
          <Button onClick={() => login()} loading={isLoginPending}>
            Get Started
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
};

export default LandingNavbar;
