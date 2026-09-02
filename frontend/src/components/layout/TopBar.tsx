
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Stats } from "@/types/api";
import { MAIN_TABS, type MainTab } from "@/types/state";

type TopBarProps = {
  stats: Stats | null;
  search: string;
  shortcutLabel: string;
  activeTab: MainTab;
  onTabChange: (tab: MainTab) => void;
  onSearch: (value: string) => void;
};

export function TopBar({ stats, search, shortcutLabel, activeTab, onTabChange, onSearch }: TopBarProps) {
  const s = stats || { total: 0, target_count: 0 };
  return (
    <header className="topbar">
      <div className="topbar-brand">
        <div className="tracesurface-dot" aria-hidden="true" />
        <span className="font-display text-[18px] font-bold tracking-[-0.02em] text-text">TraceSurface</span>
      </div>

      <nav className="mode-tabs" aria-label="主视图">
        {MAIN_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            className={cn("mode-tab", activeTab === tab.value && "active")}
            aria-current={activeTab === tab.value ? "page" : undefined}
            onClick={() => onTabChange(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <label className="topbar-search">
        <Search className="pointer-events-none absolute left-3.5 h-4 w-4 text-text-4" />
        <Input
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder="搜索路径、响应、主机"
          title="无前缀=URL+响应+主机；前缀 url:/body:/dom: 限定单字段"
          data-tracesurface-search
          className="h-9 w-full border-transparent bg-[var(--ink-0)] pl-10 pr-16 font-mono text-[12.5px] focus:border-brand"
        />
        <kbd className="pointer-events-none absolute right-2.5 rounded border border-line-2 bg-surface-chrome px-1.5 py-0.5 font-mono text-[10px] text-text-4">{shortcutLabel}</kbd>
      </label>

      <div className="topbar-stats" aria-label="扫描摘要">
        <span>
          <b>{s.target_count}</b> 站点
        </span>
        <span className="topbar-stats-dot" aria-hidden="true" />
        <span>
          <b>{s.total}</b> 请求
        </span>
      </div>
    </header>
  );
}
