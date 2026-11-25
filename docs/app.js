(() => {
  "use strict";

  const FORMS = [
    { key: "all", label: "전체" },
    { key: "아이디어 제안", label: "아이디어 제안" },
    { key: "기획서", label: "기획서" },
    { key: "결과물", label: "영상·디자인 등 결과물" },
    { key: "서포터즈", label: "서포터즈" },
    { key: "대외활동", label: "대외활동" },
    { key: "none", label: "미분류" },
  ];
  const FIELDS = ["문화기획", "문화정책", "브랜드·마케팅", "공공문제·정책제안"];
  const TARGETS = [
    { key: "univ", label: "대학생만", test: (t) => t.includes("대학생") && !t.includes("일반인") },
    { key: "general", label: "일반인 가능", test: (t) => t.includes("일반인") },
    { key: "youth", label: "청년", test: (t) => t.includes("청년") },
    { key: "unknown", label: "대상 미확인", test: (t) => !t.some((x) => x !== "청소년") },
  ];
  const TEAMS = [
    { key: "ind", label: "개인 참가 가능", test: (v) => v === "개인" || v === "개인·팀" },
    { key: "team", label: "팀 참가 가능", test: (v) => v === "팀" || v === "개인·팀" },
    { key: "unknown", label: "확인 필요", test: (v) => !v },
  ];
  const TEAM_OPTIONS = ["개인", "팀", "개인·팀"];
  const GROUPS = ["공모전 모음 사이트", "정부·공공기관", "문화예술 기관", "기타"];
  const NEW_DAYS = 3;

  // ---------- 브라우저 저장 (이 기기에서만) ----------
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 무시 */ } },
  };

  const state = {
    contests: [], status: {}, sources: [], overrides: {}, updated: null,
    form: store.get("form", "all"),
    fields: new Set(store.get("fields", [])),
    targets: new Set(store.get("targets", [])),
    teams: new Set(store.get("teams", [])),
    q: "", newOnly: false, hideRead: store.get("hideRead", false),
    read: new Set(store.get("read", [])),
    repo: store.get("repo", guessRepo()),
    token: store.get("token", ""),
  };
  const admin = () => Boolean(state.token && state.repo);

  function guessRepo() {
    const m = location.hostname.match(/^([^.]+)\.github\.io$/);
    const seg = location.pathname.split("/").filter(Boolean)[0];
    return m && seg ? `${m[1]}/${seg}` : "";
  }

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function getJSON(path, fallback) {
    try {
      const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch { return fallback; }
  }

  async function load() {
    const [contests, status, sources, overrides] = await Promise.all([
      getJSON("data/contests.json", { contests: [] }),
      getJSON("data/status.json", { sources: {} }),
      getJSON("data/sources.json", { sources: [] }),
      getJSON("data/overrides.json", {}),
    ]);
    state.contests = contests.contests || [];
    state.updated = contests.updated;
    state.status = status.sources || {};
    state.sources = sources.sources || [];
    state.overrides = { ...(overrides || {}), ...store.get("pendingOverrides", {}) };
    render();
  }

  // ---------- 계산 ----------
  const today = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10); // KST
  const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
  const md = (d) => d.slice(5).replace("-", ".");

  function view(c) {
    const ov = state.overrides[c.id] || {};
    const form = "form" in ov ? ov.form : c.form || null;
    const team = "team" in ov ? ov.team : c.team || null;
    const t = today();
    let st = c.state || "open";
    if (c.deadline && c.deadline < t) st = "closed";
    else if (c.start && c.start > t) st = "upcoming";
    else if (c.start && c.start <= t && st === "upcoming") st = "open";
    const refDay = (state.updated || t).slice(0, 10);
    const isNew = !c.initial && st !== "closed" && daysBetween(c.first_seen, refDay) < NEW_DAYS;
    const targets = (c.targets || []);
    return { ...c, form, team, st, targets, hidden: Boolean(ov.hidden), isNew, isRead: state.read.has(c.id) };
  }

  function matches(c) {
    if (c.hidden) return false;
    if (state.form !== "all") {
      if (state.form === "none" ? c.form : c.form !== state.form) return false;
    }
    if (state.fields.size && !(c.fields || []).some((f) => state.fields.has(f))) return false;
    if (state.targets.size && !TARGETS.some((x) => state.targets.has(x.key) && x.test(c.targets))) return false;
    if (state.teams.size && !TEAMS.some((x) => state.teams.has(x.key) && x.test(c.team))) return false;
    if (state.newOnly && !c.isNew) return false;
    if (state.hideRead && c.isRead) return false;
    if (state.q) {
      const hay = `${c.title} ${c.org || ""} ${c.source}`.toLowerCase();
      if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  const byDeadline = (a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999") || a.title.localeCompare(b.title);
  const byStart = (a, b) => (a.start || a.deadline || "9999").localeCompare(b.start || b.deadline || "9999");
  const byRecent = (a, b) => (b.deadline || b.last_seen || "").localeCompare(a.deadline || a.last_seen || "");

  // ---------- 렌더 ----------
  function render() {
    renderHeader();
    renderAlerts();
    renderFilters();
    renderContests();
    renderSources();
  }

  function renderHeader() {
    if (!state.updated) { $("#updated").textContent = "아직 수집 기록이 없어요"; return; }
    const txt = new Date(state.updated).toLocaleString("ko-KR", { month: "long", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" });
    $("#updated").textContent = `마지막 수집 ${txt}`;
  }

  function renderAlerts() {
    const fails = state.sources.filter((s) => state.status[s.id] && state.status[s.id].ok === false);
    $("#alerts").innerHTML = fails.length
      ? `<div class="alert">⚠ ${fails.length}곳 수집 실패: ${fails.slice(0, 6).map((s) => esc(s.name)).join(", ")}${fails.length > 6 ? " 외" : ""} · <button data-go="sources">출처 목록에서 확인</button></div>`
      : "";
  }

  function renderFilters() {
    const all = state.contests.map(view).filter((c) => !c.hidden && c.st !== "closed");
    $("#formTabs").innerHTML = FORMS.filter((f) => f.key !== "none" || all.some((c) => !c.form)).map((f) => {
      const list = all.filter((c) => f.key === "all" || (f.key === "none" ? !c.form : c.form === f.key));
      const nn = list.filter((c) => c.isNew).length;
      return `<button class="tab ${state.form === f.key ? "on" : ""}" data-form="${esc(f.key)}">${f.label}<span class="n">${list.length}</span>${nn ? `<span class="nn">+${nn}</span>` : ""}</button>`;
    }).join("");
    $("#fieldChips").innerHTML = FIELDS.map((f) => `<button class="chip ${state.fields.has(f) ? "on" : ""}" data-field="${f}">${f}</button>`).join("");
    $("#targetChips").innerHTML = TARGETS.map((t) => `<button class="chip ${state.targets.has(t.key) ? "on" : ""}" data-target="${t.key}">${t.label}</button>`).join("");
    $("#teamChips").innerHTML = TEAMS.map((t) => `<button class="chip ${state.teams.has(t.key) ? "on" : ""}" data-team="${t.key}">${t.label}</button>`).join("");
    $("#hideRead").checked = state.hideRead;
    $("#newOnly").checked = state.newOnly;
  }

  function when(c) {
    const t = today();
    if (c.st === "upcoming") {
      if (!c.start) return `<span class="dday up">접수 예정</span>${c.deadline ? `<span class="dl">~${md(c.deadline)}</span>` : ""}`;
      const n = daysBetween(t, c.start);
      return `<span class="dday up">${n <= 0 ? "곧 시작" : `${n}일 뒤 시작`}</span><span class="dl">${md(c.start)} ~ ${c.deadline ? md(c.deadline) : ""}</span>`;
    }
    if (!c.deadline) return `<span class="dl">마감일 미표기</span>`;
    const n = daysBetween(t, c.deadline);
    const label = c.st === "closed" ? "마감" : n === 0 ? "오늘 마감" : `D-${n}`;
    return `<span class="dday ${c.st !== "closed" && n <= 3 ? "soon" : ""}">${label}</span><span class="dl">${c.start ? `${md(c.start)} ` : ""}~${md(c.deadline)}</span>`;
  }

  function targetBadges(c) {
    const t = c.targets;
    const out = [];
    if (t.includes("대학생") && !t.includes("일반인")) out.push("대학생만");
    else if (t.includes("일반인")) out.push("일반인 가능");
    if (t.includes("청년")) out.push("청년");
    return out.map((x) => `<span class="badge who">${x}</span>`).join("");
  }

  function card(c) {
    const formLabel = c.form ? FORMS.find((f) => f.key === c.form)?.label || c.form : "미분류";
    const cls = c.form ? `f-${FORMS.findIndex((f) => f.key === c.form)}` : "f-none";
    const editor = admin()
      ? `<select class="edit" data-edit-form="${c.id}" aria-label="형태 수정">${FORMS.slice(1).map((f) => `<option value="${esc(f.key)}" ${((c.form || "none") === f.key) ? "selected" : ""}>${f.label}</option>`).join("")}</select>
         <select class="edit" data-edit-team="${c.id}" aria-label="개인/팀 수정"><option value="">개인/팀 ?</option>${TEAM_OPTIONS.map((o) => `<option ${c.team === o ? "selected" : ""}>${o}</option>`).join("")}</select>
         <button class="hide-btn" data-hide="${c.id}">숨기기</button>`
      : "";
    const also = (c.also || []).map((a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.source)}</a>`).join(" · ");
    return `<article class="item ${c.isRead ? "read" : ""}">
      <div class="meta">
        <span class="badge ${cls}">${esc(formLabel)}</span>
        ${c.isNew ? `<span class="badge new">NEW</span>` : ""}
        ${targetBadges(c)}
        ${c.team ? `<span class="badge team">${esc(c.team === "개인·팀" ? "개인·팀 모두" : c.team === "팀" ? "팀만" : "개인")}</span>` : ""}
        <span class="org">${esc(c.org || "")}</span>
      </div>
      <a class="title" href="${esc(c.url)}" target="_blank" rel="noopener" data-read="${c.id}">${esc(c.title)}</a>
      <div class="sub">${(c.fields || []).map((f) => `<span class="field">${esc(f)}</span>`).join("")}${c.reward ? `<span class="reward">🏆 ${esc(c.reward)}</span>` : ""}<span class="src">${esc(c.source)}${also ? ` · ${also}` : ""}</span></div>
      <div class="right">${when(c)}${editor}</div>
    </article>`;
  }

  function section(title, list, note) {
    if (!list.length) return "";
    return `<section class="sec"><h2>${title} <span class="cnt">${list.length}</span>${note ? `<span class="muted small">${note}</span>` : ""}</h2><div class="list">${list.map(card).join("")}</div></section>`;
  }

  function renderContests() {
    const list = state.contests.map(view).filter(matches);
    const fresh = list.filter((c) => c.isNew).sort((a, b) => (a.st === "upcoming") - (b.st === "upcoming") || byDeadline(a, b));
    const open = list.filter((c) => !c.isNew && c.st === "open").sort(byDeadline);
    const upcoming = list.filter((c) => !c.isNew && c.st === "upcoming").sort(byStart);
    const closed = list.filter((c) => c.st === "closed").sort(byRecent);
    const html = section("NEW", fresh, "최근 3일 안에 새로 올라온 공모전")
      + section("접수 중", open, "마감 임박 순")
      + section("접수 시작 전", upcoming, "접수 시작일 순");
    $("#sections").innerHTML = html || `<p class="empty">${state.contests.length ? "조건에 맞는 공모전이 없어요." : "아직 수집된 공모전이 없어요. 첫 수집이 끝나면 여기에 보여요."}</p>`;
    $("#closedCount").textContent = `(${closed.length})`;
    $("#closedList").innerHTML = closed.slice(0, 200).map(card).join("") || `<p class="empty">없음</p>`;
    $("#closedBox").hidden = !closed.length;
  }

  function renderSources() {
    const icon = (s) => {
      if (s.type === "link" || !s.url) return `<td class="st">🔗 직접 확인</td>`;
      const st = state.status[s.id];
      if (!st) return `<td class="st muted">대기 중</td>`;
      if (st.ok === false) return `<td class="st fail">⚠ 수집 실패<span class="err">${esc(st.error || "")}</span></td>`;
      return `<td class="st">✓ ${st.count}건</td>`;
    };
    const groups = [...new Set([...GROUPS, ...state.sources.map((s) => s.group)])];
    $("#sourceList").innerHTML = groups.map((g) => {
      const ss = state.sources.filter((s) => s.group === g);
      if (!ss.length) return "";
      return `<section class="cgroup"><h3>${esc(g)} <span class="muted small">${ss.length}</span></h3>
        <table class="ctable">${ss.map((s) => `<tr>
          ${icon(s)}
          <td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)}
            ${s.recommended ? ` <span class="rec">추천</span>` : ""}
            ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}</td>
          ${admin() && (s.type === "web" || s.type === "link") ? `<td style="width:60px;text-align:right"><button class="hide-btn" data-del="${esc(s.id)}">삭제</button></td>` : admin() ? "<td></td>" : ""}
        </tr>`).join("")}</table></section>`;
    }).join("") + `<p class="muted small" style="margin-top:20px">수집된 글 중 디자인·사진·UCC·문학·IT 같은 관심 밖 분야와 청소년 전용 공모전은 자동으로 빠져요. 문화기획·문화정책·브랜드·공공문제 분야만 남겨요.</p>`;
  }

  // ---------- GitHub에 저장 ----------
  async function gh(path, opts = {}) {
    const r = await fetch(`https://api.github.com/repos/${state.repo}/${path}`, {
      ...opts,
      headers: { Authorization: `Bearer ${state.token}`, Accept: "application/vnd.github+json", ...(opts.headers || {}) },
    });
    if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 160)}`);
    return r.status === 204 ? null : r.json();
  }
  const b64 = (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
  const unb64 = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), (c) => c.charCodeAt(0)));

  async function updateFile(path, mutate, message) {
    const cur = await gh(`contents/${path}`);
    const next = mutate(JSON.parse(unb64(cur.content)));
    await gh(`contents/${path}`, {
      method: "PUT",
      body: JSON.stringify({ message, content: b64(JSON.stringify(next, null, 1) + "\n"), sha: cur.sha }),
    });
    return next;
  }

  async function setOverride(id, patch) {
    state.overrides[id] = { ...(state.overrides[id] || {}), ...patch };
    const pending = store.get("pendingOverrides", {});
    pending[id] = state.overrides[id];
    store.set("pendingOverrides", pending);
    render();
    try {
      await updateFile("docs/data/overrides.json", (o) => ({ ...o, [id]: state.overrides[id] }), "공모전 분류 수정");
      const p = store.get("pendingOverrides", {}); delete p[id]; store.set("pendingOverrides", p);
    } catch (e) {
      alert(`저장 실패 (이 기기에만 반영됨): ${e.message}`);
    }
  }

  // ---------- 이벤트 ----------
  const toggle = (set, v) => (set.has(v) ? set.delete(v) : set.add(v));
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-form],[data-field],[data-target],[data-team],[data-view],[data-go],[data-read],[data-hide],[data-del]");
    if (!t) return;
    const d = t.dataset;
    if (d.form) { state.form = d.form; store.set("form", state.form); render(); }
    else if (d.field) { toggle(state.fields, d.field); store.set("fields", [...state.fields]); render(); }
    else if (d.target) { toggle(state.targets, d.target); store.set("targets", [...state.targets]); render(); }
    else if (d.team) { toggle(state.teams, d.team); store.set("teams", [...state.teams]); render(); }
    else if (d.view || d.go) { showView(d.view || d.go); }
    else if (d.read) { state.read.add(d.read); store.set("read", [...state.read]); setTimeout(render, 300); }
    else if (d.hide) { if (confirm("이 공모전을 숨길까요?")) setOverride(d.hide, { hidden: true }); }
    else if (d.del) {
      const s = state.sources.find((x) => x.id === d.del);
      if (s && confirm(`'${s.name}'을(를) 출처 목록에서 삭제할까요?`)) {
        updateFile("docs/data/sources.json", (o) => ({ sources: o.sources.filter((x) => x.id !== s.id) }), `출처 삭제: ${s.name}`)
          .then((o) => { state.sources = o.sources; render(); })
          .catch((err) => alert(`삭제 실패: ${err.message}`));
      }
    }
  });
  document.addEventListener("change", (e) => {
    const f = e.target.closest("[data-edit-form]");
    if (f) setOverride(f.dataset.editForm, { form: f.value === "none" ? null : f.value });
    const tm = e.target.closest("[data-edit-team]");
    if (tm) setOverride(tm.dataset.editTeam, { team: tm.value || null });
  });

  function showView(v) {
    document.querySelectorAll(".view").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
    $("#view-contests").hidden = v !== "contests";
    $("#view-sources").hidden = v !== "sources";
    window.scrollTo(0, 0);
  }

  $("#q").addEventListener("input", (e) => { state.q = e.target.value.trim(); renderContests(); });
  $("#newOnly").addEventListener("change", (e) => { state.newOnly = e.target.checked; renderContests(); });
  $("#hideRead").addEventListener("change", (e) => { state.hideRead = e.target.checked; store.set("hideRead", state.hideRead); renderContests(); });
  $("#resetFilters").addEventListener("click", () => {
    state.fields.clear(); state.targets.clear(); state.teams.clear(); state.q = ""; state.newOnly = false; state.hideRead = false;
    ["fields", "targets", "teams"].forEach((k) => store.set(k, [])); store.set("hideRead", false);
    $("#q").value = ""; render();
  });

  // 설정
  $("#openSettings").addEventListener("click", () => {
    $("#repoInput").value = state.repo; $("#tokenInput").value = state.token; $("#settingsMsg").textContent = "";
    $("#settings").showModal();
  });
  $("#saveSettings").addEventListener("click", () => {
    state.repo = $("#repoInput").value.trim(); state.token = $("#tokenInput").value.trim();
    store.set("repo", state.repo); store.set("token", state.token); render();
  });
  $("#runNow").addEventListener("click", async () => {
    state.repo = $("#repoInput").value.trim(); state.token = $("#tokenInput").value.trim();
    try {
      await gh("actions/workflows/collect.yml/dispatches", { method: "POST", body: JSON.stringify({ ref: "main" }) });
      $("#settingsMsg").textContent = "수집을 시작했어요. 5분쯤 뒤 새로고침하세요.";
    } catch (e) { $("#settingsMsg").textContent = `실패: ${e.message}`; }
  });

  // 기관 추가
  $("#groupSelect").innerHTML = GROUPS.map((g) => `<option>${g}</option>`).join("");
  $("#addSourceBtn").addEventListener("click", () => {
    if (!admin()) { alert("기관을 추가하려면 먼저 ⚙ 관리 설정에서 GitHub 토큰을 넣어주세요."); return; }
    $("#addSourceForm").reset(); $("#addSource").showModal();
  });
  $("#addSourceForm").addEventListener("submit", async (e) => {
    if (e.submitter && e.submitter.value === "cancel") return;
    const f = new FormData(e.target);
    const name = f.get("name").trim();
    const s = {
      id: name.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, ""),
      name, org: name, group: f.get("group"), type: f.get("type"), url: f.get("url").trim() || null,
    };
    if (f.get("note").trim()) s.note = f.get("note").trim();
    try {
      const o = await updateFile("docs/data/sources.json", (o) => ({ sources: [...o.sources.filter((x) => x.id !== s.id), s] }), `기관 추가: ${name}`);
      state.sources = o.sources; render();
    } catch (err) { alert(`추가 실패: ${err.message}`); }
  });

  showView("contests");
  load();
})();
