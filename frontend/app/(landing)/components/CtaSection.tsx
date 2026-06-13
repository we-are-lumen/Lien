import { Box, Button, Center, Flex, Text, Title } from "@mantine/core";

const CtaSection = () => {
  return (
    <Center px={100} py={100}>
      <Box p={50} bd={"1.5px solid black"} w={"100%"}>
        <Center>
          <Flex direction={"column"} align={"center"}>
            <Title className="tracking-tighter">
              Ready to Unlock Your Business Potential?
            </Title>
            <Text>
              Join the future of Real World Asset financing and keep your
              production cycles moving without delays.
            </Text>
            <Flex mt={30} gap={10}>
              <Button size="md">Submit an Invoice or PO</Button>
              <Button size="md" variant="outline">
                Browse the Marketplace
              </Button>
            </Flex>
          </Flex>
        </Center>
      </Box>
    </Center>
  );
};

export default CtaSection;
