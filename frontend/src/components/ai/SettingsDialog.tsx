import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, Loader2, PlugZap, Library, SlidersHorizontal } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { SelectPopover } from "@/components/shared/SelectPopover";
import type { AICatalog, AIConnectionTest, AISettings, AIModelDetails, ReasoningMode } from "@/types/ai";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { credentialScope, reasoningLabels } from "./configuration";

const fieldClass = "mt-1.5 h-9 w-full max-w-none font-sans text-[13px]";
const numberValue = (value: string) => value === "" ? null : Number(value);

export function SettingsDialog({ onClose, onSaved }: {
  onClose: () => void;
  onSaved: (settings: AISettings) => void;
}) {
  const [config, setConfig] = useState<AISettings | null>(null);
  const [original, setOriginal] = useState<AISettings | null>(null);
  const [catalog, setCatalog] = useState<AICatalog | null>(null);
  const [details, setDetails] = useState<AIModelDetails | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);
  const [modelError, setModelError] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [test, setTest] = useState<AIConnectionTest | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError("");
    Promise.all([api.aiSettings(), api.aiCatalog()]).then(([settings, models]) => {
      if (cancelled) return;
      setCatalog(models);
      setOriginal(settings);
      setConfig(settings);
    }).catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [reload]);
  useEffect(() => {
    let cancelled = false;
    setDetails(null);
    setModelError("");
    if (!config || config.mode === "custom" || !config.model) {
      setLoadingModel(false);
      return;
    }
    setLoadingModel(true);
    api.aiModelDetails(config.provider_id, config.model).then((value) => {
      if (!cancelled) setDetails(value);
    }).catch((e) => { if (!cancelled) setModelError(e.message); })
      .finally(() => { if (!cancelled) setLoadingModel(false); });
    return () => { cancelled = true; };
  }, [config?.mode, config?.provider_id, config?.model]);

  const provider = catalog?.providers.find((p) => p.id === config?.provider_id);
  const custom = config?.mode === "custom";
  const sameScope = !!config && !!original && credentialScope(config) === credentialScope(original);
  const hasKey = sameScope && original?.has_api_key;
  const change = (patch: Partial<AISettings>) => {
    setConfig((previous) => previous ? { ...previous, ...patch } : previous);
    setError(""); setTest(null);
  };
  const chooseMode = (mode: AISettings["mode"]) => {
    if (!config || mode === config.mode) return;
    change({ mode, provider_id: mode === "custom" ? "openai-chat" : "deepseek", model: "", api_base: "",
      context_window: null, max_output_tokens: null, reasoning_mode: "default",
      reasoning_effort: "", thinking_budget: null, output_mode: "prompt", reasoning_efforts: [] });
    setKey("");
  };
  const chooseProvider = (id: string) => {
    if (!config || id === config.provider_id) return;
    change({ provider_id: id, model: "", api_base: "", context_window: null, max_output_tokens: null,
      reasoning_mode: "default", reasoning_effort: "", thinking_budget: null, output_mode: "prompt", reasoning_efforts: [] });
    setKey("");
  };
  const submit = async (action: "save" | "test") => {
    if (!config) return;
    if (!config.model.trim()) { setError("请选择或填写模型名称"); return; }
    if (!hasKey && !key.trim()) { setError("请填写当前接入方式的 API Key"); return; }
    if (custom && (!config.context_window || !config.max_output_tokens)) {
      setError("请填写上下文容量和最大输出 tokens"); return;
    }
    if (config.reasoning_mode === "effort" && !(custom ? config.reasoning_efforts : details?.reasoning_efforts ?? []).includes(config.reasoning_effort)) {
      setError("请勾选支持的强度，再选择默认强度"); return;
    }
    setError(""); setBusy(action);
    if (action === "test") setTest(null);
    try {
      const { has_api_key: _, ready: _ready, ...values } = config;
      const body = { ...values, model: values.model.trim(), api_key: key.trim() };
      if (action === "test") setTest(await api.testAI(body));
      else {
        const saved = await api.saveAISettings(body);
        setKey(""); onSaved(saved); onClose();
      }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  };
  const modes: ReasoningMode[] = custom
    ? (config?.provider_id === "openai-responses" ? ["default", "effort", "disabled"]
      : config?.provider_id === "google" ? ["default", "effort", "budget", "enabled", "disabled"]
      : ["default", "effort", "budget", "enabled", "disabled", "adaptive"])
    : details?.reasoning_modes ?? ["default"];
  const efforts = custom ? config?.reasoning_efforts ?? [] : details?.reasoning_efforts ?? [];
  const toggleEffort = (effort: string) => {
    if (!config) return;
    const next = config.reasoning_efforts.includes(effort)
      ? config.reasoning_efforts.filter((value) => value !== effort)
      : (catalog?.efforts ?? []).filter((value) => value === effort || config.reasoning_efforts.includes(value));
    change({ reasoning_efforts: next, reasoning_effort: next.includes(config.reasoning_effort) ? config.reasoning_effort : "" });
  };
  const canSubmit = !!config?.model && !busy && !loadingModel && (custom || (!!details && !modelError));

  return <Dialog open onOpenChange={(v) => { if (!v && !busy) onClose(); }}>
    <DialogContent className="max-h-[90vh] w-[min(820px,94vw)]" onEscapeKeyDown={(e) => { if (busy) e.preventDefault(); }}>
      <DialogHeader><DialogTitle>模型设置</DialogTitle>
        <DialogDescription>选择分析重放结果使用的模型。请求和响应会发送给所配置的服务。</DialogDescription>
      </DialogHeader>
      {!config ? <div className="px-6 pb-6">{error ? <><p role="alert" className="text-xs leading-6 text-red">{error}</p>
        <Button variant="subtle" className="mt-3" onClick={() => setReload((r) => r + 1)}>重新读取</Button></>
        : <p className="py-8 text-sm text-text-3">正在读取设置…</p>}</div>
      : <form className="flex min-h-0 flex-col" onSubmit={(e) => { e.preventDefault(); void submit("save"); }}>
        <div className="min-h-0 overflow-auto px-6 pb-5">
          <fieldset disabled={!!busy} className="min-w-0 disabled:opacity-70">
            <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg bg-ink-1 p-1" role="group" aria-label="配置方式">
              {([{ id: "catalog", title: "从目录选择", icon: Library, text: "使用服务商和模型目录" },
                { id: "custom", title: "自定义接入", icon: SlidersHorizontal, text: "填写地址、模型与能力" }] as const).map((item) =>
                <button key={item.id} type="button" aria-pressed={config.mode === item.id} onClick={() => chooseMode(item.id)}
                  className={`rounded-md border px-3 py-2 text-left transition-colors ${config.mode === item.id ? "border-line-2 bg-surface-chrome shadow-sm" : "border-transparent text-text-3 hover:text-text"}`}>
                  <span className="flex items-center gap-2 text-[13px] font-medium"><item.icon className="h-4 w-4" />{item.title}</span>
                  <span className="mt-1 block text-[11px] text-text-3">{item.text}</span>
                </button>)}
            </div>
            <div className="grid gap-5 sm:grid-cols-2">
              <section className="min-w-0 space-y-4">
                <div><div className="text-xs text-text-2">{custom ? "API 格式" : "服务商"}</div>
                  <SelectPopover label={custom ? "API 格式" : "服务商"} value={config.provider_id} icon="none" className={fieldClass}
                    options={custom ? (catalog?.protocols ?? []).map((p) => ({ value: p.id, label: p.label })) : (catalog?.providers ?? []).map((p) => ({ value: p.id, label: p.label, meta: p.models.length ? `${p.models.length} 款` : "无目录" }))}
                    onChange={chooseProvider} />
                </div>
                {custom ? <label className="block text-xs text-text-2">模型 / 部署名称
                  <Input className="mt-1.5 h-9" value={config.model} onChange={(e) => change({ model: e.target.value })} placeholder="填写服务端接受的完整标识" />
                </label> : <div>
                  <div className="text-xs text-text-2">模型</div>
                  <SelectPopover label="模型" placeholder="选择模型" value={config.model} icon="none" className={fieldClass}
                    allowCustom options={provider?.models ?? []} onChange={(model) => change({ model, context_window: null, reasoning_mode: "default", reasoning_effort: "", thinking_budget: null })} />
                  {provider && !provider.models.length && <p className="mt-2 text-xs leading-5 text-text-3">此服务暂无内置型号。在模型选择框中输入名称即可添加。</p>}
                </div>}
                {custom && <label className="block text-xs text-text-2">API 地址
                  <Input className="mt-1.5 h-9" value={config.api_base} onChange={(e) => change({ api_base: e.target.value })} placeholder="如 https://api.example.com/v1" />
                </label>}
                <label className="block text-xs text-text-2">API Key
                  <Input className="mt-1.5 h-9" aria-label="API Key" type="password" autoComplete="new-password" value={key}
                    onChange={(e) => { setKey(e.target.value); setTest(null); setError(""); }} placeholder={hasKey ? "已配置，留空保留" : "填写密钥"} />
                  <span className="mt-1.5 block text-[11px] leading-5 text-text-3">
                    {original?.has_api_key && !sameScope ? "接入方式或地址已改变，请填写对应凭证。" : "密钥仅保存在本机，不会返回浏览器。"}
                  </span>
                </label>
                {custom && <details className="text-xs text-text-2"><summary className="cursor-pointer">结果格式</summary>
                  <SelectPopover label="结果格式" value={config.output_mode} icon="none" searchable={false} className={fieldClass}
                    options={[{ value: "prompt", label: "提示词约束" }, ...(config.provider_id === "anthropic" ? [] : [{ value: "json", label: "JSON 模式" }]), { value: "native", label: "JSON Schema" }]}
                    onChange={(value) => change({ output_mode: value as AISettings["output_mode"] })} />
                </details>}
              </section>
              <section className="min-w-0 rounded-lg border border-line-2 bg-surface-content p-4">
                <div className="mb-2 text-xs font-medium">{custom ? "容量与思考" : "分析设置"}</div>
                {custom ? <>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="text-xs text-text-2">上下文容量 tokens<Input className="mt-1.5 h-9" type="number" min={1024}
                      value={config.context_window ?? ""} onChange={(e) => change({ context_window: numberValue(e.target.value) })} placeholder="如 128000" /></label>
                    <label className="text-xs text-text-2">最大输出 tokens<Input className="mt-1.5 h-9" type="number" min={256}
                      value={config.max_output_tokens ?? ""} onChange={(e) => change({ max_output_tokens: numberValue(e.target.value) })} placeholder="如 8192" /></label>
                  </div>
                </> : loadingModel ? <p className="text-xs text-text-3">正在读取模型设置…</p>
                  : modelError ? <p role="alert" className="text-xs text-red">{modelError}</p>
                  : <p className="text-xs leading-6 text-text-3">{config.model ? "默认设置即可开始分析。" : "选择模型后设置。"}</p>}
                {(custom || modes.length > 1) && <div className="mt-3"><div className="text-xs text-text-2">思考方式</div>
                  <SelectPopover label="思考方式" value={config.reasoning_mode} icon="none" searchable={false} className={fieldClass}
                    options={modes.map((value) => ({ value, label: reasoningLabels[value] }))}
                    onChange={(value) => change({ reasoning_mode: value as ReasoningMode, reasoning_effort: "", thinking_budget: null })} />
                </div>}
                {config.reasoning_mode === "effort" && <div className="mt-3">
                  {custom && <fieldset><legend className="text-xs text-text-2">支持的强度</legend>
                    <div className="mt-2 grid grid-cols-3 gap-2">
                      {(catalog?.efforts ?? []).map((effort) => <label key={effort}
                        className={`flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 font-mono text-xs transition-colors ${efforts.includes(effort) ? "border-brand bg-[var(--brand-soft)] text-brand" : "border-line-2 text-text-2 hover:bg-ink-1"}`}>
                        <Checkbox aria-label={`支持 ${effort}`} checked={efforts.includes(effort)} onCheckedChange={() => toggleEffort(effort)} />{effort}
                      </label>)}
                    </div>
                  </fieldset>}
                  <div className="mt-2 text-xs text-text-2">默认强度</div>
                  <SelectPopover label="默认强度" value={config.reasoning_effort} placeholder={efforts.length ? "选择强度" : "先勾选支持的强度"} icon="none" searchable={false} className={fieldClass}
                    options={efforts.map((value) => ({ value, label: value }))} onChange={(reasoning_effort) => change({ reasoning_effort })} />
                </div>}
                {config.reasoning_mode === "budget" && <label className="mt-3 block text-xs text-text-2">思考预算 tokens
                  <Input className="mt-1.5 h-9" type="number" min={1024} value={config.thinking_budget ?? ""} onChange={(e) => change({ thinking_budget: numberValue(e.target.value) })} placeholder="至少 1024，且小于最大输出" /></label>}
                {!custom && <p className="mt-3 text-[11px] leading-5 text-text-3">更多思考参数可在自定义接入中设置。</p>}
              </section>
            </div>
            {!custom && <details className="mt-5 border-t border-line-2 pt-4">
              <summary className="cursor-pointer text-xs text-text-2">更换 API 地址</summary>
              <label className="mt-4 block text-xs text-text-2">API 地址
                <Input className="mt-1.5 h-9" value={config.api_base} onChange={(e) => change({ api_base: e.target.value })} placeholder="留空使用服务商默认地址" />
              </label>
            </details>}
          </fieldset>
        </div>
        <div className="shrink-0 rounded-b-lg border-t border-line-2 bg-surface-chrome px-6 py-4">
          {error && <p role="alert" className="mb-3 text-xs leading-5 text-red">{error}</p>}
          {test && <div role="status" className={`mb-3 flex items-center gap-2 text-xs leading-5 ${test.ok ? "text-green" : "text-red"}`}>
            {test.ok ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <CircleAlert className="h-4 w-4 shrink-0" />}
            <span>{test.message}{test.elapsed_ms != null && <span className="ml-2 text-text-3">{(test.elapsed_ms / 1000).toFixed(1)} 秒</span>}</span>
          </div>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button type="button" variant="subtle" disabled={!canSubmit} onClick={() => void submit("test")}>
              {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlugZap className="h-3.5 w-3.5" />}{busy === "test" ? "测试中…" : "测试连接"}
            </Button>
            <div className="flex gap-2"><Button type="button" variant="subtle" disabled={!!busy} onClick={onClose}>取消</Button>
              <Button type="submit" disabled={!canSubmit}>{busy === "save" ? "保存中…" : "保存设置"}</Button></div>
          </div>
          <p className="mt-2 text-[10.5px] leading-5 text-text-4">{catalog?.source} · 连接测试只发送虚构样例，不包含扫描数据。</p>
        </div>
      </form>}
    </DialogContent>
  </Dialog>;
}
