"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlaskConical, LayoutDashboard, Upload } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { EXPERIMENT_NAV, useSelectedExperiment } from "@/lib/selection";

type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  needsExperiment: boolean;
};

export function AppSidebar() {
  const pathname = usePathname();
  const experimentId = useSelectedExperiment();
  const experimentItems: NavItem[] = EXPERIMENT_NAV.map((item) => ({
    label: item.label,
    href: experimentId ? `/experiments/${experimentId}/${item.segment}` : "/experiments",
    icon: item.icon,
    needsExperiment: true,
  }));
  const items: NavItem[] = [
    { label: "实验表单", href: "/experiments", icon: FlaskConical, needsExperiment: false },
    ...experimentItems,
    { label: "端侧仪表盘", href: "/reproductions", icon: LayoutDashboard, needsExperiment: false },
    { label: "导入", href: "/import", icon: Upload, needsExperiment: false },
  ];
  const lockedLabels = EXPERIMENT_NAV.map((item) => item.label).join("、");

  return (
    <Sidebar>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link href="/experiments">
                <span className="text-base font-semibold">研究者工作台</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>研究</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map((item) => {
                const locked = item.needsExperiment && !experimentId;
                const Icon = item.icon;
                return (
                  <SidebarMenuItem key={item.label}>
                    {locked ? (
                      <SidebarMenuButton asChild aria-disabled="true" className="pointer-events-none opacity-50">
                        <Link href="/experiments" tabIndex={-1}>
                          <Icon />
                          <span>{item.label}</span>
                        </Link>
                      </SidebarMenuButton>
                    ) : (
                      <SidebarMenuButton asChild isActive={pathname === item.href}>
                        <Link href={item.href}>
                          <Icon />
                          <span>{item.label}</span>
                        </Link>
                      </SidebarMenuButton>
                    )}
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
            {!experimentId ? (
              <p className="px-2 pt-3 text-xs text-sidebar-foreground/70">尚未选择实验。{lockedLabels}会留在实验表单。</p>
            ) : null}
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
