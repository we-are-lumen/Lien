import LandingNavbar from "@/shared/components/navigations/LandingNavbar";

export default function LandingLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div>
      <LandingNavbar />
      {children}
    </div>
  );
}
