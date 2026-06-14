"use client";

import { Anchor, Box, Divider, Grid, Group, Stack, Text } from "@mantine/core";

const FOOTER_DATA = [
  {
    title: "Product",
    links: [
      { label: "Invoice Financing", link: "#" },
      { label: "PO Financing", link: "#" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "About", link: "#" },
      { label: "Blog", link: "#" },
      { label: "Careers", link: "#" },
    ],
  },
  {
    title: "Developers",
    links: [
      { label: "Docs", link: "#" },
      { label: "API", link: "#" },
      { label: "Status", link: "#" },
    ],
  },
];

const LandingFooter = () => {
  const groups = FOOTER_DATA.map((group) => {
    const links = group.links.map((link, index) => (
      <Anchor
        key={index}
        href={link.link}
        c="gray.6"
        size="sm"
        underline="hover"
      >
        {link.label}
      </Anchor>
    ));

    return (
      <Grid.Col span={{ base: 12, sm: 4, md: 3 }} key={group.title}>
        <Text fw={700} mb="md" size="md" c="dark">
          {group.title}
        </Text>
        <Stack gap="sm">{links}</Stack>
      </Grid.Col>
    );
  });

  return (
    <Box pt={60} pb={30} px={100} style={{ borderTop: "1.5px solid black" }}>
      <Box>
        <Grid gap="xl">
          <Grid.Col span={{ base: 12, md: 3 }}>
            <Text fw={900} size="xl" mb="md" c="dark">
              LIEN
            </Text>
            <Text c="gray.6" size="sm">
              Trade finance for the future.
            </Text>
          </Grid.Col>

          {groups}
        </Grid>

        <Divider my="xl" color="gray.2" />
        <Group justify="space-between" align="center">
          <Text c="gray.6" size="sm">
            © 2026 LIEN. All rights reserved.
          </Text>

          <Group gap="lg">
            <Anchor href="#" c="gray.6" size="sm" underline="hover">
              Privacy
            </Anchor>
            <Anchor href="#" c="gray.6" size="sm" underline="hover">
              Terms
            </Anchor>
            <Anchor href="#" c="gray.6" size="sm" underline="hover">
              Twitter
            </Anchor>
          </Group>
        </Group>
      </Box>
    </Box>
  );
};

export default LandingFooter;
