"use client";

import { useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, ChevronDown, MapPin, Search, Zap, Layers, BookOpen } from "lucide-react";
import { createTask } from "@/lib/mock-client";
import { lastTaskKey } from "@/lib/demo";
import { depthOptions, prepareBrief, type ResearchBrief } from "@/lib/research";

const presets = [
  { destination: "泉州", topics: "古城文化、公共交通", label: "泉州古城" },
  { destination: "成都", topics: "博物馆、美食", label: "成都人文" },
  { destination: "丽江", topics: "自然风光、徒步", label: "丽江山水" },
];
const depthIcons = { quick: Zap, deep: Layers, academic: BookOpen };

export function ResearchForm() {
  const router = useRouter();
  const [destination, setDestination] = useState("");
  const [topics, setTopics] = useState("");
  const [requirements, setRequirements] = useState("");
  const [depth, setDepth] = useState<ResearchBrief["depth"]>("deep");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const submitting = useRef(false);
  const lastRequest = useRef<{ body: string; key: string } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    setError("");
    submitting.current = true;
    try {
      const brief = prepareBrief(destination, topics, requirements, depth);
      const body = JSON.stringify(brief);
      if (lastRequest.current?.body !== body) lastRequest.current = { body, key: crypto.randomUUID() };
      setSending(true);
      const task = await createTask(brief, lastRequest.current.key);
      try { sessionStorage.setItem(lastTaskKey, task.task_id); } catch { /* Keep direct task navigation available. */ }
      router.push(`/workbench/${task.task_id}`);
    } catch (problem) {
      submitting.current = false;
      setSending(false);
      setError(problem instanceof Error ? problem.message : "提交失败，请重试。");
    }
  }

  return (
    <form className="panel research-form" onSubmit={submit} aria-labelledby="research-title">
      <div className="form-heading">
        <div className="icon-tile"><Search size={20} aria-hidden /></div>
        <div><h2 id="research-title">新建调研</h2><p>填写目的地与调研重点，组建你的专家团队。</p></div>
      </div>
      <div className="form-grid">
        <label><span>调研目的地 <span className="required">*</span></span>
          <span className="input-wrap"><MapPin size={18} aria-hidden /><input name="destination" placeholder="例如：泉州" value={destination} onChange={(event) => setDestination(event.target.value)} required /></span>
        </label>
        <label><span>调研主题 <span className="optional">选填</span></span>
          <input name="topics" placeholder="例如：古城文化、公共交通（用顿号分隔）" value={topics} onChange={(event) => setTopics(event.target.value)} />
        </label>
      </div>
      <details className="requirements">
        <summary><ChevronDown size={15} aria-hidden /> 补充个性化需求</summary>
        <textarea name="requirements" aria-label="个性化需求" placeholder="例如：周末两天，主要乘坐公共交通。" rows={3} value={requirements} onChange={(event) => setRequirements(event.target.value)} />
      </details>
      <fieldset className="depth-picker">
        <legend>调研深度</legend>
        <div className="depth-grid">
          {depthOptions.map(({ value, label, description }) => {
            const Icon = depthIcons[value];
            return <label className="depth-option" key={value}>
              <input type="radio" name="depth" value={value} checked={value === depth} onChange={() => setDepth(value)} />
              <Icon size={17} aria-hidden />
              <span><strong>{label}</strong><small>{description}</small></span>
            </label>;
          })}
        </div>
      </fieldset>
      <div className="form-foot">
        <div className="presets"><span>试试</span>
          {presets.map((preset) => <button className="chip" key={preset.destination} type="button" onClick={() => {
            setDestination(preset.destination); setTopics(preset.topics); setError("");
          }}>{preset.label}</button>)}
        </div>
        <button className="button primary" disabled={sending} type="submit">{sending ? "正在创建…" : "开始调研"} <ArrowRight size={17} aria-hidden /></button>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
    </form>
  );
}
