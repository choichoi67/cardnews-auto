const $ = (selector) => document.querySelector(selector);
const state = {
  data:null, activeId:null, editingId:null, pendingPsdFiles:[], signature:"", activeView:"queue",
  newsroomQuery:"", newsroomMagazine:"all", newsroomStatus:"open", editingArtifact:null
};

function toast(message, type="info") {
  const element = $("#toast");
  element.textContent = message;
  element.dataset.type = type;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), type === "error" ? 5200 : 3200);
}

async function api(path, options={}) {
  const isForm = options.body instanceof FormData;
  const response = await fetch(path, { headers:isForm ? {} : {"Content-Type":"application/json"}, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "요청을 처리하지 못했습니다.");
  return data;
}

function escapeHtml(value="") {
  const element = document.createElement("div");
  element.textContent = String(value);
  return element.innerHTML;
}

function activeProfile() {
  if (state.activeId === "__newsroom__") return null;
  return state.data?.profiles.find((profile) => profile.id === state.activeId) || null;
}

function statusLabel(value) {
  return ({ awaiting_approval:"승인 대기", approved:"제작 대기", building:"제작 중", built:"제작 완료" }[value] || "대기");
}

function jobLabel(value) {
  return ({ collect:"뉴스 수집", build:"카드 제작", "telegram-send":"Telegram 전송" }[value] || value);
}

function auditLabel(value) {
  return ({
    "profile.create":"채널 생성", "profile.update":"채널 설정 변경",
    "collect":"뉴스 수집 요청", "approve":"뉴스 승인",
    "build":"카드뉴스 제작 요청", "telegram":"Telegram 전송 요청",
    "build.queued":"제작 대기열 등록",
    "retry":"작업 재시도", "candidate.disposition":"후보 상태 변경",
    "artifact.revision_queued":"산출물 수정본 제작"
  }[value] || value);
}

function allArtifacts() {
  const newsroom = state.data?.newsroom?.artifacts || [];
  const profiles = (state.data?.profiles || []).flatMap((profile) => profile.artifacts || []);
  return [...new Map(
    [...newsroom, ...profiles].map((item) => [Number(item.id), item])
  ).values()];
}

function outputCard(item, global=false) {
  const version = Number(item.revision_number || 0);
  const channel = item.magazine_name || (state.data?.profiles || []).find((profile) => profile.id === item.magazine_id)?.name || "";
  return `<article class="output-post output-card">
    <figure class="output-stack">
      <img src="/api/artifacts/${item.id}/cover?v=${encodeURIComponent(item.built_at)}" alt="${escapeHtml(channel)} ${escapeHtml(item.topic)} 표지" loading="lazy" width="1080" height="1440">
      <figcaption class="output-status"><span>${item.original_fallback ? "원본 사진 대체" : "완료"}</span><b>${item.slide_count}장</b></figcaption>
    </figure>
    <div class="output-meta">
      <div>${global ? `<span class="channel-pill">${escapeHtml(channel)}</span>` : ""}<h3>${escapeHtml(item.topic)}</h3><p>${version ? `수정 ${version}차 · ` : ""}${new Date(item.built_at).toLocaleString("ko-KR")}</p></div>
    </div>
    <div class="output-actions">
      <button type="button" class="output-edit" data-edit-artifact="${item.id}">
        <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>
        편집
      </button>
      <button type="button" class="output-review" data-review-artifact="${item.id}" aria-label="${escapeHtml(item.topic)} 원고 검수">
        원고
      </button>
      <button type="button" title="새 버전으로 재제작" aria-label="${escapeHtml(item.topic)} 바로 재제작" data-remake-artifact="${item.id}">
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      ${item.drive_url ? `<a title="Google Drive 열기" aria-label="${escapeHtml(item.topic)} Google Drive 열기" href="${escapeHtml(item.drive_url)}" target="_blank" rel="noreferrer">
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 19h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </a>` : ""}
    </div>
  </article>`;
}

function selectTab(tabs, panels, tabKey, panelKey, name, focus=false) {
  document.querySelectorAll(tabs).forEach((tab) => {
    const selected = tab.dataset[tabKey] === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus();
  });
  document.querySelectorAll(panels).forEach((panel) => {
    panel.hidden = (panelKey ? panel.dataset[panelKey] : panel.id.replace("view-", "")) !== name;
  });
}

async function openScriptReview(artifactId) {
  try {
    const detail = await api(`/api/artifacts/${artifactId}`);
    $("#script-review-topic").textContent = detail.topic;
    $("#script-interpretation").textContent = detail.source_interpretation || "이 산출물에는 원문 해석 기록이 없습니다.";
    $("#script-summary").textContent = detail.editorial_summary || "이 산출물에는 편집 요약 기록이 없습니다.";
    $("#script-body-draft").textContent = detail.body_draft || "이 산출물에는 본문 초안 기록이 없습니다.";
    selectTab("[data-script-review-tab]", "[data-script-review-panel]", "scriptReviewTab", "scriptReviewPanel", "summary");
    $("#script-review-dialog").showModal();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function openRevision(artifactId) {
  if (state.revisionSubmitting) return;
  const artifact = allArtifacts().find((item) => Number(item.id) === Number(artifactId));
  if (!artifact) return toast("산출물 정보를 찾을 수 없습니다.", "error");
  const session = Symbol("revision");
  state.revisionSession = session;
  state.revisionLoading = false;
  state.editingArtifact = null;
  state.editingSlide = null;
  const drafts = new Map();
  const form = $("#revision-form");
  const rememberSlide = () => {
    if (!state.editingArtifact || !state.editingSlide) return;
    drafts.set(`${state.editingArtifact.id}:${state.editingSlide.number}`, {
      title: form.elements.slide_title.value, body: form.elements.slide_body.value,
      instruction: form.elements.instruction.value, target: form.elements.instruction_target.value, files: [...form.elements.reference_image.files]
    });
  };
  let versionRequest = 0;
  $("#revision-form").reset();
  $("#reference-name").textContent = "이미지를 선택하거나 끌어다 놓으세요";
  $("#revision-dialog").showModal();
  const rootId = Number(artifact.parent_artifact_id || artifact.id);
  const versions = allArtifacts()
    .filter((item) => item.magazine_id === artifact.magazine_id && (
      Number(item.id) === rootId || Number(item.parent_artifact_id || 0) === rootId
    ))
    .sort((a,b) => Number(a.revision_number || 0) - Number(b.revision_number || 0));
  const selectSlide = (slide) => {
    if (!slide || state.revisionSubmitting || state.revisionLoading) return;
    rememberSlide();
    const draft = drafts.get(`${state.editingArtifact.id}:${slide.number}`);
    form.elements.instruction.value = draft?.instruction || "";
    form.elements.instruction_target.value = draft?.target || "text";
    $("#submit-revision").disabled = slide.editable === false;
    $("#revision-notice").textContent = slide.editable === false
      ? (state.editingArtifactDetail?.partial_edit_ready ? "고정 CTA는 수정할 수 없습니다." : "이전 제작본입니다. 목록의 재제작으로 부분 수정용 기준 버전을 먼저 만드세요.")
      : (state.editingArtifactDetail?.warnings || []).join(" · ");
    form.elements.reference_image.value = "";
    if (draft?.files?.length) {
      const transfer = new DataTransfer();
      draft.files.forEach((file) => transfer.items.add(file));
      form.elements.reference_image.files = transfer.files;
    }
    $("#reference-name").textContent = form.elements.reference_image.files[0]?.name || "이미지를 선택하거나 끌어다 놓으세요";
    state.editingSlide = slide;
    $("#revision-form").elements.slide_number.value = slide.number;
    $("#revision-form").elements.slide_title.value = draft?.title ?? slide.title ?? "";
    $("#revision-form").elements.slide_body.value = draft?.body ?? slide.body ?? "";
    $("#revision-form").elements.cover_title.value = "";
    $("#selected-slide-number").textContent = String(slide.number).padStart(2, "0");
    $("#revision-action-number").textContent = String(slide.number).padStart(2, "0");
    $("#selected-slide-role").textContent = ({cover:"표지",body:"본문",cta:"CTA"}[slide.role] || "본문");
    $("#revision-cover").src = slide.image_url;
    $("#revision-cover").alt = `${slide.number}번 ${slide.role === "body" ? "본문" : slide.role === "cta" ? "CTA" : "표지"} 카드`;
    document.querySelectorAll("[data-edit-slide]").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.editSlide) === Number(slide.number));
    });
  };
  const selectVersion = async (selected) => {
    if (state.revisionSubmitting) return;
    const request = ++versionRequest;
    state.revisionLoading = true;
    $("#submit-revision").disabled = true;
    try {
      const detail = await api(`/api/artifacts/${selected.id}`);
      if (request !== versionRequest || state.revisionSession !== session || !$("#revision-dialog").open) return;
      if (!detail.slides?.length) throw new Error("수정할 카드가 없습니다.");
      rememberSlide();
      state.editingSlide = null;
      state.editingArtifact = selected;
      $("#revision-topic").textContent = selected.topic;
      $("#revision-date").textContent = new Date(selected.built_at).toLocaleString("ko-KR");
      state.editingArtifactDetail = detail;
      $("#revision-slides").innerHTML = detail.slides.map((slide) => `
        <button type="button" data-edit-slide="${slide.number}" aria-label="${slide.number}번 ${slide.role === "body" ? "본문" : slide.role === "cta" ? "CTA" : "표지"} 카드 수정">
          <img src="${escapeHtml(slide.image_url)}?v=${encodeURIComponent(selected.built_at)}" alt="" width="54" height="72">
          <span>${String(slide.number).padStart(2,"0")} · ${{cover:"표지",body:"본문",cta:"CTA"}[slide.role] || "본문"}</span>
        </button>`).join("");
      document.querySelectorAll("[data-edit-slide]").forEach((button) => {
        button.onclick = () => {
          const slide = detail.slides.find((item) => Number(item.number) === Number(button.dataset.editSlide));
          if (slide) selectSlide(slide);
        };
      });
      state.revisionLoading = false;
      selectSlide(detail.slides[0]);
      document.querySelectorAll("[data-preview-artifact]").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.previewArtifact) === Number(selected.id));
      });
    } catch (error) {
      if (request === versionRequest && state.revisionSession === session && $("#revision-dialog").open) toast(error.message, "error");
    } finally {
      if (request === versionRequest && state.revisionSession === session) {
        state.revisionLoading = false;
        $("#submit-revision").disabled = state.revisionSubmitting || !state.editingArtifact || state.editingSlide?.editable === false;
      }
    }
  };
  const profile = (state.data?.profiles || []).find((item) => item.id === artifact.magazine_id);
  $("#revision-channel").textContent = profile?.name || artifact.magazine_name || "매거진";
  $("#revision-versions").innerHTML = versions.map((item) => `
    <button type="button" data-preview-artifact="${item.id}" aria-label="${Number(item.revision_number || 0) ? `${item.revision_number}차 수정본` : "원본"} 보기">
      <img src="/api/artifacts/${item.id}/cover?v=${encodeURIComponent(item.built_at)}" alt="" width="54" height="72">
      <span>${Number(item.revision_number || 0) ? `${item.revision_number}차 수정` : "원본"}</span>
    </button>`).join("");
  document.querySelectorAll("[data-preview-artifact]").forEach((button) => {
    button.onclick = async () => {
      const selected = versions.find((item) => Number(item.id) === Number(button.dataset.previewArtifact));
      if (selected) await selectVersion(selected);
    };
  });
  await selectVersion(artifact);
}

function formatMoment(value, article=false) {
  if (!value) return article ? "발행일 미제공" : "기록 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return article ? `발행 ${String(value)}` : "기록 없음";
  return article
    ? `발행 ${date.toLocaleDateString("ko-KR", {year:"numeric", month:"2-digit", day:"2-digit"})}`
    : date.toLocaleString("ko-KR", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"});
}

function setButtonLoading(button, loading, loadingLabel="처리 중") {
  if (!button) return;
  const label = button.querySelector(".button-label");
  if (!button.dataset.originalLabel && label) button.dataset.originalLabel = label.textContent;
  button.classList.toggle("is-loading", loading);
  button.setAttribute("aria-busy", String(loading));
  button.disabled = loading || button.dataset.wasDisabled === "true";
  if (label) label.textContent = loading ? loadingLabel : button.dataset.originalLabel;
}

function setConnection(dotSelector, connected, name) {
  const dot = $(dotSelector);
  dot.classList.toggle("on", connected);
  const row = dot.parentElement;
  row.setAttribute("aria-label", `${name} ${connected ? "연결됨" : "연결 필요"}`);
  const detail = row.querySelector("small");
  if (detail) detail.textContent = connected ? "연결됨" : "연결 필요";
}

function switchView(view, focus=false) {
  state.activeView = view;
  selectTab("[data-view]", ".view-panel", "view", "", view, focus);
  history.replaceState(null, "", `#${view}`);
}

function renderWorkflow(status) {
  const activeIndex = ({ awaiting_approval:1, approved:3, building:4, built:5 }[status] ?? 0);
  document.querySelectorAll(".workflow-steps li").forEach((item, index) => {
    item.classList.toggle("active", index <= activeIndex);
    if (index < activeIndex) item.setAttribute("aria-label", `${item.textContent.trim()} 완료`);
    else if (index === activeIndex) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}

function renderNavigation(profile) {
  const newsroomActive = state.activeId === "__newsroom__";
  $("#magazine-list").innerHTML = `
    <button class="magazine-button newsroom-button ${newsroomActive ? "active" : ""}" type="button" data-id="__newsroom__" style="--profile-color:#171725" aria-pressed="${newsroomActive}">
      <span>전체 편집국</span>
    </button>` + state.data.profiles.map((item) => `
    <button class="magazine-button ${item.id === profile?.id ? "active" : ""}" type="button" data-id="${escapeHtml(item.id)}" style="--profile-color:${escapeHtml(item.color)}" aria-pressed="${item.id === profile?.id}">
      <span>${escapeHtml(item.name)}</span>
    </button>`).join("");
  document.querySelectorAll(".magazine-button").forEach((button) => {
    button.onclick = () => { state.activeId = button.dataset.id; state.signature = ""; render(); };
  });
}

function renderJobs(rows, showMagazine=false) {
  $("#job-list").innerHTML = rows.length ? rows.map((job) => `
    <div class="job ${escapeHtml(job.status)}">
      <time>${new Date(job.created_at).toLocaleTimeString("ko-KR", { hour:"2-digit", minute:"2-digit" })}</time>
      <b>${showMagazine ? `${escapeHtml(job.profile_name)} · ` : ""}${escapeHtml(jobLabel(job.action))} · ${escapeHtml(job.state.label)}</b>
      ${job.topic ? `<p class="job-topic">${escapeHtml(job.topic)}</p>` : ""}
      ${job.queue_label ? `<p class="job-queue">${escapeHtml(job.queue_label)}</p>` : ""}
      ${job.log ? `<pre>${escapeHtml(job.log)}</pre>` : ""}
      ${job.state.retryable ? `<div class="job-actions"><button class="retry-job" type="button" data-retry-job="${escapeHtml(job.id)}">다시 실행</button></div>` : ""}
    </div>`).join("") : `<div class="empty-state">실행 기록이 없습니다.</div>`;
}

function renderOperations(profileId=null) {
  const channels = (state.data.profiles || []).filter((item) => !profileId || item.id === profileId).map((profile) => {
    const jobs = profile.jobs || [];
    const failedAt = jobs.find((job) => ["failed","interrupted"].includes(job.status))?.finished_at || "";
    const successAt = jobs.find((job) => job.status === "completed")?.finished_at || "";
    const c = {
      rss:{configured:Boolean(profile.pages?.length || profile.feeds?.length), source_count:(profile.pages?.length || 0) + (profile.feeds?.length || 0)},
      openai:{configured:state.data.openai_ready},
      template:{configured:Boolean(profile.template_ready)},
      drive:{enabled:profile.drive_enabled, configured:state.data.drive_ready && Boolean(profile.drive_folder_id)},
      telegram:{enabled:profile.telegram_enabled, configured:state.data.telegram_ready, listener_running:state.data.telegram_ready}
    };
    const failed = Boolean(failedAt && failedAt > successAt);
    const missing = !c.rss?.configured || !c.openai?.configured || !c.template?.configured
      || (c.telegram?.enabled && !c.telegram?.configured) || (c.drive?.enabled && !c.drive?.configured);
    return {item:{
      magazine_name:profile.name, next_collection_at:profile.next_collection_at,
      schedule_enabled:profile.schedule_enabled,
      open_candidate_count:(profile.workflow?.candidates || []).filter((item) =>
        (item.disposition || "active") === "active" && !Number(item.built_count || 0)
      ).length,
      artifact_count:(profile.artifacts || []).length
    }, c, successAt, level:failed ? "danger" : missing ? "warning" : ""};
  });
  const unhealthy = channels.filter(({level}) => level);
  $("#health-summary").textContent = channels.length ? `${channels.length - unhealthy.length} 정상 · ${unhealthy.length} 확인 필요` : "채널 없음";
  const healthList = $("#health-list");
  healthList.className = channels.length ? "health-list" : "health-list empty-state";
  healthList.innerHTML = channels.length ? channels.map(({item, c, successAt, level}) => {
    const label = level === "danger" ? "오류 확인" : level === "warning" ? "설정 확인" : "정상";
    const connections = [
      `RSS ${c.rss?.configured ? c.rss.source_count : "미설정"}`,
      `AI ${c.openai?.configured ? "연결" : "미연결"}`,
      `템플릿 ${c.template?.configured ? "적용" : "미설정"}`,
      c.drive?.enabled ? `Drive ${c.drive.configured ? "연결" : "확인"}` : "Drive 꺼짐",
      c.telegram?.enabled
        ? `Telegram ${c.telegram.configured && c.telegram.listener_running ? "연결" : "끊김"}`
        : "Telegram 꺼짐"
    ].join(" · ");
    return `<article class="health-card ${level}">
      <div class="health-card-head"><h3>${escapeHtml(item.magazine_name)}</h3><span class="health-state">${label}</span></div>
      <dl class="health-facts">
        <div><dt>연결</dt><dd>${escapeHtml(connections)}</dd></div>
        <div><dt>검토 / 산출물</dt><dd>${item.open_candidate_count} / ${item.artifact_count}</dd></div>
        <div><dt>다음 수집</dt><dd>${item.schedule_enabled ? formatMoment(item.next_collection_at) : "수동 수집"}</dd></div>
        <div><dt>최근 성공</dt><dd>${formatMoment(successAt)}</dd></div>
      </dl>
    </article>`;
  }).join("") : "등록된 채널이 없습니다.";

  const names = new Map((state.data.profiles || []).map((item) => [item.id, item.name]));
  const events = (state.data.audit_events || []).filter((item) => names.has(item.magazine_id) && (!profileId || item.magazine_id === profileId));
  const auditList = $("#audit-list");
  auditList.className = events.length ? "audit-list" : "audit-list empty-state";
  auditList.innerHTML = events.length ? events.map((item) => {
    const details = item.details || {};
    const detail = details.name || details.title || details.action || names.get(item.magazine_id) || "";
    return `<article class="audit-event">
      <time>${formatMoment(item.created_at)}</time>
      <div><b>${escapeHtml(auditLabel(item.event_type))} · ${escapeHtml(names.get(item.magazine_id) || item.magazine_id)}</b>${detail ? `<p>${escapeHtml(detail)}</p>` : ""}</div>
      <span class="audit-actor">${escapeHtml(item.actor || "dashboard")}</span>
    </article>`;
  }).join("") : "아직 기록된 운영 변경이 없습니다.";
}

function dispositionActions(item, magazineId) {
  const disposition = item.disposition || "active";
  const candidateId = item.candidate_id || item.id || "";
  if (!candidateId) return "";
  const actions = {
    held:[["active","대기열 복구"],["hidden","숨김"]],
    hidden:[["active","숨김 해제"]],
    active:[["held","보류"],["hidden","숨김"]]
  }[disposition];
  return actions.map(([value, label]) =>
    `<button type="button" class="candidate-secondary${value === "hidden" ? " danger" : ""}" data-disposition="${value}" data-candidate="${escapeHtml(candidateId)}" data-magazine="${escapeHtml(magazineId)}">${label}</button>`
  ).join("");
}

function candidateCard(item, {index, magazineId, magazineName="", magazineColor="", actionable, label}) {
  const disposition = item.disposition || "active";
  const heading = magazineName
    ? `<div class="candidate-meta"><span class="num">${String(index).padStart(2,"0")}</span><span class="channel-pill">${escapeHtml(magazineName)}</span></div>`
    : `<span class="num">${String(index).padStart(2,"0")}</span>`;
  return `<article class="candidate ${actionable ? "" : "approved"} ${escapeHtml(disposition)}"${magazineColor ? ` style="--card-accent:${escapeHtml(magazineColor)}"` : ""}>
    ${heading}
    <div class="candidate-copy">
      <time class="candidate-date" datetime="${escapeHtml(item.published || "")}">${escapeHtml(formatMoment(item.published, true))}</time>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.summary || "요약이 없습니다.")}</p>
    </div>
    <div class="candidate-actions">
      <button type="button" data-approve="${index}" data-magazine="${escapeHtml(magazineId)}" data-global="${Boolean(magazineName)}" ${actionable ? "" : "disabled"}>${label}</button>
      ${dispositionActions(item, magazineId)}
    </div>
  </article>`;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.reviewArtifact) return openScriptReview(button.dataset.reviewArtifact);
  if (button.dataset.editArtifact) return openRevision(button.dataset.editArtifact);
  if (button.dataset.remakeArtifact) return action(
    null, button, "재제작 등록 중",
    () => api(`/api/artifacts/${button.dataset.remakeArtifact}/revise`, {
      method:"POST", body:new FormData()
    }),
    async () => { toast("새 버전을 제작 대기열에 등록했습니다.", "success"); await refresh(true); }
  );
  if (button.dataset.retryJob) return action(
    null, button, "재시도 요청 중",
    () => api(`/api/jobs/${button.dataset.retryJob}/retry`, {method:"POST"}),
    async () => { toast("실패 작업을 다시 실행합니다.", "success"); await refresh(true); }
  );
  const original = button.textContent;
  try {
    if (button.dataset.approve) {
      button.disabled = true;
      button.textContent = "승인·제작 시작 중…";
      await api(`/api/profiles/${button.dataset.magazine}/approve`, {
        method:"POST", body:JSON.stringify({index:Number(button.dataset.approve)})
      });
      const global = button.dataset.global === "true";
      if (global) state.activeId = button.dataset.magazine;
      switchView("production");
      toast(global ? "승인 완료 · 해당 매거진에서 제작을 시작했습니다." : "승인 완료 · 카드뉴스 제작을 시작했습니다.", "success");
      await refresh(true);
    } else if (button.dataset.disposition) {
      button.disabled = true;
      button.textContent = "변경 중…";
      await api(`/api/profiles/${button.dataset.magazine}/candidates/${button.dataset.candidate}/disposition`, {
        method:"POST", body:JSON.stringify({disposition:button.dataset.disposition})
      });
      toast({held:"후보를 보류했습니다.", hidden:"후보를 숨겼습니다."}[button.dataset.disposition] || "후보를 대기열로 복구했습니다.", "success");
      await refresh(true);
    }
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    toast(error.message, "error");
  }
});
