import type { ReactNode } from "react";
import { cookies } from "next/headers";

import { Shell } from "@/components/shell";

export default async function MainLayout({ children }: Readonly<{ children: ReactNode }>) {
  const cookieStore = await cookies();
  const defaultOpen = cookieStore.get("sidebar_state")?.value !== "false";
  return <Shell defaultOpen={defaultOpen}>{children}</Shell>;
}
