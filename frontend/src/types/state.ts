export type MainTab = "surface" | "replays" | "secrets" | "ai";

export const MAIN_TABS: Array<{ value: MainTab; label: string }> = [
  { value: "surface", label: "APIs" },
  { value: "replays", label: "Replays" },
  { value: "secrets", label: "Secrets" },
  { value: "ai", label: "AI Review" },
];
