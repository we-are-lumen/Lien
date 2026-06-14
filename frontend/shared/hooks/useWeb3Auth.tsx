import api from "@/api/api.instance";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { AxiosError } from "axios";
import { BrowserProvider } from "ethers";
import { useState } from "react";
import {
  EthersProviderError,
  FastAPIErrorDetail,
  UserProfile,
} from "../types/auth";
import { useRouter } from "next/navigation";
import { notifications } from "@mantine/notifications";

export function useWeb3Auth() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [customError, setCustomError] = useState<string>("");

  const {
    data: user = null,
    isLoading: isProfileLoading,
    error: profileError,
  } = useQuery<UserProfile | null, Error>({
    queryKey: ["auth-user"],
    queryFn: async (): Promise<UserProfile | null> => {
      const token =
        typeof window !== "undefined" ? localStorage.getItem("token") : null;
      if (!token) return null;

      try {
        const res = await api.get<UserProfile>("/me");
        return res.data;
      } catch {
        if (typeof window !== "undefined") {
          localStorage.removeItem("token");
        }
        return null;
      }
    },
  });

  const loginMutation = useMutation<
    string,
    Error | AxiosError<FastAPIErrorDetail>
  >({
    mutationFn: async (): Promise<string> => {
      setCustomError("");

      if (!window.ethereum) {
        throw new Error(
          "MetaMask is not installed. Please install it to continue.",
        );
      }

      const provider = new BrowserProvider(window.ethereum);
      const signer = await provider.getSigner();
      const address = await signer.getAddress();

      const nonceRes = await api.get<{ nonce: string }>(
        `/auth/nonce/${address}`,
      );
      const nonce = nonceRes.data.nonce;

      const signature = await signer.signMessage(nonce);

      const verifyRes = await api.post<{ access_token: string }>(
        "/auth/verify",
        {
          address,
          signature,
        },
      );

      return verifyRes.data.access_token;
    },
    onSuccess: async (accessToken: string) => {
      if (typeof window !== "undefined") {
        localStorage.setItem("token", accessToken);
      }
      queryClient.invalidateQueries({ queryKey: ["auth-user"] });

      const user = await api.get<UserProfile>("/me");
      localStorage.setItem("role", user.data.role);

      notifications.show({
        title: "Login Successful",
        message: "Welcome back to LIEN dashboard workspace.",
        color: "green",
        withCloseButton: true,
        autoClose: 4000,
      });

      router.push("/dashboard");
    },
    onError: (err: Error | AxiosError<FastAPIErrorDetail>) => {
      let errMsg = "Authentication failed.";

      if (axios.isAxiosError(err)) {
        errMsg = err.response?.data?.detail || err.message;
      } else {
        const providerErr = err as EthersProviderError;
        errMsg = providerErr.info?.error?.message || err.message;
      }

      setCustomError(errMsg);

      notifications.show({
        title: "Authentication Failed",
        message: errMsg,
        color: "red",
        withCloseButton: true,
        autoClose: 5000,
      });
    },
  });

  const logout = (): void => {
    if (typeof window !== "undefined") {
      localStorage.removeItem("token");
    }
    queryClient.setQueryData(["auth-user"], null);

    notifications.show({
      title: "Logged Out",
      message: "Your active wallet session has been cleared.",
      color: "gray",
    });
  };

  return {
    user,
    logout,
    login: loginMutation.mutate,
    isLoginPending: loginMutation.isPending,
    isProfileLoading,
    error: customError || profileError?.message || "",
  };
}
