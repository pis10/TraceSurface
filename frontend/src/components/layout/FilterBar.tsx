
import { RotateCcw, Settings, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SelectPopover } from "@/components/shared/SelectPopover";
import {
  BUCKETS,
  METHODS,
  RESPONSE_TYPES,
  defaultFilters,
  toggleFilterItem,
  type FilterState,
} from "@/lib/filters";
import { HV_BUILTIN_KEYWORDS, HV_MAX_CUSTOM, normalizeCustomKeyword, type HighValueState } from "@/lib/high-value";
import { fmtDuration, fmtRelTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TargetSummary } from "@/types/api";
import type { MainTab } from "@/types/state";

type FilterBarProps = {
  activeTab: MainTab;
  filters: FilterState;
  highValue: HighValueState;
  targets: TargetSummary[];
  resultLabel: string;
  onFiltersChange: (filters: FilterState) => void;
  onHighValueChange: (value: HighValueState, nextFilters?: FilterState) => void;
  onOpenPurge: () => void;
};

export function FilterBar({
  activeTab,
  filters,
  highValue,
  targets,
  resultLabel,
  onFiltersChange,
  onHighValueChange,
  onOpenPurge,
}: FilterBarProps) {
  const targetOptions = useMemo(
    () =>
      targets.map((item) => {
        const duration = item.last_finished_at && item.last_scan_at ? fmtDuration(item.last_finished_at - item.last_scan_at) : "-";
        const when = item.last_scan_at ? `${fmtRelTime(item.last_scan_at)} ago` : "";
        return {
          value: item.target_url,
          label: item.target_url,
          title: item.target_url,
          meta: `${item.replay_count} · ${duration}${when ? ` · ${when}` : ""}`,
        };
      }),
    [targets],
  );
  const totalTargetReplays = targets.reduce((sum, item) => sum + (item.replay_count || 0), 0);

  const update = (patch: Partial<FilterState>) => onFiltersChange({ ...filters, ...patch });
  const locked = highValue.on;
  const isReplays = activeTab === "replays";
  const isSurface = activeTab === "surface";

  const setHighValue = (on: boolean) => {
    if (on === highValue.on) return;
    if (on) {
      onHighValueChange(
        { ...highValue, on: true, preBuckets: filters.buckets, preRespCts: filters.respCts },
        { ...filters, buckets: ["2xx"], respCts: ["json"] },
      );
      return;
    }
    onHighValueChange(
      { ...highValue, on: false, preBuckets: null, preRespCts: null },
      { ...filters, buckets: highValue.preBuckets || filters.buckets, respCts: highValue.preRespCts || filters.respCts },
    );
  };

  return (
    <nav className="filterbar">
      <div className="filterbar-filters">
        <SelectPopover
          label="站点"
          value={filters.target}
          allLabel="全部站点"
          allMeta={`${targets.length} 个 · ${totalTargetReplays}`}
          options={targetOptions}
          hint="选择扫描过的目标站点"
          onChange={(target) => update({ target })}
        />

        {isReplays || isSurface ? <span className="filter-sep" aria-hidden="true" /> : null}

        {isReplays ? (
          <div className="filter-chip-cluster">
            <ChipGroup
              all={BUCKETS}
              value={filters.buckets}
              locked={locked}
              className={(item) => `bucket-${item}`}
              onChange={(buckets) => update({ buckets })}
            />
            <ChipGroup all={METHODS} value={filters.methods} className={(item) => `method-${item}`} onChange={(methods) => update({ methods })} />
            <ChipGroup all={RESPONSE_TYPES} value={filters.respCts} locked={locked} onChange={(respCts) => update({ respCts })} />
          </div>
        ) : null}

        {isSurface ? (
          <ChipGroup all={METHODS} value={filters.methods} className={(item) => `method-${item}`} onChange={(methods) => update({ methods })} />
        ) : null}
      </div>

      <div className="filterbar-actions">
        {isReplays ? <HighValueControl value={highValue} onToggle={setHighValue} onChange={onHighValueChange} /> : null}

        <div className={cn("filter-count", highValue.on && isReplays && "is-hot")}>
          <span className={cn("filter-count-dot", highValue.on && isReplays && "is-hot")} />
          {resultLabel || "-"}
        </div>
        <Button variant="ghost" size="sm" className="h-8 gap-1.5 px-2.5 text-[11px] font-normal" onClick={() => onFiltersChange(defaultFilters())}>
          <RotateCcw className="h-3.5 w-3.5 text-text-4" />
          重置
        </Button>
        <Button variant="danger" size="sm" className="h-8 gap-1.5 px-2.5 text-[11px] font-normal" onClick={onOpenPurge}>
          <Trash2 className="h-3.5 w-3.5" />
          清空
        </Button>
      </div>
    </nav>
  );
}

function ChipGroup<T extends string>({
  all,
  value,
  locked,
  className,
  onChange,
}: {
  all: readonly T[];
  value: T[];
  locked?: boolean;
  className?: (value: T) => string;
  onChange: (value: T[]) => void;
}) {
  return (
    <div className="segmented" role="group">
      {all.map((item) => (
        <button
          key={item}
          type="button"
          className={cn("chip", value.includes(item) && "active", locked && "locked", className?.(item))}
          onClick={(event) => {
            if (locked) return;
            onChange(toggleFilterItem(value, all, item, event.altKey));
          }}
          title="Alt-click 单独勾选这一项"
        >
          {item === "DELETE" ? "DEL" : item}
        </button>
      ))}
    </div>
  );
}

function HighValueControl({
  value,
  onToggle,
  onChange,
}: {
  value: HighValueState;
  onToggle: (on: boolean) => void;
  onChange: (value: HighValueState) => void;
}) {
  const [customInput, setCustomInput] = useState("");
  const enabled = new Set(value.builtinEnabled);

  const toggleBuiltin = (keyword: string, checked: boolean) => {
    const next = checked ? [...value.builtinEnabled, keyword] : value.builtinEnabled.filter((item) => item !== keyword);
    onChange({ ...value, builtinEnabled: Array.from(new Set(next)) });
  };

  const addCustom = () => {
    const keyword = normalizeCustomKeyword(customInput);
    if (!keyword || value.customKeywords.length >= HV_MAX_CUSTOM) return;
    if (value.customKeywords.includes(keyword) || value.builtinEnabled.includes(keyword)) return;
    onChange({ ...value, customKeywords: [...value.customKeywords, keyword] });
    setCustomInput("");
  };

  return (
    <div className="filterbar-high-value">
      <button
        type="button"
        className={cn(
          "inline-flex h-8 items-center gap-2 rounded-md border border-line-2 px-3 font-mono text-[11px] text-text-3 transition-colors hover:border-brand hover:bg-[var(--brand-soft)] hover:text-brand",
          value.on && "border-brand bg-[var(--brand-soft)] font-semibold text-brand",
        )}
        onClick={() => onToggle(!value.on)}
        title="开启后只看 2XX JSON 响应，并屏蔽未登录等噪声"
      >
        <span className={cn("h-1.5 w-1.5 rounded-full bg-brand", !value.on && "opacity-60")} />
        高价值视图
      </button>
      {value.on ? (
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="subtle" size="icon" title="配置屏蔽规则">
              <Settings className="h-3.5 w-3.5 text-text-3" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[min(720px,calc(100vw-56px))] p-5">
            <div className="mb-4 text-[12px] leading-6 text-text-3">开启后，命中以下任一规则的 2XX JSON 响应将被隐藏。</div>
            <div className="mb-5">
              <div className="mb-3 flex items-baseline gap-3 border-b border-line pb-2">
                <span className="font-mono text-[10.5px] uppercase text-text-2">内置规则</span>
                <span className="font-mono text-[10.5px] text-text-4">
                  {enabled.size} / {HV_BUILTIN_KEYWORDS.length}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-x-5 gap-y-1">
                {HV_BUILTIN_KEYWORDS.map((keyword) => (
                  <label key={keyword} className="flex min-w-0 cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-ink-2" title={keyword}>
                    <Checkbox checked={enabled.has(keyword)} onCheckedChange={(checked) => toggleBuiltin(keyword, checked === true)} />
                    <span className={cn("min-w-0 overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11.5px] text-text-2", !enabled.has(keyword) && "text-text-4 line-through")}>
                      {keyword}
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-3 flex items-baseline gap-3 border-b border-line pb-2">
                <span className="font-mono text-[10.5px] uppercase text-text-2">自定义关键词</span>
                <span className="font-mono text-[10.5px] text-text-4">
                  {value.customKeywords.length} / {HV_MAX_CUSTOM}
                </span>
              </div>
              <div className="mb-2 flex flex-col gap-1">
                {value.customKeywords.map((keyword) => (
                  <div key={keyword} className="flex items-center gap-2 rounded border border-line-2 bg-[var(--brand-soft)] px-3 py-1.5 font-mono text-[11.5px] text-brand">
                    <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{keyword}</span>
                    <button type="button" className="text-text-4 hover:text-red" onClick={() => onChange({ ...value, customKeywords: value.customKeywords.filter((item) => item !== keyword) })}>
                      x
                    </button>
                  </div>
                ))}
              </div>
              <Input
                value={customInput}
                disabled={value.customKeywords.length >= HV_MAX_CUSTOM}
                onChange={(event) => setCustomInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addCustom();
                  }
                }}
                placeholder={value.customKeywords.length >= HV_MAX_CUSTOM ? `已达上限 ${HV_MAX_CUSTOM}` : "+ 输入关键词，回车追加"}
                className="font-mono"
              />
            </div>
          </PopoverContent>
        </Popover>
      ) : null}
    </div>
  );
}
