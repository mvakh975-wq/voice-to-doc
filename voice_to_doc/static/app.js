const $ = (id) => document.getElementById(id);
const STEPS = [
  ["prepare", "Подготовка звука"],
  ["transcribe", "Распознавание"],
  ["structure", "Оформление текста"],
  ["summary", "Выжимка"],
  ["render", "Сборка документа"],
];
const UNITS = { transcribe: "кусков", structure: "блоков" };
const STATUS = { queued: "В очереди", running: "В работе", done: "Готово", paused: "Пауза", error: "Ошибка" };
const KIND = { lecture: "Лекция", interview: "Интервью" };

let timer = null;
let generation = 0; // bumped on every navigation; stale async views bail out
let selectedFile = null;
let currentJob = null;
let renderedFor = null;

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new Error((isJson && body.detail) || `Ошибка ${res.status}`);
  return body;
}

const postJson = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.max(1, Math.round(seconds / 60));
  return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, "0")} мин`;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function showError(id, message) {
  $(id).textContent = message;
  $(id).hidden = false;
}

function route() {
  clearTimeout(timer);
  const gen = ++generation;
  const match = location.hash.match(/^#\/job\/([\w-]+)$/);
  if (match) showJob(match[1], gen);
  else showHome(gen);
}

/* ---------- home ---------- */

async function showHome(gen = generation) {
  $("job").hidden = true;
  $("home").hidden = false;
  const { has_key } = await api("/api/settings");
  const jobs = await api("/api/jobs");
  if (gen !== generation) return;
  $("setup").hidden = has_key;
  $("upload-form").hidden = !has_key;
  $("jobs-empty").hidden = jobs.length > 0;
  $("jobs").replaceChildren(...jobs.map(jobRow));
  if (jobs.some((j) => j.status === "queued" || j.status === "running")) {
    timer = setTimeout(() => showHome(gen), 3000);
  }
}

function jobRow(job) {
  const link = el("a");
  link.href = `#/job/${job.id}`;
  link.append(
    el("span", "name", job.filename),
    el("span", "muted", `${fmtDate(job.created_at)} · ${fmtDuration(job.duration_s)}`),
    el("span", `status s-${job.status}`, STATUS[job.status]),
  );
  const item = el("li");
  item.append(link);
  return item;
}

function resetUpload() {
  selectedFile = null;
  $("file-input").value = "";
  $("drop-text").innerHTML = "Перетащите файл сюда или <u>выберите</u>";
  $("upload-btn").disabled = true;
}

function bindHome() {
  const drop = $("drop");
  const pick = (file) => {
    if (!file) return;
    selectedFile = file;
    $("drop-text").textContent = file.name;
    $("upload-btn").disabled = false;
  };
  $("file-input").addEventListener("change", (e) => pick(e.target.files[0]));
  ["dragenter", "dragover"].forEach((type) =>
    drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); pick(e.dataTransfer.files[0]); });

  $("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("upload-btn");
    button.disabled = true;
    button.textContent = "Загружаю…";
    $("upload-error").hidden = true;
    const form = new FormData();
    form.append("file", selectedFile);
    form.append("kind", new FormData(e.target).get("kind"));
    try {
      const { id } = await api("/api/jobs", { method: "POST", body: form });
      resetUpload();
      location.hash = `#/job/${id}`;
    } catch (err) {
      showError("upload-error", err.message);
      button.disabled = false;
    } finally {
      button.textContent = "Расшифровать";
    }
  });

  bindKeyForm("key-form", "key-input", "key-error", () => showHome(++generation));
}

function bindKeyForm(formId, inputId, errorId, after) {
  $(formId).addEventListener("submit", async (e) => {
    e.preventDefault();
    $(errorId).hidden = true;
    try {
      await postJson("/api/settings", { groq_api_key: $(inputId).value });
      $(inputId).value = "";
      after();
    } catch (err) {
      showError(errorId, err.message);
    }
  });
}

/* ---------- job ---------- */

async function showJob(id, gen = generation) {
  $("home").hidden = true;
  $("job").hidden = false;
  let job;
  try {
    job = await api(`/api/jobs/${id}`);
  } catch {
    if (gen === generation) location.hash = "#/";
    return;
  }
  if (gen !== generation) return;
  currentJob = job;
  $("job-name").textContent = job.filename;
  $("job-meta").textContent = `${KIND[job.kind]} · ${fmtDuration(job.duration_s)} · ${STATUS[job.status]}`;

  const done = job.status === "done";
  const stopped = job.status === "paused" || job.status === "error";
  $("progress").hidden = done;
  if (!done) renderSteps(job);

  $("job-alert").hidden = !stopped;
  if (stopped) {
    $("job-message").textContent = job.message;
    $("job-key-form").hidden = job.reason !== "auth";
    $("resume-btn").hidden = job.reason === "auth";
    $("resume-btn").textContent = job.status === "error" ? "Повторить" : "Продолжить";
  }

  $("result").hidden = !done;
  if (done && renderedFor !== job.id) await renderResult(job);
  if (!done) renderedFor = null;

  if (gen === generation && (job.status === "queued" || job.status === "running")) {
    timer = setTimeout(() => showJob(id, gen), 2000);
  }
}

function renderSteps(job) {
  const current = STEPS.findIndex(([key]) => key === job.step);
  const { done, total } = job.progress;
  $("steps").replaceChildren(...STEPS.map(([key, label], i) => {
    const state = current >= 0 && i < current ? "done" : i === current ? "current" : "todo";
    const item = el("li", state);
    item.append(el("span", "label", label));
    if (state === "current" && total) {
      const eta = estimate(job);
      item.append(el("span", "muted", `${done} из ${total} ${UNITS[key] || ""}`.trim() + (eta ? ` · ≈ ${eta}` : "")));
    }
    return item;
  }));
  const fraction = current < 0 ? 0 : (current + (total ? done / total : 0)) / STEPS.length;
  $("bar-fill").style.width = `${Math.round(fraction * 100)}%`;
}

function estimate(job) {
  const { done, total } = job.progress;
  if (!done || done >= total || !job.step_started_at || job.status !== "running") return "";
  const elapsed = Date.now() / 1000 - job.step_started_at;
  const left = (elapsed / done) * (total - done);
  return left < 60 ? "меньше минуты" : `${Math.round(left / 60)} мин`;
}

async function renderResult(job) {
  const md = await api(`/api/jobs/${job.id}/result.md`);
  const doc = $("doc");
  doc.dataset.md = md;
  if (window.marked) {
    // The text comes from speech recognition and an LLM: never let it inject HTML.
    const escaped = md.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    doc.innerHTML = marked.parse(escaped);
    // Only plain web links survive: no javascript:/data: URLs, no remote images.
    doc.querySelectorAll("a").forEach((a) => {
      if (/^https?:\/\//i.test(a.getAttribute("href") || "")) {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      } else {
        a.removeAttribute("href");
      }
    });
    doc.querySelectorAll("img").forEach((img) => img.remove());
  } else {
    doc.replaceChildren(el("pre", "plain", md));
  }
  $("dl-docx").href = `/api/jobs/${job.id}/result.docx`;
  $("dl-md").href = `/api/jobs/${job.id}/result.md`;
  $("re-kind").value = job.kind;
  renderedFor = job.id;
}

async function act(path, data) {
  clearTimeout(timer);
  try {
    await postJson(path, data);
  } catch (err) {
    showError("job-key-error", err.message);
  }
  renderedFor = null;
  showJob(currentJob.id, ++generation);
}

function bindJob() {
  $("resume-btn").addEventListener("click", () => act(`/api/jobs/${currentJob.id}/resume`, {}));
  $("re-btn").addEventListener("click", () =>
    act(`/api/jobs/${currentJob.id}/restructure`, { kind: $("re-kind").value }));
  $("copy-btn").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("doc").dataset.md || "");
    const button = $("copy-btn");
    button.textContent = "Скопировано";
    setTimeout(() => { button.textContent = "Копировать"; }, 1500);
  });
  bindKeyForm("job-key-form", "job-key-input", "job-key-error",
    () => act(`/api/jobs/${currentJob.id}/resume`, {}));
}

window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", () => {
  bindHome();
  bindJob();
  route();
});
