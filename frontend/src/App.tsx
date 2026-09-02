
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FilterBar } from "@/components/layout/FilterBar";
import { PurgeDialog } from "@/components/layout/PurgeDialog";
import { TopBar } from "@/components/layout/TopBar";
import { ReplaysView } from "@/components/replays/ReplaysView";
import { SecretsView } from "@/components/secrets/SecretsView";
import { ApiSurfaceView } from "@/components/surface/ApiSurfaceView";
import { Toast } from "@/components/shared/Toast";
import { api } from "@/lib/api";
import { defaultFilters, sanitizeFilters, type FilterState, type SortState } from "@/lib/filters";
import { defaultHighValue, sanitizeHighValue, type HighValueState } from "@/lib/high-value";
import type { Stats, TargetSummary } from "@/types/api";
import type { MainTab } from "@/types/state";

const FILTERS_KEY = "tracesurface:filters";
const HIGH_VALUE_KEY = "tracesurface:high-value";
const MAIN_TAB_KEY = "tracesurface:main-tab";

export default function App() {
  const [filters, setFilters] = useState<FilterState>(() => readStorage(FILTERS_KEY, defaultFilters(), sanitizeFilters));
  const [sort, setSort] = useState<SortState>({ k: "created_at", asc: false });
  const [highValue, setHighValue] = useState<HighValueState>(() => readStorage(HIGH_VALUE_KEY, defaultHighValue(), sanitizeHighValue));
  const [activeMainTab, setActiveMainTab] = useState<MainTab>(() => {
    const raw = localStorage.getItem(MAIN_TAB_KEY);
    return raw === "secrets" || raw === "replays" || raw === "surface" ? raw : "surface";
  });
  const [stats, setStats] = useState<Stats | null>(null);
  const [targets, setTargets] = useState<TargetSummary[]>([]);
  const [resultLabel, setResultLabel] = useState("-");
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [toastMessage, setToastMessage] = useState("");
  const [dataVersion, setDataVersion] = useState(0);
  const toastTimer = useRef<number | undefined>(undefined);

  const shortcutLabel = useMemo(() => (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "") ? "⌘K" : "Ctrl K"), []);

  const showToast = useCallback((message: string) => {
    setToastMessage(message);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMessage(""), 1400);
  }, []);

  const reloadSummary = useCallback(async () => {
    const [nextStats, nextTargets] = await Promise.all([api.stats(), api.targets()]);
    setStats(nextStats);
    setTargets(nextTargets.items);
  }, []);

  useEffect(() => {
    reloadSummary().catch(() => undefined);
  }, [reloadSummary, dataVersion]);

  useEffect(() => {
    localStorage.setItem(FILTERS_KEY, JSON.stringify(filters));
  }, [filters]);

  useEffect(() => {
    localStorage.setItem(HIGH_VALUE_KEY, JSON.stringify(highValue));
  }, [highValue]);

  useEffect(() => {
    localStorage.setItem(MAIN_TAB_KEY, activeMainTab);
  }, [activeMainTab]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        const input = document.querySelector<HTMLInputElement>("[data-tracesurface-search]");
        input?.focus();
        input?.select();
        return;
      }
      if (event.key === "Escape") {
        const input = document.querySelector<HTMLInputElement>("[data-tracesurface-search]");
        if (document.activeElement === input && filters.search) {
          setFilters((current) => ({ ...current, search: "" }));
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [filters.search]);

  const updateFilters = useCallback((next: FilterState) => {
    setFilters(next);
  }, []);

  const updateHighValue = useCallback((next: HighValueState, nextFilters?: FilterState) => {
    setHighValue(next);
    if (nextFilters) setFilters(nextFilters);
  }, []);

  const afterPurge = useCallback(async () => {
    await reloadSummary();
    setDataVersion((value) => value + 1);
  }, [reloadSummary]);

  const currentView = () => {
    if (activeMainTab === "secrets") return <SecretsView key={`secrets-${dataVersion}`} filters={filters} toast={showToast} onResultLabel={setResultLabel} />;
    if (activeMainTab === "replays") {
      return (
        <ReplaysView
          key={`replays-${dataVersion}`}
          filters={filters}
          highValue={highValue}
          sort={sort}
          onSortChange={setSort}
          onResultLabel={setResultLabel}
          toast={showToast}
        />
      );
    }
    return <ApiSurfaceView key={`surface-${dataVersion}`} filters={filters} toast={showToast} onResultLabel={setResultLabel} />;
  };

  return (
    <div className="workbench-bg relative z-0 flex h-full flex-col overflow-hidden">
      <div className="grain-layer" aria-hidden="true" />
      <TopBar
        stats={stats}
        search={filters.search}
        shortcutLabel={shortcutLabel}
        activeTab={activeMainTab}
        onTabChange={setActiveMainTab}
        onSearch={(search) => setFilters((current) => ({ ...current, search }))}
      />
      <FilterBar
        activeTab={activeMainTab}
        filters={filters}
        highValue={highValue}
        targets={targets}
        resultLabel={resultLabel}
        onFiltersChange={updateFilters}
        onHighValueChange={updateHighValue}
        onOpenPurge={() => setPurgeOpen(true)}
      />
      <main className="relative z-10 flex min-h-0 flex-1 flex-col overflow-hidden bg-surface-content">{currentView()}</main>
      <PurgeDialog open={purgeOpen} targets={targets} onOpenChange={setPurgeOpen} onDone={afterPurge} toast={showToast} />
      <Toast message={toastMessage} />
    </div>
  );
}

function readStorage<T>(key: string, fallback: T, sanitize: (value: unknown) => T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? sanitize(JSON.parse(raw)) : fallback;
  } catch {
    return fallback;
  }
}
