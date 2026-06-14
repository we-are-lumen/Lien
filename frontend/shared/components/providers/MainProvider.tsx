"use client";

import { appTheme } from "@/shared/theme/app.theme";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

const MainProvider = ({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) => {
  const queryClient = new QueryClient();

  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={appTheme}>
        <Notifications position="top-right" bdrs={0} bg={"white"} />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
};

export default MainProvider;
