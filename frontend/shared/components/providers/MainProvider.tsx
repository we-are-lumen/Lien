'use client'

import { appTheme } from '@/shared/theme/app.theme';
import { MantineProvider } from '@mantine/core';
import React from 'react'

const MainProvider = ({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) => {
  return <MantineProvider theme={appTheme}>{children}</MantineProvider>
};

export default MainProvider