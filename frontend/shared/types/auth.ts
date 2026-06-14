import { Eip1193Provider } from "ethers";

// 1. Extend the global window object safely to support MetaMask's provider
declare global {
    interface Window {
        ethereum?: Eip1193Provider & {
            request: (...args: unknown[]) => Promise<unknown>;
        };
    }
}

// 2. Define the user model schema returned by your FastAPI /me endpoint
export interface UserProfile {
    address: string;
    role: string
}

// 3. Define the FastAPI validation/error signature shape ({ detail: "Error text" })
export interface FastAPIErrorDetail {
    detail: string;
}

// 4. Define Ethers-specific error objects for handling rejected signature popups
export interface EthersProviderError extends Error {
    info?: {
        error?: {
            message?: string;
        };
    };
}