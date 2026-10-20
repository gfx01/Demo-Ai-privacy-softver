// Minimal chat client: optional masked preview, then streaming completion
// through the privacy gateway. All text is rendered with textContent.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const keyInput = $("key"), modelSel = $("model"), log = $("log"), input = $("input"), status = $("status");
  const history = [];
  let pending = null;

  try { keyInput.value = sessionStorage.getItem("ph_key") || ""; } catch (e) { /* storage unavailable */ }
  keyInput.addEventListener("change", () => {
    try { sessionStorage.setItem("ph_key", keyInput.value.trim()); } catch (e) { /* ignore */ }
    loadModels();
  });

  function headers() {
    return { "Content-Type": "application/json", "Authorization": "Bearer " + keyInput.value.trim() };
  }

  function addMessage(role, text) {
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.textContent = text;
    log.appendChild(div);
    div.scrollIntoView({ block: "end" });
    return div;
  }

  async function errorText(resp) {
    try {
      const body = await resp.json();
      const d = body.detail || body.error || body;
      if (typeof d === "string") return d;
      return d.message || (d.error && d.error.message) || JSON.stringify(d);
    } catch (e) { return "HTTP " + resp.status; }
  }

  async function loadModels() {
    const previous = modelSel.value;
    if (!keyInput.value.trim()) { modelSel.textContent = ""; return; }
    const resp = await fetch("/v1/models", { headers: headers() });
    if (!resp.ok) { status.textContent = "Kulcs hiba / key error: " + (await errorText(resp)); return; }
    const data = await resp.json();
    modelSel.textContent = "";
    for (const m of data.data || []) {
      if (m.id.startsWith("privacy-")) continue; // internal preview/self-test models
      const opt = document.createElement("option");
      opt.value = opt.textContent = m.id;
      modelSel.appendChild(opt);
    }
    if ([...modelSel.options].some((o) => o.value === previous)) modelSel.value = previous;
    status.textContent = "";
  }

  async function send() {
    const text = input.value.trim();
    if (!text || !keyInput.value.trim()) return;
    const messages = history.concat([{ role: "user", content: text }]);
    if (!$("preview").checked) return complete(messages, text);
    status.textContent = "Előnézet… / previewing…";
    const resp = await fetch("/api/preview", { method: "POST", headers: headers(), body: JSON.stringify({ messages }) });
    status.textContent = "";
    if (!resp.ok) { status.textContent = "Előnézet hiba / preview error: " + (await errorText(resp)); return; }
    const data = await resp.json();
    const last = data.masked_messages[data.masked_messages.length - 1];
    $("previewText").textContent = last.content;
    const counts = Object.entries(data.entity_counts).map(([k, v]) => k + "×" + v).join(", ");
    $("previewInfo").textContent = (counts || "nincs felismert adat / nothing detected") + " · " + data.mode + " · " + data.strategy;
    $("previewWarn").hidden = !data.would_block;
    $("previewWarn").textContent = data.would_block ? "Strict mód: ez a kérés blokkolva lesz. Strict mode: this request will be blocked." : "";
    $("confirm").disabled = data.would_block;
    $("previewBox").hidden = false;
    pending = { messages, text };
  }

  async function complete(messages, text) {
    $("previewBox").hidden = true;
    addMessage("user", text);
    input.value = "";
    const out = addMessage("assistant", "…");
    status.textContent = "Válasz érkezik… / receiving…";
    const resp = await fetch("/v1/chat/completions", {
      method: "POST", headers: headers(),
      body: JSON.stringify({ model: modelSel.value, messages, stream: true }),
    });
    if (!resp.ok) {
      out.textContent = "⚠ " + (await errorText(resp));
      status.textContent = "";
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", answer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
        try {
          const chunk = JSON.parse(line.slice(6));
          for (const c of chunk.choices || []) answer += (c.delta && c.delta.content) || "";
          out.textContent = answer;
        } catch (e) { /* ignore keep-alive lines */ }
      }
    }
    history.push({ role: "user", content: text }, { role: "assistant", content: answer });
    status.textContent = "";
  }

  $("send").addEventListener("click", send);
  $("confirm").addEventListener("click", () => { if (pending) complete(pending.messages, pending.text); pending = null; });
  $("cancel").addEventListener("click", () => { $("previewBox").hidden = true; pending = null; });
  $("clear").addEventListener("click", () => { history.length = 0; log.textContent = ""; });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); });
  loadModels();
})();
