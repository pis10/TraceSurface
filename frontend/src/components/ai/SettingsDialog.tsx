import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, Loader2, PlugZap } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { SelectPopover } from "@/components/shared/SelectPopover";
import type { AICatalog, AIConnectionTest, AISettings } from "@/types/ai";
import { Input } from "@/components/ui/input";
import { credentialScope, effortLabels } from "./configuration";

export function SettingsDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [config, setConfig] = useState<AISettings | null>(null);
  const [original, setOriginal] = useState<AISettings | null>(null);
  const [catalog, setCatalog] = useState<AICatalog | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [test, setTest] = useState<AIConnectionTest | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.aiSettings(), api.aiCatalog()])
      .then(([settings, models]) => {
        if (cancelled) return;
        setCatalog(models);
        setOriginal(settings);
        setConfig(settings);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const provider = catalog?.providers.find((p) => p.id === config?.provider_id);
  const model = provider?.models.find((m) => m.value === config?.model);
  const custom = config?.provider_id === "custom";
  const scopeChanged =
    !!config &&
    !!original &&
    credentialScope(config) !== credentialScope(original);
  const needsKey = !config?.has_api_key || scopeChanged;
  const change = (patch: Partial<AISettings>) => {
    if (!config) return;
    setConfig({ ...config, ...patch });
    setError("");
    setTest(null);
  };
  const chooseProvider = (id: string) => {
    if (!config || id === config.provider_id) return;
    const next = catalog?.providers.find((p) => p.id === id);
    setKey("");
    change({
      provider_id: id,
      model: "",
      api_base: next?.api_base ?? "",
      reasoning_effort: "auto",
    });
  };
  const submit = async (action: "save" | "test") => {
    if (!config) return;
    if (!config.model.trim()) {
      setError("请选择或填写模型");
      return;
    }
    if (custom && !config.api_base.trim()) {
      setError("请填写 API 地址");
      return;
    }
    if (needsKey && !key.trim()) {
      setError("请填写当前服务商的 API Key");
      return;
    }
    setError("");
    setBusy(action);
    if (action === "test") setTest(null);
    try {
      const { has_api_key: _, ...values } = config;
      const body = {
        ...values,
        model: values.model.trim(),
        api_key: key.trim(),
      };
      if (action === "test") setTest(await api.testAI(body));
      else {
        await api.saveAISettings(body);
        setKey("");
        onSaved();
        onClose();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const fieldClass = "mt-1.5 h-10 w-full max-w-none font-sans text-[13px]";
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <DialogContent className="w-[min(560px,92vw)]">
        <DialogHeader>
          <DialogTitle>模型设置</DialogTitle>
          <DialogDescription>
            分析时，请求和响应会发送给所选服务商。
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-auto px-6 pb-6">
          {!config && !error && (
            <p className="py-6 text-sm text-text-3">正在读取设置…</p>
          )}
          {config && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submit("save");
              }}
            >
              <fieldset
                disabled={!!busy}
                className="grid min-w-0 gap-4 disabled:opacity-70"
              >
                <div>
                  <div className="text-xs text-text-2">服务商</div>
                  <SelectPopover
                    label="服务商"
                    icon="none"
                    value={config.provider_id}
                    className={fieldClass}
                    options={[
                      ...(catalog?.providers ?? []).map((p) => ({
                        value: p.id,
                        label: p.label,
                      })),
                      { value: "custom", label: "自定义（OpenAI 兼容）" },
                    ]}
                    onChange={chooseProvider}
                  />
                </div>
                {custom ? (
                  <>
                    <label className="text-xs text-text-2">
                      模型名称
                      <Input
                        className="mt-1.5"
                        value={config.model}
                        onChange={(e) => change({ model: e.target.value })}
                        placeholder="填写接口提供的模型名称"
                      />
                    </label>
                    <label className="text-xs text-text-2">
                      API 地址
                      <Input
                        className="mt-1.5"
                        value={config.api_base}
                        onChange={(e) => change({ api_base: e.target.value })}
                        placeholder="https://api.example.com/v1"
                      />
                    </label>
                  </>
                ) : (
                  <div>
                    <div className="flex items-center justify-between text-xs text-text-2">
                      <span>模型</span>
                      <span className="text-text-4">
                        {provider?.models.length ?? 0} 款
                      </span>
                    </div>
                    <SelectPopover
                      label="模型"
                      placeholder="选择模型"
                      icon="none"
                      value={config.model}
                      className={fieldClass}
                      options={provider?.models ?? []}
                      onChange={(value) =>
                        change({ model: value, reasoning_effort: "auto" })
                      }
                    />
                    <p className="mt-1.5 text-[11px] leading-5 text-text-3">
                      接口地址和模型参数自动匹配。
                    </p>
                  </div>
                )}
                {model && model.reasoning_options.length > 1 && (
                  <div>
                    <div className="text-xs text-text-2">思考设置</div>
                    <SelectPopover
                      label="思考设置"
                      icon="none"
                      value={config.reasoning_effort}
                      className={fieldClass}
                      searchable={false}
                      options={model.reasoning_options.map((value) => ({
                        value,
                        label: effortLabels[value] ?? value,
                      }))}
                      onChange={(reasoning_effort) =>
                        change({ reasoning_effort })
                      }
                    />
                  </div>
                )}
                {model?.reasoning && model.reasoning_options.length === 1 && (
                  <p className="text-xs text-text-3">
                    此模型使用默认思考方式。
                  </p>
                )}
                <label className="text-xs text-text-2">
                  API Key
                  <Input
                    aria-label="API Key"
                    className="mt-1.5"
                    type="password"
                    autoComplete="new-password"
                    value={key}
                    onChange={(e) => {
                      setKey(e.target.value);
                      setTest(null);
                      setError("");
                    }}
                    placeholder={
                      needsKey ? "填写当前服务商的密钥" : "已配置，留空保留"
                    }
                  />
                  <span className="mt-1.5 block leading-5 text-text-3">
                    {scopeChanged
                      ? "服务商或地址已更换，请填写对应的密钥。"
                      : "密钥仅保存在本机。"}
                  </span>
                </label>
              </fieldset>
              {test && (
                <div
                  role="status"
                  className={`mt-4 flex items-start gap-2 rounded-md border p-3 text-xs leading-5 ${test.ok ? "border-line-2 bg-surface-content text-text-2" : "border-red text-red"}`}
                >
                  {test.ok ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green" />
                  ) : (
                    <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  )}
                  <span>
                    {test.message}
                    {test.elapsed_ms != null && (
                      <span className="ml-2 text-text-3">
                        {(test.elapsed_ms / 1000).toFixed(1)} 秒
                      </span>
                    )}
                  </span>
                </div>
              )}
              {error && (
                <p role="alert" className="mt-3 text-xs text-red">
                  {error}
                </p>
              )}
              <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line-2 pt-4">
                <Button
                  type="button"
                  variant="subtle"
                  disabled={!!busy}
                  onClick={() => void submit("test")}
                >
                  {busy === "test" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <PlugZap className="h-3.5 w-3.5" />
                  )}
                  {busy === "test" ? "测试中…" : "测试连接"}
                </Button>
                <div className="flex gap-2">
                  <Button type="button" variant="subtle" onClick={onClose}>
                    取消
                  </Button>
                  <Button type="submit" variant="default" disabled={!!busy}>
                    {busy === "save" ? "保存中…" : "保存设置"}
                  </Button>
                </div>
              </div>
              <p className="mt-3 text-[10.5px] leading-5 text-text-4">
                模型目录：{catalog?.source}
                。连接测试会发送一条简短样例，不包含扫描数据。
              </p>
            </form>
          )}
          {!config && error && (
            <p role="alert" className="text-xs text-red">
              {error}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
