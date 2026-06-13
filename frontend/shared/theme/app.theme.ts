import { createTheme } from "@mantine/core";

export const appTheme = createTheme({
    fontFamily: 'DM Sans, sans-serif',
    white: '#FAF9F6',
    colors: {
        primary: [
            '#e8f6f5',
            '#d5ecea',
            '#aad9d4',
            '#7cc4bd',
            '#56b3aa',
            '#3da79d',
            '#218380',
            '#287d7b',
            '#206f6d',
            '#155c5a',
        ],
        gray: [
            '#fafafa',
            '#f4f4f5',
            '#e4e4e7',
            '#d4d4d8',
            '#a1a1aa',
            '#71717a',
            '#52525b',
            '#3f3f46',
            '#27272a',
            '#18181b',
        ]
    },
    primaryColor: 'primary',
    defaultRadius: 'xs'

});