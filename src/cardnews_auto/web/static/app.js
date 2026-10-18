const PROFILE_CHECKBOXES = [
  "schedule_enabled", "telegram_enabled", "drive_enabled", "image_transform_enabled"
];

function renderNewsroom() {
  const newsroom = state.data.newsroom || { metrics:{}, candidates:[], artifacts:[] };
  const metrics = newsroom.metrics || {};
  const scheduled = state.data.profiles.filter((item) => item.schedule_enabled);
  const allCandidates = newsroom.candidates || [];
  const query = state.newsroomQuery.trim().toLocaleLowerCase("ko-KR");
  const candidates = allCandidates.filter((item) => {
    const disposition = item.disposition || "active";
    const status = state.newsroomStatus;
    return (state.newsroomMagazine === "all" || item.magazine_id === state.newsroomMagazine)
      && (status === "all" || (status === "open"
        ? item.status === "candidate" && disposition === "active"
        : ["held","hidden"].includes(status) ? disposition === status : item.status === status))
      && (!query || `${item.title || ""} ${item.summary || ""} ${item.magazine_name || ""}`.toLocaleLowerCase("ko-KR").includes(query));
  });
  $("#newsroom-filters").hidden = false;
  $("#newsroom-search").value = state.newsroomQuery;
  $("#newsroom-magazine-filter").innerHTML = `<option value="all">전체 매거진</option>` + state.data.profiles.map((item) =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`
  ).join("");
  $("#newsroom-magazine-filter").value = state.newsroomMagazine;
  $("#newsroom-status-filter").value = state.newsroomStatus;
  renderProfile({
    id:"", name:"전체 편집국",
    category:`${state.data.profiles.length}개 매거진의 후보·제작·산출물을 한곳에서 확인합니다.`,
    color:"#7c3aed", workflow:{candidates}, jobs:state.data.jobs || [],
    artifacts:newsroom.artifacts || [], schedule_enabled:Boolean(scheduled.length),
    collection_times:scheduled, next_collection_at:scheduled.map((item) => item.next_collection_at).filter(Boolean).sort()[0]
  }, true, metrics, allCandidates.length);
}

function renderProfile(profile, global=false, metrics={}, totalCandidates=0) {
  document.documentElement.style.setProperty("--accent", profile.color || "#7c3aed");
  $("#profile-title").textContent = profile.name;
  $("#profile-category").textContent = profile.category;
  $(".top-actions").hidden = global;
  $("#workflow-heading").textContent = global ? "전체 제작 흐름" : "오늘의 제작 흐름";
  $("#candidates-heading").textContent = global ? "전체 뉴스 후보" : "뉴스 후보";
  $("#activity-heading").textContent = global ? "전체 실행 기록" : "실행 기록";
  $("#output-heading").textContent = global ? "전체 산출물" : "채널 산출물";
  if (!global) $("#newsroom-filters").hidden = true;
  $("#send-telegram").hidden = global;

  const workflow = profile.workflow || {};
  const candidates = workflow.candidates || [];
  const activeCandidateJobs = new Map(
    (profile.jobs || [])
      .filter((job) => job.action === "build" && job.state.active && job.candidate_id)
      .map((job) => [global ? `${job.profile_id}:${job.candidate_id}` : job.candidate_id, job])
  );
  const activeBuild = (profile.jobs || []).find((job) => job.action === "build" && job.state.active);
  const buildActive = Boolean(activeBuild);
  const activeCandidateCount = global ? Number(metrics.candidates || 0) : candidates.filter((item) =>
    (item.disposition || "active") === "active" && !Number(item.built_count || 0)
  ).length;
  const approved = workflow.approved;
  const status = global
    ? metrics.active_jobs ? "approved" : metrics.artifacts ? "built" : metrics.candidates ? "awaiting_approval" : ""
    : buildActive ? "building" : workflow.status;
  renderWorkflow(status);
  $("#workflow-status").textContent = global
    ? metrics.active_jobs ? `${metrics.active_jobs}건 실행 중` : "운영 정상"
    : statusLabel(status);
  $("#workflow-detail").textContent = global
    ? metrics.failed_jobs ? `확인이 필요한 실패 ${metrics.failed_jobs}건` : "대기 중인 오류가 없습니다."
    : approved ? approved.article.title : "새로운 수집을 시작하세요";
  const liveBuild = $("#live-build-status");
  liveBuild.hidden = !buildActive;
  if (buildActive) {
    $("#live-build-detail").textContent = `${approved?.article?.title || profile.name} · ${activeBuild.state.label}`;
  }
  $("#candidate-count").textContent = Number(activeCandidateCount).toLocaleString("ko-KR");
  const profileArtifacts = profile.artifacts || [];
  const outputCount = global ? Number(metrics.artifacts || 0) : profileArtifacts.length;
  $("#output-count").textContent = outputCount.toLocaleString("ko-KR");
  $("#queue-tab-count").textContent = Number(activeCandidateCount).toLocaleString("ko-KR");
  $("#output-tab-count").textContent = outputCount.toLocaleString("ko-KR");
  $("#schedule-status").textContent = profile.schedule_enabled
    ? global ? `${profile.collection_times.length}개 채널` : `하루 ${profile.collection_times.length}회`
    : "꺼짐";
  $("#schedule-next").textContent = profile.schedule_enabled && profile.next_collection_at
    ? `다음 ${new Date(profile.next_collection_at).toLocaleString("ko-KR", { month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit" })}`
    : "예약 수집 미사용";
  $("#collected-at").textContent = global
    ? `표시 ${candidates.length} · 누적 ${totalCandidates}`
    : workflow.collected_at ? new Date(workflow.collected_at).toLocaleString("ko-KR") : "아직 수집하지 않음";

  const list = $("#candidate-list");
  if (!candidates.length) {
    list.className = "candidate-list empty-state";
    list.innerHTML = `뉴스 수집을 시작하면 최신 후보가 표시됩니다.`;
  } else {
    const candidateEntries = candidates.map((item, index) => ({ item, index }))
      .filter(({item}) => global || (item.disposition || "active") !== "hidden")
      .sort((a, b) => {
        const timestamp = (item) => {
          for (const value of [item.published, item.first_seen_at, item.last_seen_at]) {
            const parsed = Date.parse(value || "");
            if (Number.isFinite(parsed)) return parsed;
          }
          return 0;
        };
        return timestamp(b.item) - timestamp(a.item);
      });
    list.className = candidateEntries.length ? "candidate-list" : "candidate-list empty-state";
    list.innerHTML = candidateEntries.length ? candidateEntries.map(({item, index}) => {
      const isApproved = approved?.index === index + 1;
      const disposition = item.disposition || "active";
      const wasBuilt = item.status === "built" || Number(item.built_count || 0) > 0;
      const pendingJob = activeCandidateJobs.get(global ? `${item.magazine_id}:${item.candidate_id}` : item.id);
      const actionable = !isApproved && !wasBuilt && !pendingJob && disposition === "active"
        && (!global || item.status === "candidate");
      const approvedLabel = buildActive ? "제작 진행 중" : workflow.build ? "제작 완료" : "승인됨";
      return candidateCard(item, {
        index:global ? item.position : index + 1,
        magazineId:global ? item.magazine_id : profile.id,
        magazineName:global ? item.magazine_name : "",
        magazineColor:global ? item.magazine_color || "#7c3aed" : "",
        actionable,
        label:pendingJob ? (pendingJob.status === "running" ? "제작 진행 중" : `제작 대기 ${pendingJob.queue_position || ""}번`)
          : isApproved || item.status === "approved" ? approvedLabel : wasBuilt ? "제작 완료"
          : disposition === "held" ? "보류됨" : disposition === "hidden" ? "숨김" : "승인하고 제작"
      });
    }).join("") : "숨김 처리되지 않은 뉴스 후보가 없습니다. 전체 편집국의 ‘숨김’ 필터에서 복구할 수 있습니다.";
  }


  const approvedCard = $("#approved-card");
  approvedCard.className = `approved-card${approved || global ? "" : " empty"}`;
  approvedCard.innerHTML = global
    ? `<small>GLOBAL PRODUCTION</small>제작 작업은 공용 대기열에서 최대 2건씩 동시에 처리됩니다.`
    : approved ? `<small>APPROVED TOPIC</small>${escapeHtml(approved.article.title)}` : "승인된 주제가 없습니다.";
  renderJobs(profile.jobs || [], global);
  renderOperations(global ? null : profile.id);

  const gallery = $("#output-gallery");
  gallery.className = `output-gallery global-gallery${profileArtifacts.length ? "" : " empty-state"}`;
  gallery.innerHTML = profileArtifacts.length
    ? profileArtifacts.map((item) => outputCard(item, global)).join("")
    : global ? "제작이 완료되면 모든 매거진의 카드뉴스가 표시됩니다." : "제작이 완료되면 카드뉴스가 표시됩니다.";
  switchView(state.activeView);
}

function render() {
  const profile = activeProfile();
  setConnection("#openai-dot", state.data.openai_ready, "OpenAI");
  setConnection("#telegram-dot", state.data.telegram_ready, "Telegram");
  setConnection("#drive-dot", state.data.drive_ready, "Google Drive");
  renderNavigation(profile);
  return profile ? renderProfile(profile) : renderNewsroom();
}

let refreshPending = null;
async function refresh(force=false) {
  if (refreshPending) { await refreshPending; if (force) return refresh(true); return; }
  refreshPending = (async () => {
  try {
    const data = await api("/api/overview", {signal:AbortSignal.timeout(20000)});
    const signature = JSON.stringify(data);
    state.data = data;
    if (!state.activeId) state.activeId = "__newsroom__";
    if (force || signature !== state.signature) {
      state.signature = signature;
      render();
    }
    $("#data-status").hidden = true;
  } catch (error) {
    $("#data-status").hidden = false;
    $("#data-status-message").textContent = state.data ? "서버 응답이 지연되어 마지막으로 받은 데이터를 표시하고 있습니다." : "데이터를 아직 불러오지 못했습니다. 자동으로 다시 연결합니다.";
    $("#data-retry").hidden = false;
    toast(error.message, "error");
  }
  })();
  try { await refreshPending; } finally { refreshPending = null; }
}

async function action(name, button, loadingLabel, request=null, onSuccess=null) {
  const profile = activeProfile();
  if (name && !profile) return;
  button.dataset.wasDisabled = String(button.disabled);
  setButtonLoading(button, true, loadingLabel);
  try {
    const result = await (request ? request() : api(`/api/profiles/${profile.id}/${name}`, { method:"POST" }));
    if (onSuccess) await onSuccess(result);
    else {
      toast(name === "collect" ? "뉴스 수집을 시작했습니다." : "작업을 시작했습니다.", "success");
      await refresh(true);
    }
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.dataset.wasDisabled = "false";
    setButtonLoading(button, false);
  }
}

function lines(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function renderScheduleTimes(count, existing=[]) {
  const start = 8 * 60;
  const end = 22 * 60;
  const values = Array.from({length:count}, (_, index) => {
    const total = count === 1 ? 9 * 60 : Math.round(start + (end - start) * index / (count - 1));
    const fallback = `${String(Math.floor(total / 60)).padStart(2,"0")}:${String(total % 60).padStart(2,"0")}`;
    return existing[index] || fallback;
  });
  $("#schedule-times").innerHTML = values.map((value, index) => `<label>${index + 1}회차<input type="time" name="collection_time" value="${value}" required></label>`).join("");
}

function toggleConditionalSettings() {
  const form = $("#profile-form");
  const scheduleVisible = form.elements.schedule_enabled.checked;
  form.elements.schedule_count.closest("label").hidden = !scheduleVisible;
  $("#schedule-times").hidden = !scheduleVisible;
  form.elements.drive_folder_id.closest("label").hidden = !form.elements.drive_enabled.checked;

}

function openForm(profile=null) {
  state.editingId = profile?.id || null;
  $("#dialog-title").textContent = profile ? "채널 설정" : "새 채널";
  const form = $("#profile-form");
  form.reset();
  const values = profile || {
    brand_name:"NOWA", handle:"@your_handle", tone:"정보 밀도가 높고 간결한 한국어", color:"#7c3aed",
    slide_count:0, collection_size:10, schedule_enabled:false, collection_times:["09:00","14:00","20:00"],
    telegram_enabled:true, drive_enabled:false, image_transform_enabled:true
  };
  ["name","category","brand_name","handle","tone","drive_folder_id","color","image_prompt","slide_count","collection_size"].forEach((key) => {
    if (form.elements[key]) form.elements[key].value = values[key] ?? "";
  });
  form.elements.schedule_count.innerHTML = Array.from({ length:10 }, (_, index) => `<option value="${index + 1}">${index + 1}회</option>`).join("");
  form.elements.telegram_chat_id.value = values.telegram_chat_id || '';
  form.elements.telegram_thread_id.value = values.telegram_thread_id || 0;
  form.elements.telegram_bot_token_env.value = values.telegram_bot_token_env || 'TELEGRAM_BOT_TOKEN';
  form.elements.schedule_count.value = values.collection_times.length;
  renderScheduleTimes(values.collection_times.length, values.collection_times);
  PROFILE_CHECKBOXES.forEach((key) => form.elements[key].checked = Boolean(values[key]));
  form.elements.pages.value = (values.pages || []).join("\n");
  form.elements.feeds.value = (values.feeds || []).join("\n");

  $("#shared-image-policy").textContent = `전 채널 공통 이미지 정책 · ${values.image_model || "grok-imagine-image-2.0"}`;
  state.pendingPsdFiles = [];
  $("#profile-template-status").textContent = "";
  toggleConditionalSettings();
  selectTab("[data-settings-tab]", "[data-settings-panel]", "settingsTab", "settingsPanel", "basic");
  $("#profile-dialog").showModal();
  requestAnimationFrame(() => form.elements.name.focus());
}

function bindTabs(selector, key, select) {
  document.querySelectorAll(selector).forEach((tab) => {
    tab.addEventListener("click", () => select(tab.dataset[key]));
    tab.addEventListener("keydown", (event) => {
    if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll(selector)];
    const current = tabs.indexOf(tab);
    const next = event.key === 'ArrowRight' ? (current + 1) % tabs.length : (current - 1 + tabs.length) % tabs.length;
      select(tabs[next].dataset[key], true);
    });
  });
}

bindTabs("[data-view]", "view", switchView);
bindTabs("[data-settings-tab]", "settingsTab", (name, focus) =>
  selectTab("[data-settings-tab]", "[data-settings-panel]", "settingsTab", "settingsPanel", name, focus));
bindTabs("[data-script-review-tab]", "scriptReviewTab", (name, focus) =>
  selectTab("[data-script-review-tab]", "[data-script-review-panel]", "scriptReviewTab", "scriptReviewPanel", name, focus));

$("#profile-form").elements.schedule_count.onchange = (event) => {
  const existing = [...document.querySelectorAll("[name=collection_time]")].map((input) => input.value);
  renderScheduleTimes(Number(event.target.value), existing);
};
["schedule_enabled","drive_enabled","image_transform_enabled"].forEach((name) => {
  $("#profile-form").elements[name].onchange = toggleConditionalSettings;
});
[
  ["#newsroom-search", "oninput", "newsroomQuery"],
  ["#newsroom-magazine-filter", "onchange", "newsroomMagazine"],
  ["#newsroom-status-filter", "onchange", "newsroomStatus"],
].forEach(([selector, event, key]) => {
  $(selector)[event] = ({target}) => { state[key] = target.value; renderNewsroom(); };
});

$("#collect").onclick = () => action("collect", $("#collect"), "수집 시작 중");
$("#send-telegram").onclick = () => action("telegram", $("#send-telegram"), "전송 중");
$("#new-profile").onclick = () => openForm();
$("#edit-profile").onclick = () => openForm(activeProfile());
$("#close-dialog").onclick = $("#cancel-dialog").onclick = () => { if (!state.profileSaving) $("#profile-dialog").close(); };
$("#profile-dialog").addEventListener("cancel", event => { if (state.profileSaving) event.preventDefault(); });
$("#close-revision").onclick = () => $("#revision-dialog").close();
$("#revision-form").elements.reference_image.onchange = (event) => {
  $("#reference-name").textContent = event.target.files[0]?.name || "이미지를 선택하거나 끌어다 놓으세요";
};
const referenceDrop = document.querySelector(".reference-drop");
referenceDrop.ondragover = (event) => {
  event.preventDefault();
  referenceDrop.classList.add("dragging");
};
referenceDrop.ondragleave = () => referenceDrop.classList.remove("dragging");
referenceDrop.ondrop = (event) => {
  event.preventDefault();
  referenceDrop.classList.remove("dragging");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  $("#revision-form").elements.reference_image.files = event.dataTransfer.files;
  $("#reference-name").textContent = file.name;
};
$("#preview-rewrite").onclick = async () => {
  const form = $("#revision-form"), artifact = state.editingArtifact, slide = state.editingSlide;
  if (!artifact || !slide || slide.editable === false || state.revisionLoading || state.revisionSubmitting) return;
  if (form.elements.instruction_target.value !== "text") return toast("글 수정을 선택하세요.");
  const instruction = form.elements.instruction.value.trim();
  if (!instruction) return toast("글 수정 지시를 입력하세요.");
  const session = state.revisionSession;
  const title = form.elements.slide_title.value, body = form.elements.slide_body.value;
  const button = $("#preview-rewrite"); button.disabled = true;
  try {
    const result = await api(`/api/artifacts/${artifact.id}/rewrite`, {method:"POST", body:JSON.stringify({
      slide_number:slide.number, slide_title:title, slide_body:body, instruction
    })});
    if (state.revisionSession !== session || state.editingArtifact !== artifact || state.editingSlide !== slide
        || !$("#revision-dialog").open || form.elements.slide_title.value !== title
        || form.elements.slide_body.value !== body || form.elements.instruction.value.trim() !== instruction) return;
    form.elements.slide_title.value = result.title;
    form.elements.slide_body.value = result.body;
    form.elements.instruction.value = "";
    toast("수정안을 입력칸에 반영했습니다. 확인 후 제작하세요.");
  } catch (error) { toast(error.message, "error"); }
  finally { button.disabled = false; }
};
$("#revision-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state.editingArtifact || state.revisionLoading || state.revisionSubmitting || $("#submit-revision").disabled) return;
  const form = event.currentTarget;
  if (form.elements.instruction_target.value === "text" && form.elements.instruction.value.trim())
    return toast("원고 수정안 확인을 먼저 눌러주세요.", "error");
  const button = $("#submit-revision");
  state.revisionSubmitting = true;
  button.dataset.wasDisabled = "false";
  setButtonLoading(button, true, "수정본 등록 중");
  try {
    await api(`/api/artifacts/${state.editingArtifact.id}/revise`, {
      method:"POST",
      body:new FormData(event.currentTarget)
    });
    $("#revision-dialog").close();
    toast("수정본을 제작 대기열에 등록했습니다.", "success");
    switchView("production");
    await refresh(true);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.revisionSubmitting = false;
    setButtonLoading(button, false);
    button.disabled = state.revisionLoading || state.editingSlide?.editable === false;
  }
};

$("#profile-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.checkValidity()) {
    form.reportValidity();
    form.querySelector(":invalid")?.focus();
    return;
  }
  const submit = event.submitter || form.querySelector(".dialog-actions button[type=submit]");
  if (state.profileSaving) return;
  state.profileSaving = true;
  submit.dataset.wasDisabled = "false";
  setButtonLoading(submit, true, "저장 중");
  const body = Object.fromEntries(new FormData(form));
  ["template_set_name","schedule_count","collection_time","approved_cover","approved_body"].forEach((key) => delete body[key]);
  body.slide_count = Number(body.slide_count || 0);
  body.collection_size = Number(body.collection_size || 10);
  body.telegram_thread_id = Number(body.telegram_thread_id || 0);
  body.collection_times = [...document.querySelectorAll("[name=collection_time]")].map((input) => input.value);
  ["pages","feeds"].forEach((key) => body[key] = lines(body[key]));
  PROFILE_CHECKBOXES.forEach((key) => body[key] = form.elements[key].checked);
  const controls = [...form.querySelectorAll("input:not(:disabled), textarea:not(:disabled), select:not(:disabled), button:not(:disabled)")];
  controls.forEach(control => control.disabled = true);
  try {
    const path = state.editingId ? `/api/profiles/${state.editingId}` : "/api/profiles";
    const saved = await api(path, { method:state.editingId ? "PUT" : "POST", body:JSON.stringify(body) });
    state.activeId = saved.id;
    state.editingId = saved.id;
    $("#dialog-title").textContent = "채널 설정";
    for (const input of form.querySelectorAll("[data-psd-role]")) {
      const file = input.files[0];
      if (!file) continue;
      $("#profile-template-status").textContent = `${file.name} · PSD를 가져오는 중입니다…`;
      const upload = new FormData();
      upload.append("file", file);
      upload.append("role", input.dataset.psdRole);
      const draft = await api(`/api/template-lab/${encodeURIComponent(saved.id)}`, {method:"POST", body:upload});
      state.pendingPsdFiles.push(draft.id);
      input.value = "";
    }
    if (state.pendingPsdFiles.length || submit.id === "profile-template-open") {
      const query = new URLSearchParams({channel:saved.id});
      if (state.pendingPsdFiles.length) query.set("draft", state.pendingPsdFiles[0]);
      location.href = `/template-lab-photocraft.html?${query}`;
      return;
    }
    $("#profile-dialog").close();
    toast("채널 설정을 저장했습니다.", "success");
    await refresh(true);
  } catch (error) {
    $("#profile-template-status").textContent = state.editingId
      ? `채널은 저장되어 있습니다. ${error.message} 선택한 파일을 확인하고 다시 저장하세요. 완료된 PSD는 보관함에 유지됩니다.`
      : error.message;
    toast(error.message, "error");
  } finally {
    state.profileSaving = false;
    controls.forEach(control => control.disabled = false);
    setButtonLoading(submit, false);
  }
};

if (location.hash === "#template-lab") location.replace("/template-lab-photocraft.html");
$("#data-retry").onclick = () => refresh(true);
const initialView = ["queue","production","outputs"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "queue";
switchView(initialView);
refresh(true);
setInterval(() => { if (!$("#profile-dialog").open) refresh(); }, 1500);
