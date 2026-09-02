
import { Check, ChevronDown, Globe2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type Option = {
  value: string;
  label: string;
  meta?: string;
  title?: string;
};

type SelectPopoverProps = {
  label: string;
  value: string;
  allLabel: string;
  allMeta?: string;
  hint?: string;
  options: Option[];
  icon?: "check" | "globe";
  onChange: (value: string) => void;
};

export function SelectPopover({ label, value, allLabel, allMeta, hint, options, icon = "check", onChange }: SelectPopoverProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? options.filter((option) => option.label.toLowerCase().includes(needle)) : options;
  }, [options, query]);
  const Icon = icon === "globe" ? Globe2 : Check;

  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
    setQuery("");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="subtle"
          title={hint || label}
          className={cn(
            "h-8 min-w-[8.5rem] max-w-[16rem] justify-start px-2.5 font-mono text-[11.5px]",
            value && "border-brand bg-[var(--brand-soft)] text-brand",
          )}
        >
          <Icon className="h-3.5 w-3.5 shrink-0 text-text-4" />
          <span className="min-w-0 flex-1 overflow-hidden text-ellipsis text-left">{value || label}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-text-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-2">
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`筛选${label}...`} className="mb-2 h-8 font-mono" autoFocus />
        <div className="max-h-72 overflow-auto">
          <OptionRow active={!value} label={allLabel} meta={allMeta} onClick={() => choose("")} />
          {filtered.length ? (
            filtered.map((option) => (
              <OptionRow
                key={option.value}
                active={option.value === value}
                label={option.label}
                meta={option.meta}
                title={option.title}
                onClick={() => choose(option.value)}
              />
            ))
          ) : (
            <div className="px-3 py-5 text-center font-mono text-[11px] text-text-4">无匹配</div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function OptionRow({ active, label, meta, title, onClick }: { active: boolean; label: string; meta?: string; title?: string; onClick: () => void }) {
  return (
    <button
      type="button"
      title={title || label}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left font-mono text-[11.5px] text-text-2 transition-colors hover:bg-ink-2 hover:text-text",
        active && "bg-[var(--brand-soft)] text-brand",
      )}
      onClick={onClick}
    >
      <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">{label}</span>
      {meta ? <span className="max-w-[42%] shrink-0 overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px] text-text-4">{meta}</span> : null}
    </button>
  );
}
