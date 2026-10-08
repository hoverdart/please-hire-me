"use strict";
let materialOffset = 0;
let materialPagingBusy = false;
let renderedMaterialsSignature = null;
let materialSearch = "",
  materialStatus = "all";
let token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
try {
  token ||= sessionStorage.getItem("hireme-token") || "";
  if (token) sessionStorage.setItem("hireme-token", token);
} catch {
  // Restricted browser storage must not prevent a freshly opened dashboard from working.
}
history.replaceState(null, "", location.pathname);
let state = null,
  view = "today";
let refreshing = null;
let workerRefreshTimer = null;
let backupBusy = false;
let savedViewBusy = false,
  savedViewsSignature = null;
let postingImportBusy = false,
  postingImportPreview = null;
let accountTransferBusy = false;
let accountCredentialBusy = false;
let transcriptWithdrawalBusy = false;
const selectedDownloadBusy = new Set();
const selectedDownloadHashes = {};
let postingCheckRequest = 0;
let diagnosticsResult = null,
  diagnosticsBusy = false;
let renderedAccountSignature = null;
let aliasJsonMode = false;
let renderedBlockedSignature = null;
let sourceHealthState = null,
  sourceHealthOffset = 0,
  sourceHealthBusy = false,
  sourceHealthRequest = 0,
  sourceHealthTimer,
  renderedSourceHealthSignature = null;
let runHistoryState = null,
  runHistoryOffset = 0,
  runHistoryBusy = false,
  runHistoryRequest = 0,
  runHistoryTimer,
  renderedRunHistorySignature = null;
let outcomeState = null,
  outcomeOffset = 0,
  outcomeBusy = false,
  outcomeRequest = 0,
  outcomeTimer,
  renderedOutcomeSignature = null;
let questionState = null,
  questionOffset = 0,
  questionBusy = false,
  questionRequest = 0,
  questionTimer,
  renderedQuestionSignature = null;
let accountState = null,
  accountOffset = 0,
  accountBusy = false,
  accountTimer,
  accountRequest = 0;
let scheduleLoaded = false,
  scheduleBusy = false,
  scheduleStatus = null;
const dirtyForms = new WeakSet();
const expandedEvidence = new Set(),
  evidenceCache = new Map(),
  evidenceImages = new Map();
document.addEventListener("input", (event) => {
  if (event.target.form) dirtyForms.add(event.target.form);
  if (["settings-form", "facts-form"].includes(event.target.form?.id))
    updateDraftControls();
});
document.addEventListener("change", (event) => {
  if (event.target.form) dirtyForms.add(event.target.form);
  if (["settings-form", "facts-form"].includes(event.target.form?.id))
    updateDraftControls();
});
function editing(selector) {
  const form = $(selector);
  return (
    form.dataset.saving === "true" ||
    form.contains(document.activeElement) ||
    dirtyForms.has(form)
  );
}
function updateDraftControls() {
  for (const [formId, buttonId] of [
    ["settings-form", "discard-preferences"],
    ["facts-form", "discard-fact-edits"],
  ]) {
    const form = $("#" + formId);
    $("#" + buttonId).disabled =
      !state ||
      state.demo ||
      form.dataset.saving === "true" ||
      !dirtyForms.has(form);
  }
}
window.addEventListener("beforeunload", (event) => {
  const unfinished = [$("#facts-form"), $("#settings-form"), $("#account-credentials-form"), $("#basic-context-form"), $("#context-form")].some(
    (form) => dirtyForms.has(form) || form.dataset.saving === "true",
  );
  if (!unfinished && !opportunityNotes.hasDrafts()) return;
  event.preventDefault();
  event.returnValue = "";
});
function blurForm(form) {
  if (form.contains(document.activeElement)) document.activeElement.blur();
}
function saved(form) {
  if (!form) return;
  dirtyForms.delete(form);
  if (["settings-form", "facts-form"].includes(form.id)) updateDraftControls();
  if (form.id) {
    const indicator = document.querySelector(`[data-draft-for="${form.id}"]`);
    if (indicator) indicator.textContent = "";
  }
}

function updateAccountTransferControls() {
  if (!state) return;
  const hasEmail = state.facts.email?.confirmed && state.facts.email.value,
    hasHistory = state.employer_accounts.length > 0;
  const ready =
    !state.demo &&
    !state.settings.live_enabled &&
    !state.worker_running &&
    hasEmail &&
    hasHistory;
  for (const form of [$("#account-export-form"), $("#account-import-form")]) {
    form.querySelector("button").disabled = accountTransferBusy || accountCredentialBusy || !ready;
    for (const input of form.querySelectorAll("input"))
      input.disabled = accountTransferBusy || accountCredentialBusy || state.demo;
  }
  const message = state.demo
    ? "Encrypted password transfers are available in your own workspace."
    : accountTransferBusy
      ? "Processing the encrypted transfer. No account verification or submission settings will change."
      : state.worker_running || state.settings.live_enabled
        ? "Pause submissions and wait for active work to finish before transferring passwords."
        : !hasEmail
          ? "Confirm your applicant email in Your facts first."
          : !hasHistory
            ? "No employer account history is saved here. Restore matching history before importing passwords."
            : "Ready for an encrypted transfer. Submissions are paused.";
  if ($("#account-transfer-readiness").textContent !== message)
    $("#account-transfer-readiness").textContent = message;
}
function transferFeedback(id, message, error = false) {
  const feedback = $(id);
  feedback.textContent = message;
  feedback.setAttribute("role", error ? "alert" : "status");
}

function updateAccountCredentialControls() {
  if (!state) return;
  const form = $("#account-credentials-form");
  const email = state.facts.email?.confirmed ? state.facts.email.value : "";
  form.elements.email.value = email;
  for (const input of form.querySelectorAll("input")) input.disabled = state.demo || accountCredentialBusy;
  form.querySelector("button").disabled = state.demo || accountCredentialBusy || accountTransferBusy || state.worker_running || state.settings.live_enabled || !email;
  $("#account-credentials-readiness").textContent = state.demo
    ? "Credential entry is available in your own workspace."
    : accountCredentialBusy ? "Saving private employer credentials…"
    : state.worker_running || state.settings.live_enabled ? "Pause submissions and wait for active work to finish before saving credentials."
    : !email ? "Confirm your applicant email in Your facts first."
    : "Ready to save locally. No employer account will be created by this action.";
}


function renderAccounts() {
  const parent = document.querySelector("#employer-accounts");
  if (
    [...parent.querySelectorAll("form")].some(
      (form) => dirtyForms.has(form) || form.contains(document.activeElement),
    )
  )
    return;
  if (!accountState) {
    parent.replaceChildren(el("p", "Loading account history…", "help"));
    updateAccountControls();
    return;
  }
  const accounts = accountState.accounts;
  const signature = JSON.stringify([
    accounts,
    $("#account-filter").value,
    $("#account-search").value,
    state.demo,
  ]);
  if (renderedAccountSignature === signature && parent.childElementCount) {
    updateAccountControls();
    return;
  }
  renderedAccountSignature = signature;
  parent.replaceChildren();
  updateAccountControls();
  if (!accounts.length)
    return empty(
      parent,
      $("#account-search").value.trim()
        ? "No accounts match your search."
        : $("#account-filter").value === "all"
          ? "No employer accounts created by this worker."
          : "No employer accounts need verification. Choose All accounts to review their history.",
    );
  for (const account of accounts) {
    const box = el("article", undefined, "question");
    box.append(
      el("h3", account.company),
      el(
        "p",
        `${account.origin} · ${{ uncertain: "Needs your verification", confirmed: "Account verified", creating: "Creating account", signing_in: "Signing in" }[account.state] || account.state.replaceAll("_", " ")}`,
      ),
    );
    if (account.state === "uncertain") {
      const form = el("form"),
        evidence = el("textarea");
      evidence.required = true;
      evidence.minLength = 10;
      evidence.maxLength = 2000;
      evidence.rows = 2;
      evidence.setAttribute(
        "aria-label",
        `Account confirmation evidence for ${account.company}`,
      );
      evidence.placeholder =
        "How did you verify that this account exists and you can sign in?";
      const confirm = el("button", "Confirm verified account"),
        discard = el("button", "Discard draft", "secondary");
      confirm.disabled = state.demo;
      discard.type = "button";
      discard.dataset.discardDraft = "true";
      discard.hidden = true;
      discard.onclick = () => {
        evidence.value = "";
        saved(form);
        updateAccountControls();
      };
      form.append(evidence, confirm, discard);
      form.onsubmit = async (event) => {
        event.preventDefault();
        try {
          await api("/api/account-confirm", {
            id: account.id,
            note: evidence.value,
          });
          saved(form);
          blurForm(form);
          await refresh();
          await loadAccountLedger();
          note("Account confirmed. The next batch can reuse its credentials.");
        } catch (error) {
          note(error.message, true);
        }
      };
      box.append(form);
    }
    parent.append(box);
  }
}
const $ = (s) => document.querySelector(s);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const note = (text, error = false) => {
  const notice = $("#notice");
  notice.textContent = text;
  notice.classList.toggle("error", error);
  notice.setAttribute("role", error ? "alert" : "status");
};
async function api(path, data, raw = false, extraHeaders = {}) {
  let response;
  try {
    response = await fetch(path, {
      method: data === undefined ? "GET" : "POST",
      headers: {
        ...extraHeaders,
        "X-Hireme-Token": token,
        ...(!raw && data !== undefined
          ? { "Content-Type": "application/json" }
          : {}),
      },
      body: data === undefined ? undefined : raw ? data : JSON.stringify(data),
    });
  } catch {
    throw new Error(
      "Cannot reach your application desk. Check that the dashboard is still running, then try again.",
    );
  }
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error(
      "The dashboard returned an unexpected response. Refresh the page and try again.",
    );
  }
  if (!response.ok) throw new Error(value.error || "Request failed");
  // Successful saves release draft protection only for the form that was saved.
  const forms = {
    "/api/facts": "#facts-form",
    "/api/template": "#template-form",
    "/api/context-text": "#context-form",
    "/api/basic-context": "#basic-context-form",
  };
  if (forms[path]) saved($(forms[path]));
  return value;
}
$("#account-credentials-form").onsubmit = async (event) => {
  event.preventDefault();
  if (accountCredentialBusy || accountTransferBusy) return;
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  accountCredentialBusy = true;
  form.dataset.saving = "true";
  render();
  try {
    await api("/api/account-credentials", data);
    form.reset();
    saved(form);
    transferFeedback("#account-credentials-feedback", "Credentials saved privately for this employer. No account has been created. Existing account automation can use them on supported signup forms.");
  } catch (error) {
    transferFeedback("#account-credentials-feedback", error.message, true);
  } finally {
    delete form.dataset.saving;
    accountCredentialBusy = false;
    render();
  }
};

$("#account-export-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state)
    return transferFeedback(
      "#account-export-feedback",
      "Wait for the local desk to finish loading, then try again.",
      true,
    );
  const form = event.target,
    passphrase = form.elements.passphrase.value,
    confirmation = form.elements.confirmation.value;
  if (passphrase !== confirmation)
    return transferFeedback(
      "#account-export-feedback",
      "The transfer passphrases must match.",
      true,
    );
  if (accountTransferBusy) return;
  accountTransferBusy = true;
  render();
  transferFeedback(
    "#account-export-feedback",
    "Encrypting your existing employer passwords…",
  );
  try {
    const response = await fetch("/api/account-vault-export", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Hireme-Token": token },
      body: JSON.stringify({ passphrase, confirmation }),
    }).catch(() => {
      throw new Error(
        "Cannot reach your application desk. Check that the dashboard is running and try again.",
      );
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(data?.error || "Encrypted export could not finish.");
    }
    if (
      response.headers.get("Content-Type")?.split(";")[0] !==
      "application/octet-stream"
    )
      throw new Error(
        "The server did not return an encrypted transfer. Reconnect and try again.",
      );
    const url = URL.createObjectURL(await response.blob()),
      anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "account-credentials.encrypted";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    transferFeedback(
      "#account-export-feedback",
      "Encrypted transfer downloaded. Keep the passphrase separately. Restore matching history on the destination before importing this file.",
    );
  } catch (error) {
    transferFeedback("#account-export-feedback", error.message, true);
  } finally {
    form.reset();
    saved(form);
    accountTransferBusy = false;
    render();
  }
};
$("#account-import-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state)
    return transferFeedback(
      "#account-import-feedback",
      "Wait for the local desk to finish loading, then try again.",
      true,
    );
  const form = event.target,
    file = form.elements.archive.files[0],
    passphrase = form.elements.passphrase.value;
  if (!file || accountTransferBusy) return;
  if (!file.size || file.size > 2 * 1024 * 1024)
    return transferFeedback(
      "#account-import-feedback",
      "Choose an encrypted credential transfer up to 2 MiB.",
      true,
    );
  accountTransferBusy = true;
  render();
  transferFeedback(
    "#account-import-feedback",
    "Checking the encrypted transfer and matching account history…",
  );
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 32768)
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    const result = await api("/api/account-vault-import", {
      archive: btoa(binary),
      passphrase,
    });
    await refresh();
    transferFeedback(
      "#account-import-feedback",
      `${result.accounts} employer account${result.accounts === 1 ? "" : "s"} recovered. Existing account verification states are unchanged. Submissions remain paused.`,
    );
  } catch (error) {
    transferFeedback("#account-import-feedback", error.message, true);
  } finally {
    form.reset();
    saved(form);
    accountTransferBusy = false;
    render();
  }
};

function show(name) {
  const modelRoute = name === "providers";
  if (modelRoute) name = "connections";
  if (window.connectedWorkspace) window.connectedWorkspace.placeLedger(name);
  view = name;
  if(name === "materials") window.connectedWorkspace?.loadArtifacts();
  if (name === "settings" && state && !scheduleLoaded) loadSchedule();
  if (name === "questions" && state) {
    loadAccountLedger();
    loadQuestionLedger();
    loadOutcomeLedger();
  }
  $("#page-eyebrow").textContent = {
    setup: "YOUR NEXT CHAPTER",
    today: "YOUR SEARCH, IN MOTION",
    questions: "A LITTLE HELP GOES A LONG WAY",
    profile: "THE FACTS THAT MAKE YOU, YOU",
    materials: "YOUR EXPERIENCE, IN YOUR WORDS",
    settings: "A SEARCH THAT FITS YOUR LIFE",
    providers: "YOUR CHOICE OF MODEL",
    connections: "KEEP YOUR SEARCH CONNECTED",
  }[name];
  document.querySelectorAll(".view").forEach((n) => (n.hidden = n.id !== name));
  document
    .querySelectorAll("[data-view]")
    .forEach((n) =>
      n.setAttribute(
        "aria-current",
        n.dataset.view === name ? "page" : "false",
      ),
    );
  $("#heading").textContent = {
    setup: "Make it yours",
    providers: "Your model connection",
    today: "Today’s applications",
    opportunities: "Jobs",
    questions: "A few things need you",
    profile: "Your verified facts",
    materials: "Materials",
    connections: "Connections",
    settings: "Your search preferences",
  }[name];
  $("#subheading").textContent = {
    setup: "Set up once. Save as you go.",
    providers: "Choose who processes your application context.",
    today: "A real record of what the worker submitted, held and discovered.",
    questions: "Resolve the exception. Let the next cycle do the rest.",
    profile: "Saved once, reused across applications. Unknown means unknown.",
    materials:
      "Reviewed sources guide the voice and facts in your applications.",
    opportunities: "One queue across your connected platforms and employer sites.",
    connections: "Job platforms, model providers and email for your private workspace.",
    settings: "Choose your search boundaries. Throughput never overrides them.",
  }[name];
  if(modelRoute) { $("#model-connection-title").scrollIntoView({block:"start"}); $("#model-connection-title").focus({preventScroll:true}); }
}
const toolDestinations = [
  {
    label: "Upload a resume",
    description: "Add or repair the PDF that supports your applicant facts.",
    aliases: "cv curriculum vitae document",
    view: "profile",
    target: "#resume-state",
    common: true,
  },
  {
    label: "Confirm applicant facts",
    description: "Review personal details and answers you have confirmed.",
    aliases:
      "name email phone school education authorization sponsorship skills profile",
    view: "profile",
    target: "#facts-form",
    common: true,
  },
  {
    label: "Review unanswered questions",
    description: "Resolve questions that need your own answer.",
    aliases: "needs you missing answer",
    view: "questions",
    target: "#question-list",
    common: true,
  },
  {
    label: "Review uncertain outcomes",
    description:
      "Check recorded evidence before marking an application submitted or not submitted.",
    aliases: "unknown duplicate verification outcome confirmation",
    view: "questions",
    target: "#uncertain",
    common: true,
  },
  {
    label: "Prepare drafts for review",
    description: "Prepare answers and documents while submissions stay paused.",
    aliases: "cover letter writing draft preview",
    view: "today",
    target: "#prepare-panel",
    common: true,
  },
  {
    label: "Download history backup",
    description: "Keep a private copy of documents and application history.",
    aliases: "export zip restore migrate move computer data",
    view: "settings",
    target: "#download-backup",
    common: true,
  },
  {
    label: "Import a posting spreadsheet",
    description: "Check a CSV and review its postings before saving them.",
    aliases: "bulk jobs opportunities excel sheets import",
    view: "today",
    target: "#posting-import-panel",
    common: true,
  },
  {
    label: "Check local setup",
    description: "Read setup checks and download a redacted support report.",
    aliases: "doctor troubleshooting diagnostics help error support",
    view: "setup",
    target: "#setup-diagnostics",
    common: true,
  },
  {
    label: "Overview",
    description: "Browse your opportunity ledger and current progress.",
    aliases: "home today applications jobs search",
    view: "today",
    target: "#heading",
  },
  {
    label: "Needs you",
    description:
      "Questions, uncertain applications and employer account checks.",
    aliases: "attention review blocked captcha",
    view: "questions",
    target: "#heading",
  },
  {
    label: "Your facts",
    description: "Applicant documents and confirmed personal details.",
    aliases: "profile resume transcript identity",
    view: "profile",
    target: "#heading",
  },
  {
    label: "Writing and context",
    description: "Upload, search and approve sources for application writing.",
    aliases:
      "materials samples cover letter personal style reference documents",
    view: "materials",
    target: "#heading",
  },
  {
    label: "Preferences",
    description: "Choose roles, locations, company boundaries and budgets.",
    aliases: "settings criteria search limits pace timezone",
    view: "settings",
    target: "#heading",
  },
  {
    label: "Model connection",
    description:
      "Choose a provider and configure its CLI login or API connection.",
    aliases: "ai claude codex openai anthropic key subscription billing login",
    view: "providers",
    target: "#provider-form",
  },
  {
    label: "Email automation",
    description: "Configure verification email and batch reports.",
    aliases: "gmail oauth client token mail connect authorization",
    view: "connections",
    target: "#heading",
  },
  {
    label: "Setup checklist",
    description: "Follow the steps for your own application workspace.",
    aliases: "onboarding first start getting started",
    view: "setup",
    target: "#heading",
  },
  {
    label: "Upload an optional transcript",
    description: "Add, download or withdraw the selected transcript PDF.",
    aliases: "grades academic education document",
    view: "profile",
    target: "#transcript-state",
  },
  {
    label: "Review saved answers",
    description:
      "Inspect answers you confirmed for reuse and withdraw outdated ones.",
    aliases: "question responses reuse revoke",
    view: "profile",
    target: "#saved-answers-panel",
  },
  {
    label: "Save opportunity filters",
    description:
      "Name your search, status and sorting for one-click reopening.",
    aliases: "saved views favorites bookmarks prepared filters",
    view: "today",
    target: "#saved-views-panel",
  },
  {
    label: "Add a specific posting",
    description: "Save an official application link and posting details.",
    aliases: "job opportunity manual url company role",
    view: "today",
    target: "#job-form",
  },
  {
    label: "Check a history backup",
    description:
      "Test a private ZIP through a temporary restore before moving it.",
    aliases: "inspect validate restore zip migrate computer",
    view: "settings",
    target: "#backup-check-panel",
  },
  {
    label: "Move employer passwords",
    description:
      "Export or import encrypted passwords separately from history backups.",
    aliases: "account credentials transfer passphrase migration computer",
    view: "questions",
    target: "#account-transfer-panel",
  },
  {
    label: "Review employer accounts",
    description: "Check employer account states and record verified outcomes.",
    aliases: "registration sign in password login uncertain",
    view: "questions",
    target: "#employer-accounts",
  },
  {
    label: "Review batch history",
    description: "Search complete run history and recorded progress.",
    aliases: "runs logs cycle preparation discovery errors",
    view: "today",
    target: "#run-history-panel",
  },
  {
    label: "Review discovery source health",
    description: "See which job sources were available or need another check.",
    aliases: "network failed boards sources unavailable search errors",
    view: "today",
    target: "#source-health-panel",
  },
  {
    label: "Apply the saved schedule",
    description: "Review and apply your saved batch interval on this computer.",
    aliases: "automatic timer scheduler hours cron service daemon",
    view: "settings",
    target: "#apply-schedule",
  },
];
let toolSearchOrigin = null,
  pendingToolDestination = null;
function renderToolResults() {
  const query = $("#tool-search").value.trim().toLowerCase();
  const terms = query.split(/\s+/).filter(Boolean);
  const results = toolDestinations.filter((item) =>
    query
      ? terms.every((term) =>
          `${item.label} ${item.description} ${item.aliases}`
            .toLowerCase()
            .includes(term),
        )
      : item.common,
  );
  $("#tool-search-count").textContent = query
    ? `${results.length} ${results.length === 1 ? "tool" : "tools"} found.`
    : "Common tools. Type to search every section and task.";
  const parent = $("#tool-search-results");
  parent.replaceChildren();
  if (!results.length)
    parent.append(
      el(
        "p",
        "No tools match that search. Try resume, backup, questions or preferences.",
        "help",
      ),
    );
  for (const item of results) {
    const button = el("button", undefined, "secondary tool-result");
    button.type = "button";
    button.append(el("strong", item.label), el("span", item.description));
    button.onclick = () => {
      pendingToolDestination = item;
      closeToolSearch();
    };
    parent.append(button);
  }
}
function openToolSearch() {
  const dialog = $("#tool-dialog");
  if (dialog.open) {
    $("#tool-search").focus();
    return;
  }
  if ($("#job-dialog").open) $("#job-dialog").close();
  toolSearchOrigin = document.activeElement;
  pendingToolDestination = null;
  $("#tool-search").value = "";
  renderToolResults();
  dialog.showModal();
  document.body.classList.add("dialog-open");
  $("#tool-search").focus({ preventScroll: true });
}
$("#find-tool").onclick = openToolSearch;
function closeToolSearch() {
  $("#tool-dialog").close();
  document.body.classList.toggle("dialog-open", $("#job-dialog").open);
}
$("#close-tool-dialog").onclick = closeToolSearch;
$("#tool-search").oninput = renderToolResults;
$("#tool-search").onkeydown = (event) => {
  const buttons = [...$("#tool-search-results").querySelectorAll("button")];
  if (["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) {
    event.preventDefault();
    if (event.key === "Enter") buttons[0]?.click();
    else (event.key === "ArrowDown" ? buttons[0] : buttons.at(-1))?.focus();
  }
};
$("#tool-search-results").onkeydown = (event) => {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const buttons = [...$("#tool-search-results").querySelectorAll("button")];
  const index = buttons.indexOf(document.activeElement);
  if (index < 0 || !buttons.length) return;
  event.preventDefault();
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? buttons.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) %
          buttons.length;
  buttons[next].focus();
};
$("#tool-dialog").addEventListener("close", () => {
  document.body.classList.toggle("dialog-open", $("#job-dialog").open);
  const destination = pendingToolDestination;
  pendingToolDestination = null;
  if (!destination) {
    if (toolSearchOrigin?.isConnected)
      toolSearchOrigin.focus({ preventScroll: true });
    return;
  }
  show(destination.view);
  let target = $(destination.target) || $("#heading");
  for (let parent = target; parent; parent = parent.parentElement)
    if (parent.tagName === "DETAILS") parent.open = true;
  // Navigate to the surrounding section, never activate an action button.
  if (target.matches("button"))
    target = target.closest(".section") || $("#heading");
  target.tabIndex = -1;
  target.scrollIntoView({
    block: "start",
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
  target.focus({ preventScroll: true });
});
$("#tool-dialog").addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeToolSearch();
  }
});
$("#tool-dialog").addEventListener("click", (event) => {
  if (event.target !== event.currentTarget) return;
  const bounds = event.currentTarget.getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    closeToolSearch();
});
document.addEventListener("keydown", (event) => {
  if (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey &&
    event.key.toLowerCase() === "k"
  ) {
    event.preventDefault();
    openToolSearch();
  }
});
function link(url, text) {
  const a = el("a", text);
  if (!state?.demo && /^https:\/\//.test(url)) {
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  }
  return a;
}
function date(v) {
  return v
    ? new Date(v).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: state?.settings.timezone,
      })
    : "—";
}
function empty(parent, text, title = "Nothing here yet") {
  const box = el("div", undefined, "empty");
  box.append(
    el("strong", title),
    el("p", text),
  );
  parent.append(box);
}
const statusLabels = {
  manually_applied: "Applied manually",
  skipped: "Don’t apply",
  confirmed: "Submitted",
  blocked: "Needs action",
  unknown: "Uncertain",
  awaiting_verification: "Check email",
  discovered: "Ready to evaluate",
  rejected: "Not a match",
  skipped: "Skipped",
  attempting: "Applying",
  manual: "Manual handling",
};
function jobPayload(job) {
  try {
    const payload = JSON.parse(job.payload);
    return payload && typeof payload === "object" && !Array.isArray(payload)
      ? payload
      : {};
  } catch {
    return {};
  }
}
let ledgerSignature = "";
let ledgerState = null,
  ledgerOffset = 0,
  ledgerRequest = 0,
  ledgerTimer = null;
async function loadLedger(reset = false) {
  if (!state) return;
  if (reset) ledgerOffset = 0;
  const request = ++ledgerRequest;
  const params = new URLSearchParams({
    search: $("#job-search").value,
    status: $("#status-filter").value,
    sort: $("#job-sort").value,
    offset: String(ledgerOffset),
    source: $("#connection-source")?.value || "all",
    destination: $("#connection-destination")?.value || "all",
    min_fit: $("#connection-fit")?.value || "0",
  });
  $("#jobs").setAttribute("aria-busy", "true");
  $("#ledger-previous").disabled = true;
  $("#ledger-next").disabled = true;
  try {
    const result = await api("/api/jobs?" + params);
    if (request !== ledgerRequest) return;
    if (result.total > 0 && result.offset >= result.total) {
      ledgerOffset =
        Math.floor((result.total - 1) / result.limit) * result.limit;
      return loadLedger(false);
    }
    ledgerState = result;
    renderLedger();
    return true;
  } catch (error) {
    if (request === ledgerRequest) {
      note(error.message, true);
      if (ledgerState) {
        $("#ledger-previous").disabled = ledgerState.offset === 0;
        $("#ledger-next").disabled =
          ledgerState.offset + ledgerState.limit >= ledgerState.total;
      }
      $("#ledger-page-label").textContent =
        "Could not refresh. Showing the last loaded results.";
      return false;
    }
  } finally {
    if (request === ledgerRequest)
      $("#jobs").setAttribute("aria-busy", "false");
  }
}
function savedViewFeedback(text, error = false) {
  const status = $("#saved-view-status");
  status.textContent = text;
  status.setAttribute("role", error ? "alert" : "status");
}
function currentViewFilters() {
  return {
    search: $("#job-search").value,
    status: $("#status-filter").value,
    sort: $("#job-sort").value,
    source: $("#connection-source").value,
    destination: $("#connection-destination").value,
    min_fit: Number($("#connection-fit").value),
  };
}
function renderSavedViews() {
  if (!state) return;
  const views = state.saved_views || [];
  const signature = JSON.stringify(views);
  if (signature !== savedViewsSignature) {
    const parent = $("#saved-views");
    parent.replaceChildren();
    if (!views.length)
      parent.append(
        el(
          "p",
          "No saved views yet. Set your filters, then give this view a name.",
          "help",
        ),
      );
    for (const item of views) {
      const card = el("article", undefined, "saved-view");
      card.append(el("h3", item.name));
      const label = (selector, value) =>
        [...$(selector).options].find((option) => option.value === value)
          ?.textContent || value;
      card.append(
        el(
          "p",
          [
            item.search ? `Search: ${item.search}` : "All companies and roles",
            label("#status-filter", item.status),
            label("#job-sort", item.sort),
          ].join(" · "),
          "help",
        ),
      );
      const actions = el("div", undefined, "actions");
      for (const [action, text] of [
        ["open", "Open view"],
        ["replace", "Replace with current filters"],
        ["delete", "Remove view"],
      ]) {
        const button = el("button", text, "secondary");
        button.type = "button";
        button.dataset.viewAction = action;
        button.dataset.viewId = item.id;
        button.setAttribute("aria-label", `${text}: ${item.name}`);
        button.onclick = () =>
          action === "open"
            ? openSavedView(item, button)
            : changeSavedView(
                {
                  action,
                  id: item.id,
                  ...(action === "replace" ? currentViewFilters() : {}),
                },
                item.name,
                button,
              );
        actions.append(button);
      }
      card.append(actions);
      parent.append(card);
    }
    savedViewsSignature = signature;
  }
  for (const control of $("#saved-view-form").elements)
    control.disabled = state.demo || savedViewBusy;
  for (const button of $("#saved-views").querySelectorAll("button"))
    button.disabled =
      savedViewBusy || (state.demo && button.dataset.viewAction !== "open");
  for (const control of [$("#job-search"), $("#status-filter"), $("#job-sort")])
    control.disabled = savedViewBusy;
}
async function changeSavedView(data, name, button) {
  if (!state || state.demo || savedViewBusy) return;
  const focusedControl = document.activeElement;
  const focused =
    focusedControl === button || $("#saved-view-form").contains(focusedControl);
  savedViewBusy = true;
  renderSavedViews();
  try {
    if (refreshing) await refreshing;
    const result = await api("/api/saved-view", data);
    state.saved_views = result.views;
    if (data.action === "save") {
      $("#saved-view-form").reset();
      saved($("#saved-view-form"));
    }
    savedViewFeedback(
      data.action === "delete"
        ? `Removed view: ${name}. Opportunity records stay unchanged.`
        : `Saved view: ${name}.`,
    );
  } catch (error) {
    savedViewFeedback(error.message, true);
  } finally {
    savedViewBusy = false;
    releaseDeferredRefresh();
    renderSavedViews();
    if (focused && document.activeElement === document.body) {
      const replacement = [
        ...$("#saved-views").querySelectorAll("button"),
      ].find(
        (candidate) =>
          candidate.dataset.viewId === data.id &&
          candidate.dataset.viewAction === data.action,
      );
      (replacement || $("#saved-view-form input")).focus({
        preventScroll: true,
      });
    }
  }
}
async function openSavedView(item, button) {
  if (!state || savedViewBusy) return;
  savedViewBusy = true;
  const focused = document.activeElement === button;
  let opened = false;
  clearTimeout(ledgerTimer);
  renderSavedViews();
  let previous;
  try {
    if (refreshing) await refreshing;
    previous = { ...currentViewFilters(), offset: ledgerOffset };
    $("#job-search").value = item.search;
    $("#status-filter").value = item.status;
    $("#job-sort").value = item.sort;
    $("#connection-source").value = item.source || "all";
    $("#connection-destination").value = item.destination || "all";
    $("#connection-fit").value = String(item.min_fit || 0);
    const loaded = await loadLedger(true);
    if (loaded === false) {
      $("#job-search").value = previous.search;
      $("#status-filter").value = previous.status;
      $("#job-sort").value = previous.sort;
      $("#connection-source").value = previous.source;
      $("#connection-destination").value = previous.destination;
      $("#connection-fit").value = String(previous.min_fit);
      ledgerOffset = previous.offset;
      renderLedger();
      savedViewFeedback(
        "Could not open the saved view. Your previous filters and results are available. Try Open view again.",
        true,
      );
    } else if (loaded === true) {
      opened = true;
      savedViewFeedback(`Opened view: ${item.name}.`);
    }
  } finally {
    savedViewBusy = false;
    releaseDeferredRefresh();
    renderSavedViews();
    if (
      focused &&
      (document.activeElement === document.body ||
        document.activeElement === button)
    ) {
      if (opened) scrollLedgerIntoView();
      else button.focus({ preventScroll: true });
    }
  }
}
$("#saved-view-form").onsubmit = (event) => {
  event.preventDefault();
  const name = $("#saved-view-form input").value;
  return changeSavedView(
    { action: "save", name, ...currentViewFilters() },
    name,
    $("#saved-view-form button"),
  );
};
function renderLedger() {
  if (!state) return;
  $("#ledger-scope").textContent = "";
  if (!ledgerState) {
    const parent = $("#jobs");
    parent.replaceChildren();
    empty(
      parent,
      "Your saved opportunities are on their way.",
      "Loading your ledger",
    );
    return;
  }
  $("#ledger-count").textContent = String(ledgerState.total);
  const pageLabel = ledgerState.total
    ? `${ledgerState.offset + 1}–${Math.min(ledgerState.offset + ledgerState.limit, ledgerState.total)} of ${ledgerState.total} opportunities`
    : "No opportunities to show";
  if ($("#ledger-page-label").textContent !== pageLabel)
    $("#ledger-page-label").textContent = pageLabel;
  $("#ledger-previous").disabled = ledgerState.offset === 0;
  $("#ledger-next").disabled =
    ledgerState.offset + ledgerState.limit >= ledgerState.total;
  const query = $("#job-search").value.trim(),
    filter = $("#status-filter").value;
  const signature = JSON.stringify([
    ledgerState.jobs,
    ledgerState.applications,
    query,
    filter,
  ]);
  if (signature !== ledgerSignature) {
    table(ledgerState.jobs, $("#jobs"), !!query || filter !== "all");
    ledgerSignature = signature;
  }
}
function evidenceKey(app) {
  return `${app.id}:${app.updated}:${app.hash}:${app.state}:${app.screenshot || ""}`;
}
async function loadEvidence(app, parent) {
  if (parent.dataset.loading === "true" || parent.dataset.loaded === "true")
    return;
  parent.dataset.loading = "true";
  parent.dataset.applicationId = app.id;
  parent.setAttribute("aria-busy", "true");
  parent.replaceChildren(el("p", "Loading recorded answers…", "help"));
  const key = evidenceKey(app);
  try {
    if (!evidenceCache.has(key)) {
      const request =
        typeof app.package === "string"
          ? Promise.resolve(app)
          : api("/api/application/" + encodeURIComponent(app.id)).then(
              (result) => result.application,
            );
      evidenceCache.set(key, request);
      request.catch(() => {
        if (evidenceCache.get(key) === request) evidenceCache.delete(key);
      });
      if (evidenceCache.size > 100)
        evidenceCache.delete(evidenceCache.keys().next().value);
    }
    const record = await evidenceCache.get(key);
    if (!parent.isConnected || parent.dataset.applicationId !== app.id) return;
    renderEvidence(record, parent);
    parent.dataset.loaded = "true";
  } catch (error) {
    evidenceCache.delete(key);
    if (!parent.isConnected || parent.dataset.applicationId !== app.id) return;
    const message = el(
        "p",
        `Recorded evidence could not be loaded: ${error.message}`,
        "help",
      ),
      retry = el("button", "Retry evidence", "secondary");
    message.setAttribute("role", "alert");
    retry.type = "button";
    retry.onclick = () => loadEvidence(app, parent);
    parent.replaceChildren(message, retry);
  } finally {
    if(parent.dataset.applicationId === app.id) { delete parent.dataset.loading; parent.removeAttribute("aria-busy"); }
  }
}

async function downloadPdf(button, feedback, path, filename) {
  if (button.disabled) return;
  const returnFocus = document.activeElement === button;
  button.disabled = true;
  feedback.textContent = "Preparing your PDF download…";
  feedback.setAttribute("role", "status");
  try {
    const response = await fetch(path, {
      headers: { "X-Hireme-Token": token },
    }).catch(() => {
      throw new Error(
        "Cannot reach your application desk. Check that the dashboard is still running, then try again.",
      );
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(error?.error || "The PDF could not be downloaded.");
    }
    if (
      response.headers.get("Content-Type")?.split(";")[0] !== "application/pdf"
    )
      throw new Error(
        "The server did not return a PDF. Reconnect and try again.",
      );
    const url = URL.createObjectURL(await response.blob()),
      anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    feedback.textContent =
      "PDF downloaded. Keep this private document in a safe location.";
  } catch (error) {
    feedback.textContent = `${error.message} You can try the download again.`;
    feedback.setAttribute("role", "alert");
  } finally {
    button.disabled = false;
    if (
      returnFocus &&
      button.isConnected &&
      document.activeElement === document.body
    )
      button.focus({ preventScroll: true });
  }
}

function renderEvidence(app, parent) {
  let parsed;
  try {
    parsed = JSON.parse(app.package);
  } catch {
    throw new Error("The stored answer package could not be read.");
  }
  if (!Array.isArray(parsed.answers))
    throw new Error("The stored answer package is incomplete.");
  const list = el("ul", undefined, "answer-log");
  for (const answer of parsed.answers) {
    const li = el("li"),
      provenance = answer.provenance || {};
    li.append(
      el("strong", answer.field?.label || "Recorded field"),
      el("p", answer.value),
      el(
        "small",
        provenance.fact_key
          ? `Verified fact: ${state.fact_labels[provenance.fact_key] || provenance.fact_key}`
          : provenance.sample_parts
            ? "Your approved writing samples"
            : provenance.template_id
              ? "Your approved writing sample"
              : provenance.resume_quote
                ? "Verified resume evidence"
                : provenance.job_source
                  ? "Recorded discovery source"
                  : provenance.contextual_preference
                    ? "Selected from your confirmed skills and availability"
                    : provenance.job_title
                      ? "Role from this posting"
                      : "Your saved answer",
      ),
    );
    list.append(li);
  }
  if (!parsed.answers.length)
    list.append(el("li", "No recorded answers for this attempt."));
  parent.replaceChildren(list);
  if (Array.isArray(parsed.documents) && parsed.documents.length) {
    const documents = el("div", undefined, "recorded-documents");
    documents.append(
      el("h3", "Recorded documents"),
      el(
        "p",
        "Download the exact PDF recorded with this draft or attempt. Current uploads may have changed since then.",
        "help",
      ),
    );
    parsed.documents.forEach((attachment, index) => {
      const names = {
        resume: "resume",
        transcript: "transcript",
        cover_letter: "cover letter",
        supplemental_response: "response PDF",
      };
      if (
        !attachment ||
        typeof attachment.kind !== "string" ||
        typeof attachment.hash !== "string" ||
        !Object.hasOwn(names, attachment.kind) ||
        !/^[a-f0-9]{64}$/.test(attachment.hash || "") ||
        attachment.filename !== attachment.hash + ".pdf"
      ) {
        documents.append(
          el("p", "A recorded document's details could not be read.", "help"),
        );
        return;
      }
      const row = el("div", undefined, "recorded-document"),
        button = el(
          "button",
          "Download recorded " + names[attachment.kind],
          "secondary",
        ),
        feedback = el("p", "", "help");
      button.type = "button";
      feedback.setAttribute("role", "status");
      if (attachment.generated === true)
        row.append(el("p", "Generated for this opportunity", "help"));
      button.onclick = () =>
        downloadPdf(
          button,
          feedback,
          `/api/application-document/${encodeURIComponent(app.id)}/${index}/${attachment.hash}`,
          attachment.kind === "cover_letter"
            ? "cover-letter.pdf"
            : attachment.kind + ".pdf",
        );
      row.append(button, feedback);
      documents.append(row);
    });
    parent.append(documents);
  }
  if (app.connection_receipt) {
    const receipt = el("details");
    receipt.append(el("summary", "Platform acknowledgement"),el("pre", app.connection_receipt.receipt));
    parent.append(receipt);
  }
  if (app.screenshot) {
    const button = el("button", "View confirmation", "secondary"),
      feedback = el("p", "", "help"),
      imageBox = el("div"),
      imageKey = evidenceKey(app);
    button.type = "button";
    let record = evidenceImages.get(imageKey);
    if (!record) {
      record = { status: "idle", blob: null, error: "", render: null };
      evidenceImages.set(imageKey, record);
    }
    const trimImages = () => {
      for (const [key, value] of evidenceImages) {
        if (evidenceImages.size <= 10) break;
        if (key !== imageKey && value.status !== "loading")
          evidenceImages.delete(key);
      }
    };
    const imageError =
      "The recorded image could not be displayed. Try again or verify the outcome at the employer.";
    const updateImage = () => {
      if (!parent.isConnected) return;
      button.disabled = record.status === "loading";
      feedback.textContent =
        record.status === "loading" ? "Loading recorded image…" : record.error;
      feedback.setAttribute("role", record.error ? "alert" : "status");
      imageBox.setAttribute("aria-busy", String(record.status === "loading"));
      if (record.status !== "ready") {
        imageBox.replaceChildren(button);
        return;
      }
      const img = el("img"), url = URL.createObjectURL(record.blob), blob = record.blob;
      img.alt = "Recorded page after submission";
      img.className = "evidence";
      img.onload = () => URL.revokeObjectURL(url);
      img.onerror = () => {
        URL.revokeObjectURL(url);
        if (record.status !== "ready" || record.blob !== blob) return;
        record.status = "error";
        record.blob = null;
        record.error = imageError;
        record.render?.();
      };
      imageBox.replaceChildren(img);
      img.src = url;
      // Decoding also settles when a refresh detaches this node, ensuring its
      // temporary URL is released even if load/error events are cancelled.
      img.decode().finally(() => URL.revokeObjectURL(url)).catch(() => {});
    };
    // A filtered/refreshing ledger replaces nodes. Pending requests update the
    // latest connected view, while status and failures survive that replacement.
    record.render = updateImage;
    button.onclick = async () => {
      if (record.status === "loading") return;
      evidenceImages.set(imageKey, record);
      record.status = "loading";
      record.error = "";
      record.render?.();
      try {
        const response = await fetch(
          "/api/screenshot/" + encodeURIComponent(app.screenshot),
          { headers: { "X-Hireme-Token": token } },
        );
        if (!response.ok)
          throw new Error(
            "Screenshot unavailable. Try again or verify the outcome at the employer.",
          );
        const blob = await response.blob(), url = URL.createObjectURL(blob);
        try {
          // Never cache an invalid image while a detached DOM node decodes it.
          await new Promise((resolve, reject) => {
            const probe = new Image();
            probe.onload = resolve;
            probe.onerror = () => reject(new Error(imageError));
            probe.src = url;
          });
        } finally {
          URL.revokeObjectURL(url);
        }
        record.blob = blob;
        record.status = "ready";
      } catch (error) {
        record.blob = null;
        record.status = "error";
        record.error = error.message;
      } finally {
        record.render?.();
        trimImages();
      }
    };
    parent.append(imageBox, feedback);
    updateImage();
    trimImages();
  }
}

function jobActions(job, cell, app) {
  if (
    app &&
    ["confirmed", "submitting", "unknown", "awaiting_verification"].includes(
      app.state,
    )
  )
    return;
  const actions = ["manually_applied", "skipped"].includes(job.status)
    ? [["undo", "Undo"]]
    : [
        ["manually_applied", "Applied manually"],
        ["skipped", "Don’t apply"],
      ];
  const buttons = el("div", undefined, "job-actions");
  for (const [decision, label] of actions) {
    const button = el("button", label, "secondary");
    button.type = "button";
    button.disabled = state.demo;
    button.setAttribute(
      "aria-label",
      `${label}: ${job.company} — ${job.title}`,
    );
    button.onclick = async () => {
      const originView = view;
      buttons
        .querySelectorAll("button")
        .forEach((control) => (control.disabled = true));
      try {
        await api("/api/job-decision", { id: job.id, decision });
        await refresh();
        note(
          decision === "undo"
            ? "Job returned to the queue."
            : decision === "skipped"
              ? "Job skipped. It will not be attempted."
              : "Manual application recorded. It will not be attempted.",
        );
        if (view === originView && document.activeElement === document.body) {
          const target = $("#jobs");
          target.focus({ preventScroll: true });
        }
      } catch (error) {
        buttons
          .querySelectorAll("button")
          .forEach((control) => (control.disabled = state.demo));
        note(error.message, true);
        if (view === originView && document.activeElement === document.body)
          button.focus({ preventScroll: true });
      }
    };
    buttons.append(button);
  }
  cell.append(buttons);
}

function table(jobs, parent, filtered = false) {
  // A refresh can arrive before the browser dispatches a details toggle event.
  // Capture the live DOM first so an opened answer package survives that race.
  for (const detail of parent.querySelectorAll('details[data-evidence-id]')) {
    const key = `${parent.id}:${detail.dataset.evidenceId}`;
    if (detail.open) expandedEvidence.add(key);
    else expandedEvidence.delete(key);
  }
  const focusedEvidence =
    parent.contains(document.activeElement) &&
    document.activeElement.tagName === "SUMMARY"
      ? document.activeElement.parentElement.dataset.evidenceId
      : null;
  parent.replaceChildren();
  if (!jobs.length) {
    const mainLedger = parent.id === "jobs";
    empty(
      parent,
      mainLedger
        ? filtered
          ? "Try another search or show all statuses."
          : "Your opportunities will appear here after your first discovery batch. You can also add a posting you found yourself."
        : "You’re all clear here. Your desk will let you know when an opportunity needs a closer look.",
      mainLedger
        ? filtered
          ? "No matching opportunities"
          : "Your next chapter starts here"
        : "All clear",
      mainLedger ? "↗" : "✓",
    );
    return;
  }
  const t = el("table"),
    head = el("thead"),
    hr = el("tr");
  t.setAttribute("aria-label", "Application opportunities");
  ["Opportunity", "Status", "Fit", "Record"].forEach((x) => {
    const th = el("th", x);
    th.scope = "col";
    hr.append(th);
  });
  head.append(hr);
  t.append(head);
  const body = el("tbody");
  for (const job of jobs) {
    const row = el("tr"),
      a = el("td"),
      companyLine = el("div", undefined, "company-line"),
      info = el("div", undefined, "company-info");
    const avatar = el(
      "span",
      job.company.trim().slice(0, 1).toUpperCase() || "?",
      "company-avatar",
    );
    avatar.setAttribute("aria-hidden", "true");
    const title = link(job.url, job.title);
    title.className = "job-title";
    info.append(
      title,
      el(
        "p",
        `${job.company} · ${jobPayload(job).location || "Location not stated"}`,
      ),
    );
    if (job.reason)
      info.append(el("p", job.reason_label || job.reason, "job-reason"));
    if (parent.id === "blocked-jobs" && job.next_step)
      info.append(el("p", job.next_step, "help"));
    if (job.hold) {
      info.append(el("p", job.hold.next_action, "help"));
      const evidence = el("details");
      evidence.append(el("summary", "Why this is held"), el("p", `${job.hold.category} · ${job.hold.stage}`, "help"));
      if (job.hold.field) evidence.append(el("p", job.hold.field));
      for (const field of job.hold.fields || []) evidence.append(el("p", `${field.label} · ${field.reason.replaceAll("_", " ")}${field.options.length ? ` · Choices: ${field.options.join("; ")}` : ""}`, "help"));
      if (job.hold.sources?.length) evidence.append(el("p", `Confirmed information available: ${job.hold.sources.map(source => `${state.fact_labels[source.fact_key] || source.fact_key} (revision ${source.revision})`).join(", ")}`, "help"));
      if (job.hold.retry_at) evidence.append(el("p", `Next retry: ${date(new Date(job.hold.retry_at * 1000).toISOString())}`, "help"));
      const recheck = el("button", "Recheck", "secondary");
      recheck.type = "button"; recheck.disabled = state.demo;
      recheck.onclick = async () => {
        recheck.disabled = true;
        try { const result = await api("/api/recheck", {job_id:job.id});
          note(result.ready ? "Ready for the next cycle. Submission checks still apply." : "Still held. Resolve the recorded requirement first."); await refresh();
        } catch(error) { note(error.message, true); } finally {recheck.disabled = state.demo;}
      };
      evidence.append(recheck);info.append(evidence);
    }
    companyLine.append(avatar, info);
    a.append(companyLine);
    const b = el("td");
    b.dataset.label = "Status";
    b.append(
      el(
        "span",
        job.status_label ||
          statusLabels[job.status] ||
          job.status.replaceAll("_", " "),
        "state " + (job.display_status || job.status),
      ),
    );
    const c = el("td");
    c.dataset.label = "Fit";
    c.append(
      el(
        "span",
        job.score > 0 ? `${job.score}/100` : "Not evaluated",
        job.score > 0 ? "fit-score" : "subtle",
      ),
    );
    const d = el("td");
    const viewDetails = el("button", "View details", "opportunity-details");
    viewDetails.type = "button";
    viewDetails.setAttribute(
      "aria-label",
      `View details for ${job.company} · ${job.title}`,
    );
    viewDetails.onclick = () => openOpportunity(job);
    d.append(viewDetails);
    const app = (
      parent.id === "jobs"
        ? ledgerState?.applications || state.applications
        : state.applications
    ).find((x) => x.job_id === job.id);
    if (app) {
      const detail = el("details");
      detail.dataset.evidenceId = app.id;
      detail.append(el("summary", "Answers & evidence"));
      const evidence = el("div");
      evidence.setAttribute("aria-live", "polite");
      detail.append(evidence);
      const expansionKey = `${parent.id}:${app.id}`;
      detail.ontoggle = () => {
        if (!detail.isConnected) return;
        if (detail.open) {
          expandedEvidence.add(expansionKey);
          loadEvidence(app, evidence);
        } else expandedEvidence.delete(expansionKey);
      };
      detail.open = expandedEvidence.has(expansionKey);
      d.append(detail);
    } else
      d.append(
        el(
          "span",
          job.status === "manually_applied"
            ? "Recorded by you"
            : "No attempt yet",
          "subtle",
        ),
      );
    jobActions(job, d, app);
    if (job.reason) {
      const diagnostic = el("details", undefined, "diagnostic");
      diagnostic.dataset.evidenceId = "diagnostic:" + job.id;
      const expansionKey = `${parent.id}:diagnostic:${job.id}`;
      diagnostic.ontoggle = () => {
        if (diagnostic.open) expandedEvidence.add(expansionKey);
        else expandedEvidence.delete(expansionKey);
      };
      diagnostic.open = expandedEvidence.has(expansionKey);
      diagnostic.append(
        el("summary", "Recorded details"),
        el("p", job.reason, "subtle"),
      );
      d.append(diagnostic);
    }
    row.append(a, b, c, d);
    body.append(row);
  }
  t.append(body);
  parent.append(t);
  if (focusedEvidence)
    [...parent.querySelectorAll("details[data-evidence-id]")]
      .find((detail) => detail.dataset.evidenceId === focusedEvidence)
      ?.querySelector("summary")
      .focus({ preventScroll: true });
}
function attentionCount() {
  const jobIds = new Set(state.questions.map((q) => q.job_id));
  state.jobs
    .filter(
      (j) =>
        j.requires_attention ??
        ["blocked", "unknown", "awaiting_verification"].includes(j.status),
    )
    .forEach((j) => jobIds.add(j.id));
  state.applications
    .filter((a) => ["unknown", "awaiting_verification"].includes(a.state))
    .forEach((a) => jobIds.add(a.job_id));
  return (
    jobIds.size +
    (state.employer_accounts || []).filter((a) => a.state === "uncertain")
      .length
  );
}
function renderOverview(submitted) {
  const count = state.summary?.attention_count ?? attentionCount(),
    ready =
      state.summary?.status_counts.discovered ??
      state.jobs.filter((j) => j.status === "discovered").length;
  $("#metric-submitted").textContent = String(submitted);
  $("#metric-submitted-help").textContent =
    `of ${state.settings.target_per_day} daily target`;
  $("#submission-progress").max = state.settings.target_per_day;
  $("#submission-progress").value = submitted;
  $("#metric-attention").textContent = String(count);
  $("#metric-attention-help").textContent =
    count === 0
      ? "You’re all caught up"
      : `${count === 1 ? "One item needs" : `${count} items need`} a little help`;
  $("#metric-opportunities").textContent = String(
    state.summary?.job_count ?? state.jobs.length,
  );
  $("#metric-ready").textContent = ready
    ? `${ready} ready to evaluate`
    : "Discovery is ready when you are";
  $("#worker-dot").classList.toggle("enabled", state.settings.live_enabled);
  $("#workspace-date").textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: state.settings.timezone,
  })
    .format(new Date())
    .toUpperCase();
  $("#question-count").textContent = count ? String(count) : "";
}
function questionDrafts() {
  return [...document.querySelectorAll("#question-list form")].filter((form) =>
    dirtyForms.has(form),
  );
}
function updateQuestionControls() {
  const draft = questionDrafts().length > 0;
  $("#question-search").disabled = draft;
  $("#question-retry").disabled = draft || questionBusy;
  $("#question-previous").disabled =
    draft || questionBusy || !questionState || questionState.offset === 0;
  $("#question-next").disabled =
    draft ||
    questionBusy ||
    !questionState ||
    questionState.offset + questionState.limit >= questionState.total;
  $("#question-draft-help").hidden = !draft;
  for (const form of document.querySelectorAll("#question-list form"))
    form.querySelector("[data-question-discard]").hidden =
      !dirtyForms.has(form);
  $("#question-page").textContent = questionState
    ? questionState.total
      ? questionState.questions.length
        ? `${questionState.offset + 1}–${Math.min(questionState.offset + questionState.limit, questionState.total)} of ${questionState.total} unanswered questions`
        : "Refreshing question page…"
      : "0 unanswered questions"
    : "Loading questions…";
}
async function loadQuestionLedger(reset = false, focus = false) {
  if (!state || questionBusy || questionDrafts().length) return;
  if (reset) questionOffset = 0;
  questionBusy = true;
  updateQuestionControls();
  const request = ++questionRequest;
  const parent = $("#question-list");
  parent.setAttribute("aria-busy", "true");
  try {
    const result = await api(
      `/api/questions?${new URLSearchParams({ search: $("#question-search").value, offset: questionOffset })}`,
    );
    if (request !== questionRequest) return;
    questionState = result;
    questionOffset = result.offset;
    $("#question-error").hidden = true;
    $("#question-retry").hidden = true;
    renderQuestionList();
    if (focus) {
      parent.focus();
      parent.scrollIntoView({ block: "start", behavior: "auto" });
    }
  } catch (error) {
    $("#question-error").textContent =
      `Could not load questions: ${error.message}`;
    $("#question-error").hidden = false;
    $("#question-retry").hidden = false;
  } finally {
    questionBusy = false;
    parent.removeAttribute("aria-busy");
    updateQuestionControls();
    if (request !== questionRequest && !questionDrafts().length)
      loadQuestionLedger(true);
  }
}
// What each question reason means for the applicant, in their terms.
const questionReasons = {
  missing_fact: "None of your facts or context answers this yet.",
  option_mismatch: "Your facts don’t clearly match one of these choices. Pick one here, or make the related fact more specific.",
  stale_answer: "Your earlier answer no longer matches your current facts. Choose again.",
  mapping_review: "This looked related to one of your facts, but not closely enough to use it automatically.",
  writing_unsupported: "Claude’s draft didn’t pass its source check. It retries automatically; you can also write this yourself.",
  provider_timeout: "The model didn’t answer in time. This retries automatically.",
  provider_error: "The model request failed. This retries automatically.",
  provider_invalid_output: "The model returned an unusable answer. This retries automatically.",
  numeric_answer_needed: "This field needs a number.",
  answer_too_long: "The saved answer is longer than this field allows.",
  repeated_entry_review: "This form repeats a section; confirm the answer for this entry.",
};
const contextReasons = new Set(["missing_fact", "option_mismatch", "stale_answer", "mapping_review"]);
function shortLabel(label, limit = 140) {
  const text = label.replace(/\s+/g, " ").replace(/[\s*]+$/, "");
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit).match(/^(.{20,}?[?.:])\s/);
  return cut ? cut[1] + " …" : text.slice(0, limit).trimEnd() + "…";
}
function answerInContext(label) {
  show("profile");
  const box = $("#context-inbox-form textarea");
  box.value = `About “${shortLabel(label, 120)}”: `;
  $("#context-inbox").scrollIntoView({ behavior: "smooth", block: "start" });
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}
function renderQuestionList() {
  if (
    [...document.querySelectorAll("#question-list form")].some(
      (form) => dirtyForms.has(form) || form.contains(document.activeElement),
    )
  ) {
    updateQuestionControls();
    return;
  }
  const q = $("#question-list");
  if (!questionState) {
    empty(q, "Loading unanswered questions…");
    updateQuestionControls();
    return;
  }
  const signature = JSON.stringify([
    questionState.questions,
    state.facts,
    state.demo,
    $("#question-search").value,
  ]);
  if (signature === renderedQuestionSignature) {
    updateQuestionControls();
    return;
  }
  renderedQuestionSignature = signature;
  q.replaceChildren();
  if (!questionState.questions.length)
    empty(
      q,
      $("#question-search").value.trim()
        ? "No unanswered questions match your search."
        : "No unanswered personal questions. New questions will appear here without stopping the rest of the search.",
    );
  for (const x of questionState.questions) {
    const box = el("article", undefined, "question");
    box.append(el("h3", x.label));
    if (x.field_context?.section) box.append(el("p", `Form section: ${x.field_context.section}${Number.isInteger(x.field_context.section_entry) ? ` · entry ${x.field_context.section_entry + 1}` : ""}`, "help"));
    if (x.company)
      box.append(
        state.demo
          ? el("p", `${x.company} · ${x.title}`)
          : link(x.url, `${x.company} · ${x.title}`),
      );
    box.append(el("p", questionReasons[x.reason] || x.reason.replaceAll("_", " "), "subtle"));
    const f = el("form");
    let options;
    try {
      options = JSON.parse(x.options);
      if (
        !Array.isArray(options) ||
        options.some((option) => typeof option !== "string")
      )
        throw new Error();
    } catch {
      box.append(
        el(
          "p",
          "This saved question has invalid choices. Check the private history or skip the company before proceeding.",
          "help",
        ),
      );
      q.append(box);
      continue;
    }
    const input = options.length ? el("select") : el("textarea");
    input.required = true;
    input.setAttribute("aria-label", x.label);
    if (options.length) {
      input.append(new Option("Choose an answer", ""));
      options.forEach((v) => input.append(new Option(v, v)));
    } else input.rows = 3;
    const bind = el("select");
    bind.setAttribute("aria-label", "Optional confirmed fact");
    bind.append(new Option("Save as an exact answer to this question", ""));
    for (const [k, v] of Object.entries(state.facts)) {
      if (v.confirmed)
        bind.append(new Option(`Use ${state.fact_labels[k]}: ${v.value}`, k));
    }
    bind.onchange = () => {
      if (bind.value) input.value = state.facts[bind.value].value;
    };
    const b = el("button", "Save once and reuse"),
      discard = el("button", "Discard draft", "secondary");
    b.disabled = state.demo;
    discard.type = "button";
    discard.dataset.questionDiscard = "true";
    discard.hidden = true;
    discard.onclick = () => {
      input.value = "";
      bind.value = "";
      saved(f);
      updateQuestionControls();
      input.focus();
    };
    const actions = el("div", undefined, "actions");
    actions.append(b, discard);
    if (contextReasons.has(x.reason) && !state.demo) {
      // A fact or context note applies to every employer, not just this question.
      const everywhere = el("button", "Answer for every employer", "secondary");
      everywhere.type = "button";
      everywhere.onclick = () => answerInContext(x.label);
      actions.append(everywhere);
    }
    f.append(input, bind, actions);
    f.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api("/api/answer", {
          id: x.id,
          value: input.value,
          fact_key: bind.value || null,
        });
        saved(f);
        blurForm(f);
        note(
          "Answer saved. Matching applications can use it in the next cycle.",
        );
        box.remove();
        const previousLength = questionState.questions.length;
        questionState.questions = questionState.questions.filter(
          (question) => question.id !== x.id,
        );
        if (questionState.questions.length < previousLength)
          questionState.total = Math.max(0, questionState.total - 1);
        renderedQuestionSignature = null;
        updateQuestionControls();
        await loadQuestionLedger();
        await refresh();
      } catch (e) {
        note(e.message, true);
      }
    };
    box.append(f);
    q.append(box);
  }
  updateQuestionControls();
}
$("#question-search").addEventListener("input", () => {
  clearTimeout(questionTimer);
  questionRequest++;
  questionTimer = setTimeout(() => loadQuestionLedger(true), 220);
});
$("#question-previous").onclick = () => {
  questionOffset = Math.max(0, questionOffset - (questionState?.limit || 25));
  loadQuestionLedger(false, true);
};
$("#question-next").onclick = () => {
  questionOffset += questionState?.limit || 25;
  loadQuestionLedger(false, true);
};
$("#question-retry").onclick = () => loadQuestionLedger(true);
for (const type of ["input", "change"])
  document.addEventListener(type, (event) => {
    if (event.target.closest?.("#question-list")) updateQuestionControls();
  });
function outcomeDrafts() {
  return [...document.querySelectorAll("#uncertain form")].filter((form) =>
    dirtyForms.has(form),
  );
}
function updateOutcomeControls() {
  const draft = outcomeDrafts().length > 0;
  $("#outcome-search").disabled = draft;
  $("#outcome-filter").disabled = draft;
  $("#outcome-retry").disabled = draft || outcomeBusy;
  $("#outcome-previous").disabled =
    draft || outcomeBusy || !outcomeState || outcomeState.offset === 0;
  $("#outcome-next").disabled =
    draft ||
    outcomeBusy ||
    !outcomeState ||
    outcomeState.offset + outcomeState.limit >= outcomeState.total;
  $("#outcome-draft-help").hidden = !draft;
  for (const form of document.querySelectorAll("#uncertain form"))
    form.querySelector("[data-outcome-discard]").hidden = !dirtyForms.has(form);
  $("#outcome-page").textContent = outcomeState
    ? outcomeState.total
      ? outcomeState.applications.length
        ? `${outcomeState.offset + 1}–${Math.min(outcomeState.offset + outcomeState.limit, outcomeState.total)} of ${outcomeState.total} unresolved outcomes`
        : "Refreshing outcome page…"
      : "0 unresolved outcomes"
    : "Loading outcomes…";
}
async function loadOutcomeLedger(reset = false, focus = false) {
  if (!state || outcomeBusy || outcomeDrafts().length) return;
  if (reset) outcomeOffset = 0;
  outcomeBusy = true;
  updateOutcomeControls();
  const request = ++outcomeRequest,
    parent = $("#uncertain");
  parent.setAttribute("aria-busy", "true");
  try {
    const result = await api(
      `/api/outcomes?${new URLSearchParams({ search: $("#outcome-search").value, status: $("#outcome-filter").value, offset: outcomeOffset })}`,
    );
    if (request !== outcomeRequest) return;
    outcomeState = result;
    outcomeOffset = result.offset;
    $("#outcome-error").hidden = true;
    $("#outcome-retry").hidden = true;
    renderOutcomes();
    if (focus) {
      parent.focus();
      parent.scrollIntoView({ block: "start", behavior: "auto" });
    }
  } catch (error) {
    $("#outcome-error").textContent =
      `Could not load unresolved outcomes: ${error.message}`;
    $("#outcome-error").hidden = false;
    $("#outcome-retry").hidden = false;
  } finally {
    outcomeBusy = false;
    parent.removeAttribute("aria-busy");
    updateOutcomeControls();
    if (request !== outcomeRequest && !outcomeDrafts().length)
      loadOutcomeLedger(true);
  }
}
function renderOutcomes() {
  if (
    [...document.querySelectorAll("#uncertain form")].some(
      (form) => dirtyForms.has(form) || form.contains(document.activeElement),
    )
  ) {
    updateOutcomeControls();
    return;
  }
  const u = $("#uncertain");
  if (!outcomeState) {
    empty(u, "Loading unresolved outcomes…");
    updateOutcomeControls();
    return;
  }
  const signature = JSON.stringify([
    outcomeState.applications,
    state.demo,
    $("#outcome-filter").value,
    $("#outcome-search").value,
  ]);
  if (signature === renderedOutcomeSignature) {
    updateOutcomeControls();
    return;
  }
  renderedOutcomeSignature = signature;
  const focusedEvidence = document.activeElement.closest?.(
    "#uncertain details[data-outcome-evidence]",
  )?.dataset.outcomeEvidence;
  u.replaceChildren();
  const unknown = outcomeState.applications;
  if (!unknown.length)
    empty(
      u,
      $("#outcome-search").value.trim()
        ? "No unresolved outcomes match your search."
        : "No unresolved outcomes in this view.",
    );
  for (const a of unknown) {
    const box = el("article", undefined, "question");
    box.append(
      el(
        "h3",
        a.company ? `${a.company} · ${a.title}` : a.company_key || a.job_id,
      ),
    );
    if (a.url && !state.demo) box.append(link(a.url, "Verify at the employer"));
    box.append(
      el("p", `Attempt recorded: ${date(a.attempted || a.created)}`, "help"),
    );
    if (a.state === "awaiting_verification")
      box.append(
        el(
          "p",
          "Email verification pending. This application is held and will not be retried automatically.",
          "subtle",
        ),
      );
    const detail = el("details"),
      summary = el("summary", "Saved answers & evidence"),
      evidence = el("div"),
      expansionKey = `outcome:${a.id}`;
    detail.dataset.outcomeEvidence = a.id;
    evidence.setAttribute("aria-live", "polite");
    detail.append(summary);
    if (a.confirmation) detail.append(el("p", a.confirmation, "help"));
    detail.append(evidence);
    detail.ontoggle = () => {
      if (detail.open) {
        expandedEvidence.add(expansionKey);
        loadEvidence(a, evidence);
      } else expandedEvidence.delete(expansionKey);
    };
    detail.open = expandedEvidence.has(expansionKey);
    box.append(detail);
    const f = el("form"),
      select = el("select");
    select.setAttribute("aria-label", "Verified outcome");
    select.required = true;
    select.append(
      new Option("Choose the outcome you verified", ""),
      new Option("Employer confirms submission", "true"),
      new Option("Verified no submission occurred", "false"),
    );
    const text = el("textarea");
    text.required = true;
    text.minLength = 10;
    text.rows = 2;
    text.placeholder = "How did you verify the outcome?";
    text.setAttribute("aria-label", "Verification evidence");
    const b = el("button", "Record verified outcome"),
      discard = el("button", "Discard draft", "secondary");
    b.disabled = state.demo;
    discard.type = "button";
    discard.dataset.outcomeDiscard = "true";
    discard.hidden = true;
    discard.onclick = () => {
      text.value = "";
      select.value = "";
      saved(f);
      updateOutcomeControls();
      text.focus();
    };
    const actions = el("div", undefined, "actions");
    actions.append(b, discard);
    f.append(select, text, actions);
    f.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api("/api/reconcile", {
          id: a.id,
          submitted: select.value === "true",
          note: text.value,
        });
        saved(f);
        blurForm(f);
        box.remove();
        const previousLength = outcomeState.applications.length;
        outcomeState.applications = outcomeState.applications.filter(
          (application) => application.id !== a.id,
        );
        if (outcomeState.applications.length < previousLength)
          outcomeState.total = Math.max(0, outcomeState.total - 1);
        renderedOutcomeSignature = null;
        updateOutcomeControls();
        await loadOutcomeLedger();
        await refresh();
        note(
          "Outcome recorded. A non-submitted attempt remains held for manual handling.",
        );
      } catch (e) {
        note(e.message, true);
      }
    };
    box.append(f);
    u.append(box);
    if (focusedEvidence === a.id) summary.focus({ preventScroll: true });
  }
  updateOutcomeControls();
}
$("#outcome-search").addEventListener("input", () => {
  clearTimeout(outcomeTimer);
  outcomeRequest++;
  outcomeTimer = setTimeout(() => loadOutcomeLedger(true), 220);
});
$("#outcome-filter").onchange = () => {
  outcomeRequest++;
  loadOutcomeLedger(true);
};
$("#outcome-previous").onclick = () => {
  outcomeOffset = Math.max(0, outcomeOffset - (outcomeState?.limit || 25));
  loadOutcomeLedger(false, true);
};
$("#outcome-next").onclick = () => {
  outcomeOffset += outcomeState?.limit || 25;
  loadOutcomeLedger(false, true);
};
$("#outcome-retry").onclick = () => loadOutcomeLedger(true);
for (const type of ["input", "change"])
  document.addEventListener(type, (event) => {
    if (event.target.closest?.("#uncertain")) updateOutcomeControls();
  });
function renderQuestions() {
  if (
    [...document.querySelectorAll("#questions form")].some(
      (form) => dirtyForms.has(form) || form.contains(document.activeElement),
    )
  )
    return;
  renderQuestionList();
  const blocked = state.jobs
      .filter((j) => j.status === "blocked" && (j.requires_attention ?? true))
      .slice(0, 12),
    heldTotal = state.summary?.held_count ?? blocked.length,
    ids = new Set(blocked.map((job) => job.id)),
    signature = JSON.stringify([
      blocked,
      state.applications.filter((application) => ids.has(application.job_id)),
      heldTotal,
      state.demo,
    ]);
  $("#blocked-scope").textContent = heldTotal
    ? `Showing ${blocked.length} of ${heldTotal} held opportunities. Open the complete list to search and browse 50 per page.`
    : "No held opportunities in this section. Eligibility mismatches and company limits remain in the ledger.";
  $("#view-all-holds").disabled = !heldTotal;
  if (signature !== renderedBlockedSignature) {
    if (heldTotal && !blocked.length)
      empty(
        $("#blocked-jobs"),
        "Open the complete held-opportunity list below to review these records.",
      );
    else {
      const parent = $("#blocked-jobs");parent.replaceChildren();
      const labels = {information:"Missing information",mapping:"Employer choices need review",documents:"Documents and writing",account:"Accounts and verification",unsupported:"Manual completion",review:"Failures to review",transient:"Waiting for a retry"};
      for (const category of [...new Set(blocked.map(job => job.hold?.category || "review"))]) {
        const section=el("section");section.append(el("h3", labels[category] || "Other holds"));
        const rows=el("div");table(blocked.filter(job => (job.hold?.category || "review") === category),rows);section.append(rows);parent.append(section);
      }
    }
    renderedBlockedSignature = signature;
  }
  renderOutcomes();
}
$("#view-all-holds").onclick = async () => {
  $("#job-search").value = "";
  $("#status-filter").value = "blocked";
  show("today");
  await loadLedger(true);
  scrollLedgerIntoView();
};
const groups = {
  Contact: [
    "full_name",
    "first_name",
    "last_name",
    "preferred_name",
    "name_pronunciation",
    "email",
    "phone",
    "location",
    "street",
    "city",
    "state",
    "postal_code",
    "country",
    "linkedin",
    "github",
    "website",
  ],
  "Education & experience": [
    "school",
    "high_school",
    "degree",
    "major",
    "graduation",
    "college_start",
    "highest_completed_degree",
    "gpa",
    "professional_years",
    "skills",
    "programming_proficiency",
  ],
  "Authorization & availability": [
    "work_authorized_us",
    "needs_sponsorship",
    "citizenship",
    "us_person",
    "unrestricted_authorization",
    "temporary_work_authorization",
    "earliest_start",
    "latest_start",
    "salary",
    "notice_period",
    "relocate",
    "onsite",
    "summer_2027_relocate",
    "worked_outside_resume",
    "contacts_outside_resume",
    "summer_2027_available",
  ],
  "Employer screening questions": [
    "conflict_disclosures",
    "outside_business_activity",
    "business_activity_details",
    "government_official",
    "recruitment_data_consent",
    "demographic_data_consent",
  ],
  "Optional disclosures & consent": [
    "race",
    "hispanic_latino",
    "gender",
    "pronouns",
    "veteran",
    "disability",
    "recording",
    "background_check",
    "sms",
    "native_name",
  ],
};
const yesNoFacts = new Set([
  "temporary_work_authorization",
  "work_authorized_us",
  "needs_sponsorship",
  "us_person",
  "unrestricted_authorization",
  "relocate",
  "onsite",
  "recording",
  "background_check",
  "sms",
  "worked_outside_resume",
  "contacts_outside_resume",
  "summer_2027_relocate",
]);
const monthFacts = new Set([
  "graduation",
  "college_start",
  "earliest_start",
  "latest_start",
]);
const factHelp = {
  race: "Use your most specific answer (for example, South Asian rather than Asian). A specific answer can fill broader employer choices; a broad one cannot fill narrower choices.",
  gender: "Use your most specific answer (for example, Cisgender man rather than Male). It can fill broader choices such as Man or Male.",
  veteran: "Use your most specific answer (for example, I have never served in the military). It can fill broader choices such as I am not a protected veteran.",
  disability: "Use your most specific answer, including whether you have had a disability in the past.",
  conflict_disclosures: "Answer Yes if any listed disclosure could apply. A Yes is never expanded into details; those stay with you.",
  hispanic_latino: "Hispanic / Latino ethnicity is separate from race. Use your chosen response; an Asian race answer does not determine it.",
  programming_proficiency: "Your own assessment: Beginner, Intermediate, Advanced or Expert. Experience does not set this rating automatically.",
  skills:
    "Separate skills with commas. Include only skills you can honestly support.",
  professional_years:
    "Use your actual qualifying professional experience. Enter 0 if you have none.",
  us_person:
    "Answer based on your own confirmed export-control status. Do not infer this from your address.",
  needs_sponsorship: "Include sponsorship you will need now or in the future.",
  work_authorized_us:
    "Use your current authorization status; this is not extracted from your resume.",
  graduation: "Choose your expected graduation month and year.",
  email:
    "Use the email you want employers to contact. It is tied to your application history.",
};
function renderFacts() {
  const parent = $("#fact-fields");
  parent.replaceChildren();
  // Every fact the resolver can use must be editable here, grouped or not.
  const grouped = new Set(Object.values(groups).flat()),
    ungrouped = Object.keys(state.fact_labels).filter((key) => !grouped.has(key)),
    entries = Object.entries(groups)
      .map(([name, keys]) => [name, keys.filter((key) => key in state.fact_labels)])
      .concat(ungrouped.length ? [["Other facts", ungrouped]] : []);
  for (const [name, keys] of entries) {
    const group = el("fieldset", undefined, "fact-group"),
      grid = el("div", undefined, "form-grid");
    group.append(el("legend", name));
    const optionalGroup = keys.every((key) => !state.required.includes(key));
    if (optionalGroup) {
      group.dataset.optionalGroup = "true";
      group.hidden = !$("#show-optional-facts").checked;
    }
    for (const key of keys) {
      const required = state.required.includes(key),
        label = el(
          "label",
          state.fact_labels[key] + (required ? " · required" : ""),
        );
      const input = el(key === "skills" ? "textarea" : "input");
      input.name = key;
      input.id = "fact-" + key;
      input.value = state.facts[key]?.value || "";
      if (key === "skills") input.rows = 2;
      if (required) input.required = true;
      if (yesNoFacts.has(key) || state.boolean_facts?.includes(key)) {
        input.setAttribute("list", "yes-no-values");
        input.pattern = "Yes|No";
        input.placeholder = "Choose Yes or No";
      }
      if (monthFacts.has(key)) input.type = "month";
      if (key === "email") {
        input.type = "email";
        input.autocomplete = "email";
      }
      if (key === "phone") {
        input.type = "tel";
        input.autocomplete = "tel";
      }
      if (key === "professional_years") {
        input.type = "number";
        input.min = "0";
        input.max = "80";
        input.step = "0.1";
      }
      const autocomplete = {
        full_name: "name",
        first_name: "given-name",
        last_name: "family-name",
        street: "street-address",
        postal_code: "postal-code",
        city: "address-level2",
        state: "address-level1",
        country: "country-name",
      };
      if (autocomplete[key]) input.autocomplete = autocomplete[key];
      if (
        state.facts[key] &&
        !state.facts[key].confirmed &&
        state.facts[key].source !== "revoked"
      )
        label.append(
          el("span", "Extracted from resume — please confirm", "candidate"),
        );
      label.append(input);
      if (factHelp[key]) {
        const help = el("span", factHelp[key], "help");
        help.id = "help-" + key;
        input.setAttribute("aria-describedby", help.id);
        label.append(help);
      }
      if (!required) {
        label.dataset.optionalFact = "true";
        label.hidden = !$("#show-optional-facts").checked;
      }
      grid.append(label);
    }
    group.append(grid);
    parent.append(group);
  }
  $("#setup-status").textContent = state.missing_setup.length
    ? "Still needed: " +
      state.missing_setup.map((key) => state.fact_labels[key] || key).join(", ")
    : "Required facts are confirmed. You can start automatic applications.";
  $("#complete-setup").disabled = state.missing_setup.length > 0;
  $("#fact-completion").textContent =
    `${state.required.filter((key) => state.facts[key]?.confirmed).length} of ${state.required.length} required facts confirmed`;
}
$("#show-optional-facts").onchange = (event) => {
  document
    .querySelectorAll("[data-optional-fact],[data-optional-group]")
    .forEach((field) => (field.hidden = !event.target.checked));
};
function updateProviderFields() {
  const form = $("#provider-form"),
    paid = form.elements.provider.value.endsWith("-api");
  $("#provider-key-field").hidden = !paid;
  form.elements.key.disabled = !paid;
  $("#provider-model-help").textContent = paid
    ? "Use an exact model ID from your selected vendor. API usage has separate billing."
    : "Optional. Leave blank to use the provider’s configured model.";
  form.elements.provider_model.required = paid;
}
$("#provider-form [name=provider]").addEventListener(
  "change",
  updateProviderFields,
);
const templateCategories = {
  motivation: "Why this role",
  project: "A project I built",
  experience: "My background",
};
function renderTemplates() {
  const parent = $("#templates");
  if (
    [...parent.querySelectorAll("form")].some(
      (form) => dirtyForms.has(form) || form.contains(document.activeElement),
    )
  )
    return;
  parent.replaceChildren();
  for (const template of state.templates) {
    const details = el("details", undefined, "saved-template");
    details.append(
      el(
        "summary",
        `${templateCategories[template.category] || template.category} · ${template.body.slice(0, 70)}`,
      ),
    );
    if (template.id.startsWith("material:")) {
      const source = el(
        "button",
        "Review source in Writing & context",
        "secondary",
      );
      source.type = "button";
      source.onclick = () => show("materials");
      details.append(
        el("p", template.body, "approved-body"),
        el(
          "p",
          "This wording comes from a reviewed personal document. Edit its source or revoke its approved use in Writing & context.",
          "help",
        ),
        source,
      );
    } else {
      const form = el("form", undefined, "material-review"),
        categoryLabel = el("label", "Use for"),
        category = el("select");
      for (const [value, label] of Object.entries(templateCategories))
        category.append(new Option(label, value));
      category.value = template.category;
      categoryLabel.append(category);
      const bodyLabel = el("label", "Your confirmed wording"),
        body = el("textarea");
      body.rows = 5;
      body.required = true;
      body.minLength = 20;
      body.maxLength = 12000;
      body.value = template.body;
      bodyLabel.append(body);
      const buttons = el("div", undefined, "actions"),
        saveButton = el("button", "Save revised wording"),
        revoke = el("button", "Stop using this wording", "secondary");
      revoke.type = "button";
      buttons.append(saveButton, revoke);
      form.append(
        categoryLabel,
        bodyLabel,
        el(
          "p",
          "Saving approves this revision. Older answers based on this text must pass a fresh source check.",
          "help",
        ),
        buttons,
      );
      form.onsubmit = async (event) => {
        event.preventDefault();
        try {
          await api("/api/template-edit", {
            id: template.id,
            category: category.value,
            body: body.value,
          });
          saved(form);
          blurForm(form);
          await refresh();
          note(
            "Revised wording saved. Future answers must use the updated source.",
          );
        } catch (error) {
          note(error.message, true);
        }
      };
      revoke.onclick = async () => {
        revoke.disabled = true;
        try {
          await api("/api/template-revoke", { id: template.id });
          saved(form);
          blurForm(form);
          await refresh();
          note(
            "Approved wording withdrawn. It will no longer support new answers. Application history stays recorded.",
          );
        } catch (error) {
          note(error.message, true);
          revoke.disabled = false;
        }
      };
      details.append(form);
    }
    parent.append(details);
  }
}
function updateSelectedDownloads() {
  if (!state) return;
  for (const kind of ["resume", "transcript"]) {
    const item = state.documents.find((item) => item.kind === kind),
      button = $("#download-selected-" + kind),
      feedback = $("#selected-" + kind + "-feedback");
    button.hidden = !item;
    button.disabled =
      !item ||
      typeof item.hash !== "string" ||
      !/^[a-f0-9]{64}$/.test(item.hash) ||
      item.available === false ||
      selectedDownloadBusy.has(kind) ||
      $("#" + kind + "-upload").disabled;
    feedback.hidden = !item;
    if (
      !selectedDownloadBusy.has(kind) &&
      selectedDownloadHashes[kind] !== item?.hash
    ) {
      feedback.textContent = "";
      feedback.setAttribute("role", "status");
      selectedDownloadHashes[kind] = item?.hash;
    }
  }
}
for (const kind of ["resume", "transcript"]) {
  $("#download-selected-" + kind).onclick = async () => {
    if (!state) return;
    const item = state.documents.find((item) => item.kind === kind);
    if (!item || selectedDownloadBusy.has(kind)) return;
    selectedDownloadBusy.add(kind);
    try {
      await downloadPdf(
        $("#download-selected-" + kind),
        $("#selected-" + kind + "-feedback"),
        `/api/selected-document/${kind}/${item.hash}`,
        kind + ".pdf",
      );
    } finally {
      selectedDownloadBusy.delete(kind);
      updateSelectedDownloads();
    }
  };
}
function renderDocumentStatus() {
  const resume = state.documents.find((document) => document.kind === "resume"),
    transcript = state.documents.find(
      (document) => document.kind === "transcript",
    );
  $("#resume-state").textContent = resume
    ? resume.available === false
      ? "Your saved resume file is unavailable. Import a resume PDF again before starting a batch."
      : "Resume imported and stored privately."
    : "No resume imported.";
  $("#transcript-state").textContent = transcript
    ? transcript.available === false
      ? "Your saved transcript file is unavailable. Import a transcript PDF again, or stop using it for future uploads."
      : "Transcript imported and stored privately. Upload another PDF to replace it."
    : "No transcript imported. Jobs requiring one will appear in Needs you.";
  updateTranscriptWithdrawal();
  updateSelectedDownloads();
}

function renderSettings() {
  const f = $("#settings-form");
  for (const input of f.elements) {
    if (!input.name) continue;
    const v = state.settings[input.name];
    if (input.type === "checkbox") {
      input.checked = !!v;
      continue;
    }
    input.value =
      input.name === "cycle_timeout_seconds"
        ? v / 60
        : Array.isArray(v)
          ? v.join("\n")
          : typeof v === "object"
            ? JSON.stringify(v, null, 2)
            : v;
  }
  renderAliasRows(state.settings.company_aliases);
  updateDraftControls();
}

function addAliasRow(other = "", main = "", focus = false) {
  const row = el("div", undefined, "company-alias-row"),
    otherLabel = el("label", "Other name"),
    mainLabel = el("label", "Main name"),
    otherInput = el("input"),
    mainInput = el("input"),
    remove = el("button", "Remove", "secondary");
  otherInput.dataset.aliasOther = "true";
  mainInput.dataset.aliasMain = "true";
  otherInput.value = other;
  mainInput.value = main;
  otherInput.maxLength = mainInput.maxLength = 200;
  otherInput.required = mainInput.required = true;
  otherInput.disabled = mainInput.disabled = aliasJsonMode;
  otherLabel.append(otherInput);
  mainLabel.append(mainInput);
  remove.type = "button";
  remove.setAttribute(
    "aria-label",
    other ? `Remove alternate name ${other}` : "Remove alternate company name",
  );
  otherInput.addEventListener("input", () =>
    remove.setAttribute(
      "aria-label",
      otherInput.value.trim()
        ? `Remove alternate name ${otherInput.value.trim()}`
        : "Remove alternate company name",
    ),
  );
  remove.onclick = () => {
    row.remove();
    $("#alias-json-field textarea").dispatchEvent(
      new Event("input", { bubbles: true }),
    );
    $("#add-company-alias").focus();
    updateAliasEmpty();
  };
  row.append(otherLabel, mainLabel, remove);
  $("#company-alias-rows").append(row);
  updateAliasEmpty();
  if (focus) otherInput.focus();
}
function updateAliasEmpty() {
  const rows = $("#company-alias-rows");
  rows.querySelector(".help")?.remove();
  if (!rows.querySelector(".company-alias-row"))
    rows.append(
      el(
        "p",
        "No alternate names saved. Add one only when an employer uses more than one name.",
        "help",
      ),
    );
}
function renderAliasRows(aliases) {
  $("#company-alias-rows").replaceChildren();
  for (const [other, main] of Object.entries(aliases || {}))
    addAliasRow(other, main);
  updateAliasEmpty();
}
function readAliasRows() {
  const aliases = Object.create(null),
    keys = new Set();
  for (const row of document.querySelectorAll(
    "#company-alias-rows .company-alias-row",
  )) {
    const other = row.querySelector("[data-alias-other]").value.trim(),
      main = row.querySelector("[data-alias-main]").value.trim(),
      key = other.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (!other || !main)
      throw new Error("Fill both company names, or remove the empty row.");
    if (!key)
      throw new Error(
        "The other company name needs at least one letter from A–Z or a number.",
      );
    if (keys.has(key))
      throw new Error(
        `“${other}” repeats another alternate name. Keep one mapping for each name.`,
      );
    keys.add(key);
    aliases[other] = main;
  }
  return aliases;
}
function readAliasJson() {
  try {
    const value = JSON.parse($("#alias-json-field textarea").value || "{}");
    if (
      !value ||
      Array.isArray(value) ||
      typeof value !== "object" ||
      Object.values(value).some((v) => typeof v !== "string")
    )
      throw new Error();
    return value;
  } catch {
    throw new Error(
      'Company aliases must be a valid JSON object, for example {"Acme Inc": "Acme"}.',
    );
  }
}
$("#add-company-alias").onclick = () => {
  addAliasRow("", "", true);
  $("#alias-json-field textarea").dispatchEvent(
    new Event("input", { bubbles: true }),
  );
};
$("#alias-json-toggle").onclick = () => {
  try {
    if (aliasJsonMode) renderAliasRows(readAliasJson());
    else
      $("#alias-json-field textarea").value = JSON.stringify(
        readAliasRows(),
        null,
        2,
      );
    aliasJsonMode = !aliasJsonMode;
    $("#company-alias-rows").hidden = aliasJsonMode;
    $("#alias-json-field").hidden = !aliasJsonMode;
    $("#add-company-alias").hidden = aliasJsonMode;
    $("#alias-json-toggle").textContent = aliasJsonMode
      ? "Use name fields"
      : "Edit as JSON";
    $("#alias-json-toggle").setAttribute(
      "aria-expanded",
      String(aliasJsonMode),
    );
    for (const input of document.querySelectorAll("#company-alias-rows input"))
      input.disabled = aliasJsonMode;
    $("#alias-error").hidden = true;
    if (aliasJsonMode) $("#alias-json-field textarea").focus();
  } catch (error) {
    $("#alias-error").textContent = error.message;
    $("#alias-error").hidden = false;
  }
};
function runRow(run) {
  const row = el("div", undefined, "run-row");
  let detail = run.detail || "No batch details recorded yet.";
  try {
    const data = JSON.parse(detail);
    if (!data || typeof data !== "object" || Array.isArray(data))
      throw new Error();
    const outcomes = Object.entries(data.outcomes || {})
      .filter(([key]) => key !== "confirmed")
      .map(
        ([key, value]) =>
          `${statusLabels[key] || key.replaceAll("_", " ")}: ${value}`,
      )
      .join("; ");
    const reason =
      (typeof data.reason === "string" &&
      data.reason.startsWith("cycle_timeout")
        ? "Batch time budget reached. Completed progress is saved."
        : data.reason) ||
      Object.entries(data.reasons || {})
        .map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`)
        .join("; ");
    detail =
      data.mode === "discovery"
        ? `Opportunity search · ${data.added || 0} new listings${data.sources_checked !== undefined ? ` · ${data.sources_checked} sources checked · ${data.sources_failed || 0} unavailable` : ""} · No submissions.${data.time_limit_reached ? " Search time limit reached." : ""} ${reason}`.trim()
        : data.mode === "prepare"
          ? `Preparation batch · ${data.prepared ?? data.confirmed ?? 0} prepared · ${data.attempts || 0} attempted · No submissions. ${outcomes} ${reason}`.trim()
          : `${run.submitted ?? data.confirmed ?? 0} submitted · ${data.attempts || 0} attempted. ${outcomes} ${reason}`.trim();
  } catch {}
  if (run.model_requests_used !== undefined)
    detail += ` · ${run.model_requests_used} model ${run.model_requests_used === 1 ? "request" : "requests"}`;
  row.append(
    el("span", date(run.started)),
    el(
      "span",
      {
        finished: "Finished",
        running: "Running",
        paused: "Paused",
        blocked: "Stopped for a blocker",
        interrupted: "Interrupted",
      }[run.status] || run.status.replaceAll("_", " "),
    ),
    el("span", detail),
  );
  return row;
}
function renderRuns() {
  if ($("#run-history-panel").open) loadRunHistory();
  const parent = $("#runs");
  parent.replaceChildren();
  if (!state.runs.length) {
    empty(
      parent,
      "Finish your setup and start a batch. This is where you’ll see what ran and how it went.",
      "Your desk is ready for its first batch",
    );
    return;
  }
  for (const run of state.runs.slice(0, 5)) parent.append(runRow(run));
}

function updateSourceHealthControls() {
  $("#source-health-previous").disabled =
    sourceHealthBusy || !sourceHealthState || sourceHealthState.offset === 0;
  $("#source-health-next").disabled =
    sourceHealthBusy ||
    !sourceHealthState ||
    sourceHealthState.offset + sourceHealthState.limit >=
      sourceHealthState.total;
  $("#source-health-retry").disabled = sourceHealthBusy;
  $("#source-health-page").textContent = sourceHealthState
    ? sourceHealthState.total
      ? `${sourceHealthState.offset + 1}–${Math.min(sourceHealthState.offset + sourceHealthState.limit, sourceHealthState.total)} of ${sourceHealthState.total} sources`
      : "0 sources"
    : "Loading source checks…";
}
async function loadSourceHealth(reset = false, focus = false) {
  if (!state || sourceHealthBusy) return;
  if (reset) sourceHealthOffset = 0;
  sourceHealthBusy = true;
  updateSourceHealthControls();
  const request = ++sourceHealthRequest,
    parent = $("#sources");
  parent.setAttribute("aria-busy", "true");
  try {
    const result = await api(
      `/api/sources?${new URLSearchParams({ search: $("#source-search").value, status: $("#source-filter").value, offset: sourceHealthOffset })}`,
    );
    if (request !== sourceHealthRequest) return;
    sourceHealthState = result;
    sourceHealthOffset = result.offset;
    const signature = JSON.stringify(result.sources);
    if (signature !== renderedSourceHealthSignature) {
      parent.replaceChildren();
      if (!result.sources.length)
        empty(
          parent,
          result.summary.total
            ? "No source checks match these filters."
            : "Source checks appear after finding opportunities. Earlier opportunities stay saved if a source is temporarily unavailable.",
          result.summary.total ? "Try another search" : "Discovery is ready",
          "↗",
        );
      for (const source of result.sources) {
        const row = el("div", undefined, "source-health-row");
        row.append(
          el("strong", source.id),
          el(
            "p",
            `${source.status === "ok" ? "Available" : source.status === "error" ? "Unavailable" : source.status} · Checked ${date(source.checked)}`,
            "help",
          ),
        );
        if (source.error) row.append(el("p", source.error, "subtle"));
        parent.append(row);
      }
      renderedSourceHealthSignature = signature;
    }
    const summary = `${result.summary.total} sources checked · ${result.summary.available} available · ${result.summary.unavailable} unavailable`;
    if ($("#source-health-summary").textContent !== summary)
      $("#source-health-summary").textContent = summary;
    $("#source-health-error").hidden = true;
    $("#source-health-retry").hidden = true;
    if (focus) {
      parent.focus();
      parent.scrollIntoView({ block: "start", behavior: "auto" });
    }
  } catch (error) {
    $("#source-health-error").textContent =
      `Could not load source checks: ${error.message}`;
    $("#source-health-error").hidden = false;
    $("#source-health-retry").hidden = false;
  } finally {
    sourceHealthBusy = false;
    parent.removeAttribute("aria-busy");
    updateSourceHealthControls();
    if (request !== sourceHealthRequest) loadSourceHealth(true);
  }
}
$("#source-health-panel").ontoggle = () => {
  if ($("#source-health-panel").open) loadSourceHealth();
};
$("#source-search").addEventListener("input", () => {
  clearTimeout(sourceHealthTimer);
  sourceHealthRequest++;
  sourceHealthTimer = setTimeout(() => loadSourceHealth(true), 220);
});
$("#source-filter").onchange = () => {
  sourceHealthRequest++;
  loadSourceHealth(true);
};
$("#source-health-previous").onclick = () => {
  sourceHealthOffset = Math.max(
    0,
    sourceHealthOffset - (sourceHealthState?.limit || 25),
  );
  loadSourceHealth(false, true);
};
$("#source-health-next").onclick = () => {
  sourceHealthOffset += sourceHealthState?.limit || 25;
  loadSourceHealth(false, true);
};
$("#source-health-retry").onclick = () => loadSourceHealth(true);

function updateRunHistoryControls() {
  $("#run-history-previous").disabled =
    runHistoryBusy || !runHistoryState || runHistoryState.offset === 0;
  $("#run-history-next").disabled =
    runHistoryBusy ||
    !runHistoryState ||
    runHistoryState.offset + runHistoryState.limit >= runHistoryState.total;
  $("#run-history-retry").disabled = runHistoryBusy;
  $("#run-history-page").textContent = runHistoryState
    ? runHistoryState.total
      ? `${runHistoryState.offset + 1}–${Math.min(runHistoryState.offset + runHistoryState.limit, runHistoryState.total)} of ${runHistoryState.total} recorded batches`
      : "0 recorded batches"
    : "Loading batch history…";
}
async function loadRunHistory(reset = false, focus = false) {
  if (!state || runHistoryBusy) return;
  if (reset) runHistoryOffset = 0;
  runHistoryBusy = true;
  updateRunHistoryControls();
  const request = ++runHistoryRequest,
    parent = $("#run-history");
  parent.setAttribute("aria-busy", "true");
  try {
    const result = await api(
      `/api/runs?${new URLSearchParams({ search: $("#run-search").value, status: $("#run-filter").value, offset: runHistoryOffset })}`,
    );
    if (request !== runHistoryRequest) return;
    runHistoryState = result;
    runHistoryOffset = result.offset;
    const signature = JSON.stringify(result.runs);
    if (signature !== renderedRunHistorySignature) {
      parent.replaceChildren();
      if (!result.runs.length)
        empty(parent, "No recorded batches match these filters.");
      for (const run of result.runs) parent.append(runRow(run));
      renderedRunHistorySignature = signature;
    }
    $("#run-history-error").hidden = true;
    $("#run-history-retry").hidden = true;
    if (focus) {
      parent.focus();
      parent.scrollIntoView({ block: "start", behavior: "auto" });
    }
  } catch (error) {
    $("#run-history-error").textContent =
      `Could not load batch history: ${error.message}`;
    $("#run-history-error").hidden = false;
    $("#run-history-retry").hidden = false;
  } finally {
    runHistoryBusy = false;
    parent.removeAttribute("aria-busy");
    updateRunHistoryControls();
    if (request !== runHistoryRequest) loadRunHistory(true);
  }
}
$("#run-history-panel").ontoggle = () => {
  if ($("#run-history-panel").open) loadRunHistory();
};
$("#run-search").addEventListener("input", () => {
  clearTimeout(runHistoryTimer);
  runHistoryRequest++;
  runHistoryTimer = setTimeout(() => loadRunHistory(true), 220);
});
$("#run-filter").onchange = () => {
  runHistoryRequest++;
  loadRunHistory(true);
};
$("#run-history-previous").onclick = () => {
  runHistoryOffset = Math.max(
    0,
    runHistoryOffset - (runHistoryState?.limit || 25),
  );
  loadRunHistory(false, true);
};
$("#run-history-next").onclick = () => {
  runHistoryOffset += runHistoryState?.limit || 25;
  loadRunHistory(false, true);
};
$("#run-history-retry").onclick = () => loadRunHistory(true);

function render() {
  const dayFormatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: state.settings.timezone,
  });
  const today = dayFormatter.format(new Date());
  const submitted =
    state.summary?.submitted_today ??
    state.applications.filter(
      (application) =>
        application.state === "confirmed" &&
        application.attempted &&
        dayFormatter.format(new Date(application.attempted)) === today,
    ).length;
  renderOverview(submitted);
  window.connectedWorkspace?.render(state);
  renderCycleFunnel();
  $("#daily-progress").textContent =
    submitted >= state.settings.target_per_day
      ? "Daily target reached. Every step counts."
      : `${submitted} confirmed today · ${Math.max(0, state.settings.target_per_day - submitted)} to your daily target`;
  $("#daily-target").textContent =
    `Daily target: ${state.settings.target_per_day}`;
  $("#setup-callout").hidden =
    state.demo ||
    (state.settings.onboarding_complete && !state.missing_setup.length);
  $("#worker-state").textContent = state.demo
    ? "Read-only sample workspace"
    : accountTransferBusy
      ? "Moving encrypted employer passwords…"
      : state.worker_running
        ? state.worker_mode === "discovery"
          ? "Finding opportunities…"
          : state.worker_mode === "prepare"
            ? "Preparing applications…"
            : state.settings.live_enabled
              ? "Batch running"
              : "Pausing active batch…"
        : state.worker_recovery?.recovery_needed
          ? "Recovery needed"
          : !state.settings.onboarding_complete ||
              state.missing_setup.length > 0
            ? "Setup needed"
            : state.settings.live_enabled
              ? "Automatic submissions enabled"
              : "Submissions paused";
  const finding = state.worker_running && state.worker_mode === "discovery";
  const preparing = state.worker_running && state.worker_mode === "prepare";
  $("#pause").textContent = finding
    ? "Stop search & pause"
    : preparing
      ? "Stop preparation & pause"
      : state.settings.live_enabled
        ? "Pause"
        : "Resume";
  $("#pause").disabled =
    state.demo ||
    ((accountTransferBusy || accountCredentialBusy) && !state.settings.live_enabled) ||
    (!finding &&
      !preparing &&
      !state.settings.live_enabled &&
      (!state.settings.onboarding_complete || state.missing_setup.length > 0));
  $("#discover").disabled =
    state.demo ||
    accountTransferBusy || accountCredentialBusy ||
    state.worker_running ||
    state.worker_recovery?.recovery_needed;
  $("#run").disabled =
    state.demo ||
    accountTransferBusy || accountCredentialBusy ||
    state.worker_recovery?.recovery_needed ||
    state.worker_running ||
    !state.settings.onboarding_complete ||
    !state.settings.live_enabled ||
    state.missing_setup.length > 0;
  $("#prepare").disabled =
    state.demo ||
    accountTransferBusy || accountCredentialBusy ||
    state.worker_running ||
    state.worker_recovery?.recovery_needed ||
    !state.settings.onboarding_complete ||
    state.missing_setup.length > 0 ||
    state.settings.live_enabled;
  $("#prepare-help").textContent = state.demo
    ? "Draft preparation is available in your own workspace."
    : state.worker_running
      ? "Wait for the active batch to finish, or use Stop preparation & pause to cancel."
      : state.worker_recovery?.recovery_needed
        ? "Recover interrupted work before preparing drafts."
        : !state.settings.onboarding_complete || state.missing_setup.length > 0
          ? "Finish Setup checklist before preparing drafts."
          : state.settings.live_enabled
            ? "Pause automatic submissions to prepare drafts for review."
            : "Ready to prepare. Submissions will stay paused.";
  $("#recovery-banner").hidden = !state.worker_recovery?.recovery_needed;
  $("#worker-error").hidden = !state.worker_error;
  if ($("#worker-error").textContent !== (state.worker_error || ""))
    $("#worker-error").textContent = state.worker_error || "";
  $("#recover-worker").disabled = state.demo || state.worker_running;
  for (const control of $("#posting-import-form").elements)
    control.disabled = state.demo || postingImportBusy;
  $("#posting-import-save").disabled =
    state.demo || postingImportBusy || !postingImportPreview;
  $("#demo-banner").hidden = !state.demo;
  renderSavedViews();
  updateScheduleControls();
  $("#download-backup").disabled =
    state.demo || state.worker_running || backupBusy;
  for (const control of $("#backup-check-form").elements)
    control.disabled = state.demo || backupCheckBusy;
  $("#backup-state").textContent = backupBusy
    ? "Preparing your private archive…"
    : state.demo
      ? "Backups are available in your own workspace."
      : state.worker_running
        ? "Wait for the active batch to finish before downloading."
        : "Your original ledger stays on this computer. The downloaded copy is a separate backup.";
  renderLedger();
  renderSetup();
  renderQuestions();
  renderAccounts();
  updateAccountTransferControls();
  updateAccountCredentialControls();
  renderTemplates();
  renderMaterials();
  renderMail();
  renderDocumentStatus();
  if (!editing("#facts-form")) renderFacts();
  renderContextNeeds();
  if (!editing("#basic-context-form")) {
    const context = state.basic_context || { text: "", revision: 0 };
    $("#basic-context-form textarea").value = context.text;
    $("#basic-context-form").dataset.revision = context.revision;
    $("#basic-context-status").textContent = context.text
      ? context.confirmed && context.role === "personal"
        ? "Saved and approved. Used alongside your resume and confirmed facts."
        : "Saved, but not approved as factual context. Review and save to use it."
      : "Your saved context will stay here so you can update it.";
  }
  if (!editing("#settings-form")) renderSettings();
  updateDraftControls();
  renderRuns();
  if ($("#source-health-panel").open) loadSourceHealth();
  show(view);
}
let deferredRefresh = null;
function releaseDeferredRefresh() {
  if (!deferredRefresh || materialPagingBusy || savedViewBusy) return;
  const waiting = deferredRefresh;
  deferredRefresh = null;
  refresh().then(waiting.resolve, waiting.reject);
}
async function refresh() {
  if (materialPagingBusy || savedViewBusy) {
    if (!deferredRefresh) {
      let resolve, reject;
      const promise = new Promise((done, failed) => {
        resolve = done;
        reject = failed;
      });
      deferredRefresh = { promise, resolve, reject };
    }
    return deferredRefresh.promise;
  }
  if (refreshing) {
    await refreshing;
    return refresh();
  }
  refreshing = (async () => {
    try {
      state = await api(materialStateUrl(materialOffset));
      render();
      await loadLedger(false);
      $("#connection-status").hidden = true;
    } catch (error) {
      const status = $("#connection-status");
      status.hidden = false;
      $("#connection-message").textContent = error.message;
      if (!state) note(error.message, true);
    } finally {
      refreshing = null;
      clearTimeout(workerRefreshTimer);
      // Cancellation is asynchronous. Follow the running worker to its terminal
      // state instead of leaving controls stale until the idle refresh.
      if (state?.worker_running && !document.hidden)
        workerRefreshTimer = setTimeout(() => {
          if (!document.hidden) refresh();
        }, 1500);
    }
  })();
  return refreshing;
}
document
  .querySelectorAll("[data-view]")
  .forEach((b) => (b.onclick = () => show(b.dataset.view)));
$("#setup-link").onclick = () => show("setup");
$("#status-filter").onchange = () => loadLedger(true);
$("#job-sort").onchange = () => loadLedger(true);
$("#job-search").oninput = () => {
  clearTimeout(ledgerTimer);
  ledgerTimer = setTimeout(() => loadLedger(true), 200);
};
$("#review-queue").onclick = () => show("questions");
$("#add-posting").onclick = () => {
  const panel = $("#add-posting-panel");
  panel.open = true;
  panel.scrollIntoView({
    block: "center",
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
  $("#job-form [name=company]").focus({ preventScroll: true });
};
$("#discard-fact-edits").onclick = () => {
  const form = $("#facts-form");
  if (!state || state.demo || form.dataset.saving === "true") return;
  saved(form);
  $("#confirm-facts").checked = false;
  renderFacts();
  updateDraftControls();
  form.querySelector('button[type="submit"]').focus({ preventScroll: true });
  note(
    "Unsaved fact edits discarded. Saved facts and extracted proposals are unchanged.",
  );
};
$("#facts-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!$("#confirm-facts").checked) return;
  const facts = Object.fromEntries(
    [...new FormData(e.target)].filter(([, v]) => v.trim()),
  );
  try {
    const clear = [...new FormData(e.target)]
      .filter(([key, value]) => !value.trim() && state.facts[key]?.value)
      .map(([key]) => key);
    await api("/api/facts", { facts, clear });
    $("#confirm-facts").checked = false;
    note(
      clear.length
        ? "Facts saved. Cleared values will no longer be reused."
        : "Confirmed facts saved. They will be reused automatically.",
    );
    blurForm(e.target);
    await refresh();
  } catch (e) {
    note(e.message, true);
  }
};
$("#resume-upload").onchange = async (e) => {
  const input = e.target,
    f = input.files[0];
  if (!f) return;
  input.disabled = true;
  updateSelectedDownloads();
  try {
    const result = await api("/api/resume", f, true);
    note(
      result.repaired
        ? "Stored resume PDF repaired from your matching upload. Confirm any new or changed extracted values."
        : "Resume imported. Confirm any new or changed extracted values and supply the remaining facts.",
    );
    await refresh();
  } catch (e) {
    note(e.message, true);
  } finally {
    input.disabled = false;
    input.value = "";
    updateSelectedDownloads();
  }
};
$("#settings-form").addEventListener("focusin", (event) => {
  const target = event.target;
  if (target.closest(".preferences-savebar")) return;
  requestAnimationFrame(() => {
    if (document.activeElement !== target || view !== "settings") return;
    const field = target.getBoundingClientRect();
    const bar = $(".preferences-savebar").getBoundingClientRect();
    if (field.bottom > bar.top - 16 && field.top < bar.bottom)
      target.scrollIntoView({ block: "center", behavior: "instant" });
  });
});
$("#discard-preferences").onclick = () => {
  const form = $("#settings-form");
  if (!state || state.demo || form.dataset.saving === "true") return;
  saved(form);
  renderSettings();
  updateScheduleControls();
  form.querySelector('button[type="submit"]').focus({ preventScroll: true });
  note("Unsaved preferences discarded. Your saved preferences are unchanged.");
};
$("#settings-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const data = {};
    if (!aliasJsonMode)
      $("#alias-json-field textarea").value = JSON.stringify(readAliasRows());
    for (const input of event.target.elements) {
      if (!input.name) continue;
      if (input.type === "checkbox") data[input.name] = input.checked;
      else if (input.type === "number")
        data[input.name] =
          input.name === "cycle_timeout_seconds"
            ? Math.round(Number(input.value) * 60)
            : Number(input.value);
      else if (input.name === "company_aliases") {
        data[input.name] = readAliasJson();
      } else if (Array.isArray(state.settings[input.name]))
        data[input.name] = input.value
          .split("\n")
          .map((v) => v.trim())
          .filter(Boolean);
      else data[input.name] = input.value;
    }
    await api("/api/settings", data);
    saved(event.target);
    note("Search preferences saved.");
    blurForm(event.target);
    await refresh();
    updateScheduleControls();
  } catch (error) {
    note(error.message, true);
  }
};
$("#complete-setup").onclick = async () => {
  try {
    const r = await api("/api/complete-setup", { start: true });
    note(r.message || "Automatic applications enabled.");
    await refresh();
  } catch (e) {
    note(e.message, true);
  }
};
$("#pause").onclick = async () => {
  try {
    await api(
      state.settings.live_enabled ||
        (state.worker_running &&
          ["discovery", "prepare"].includes(state.worker_mode))
        ? "/api/pause"
        : "/api/resume-worker",
      {},
    );
    await refresh();
  } catch (e) {
    note(e.message, true);
  }
};
$("#run").onclick = async () => {
  try {
    await api("/api/run", {});
    note("Cycle started. You can keep working; the ledger will update.");
    await refresh();
  } catch (e) {
    note(e.message, true);
  }
};
$("#prepare").onclick = async () => {
  $("#prepare").disabled = true;
  try {
    await api("/api/prepare", {});
    note("Preparing drafts for review. Submissions remain paused.");
    await refresh();
  } catch (error) {
    note(error.message, true);
    await refresh();
  }
};
$("#discover").onclick = async () => {
  try {
    await api("/api/discover", {});
    note(
      "Finding public opportunities. This search does not prepare or submit applications.",
    );
    await refresh();
  } catch (error) {
    note(error.message, true);
  }
};
function postingImportFeedback(text, error = false) {
  const status = $("#posting-import-status");
  status.textContent = text;
  status.setAttribute("role", error ? "alert" : "status");
}
function clearPostingPreview() {
  postingImportPreview = null;
  $("#posting-import-preview").hidden = true;
  $("#posting-import-save").hidden = true;
  $("#posting-import-save").disabled = true;
}
$("#posting-import-file").onchange = () => {
  clearPostingPreview();
  postingImportFeedback("");
};
$("#posting-import-template").onclick = () => {
  const url = URL.createObjectURL(
    new Blob(["\uFEFFCompany,Role,Application URL,Location,Posting text\r\n"], {
      type: "text/csv;charset=utf-8",
    }),
  );
  const anchor = el("a");
  anchor.href = url;
  anchor.download = "posting-template.csv";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$("#posting-import-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state || state.demo || postingImportBusy) return;
  clearPostingPreview();
  const file = $("#posting-import-file").files[0];
  if (!file || !file.size || file.size > 1024 ** 2) {
    postingImportFeedback(
      "Choose a CSV up to 1 MiB with at most 500 postings.",
      true,
    );
    return;
  }
  postingImportBusy = true;
  render();
  postingImportFeedback("Checking every posting before saving…");
  try {
    const result = await api("/api/posting-import-preview", file, true, {
      "Content-Type": "text/csv",
    });
    postingImportPreview = { file, hash: result.hash };
    const preview = $("#posting-import-preview");
    preview.replaceChildren(
      el("h3", `${result.total.toLocaleString()} postings ready to add`),
    );
    preview.append(
      el(
        "p",
        `${result.new.toLocaleString()} new postings · ${result.existing.toLocaleString()} existing postings to refresh.`,
      ),
    );
    const list = el("ul");
    for (const row of result.sample)
      list.append(
        el(
          "li",
          [row.company, row.title, row.location].filter(Boolean).join(" · "),
        ),
      );
    preview.append(list);
    if (result.total > result.sample.length)
      preview.append(
        el(
          "p",
          `Showing the first ${result.sample.length} of ${result.total} checked rows.`,
          "help",
        ),
      );
    preview.append(
      el(
        "p",
        "Existing application outcomes and company limits stay in place. This import does not prepare or submit applications.",
        "help",
      ),
    );
    preview.hidden = false;
    $("#posting-import-save").hidden = false;
    $("#posting-import-save").textContent =
      `Add ${result.total.toLocaleString()} checked postings`;
    postingImportFeedback(
      "CSV checked. Review the preview, then add the postings when ready.",
    );
  } catch (error) {
    postingImportFeedback(error.message, true);
  } finally {
    postingImportBusy = false;
    render();
  }
};
$("#posting-import-save").onclick = async () => {
  if (!state || state.demo || postingImportBusy || !postingImportPreview)
    return;
  const button = $("#posting-import-save"),
    hadFocus = document.activeElement === button;
  let saved = false;
  const checked = postingImportPreview;
  postingImportBusy = true;
  render();
  postingImportFeedback("Saving the checked postings…");
  try {
    const result = await api("/api/posting-import", checked.file, true, {
      "Content-Type": "text/csv",
      "X-Import-Hash": checked.hash,
    });
    clearPostingPreview();
    $("#posting-import-form").reset();
    saved = true;
    await refresh();
    postingImportFeedback(
      `Saved ${result.total.toLocaleString()} postings: ${result.new.toLocaleString()} new and ${result.existing.toLocaleString()} refreshed. Application outcomes stay unchanged.`,
    );
  } catch (error) {
    postingImportFeedback(
      `${error.message} Your checked preview is available to try again.`,
      true,
    );
  } finally {
    postingImportBusy = false;
    render();
    if (
      hadFocus &&
      (document.activeElement === document.body ||
        document.activeElement === button)
    )
      (saved ? $("#posting-import-file") : button).focus({
        preventScroll: true,
      });
  }
};

$("#job-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/job", Object.fromEntries(new FormData(e.target)));
    e.target.reset();
    note(
      "Posting added. Eligibility will be checked before any form is filled.",
    );
    await refresh();
  } catch (e) {
    note(e.message, true);
  }
};
refresh().then(() => {
  if (state && !state.settings.onboarding_complete && view === "today")
    show("setup");
});
setInterval(() => {
  if (
    !document.hidden &&
    !document.activeElement.matches("input,textarea,select")
  )
    refresh();
}, 15000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
$("#retry-connection").onclick = refresh;

function accountDrafts() {
  return [...$("#employer-accounts").querySelectorAll("form")].some((form) =>
    dirtyForms.has(form),
  );
}
function updateAccountControls() {
  const draft = accountDrafts();
  $("#account-search").disabled = draft;
  $("#account-filter").disabled = draft;
  $("#account-retry").disabled = draft || accountBusy;
  $("#account-previous").disabled =
    draft || accountBusy || !accountState || accountState.offset === 0;
  $("#account-next").disabled =
    draft ||
    accountBusy ||
    !accountState ||
    accountState.offset + accountState.accounts.length >= accountState.total;
  $("#account-draft-help").hidden = !draft;
  for (const form of $("#employer-accounts").querySelectorAll("form"))
    form.querySelector("[data-discard-draft]").hidden = !dirtyForms.has(form);
  $("#account-page").textContent = accountState
    ? accountState.total
      ? `${accountState.offset + 1}–${accountState.offset + accountState.accounts.length} of ${accountState.total} accounts`
      : "0 matching accounts"
    : "Checking account history…";
}
async function loadAccountLedger(reset = false, focus = false) {
  if (!state || accountDrafts() || (accountBusy && !reset && !focus)) return;
  if (reset) accountOffset = 0;
  const request = ++accountRequest;
  accountBusy = true;
  $("#account-error").hidden = true;
  $("#account-retry").hidden = true;
  $("#employer-accounts").setAttribute("aria-busy", "true");
  updateAccountControls();
  try {
    const query = new URLSearchParams({
      search: $("#account-search").value,
      status: $("#account-filter").value,
      offset: accountOffset,
    });
    const result = await api(`/api/accounts?${query}`);
    if (request !== accountRequest) return;
    accountState = result;
    accountOffset = result.offset;
    renderAccounts();
    if (focus) $("#employer-accounts").focus({ preventScroll: true });
  } catch (error) {
    if (request === accountRequest) {
      $("#account-error").textContent = error.message;
      $("#account-error").hidden = false;
      $("#account-retry").hidden = false;
    }
  } finally {
    if (request === accountRequest) {
      accountBusy = false;
      $("#employer-accounts").removeAttribute("aria-busy");
      updateAccountControls();
    }
  }
}
$("#account-search").oninput = () => {
  clearTimeout(accountTimer);
  accountTimer = setTimeout(() => loadAccountLedger(true), 200);
};
$("#account-filter").onchange = () => loadAccountLedger(true);
$("#account-retry").onclick = () => loadAccountLedger();
$("#account-previous").onclick = () => {
  accountOffset = Math.max(0, accountOffset - 25);
  loadAccountLedger(false, true);
};
$("#account-next").onclick = () => {
  accountOffset += 25;
  loadAccountLedger(false, true);
};
for (const type of ["input", "change"])
  document.addEventListener(type, (event) => {
    if (
      event.target.form &&
      $("#employer-accounts").contains(event.target.form)
    )
      updateAccountControls();
  });

function updateScheduleControls() {
  const unsaved = dirtyForms.has($("#settings-form"));
  $("#apply-schedule").disabled =
    !state ||
    state.demo ||
    scheduleBusy ||
    !scheduleStatus ||
    scheduleStatus.error ||
    (scheduleStatus.installed && !scheduleStatus.matches_applicant) ||
    state.worker_running ||
    state.worker_recovery?.recovery_needed ||
    !state.settings.onboarding_complete ||
    state.missing_setup.length > 0 ||
    unsaved;
  $("#check-schedule").disabled = scheduleBusy;
  if (!scheduleStatus || !state) return;
  $("#schedule-state").textContent = state.demo
    ? "Schedules are unavailable in the read-only sample."
    : scheduleStatus.error ||
      (scheduleStatus.installed && !scheduleStatus.matches_applicant
        ? "The saved schedule belongs to another applicant or cannot be verified. Manage it from its original instance before replacing it."
        : `${scheduleStatus.installed ? `Installed interval: ${scheduleStatus.interval_hours ?? "unknown"} hours.${scheduleStatus.enabled === false ? " The system timer is disabled." : ""}` : "No automatic schedule is installed."} Saved interval: ${state.settings.schedule_hours} hours.${unsaved ? " Save your pending preferences before applying." : ""}`);
}
async function loadSchedule() {
  if (scheduleBusy) return;
  scheduleLoaded = true;
  scheduleBusy = true;
  updateScheduleControls();
  try {
    scheduleStatus = await api("/api/schedule");
  } catch (error) {
    scheduleStatus = { error: error.message };
  } finally {
    scheduleBusy = false;
    updateScheduleControls();
  }
}
$("#check-schedule").onclick = loadSchedule;
$("#apply-schedule").onclick = async () => {
  if (scheduleBusy) return;
  scheduleBusy = true;
  updateScheduleControls();
  try {
    const result = await api("/api/schedule-apply", {});
    scheduleStatus = {
      installed: true,
      matches_applicant: true,
      interval_hours: result.interval_hours,
    };
    note(result.message);
  } catch (error) {
    note(error.message, true);
  } finally {
    scheduleBusy = false;
    updateScheduleControls();
  }
};
for (const type of ["input", "change"])
  document.addEventListener(type, (event) => {
    if (event.target.form?.id === "settings-form") updateScheduleControls();
  });

let answerOffset = 0,
  answerRequest = 0,
  answerTimer;
async function loadSavedAnswers(reset = false, focus = false) {
  if (!state) {
    await (refreshing || refresh());
    if (!state) return;
  }
  if (reset) answerOffset = 0;
  const request = ++answerRequest;
  const parent = $("#saved-answers");
  parent.setAttribute("aria-busy", "true");
  try {
    const query = new URLSearchParams({
      search: $("#answer-search").value,
      offset: answerOffset,
    });
    const result = await api(`/api/saved-answers?${query}`);
    if (request !== answerRequest) return;
    answerOffset = result.offset;
    const focusedAnswer=document.activeElement?.closest("details[data-answer-id]")?.dataset.answerId;
    for (const details of parent.querySelectorAll("details[data-answer-id]")) {
      const key=`saved-answer:${details.dataset.answerId}`;
      if(details.open)expandedEvidence.add(key);else expandedEvidence.delete(key);
    }
    parent.replaceChildren();
    if (!result.answers.length)
      empty(
        parent,
        $("#answer-search").value.trim()
          ? "No saved answers match your search."
          : "No exact answers saved yet. Questions that need your input appear in Needs you.",
      );
    for (const answer of result.answers) {
      const details = el("details", undefined, "saved-template");
      details.dataset.answerId=answer.id;
      const expansionKey=`saved-answer:${answer.id}`;
      details.ontoggle=()=>{if(details.open)expandedEvidence.add(expansionKey);else expandedEvidence.delete(expansionKey);};
      details.open=expandedEvidence.has(expansionKey);
      details.append(
        el("summary", answer.question),
        el(
          "p",
          answer.host.includes("|")
            ? `Employer: ${answer.host.split("|").slice(1).join("|")} · ${answer.host.split("|")[0]}`
            : `Form host: ${answer.host}`,
          "help",
        ),
        el("p", answer.value, "approved-body"),
      );
      if (answer.fact_key)
        details.append(
          el(
            "p",
            `Linked to confirmed fact: ${state.fact_labels[answer.fact_key] || answer.fact_key}`,
            "help",
          ),
        );
      const revoke = el("button", "Stop reusing this answer", "secondary");
      revoke.type = "button";
      revoke.disabled = state.demo;
      revoke.onclick = async () => {
        revoke.disabled = true;
        try {
          await api("/api/answer-revoke", { id: answer.id });
          await loadSavedAnswers(false, true);
          await refresh();
          note(
            "Saved answer withdrawn. The worker will ask again if confirmed sources cannot answer the question. Past application evidence stays recorded.",
          );
        } catch (error) {
          note(error.message, true);
          revoke.disabled = state.demo;
        }
      };
      details.append(revoke);
      parent.append(details);
    }
    $("#answers-page").textContent = result.total
      ? `${result.offset + 1}–${result.offset + result.answers.length} of ${result.total} saved answers`
      : "0 saved answers";
    $("#answers-previous").disabled = result.offset === 0;
    $("#answers-next").disabled =
      result.offset + result.answers.length >= result.total;
    if(focusedAnswer && !focus) {
      const detail=[...parent.querySelectorAll("details[data-answer-id]")].find(item=>item.dataset.answerId===focusedAnswer);
      detail?.querySelector("summary")?.focus({preventScroll:true});
    }
    if (focus) {
      parent.tabIndex = -1;
      parent.focus({ preventScroll: true });
    }
  } catch (error) {
    if (request === answerRequest) note(error.message, true);
  } finally {
    if (request === answerRequest) parent.removeAttribute("aria-busy");
  }
}
$("#saved-answers-panel").ontoggle = () => {
  if ($("#saved-answers-panel").open) loadSavedAnswers();
};
$("#answer-search").oninput = () => {
  clearTimeout(answerTimer);
  answerRequest++;
  answerTimer = setTimeout(() => loadSavedAnswers(true), 200);
};
$("#answers-previous").onclick = () => {
  answerOffset = Math.max(0, answerOffset - 25);
  loadSavedAnswers(false, true);
};
$("#answers-next").onclick = () => {
  answerOffset += 25;
  loadSavedAnswers(false, true);
};

$("#template-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/template", Object.fromEntries(new FormData(e.target)));
    e.target.reset();
    note("Approved wording saved.");
    await refresh();
  } catch (e) {
    note(e.message, true);
  }
};

$("#transcript-upload").onchange = async (e) => {
  const input = e.target,
    f = input.files[0];
  if (!f) return;
  input.disabled = true;
  updateSelectedDownloads();
  try {
    const result = await api("/api/transcript", f, true);
    note(
      result.repaired
        ? "Stored transcript PDF repaired from your matching upload and selected for future uploads."
        : "Transcript saved. Matching applications can upload it automatically in the next cycle.",
    );
    await refresh();
  } catch (error) {
    note(error.message, true);
  } finally {
    input.disabled = false;
    input.value = "";
    updateSelectedDownloads();
  }
};

function updateTranscriptWithdrawal() {
  const present = state.documents.some(
    (document) => document.kind === "transcript",
  );
  $("#withdraw-transcript").hidden = !present;
  $("#transcript-withdraw-help").hidden = !present;
  $("#withdraw-transcript").disabled =
    state.demo || state.worker_running || transcriptWithdrawalBusy;
}
$("#withdraw-transcript").onclick = async () => {
  transcriptWithdrawalBusy = true;
  updateTranscriptWithdrawal();
  try {
    await api("/api/transcript-withdraw", {});
    await refresh();
    note(
      "Transcript withdrawn from future uploads. Past application evidence stays stored privately.",
    );
    $("#transcript-upload").focus();
  } catch (error) {
    note(error.message, true);
  } finally {
    transcriptWithdrawalBusy = false;
    updateTranscriptWithdrawal();
  }
};

function materialDrafts() {
  return [...$("#material-list").querySelectorAll("form")].some((form) =>
    dirtyForms.has(form),
  );
}
function materialStateUrl(
  offset,
  search = materialSearch,
  status = materialStatus,
) {
  const query = new URLSearchParams({ material_offset: offset });
  if (search) query.set("material_search", search);
  if (status !== "all") query.set("material_status", status);
  return "/api/state?" + query;
}
$("#material-filter-form").onsubmit = (event) => {
  event.preventDefault();
  return changeMaterialPage(
    0,
    $("#material-search").value,
    $("#material-filter").value,
  );
};
$("#clear-material-filter").onclick = async () => {
  if (await changeMaterialPage(0, "", "all")) {
    $("#material-search").value = "";
    $("#material-filter").value = "all";
  }
};
async function changeMaterialPage(
  offset,
  search = materialSearch,
  status = materialStatus,
) {
  const originView = view,
    originControl = document.activeElement;
  if (refreshing) await refreshing;
  if (materialPagingBusy) return;
  if (materialDrafts())
    return note(
      "Save your source edits or choose Discard excerpt edits before changing pages or filters.",
      true,
    );
  const list = $("#material-list"),
    controls = [
      ...document.querySelectorAll(
        "#material-list input,#material-list textarea,#material-list select,#material-list button,#material-filter-form input,#material-filter-form select,#material-filter-form button",
      ),
    ].map((control) => [control, control.disabled]);
  materialPagingBusy = true;
  controls.forEach(([control]) => (control.disabled = true));
  list.setAttribute("aria-busy", "true");
  try {
    const result = await api(materialStateUrl(offset, search, status));
    state = result;
    materialOffset = result.material_offset;
    materialSearch = result.material_search;
    materialStatus = result.material_status;
    controls.forEach(([control, disabled]) => (control.disabled = disabled));
    materialPagingBusy = false;
    const moveFocus =
      originView === "materials" &&
      view === "materials" &&
      (document.activeElement === document.body ||
        list.contains(document.activeElement) ||
        $("#material-filter-form").contains(document.activeElement));
    if (list.contains(document.activeElement)) document.activeElement.blur();
    render();
    if (moveFocus) {
      list.focus({ preventScroll: true });
      list.scrollIntoView({ block: "start", behavior: "instant" });
    }
    return true;
  } catch (error) {
    note(
      `Could not change source pages. ${error.message} Try your search or page button again.`,
      true,
    );
    controls.forEach(([control, disabled]) => (control.disabled = disabled));
    if (
      originView === view &&
      document.activeElement === document.body &&
      originControl?.isConnected &&
      !originControl.disabled
    )
      originControl.focus({ preventScroll: true });
  } finally {
    materialPagingBusy = false;
    list.removeAttribute("aria-busy");
    releaseDeferredRefresh();
  }
}
function updateMaterialWarning(box, source) {
  const warning = box.querySelector(".source-file-warning");
  if (source.original_available !== false) {
    warning?.remove();
    return;
  }
  if (warning) return;
  const message = el(
    "p",
    "The original file is missing, unreadable or has permissions that need repair. Re-upload the matching original with the same document purpose to repair it. Your reviewed excerpt is still saved.",
    "source-file-warning",
  );
  box.insertBefore(message, box.querySelector("form"));
}
function renderMaterials() {
  if (materialPagingBusy) return;
  materialOffset = state.material_offset;
  const filterSummary = `${state.material_count} matching source${state.material_count === 1 ? "" : "s"} of ${state.material_total} in your library${materialSearch ? ` · Search: ${materialSearch}` : ""}${materialStatus !== "all" ? ` · ${$("#material-filter option[value=" + materialStatus + "]").textContent}` : ""}.`;
  if ($("#material-filter-status").textContent !== filterSummary)
    $("#material-filter-status").textContent = filterSummary;
  const list = $("#material-list");
  // File health can change without replacing an excerpt someone is editing.
  const currentSources = new Map(
    state.materials.map((source) => [String(source.id), source]),
  );
  for (const box of list.querySelectorAll("article[data-source-id]")) {
    const source = currentSources.get(box.dataset.sourceId);
    if (source) updateMaterialWarning(box, source);
  }
  if (
    list.contains(document.activeElement) ||
    [...list.querySelectorAll("form")].some((form) => dirtyForms.has(form))
  )
    return;
  const signature = JSON.stringify({
    offset: state.material_offset,
    count: state.material_count,
    total: state.material_total,
    search: materialSearch,
    status: materialStatus,
    sources: state.materials.map((source) => [
      source.id,
      source.revision,
      source.updated,
      source.original_name,
      source.kind,
      source.role,
      source.confirmed,
      source.original_available,
    ]),
  });
  if (signature === renderedMaterialsSignature) return;
  list.replaceChildren();
  if (state.material_count > 20) {
    const controls = el("div", undefined, "actions"),
      previous = el("button", "Newer sources", "secondary"),
      next = el("button", "Older sources", "secondary");
    previous.disabled = materialOffset === 0;
    next.disabled = materialOffset + 20 >= state.material_count;
    previous.onclick = () =>
      changeMaterialPage(Math.max(0, materialOffset - 20));
    next.onclick = () => changeMaterialPage(materialOffset + 20);
    controls.append(
      previous,
      el(
        "p",
        `${materialOffset + 1}–${Math.min(materialOffset + 20, state.material_count)} of ${state.material_count}`,
      ),
      next,
    );
    list.append(controls);
  }
  if (!state.materials.length) {
    empty(
      list,
      state.material_total
        ? "No sources match these filters. Try another search or choose Clear filters."
        : "Upload a writing sample, cover-letter example or supporting document to begin. Each source gets a review before the model uses it.",
    );
    renderedMaterialsSignature = signature;
    return;
  }
  for (const source of state.materials) {
    const box = el("article", undefined, "section");
    box.dataset.sourceId = source.id;
    box.append(
      el("h2", source.original_name),
      el(
        "p",
        `${source.kind.replaceAll("_", " ")} · ${source.confirmed ? "Approved" : "Needs review"} · ${date(source.updated)}`,
        "subtle",
      ),
    );
    updateMaterialWarning(box, source);
    const form = el("form", undefined, "material-review");
    const textLabel = el("label", "Reviewed excerpt");
    const text = el("textarea");
    text.rows = 8;
    text.required = true;
    text.minLength = 20;
    text.maxLength = 12000;
    text.value = source.text;
    textLabel.append(text);
    const roleLabel = el("label", "How the model can use this");
    const role = el("select");
    role.append(
      new Option(
        "Background reference — no claim that I did this work",
        "reference",
      ),
      new Option("My own factual work and experience", "personal"),
      new Option("Writing style and structure only", "style"),
    );
    role.value = source.role;
    roleLabel.append(role);
    const approvedLabel = el("label", undefined, "confirmation");
    const approved = el("input");
    approved.type = "checkbox";
    approved.checked = !!source.confirmed;
    approvedLabel.append(
      approved,
      document.createTextNode(
        "I reviewed this excerpt and approve its selected use. Uncheck to stop using it.",
      ),
    );
    const button = el("button", "Save reviewed source"),
      discard = el("button", "Discard excerpt edits", "secondary");
    discard.type = "button";
    discard.onclick = () => {
      text.value = source.text;
      role.value = source.role;
      approved.checked = !!source.confirmed;
      saved(form);
      text.focus();
      note(
        "Excerpt edits discarded. Saved approval and source text are unchanged.",
      );
    };
    form.append(
      textLabel,
      el(
        "p",
        "Keep up to 12,000 characters per reviewed excerpt. Example qualifications belong in style-only sources unless they describe your own work.",
        "help",
      ),
      roleLabel,
      approvedLabel,
      button,
      discard,
    );
    form.onsubmit = async (event) => {
      event.preventDefault();
      button.disabled = true;
      try {
        const result = await api("/api/material-review", {
          id: source.id,
          text: text.value,
          role: role.value,
          confirmed: approved.checked,
        });
        saved(form);
        blurForm(form);
        await refresh();
        note(
          result.changed === false
            ? "Source unchanged. Its approval and drafts are preserved."
            : `Source saved.${result.drafts_removed ? ` ${result.drafts_removed} unattempted draft${result.drafts_removed === 1 ? " was" : "s were"} discarded for a fresh source check.` : ""} Recorded application evidence stays available.`,
        );
      } catch (error) {
        note(error.message, true);
      } finally {
        button.disabled = false;
      }
    };
    box.append(form);
    list.append(box);
  }
  renderedMaterialsSignature = signature;
}
$("#material-upload-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target,
    file = form.elements.file.files[0],
    button = form.querySelector("button");
  if (!file) return;
  if (file.size > 20 * 1024 * 1024)
    return note("Choose a file up to 20 MiB.", true);
  button.disabled = true;
  form.elements.file.disabled = true;
  form.elements.kind.disabled = true;
  try {
    const result = await api("/api/material-upload", file, true, {
      "X-Upload-Name": encodeURIComponent(file.name),
      "X-Material-Kind": form.elements.kind.value,
    });
    form.reset();
    await refresh();
    note(
      result.repaired
        ? "Original source file restored from your matching upload. Existing excerpt reviews and application evidence are preserved."
        : result.existing
          ? "This source is already saved. Its reviewed excerpt and approval are unchanged."
          : "Source uploaded. Review the excerpt and choose how the model can use it.",
    );
  } catch (error) {
    note(error.message, true);
  } finally {
    button.disabled = false;
    form.elements.file.disabled = false;
    form.elements.kind.disabled = false;
  }
};

function renderMail() {
  const mail = state.gmail;
  $("#gmail-state").textContent = mail.connected
    ? `Authorization saved for ${mail.email}.`
    : mail.client_configured
      ? "OAuth client saved. Run the connection command below to authorize Gmail."
      : "No Gmail authorization saved.";
  const form = $("#mail-settings-form");
  if (!editing("#mail-settings-form"))
    for (const input of form.elements) {
      if (input.name) input.checked = !!state.settings[input.name];
    }
  const reports = $("#report-delivery");
  reports.replaceChildren();
  if (!state.reports.length)
    empty(
      reports,
      "Batch reports appear here after the worker runs. Email delivery is optional.",
    );
  for (const report of state.reports)
    reports.append(
      el(
        "p",
        `${date(report.created)} · ${report.state}${report.last_error ? " · " + report.last_error : ""}`,
      ),
    );
}
$("#gmail-client-upload").onchange = async (event) => {
  const input = event.target,
    file = input.files[0];
  if (!file) return;
  if (file.size > 65536) {
    input.value = "";
    return note("Choose the Google OAuth client JSON, up to 64 KiB.", true);
  }
  input.disabled = true;
  try {
    await api("/api/gmail-client", file, true);
    await refresh();
    note(
      "OAuth client stored privately. Run the connection command to authorize Gmail.",
    );
  } catch (error) {
    note(error.message, true);
  } finally {
    input.disabled = false;
    input.value = "";
  }
};
$("#mail-settings-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    await api("/api/settings", {
      gmail_reports: form.elements.gmail_reports.checked,
      gmail_verification: form.elements.gmail_verification.checked,
    });
    saved(form);
    blurForm(form);
    await refresh();
    note("Email preferences saved.");
  } catch (error) {
    note(error.message, true);
  }
};
$("#flush-reports").onclick = async (event) => {
  const button = event.target;
  button.disabled = true;
  try {
    const result = await api("/api/reports/flush", {});
    await refresh();
    note(
      result.error
        ? `Reports remain queued: ${result.error}`
        : result.batches > 1
          ? `One email sent covering ${result.batches} batches.`
          : `${result.sent} report(s) sent.`,
      !!result.error,
    );
  } catch (error) {
    note(error.message, true);
  } finally {
    button.disabled = false;
  }
};

const setupSteps = [
  [
    "Documents",
    "profile",
    "Import your required resume and optional transcript. Cover-letter examples go in Writing & context.",
  ],
  [
    "Key information",
    "profile",
    "Confirm the facts extracted from your resume. Add authorization and availability yourself.",
  ],
  [
    "Additional context",
    "materials",
    "Add your own experience or supporting research; choose the correct approved use.",
  ],
  [
    "Writing samples",
    "materials",
    "Upload essays or cover-letter examples. Style sources guide voice, not qualifications.",
  ],
  [
    "Job preferences",
    "settings",
    "Choose roles, locations, seniority and exclusions. Save preferences before continuing.",
  ],
  [
    "Schedule and limits",
    "settings",
    "Choose batch frequency, submission ceilings, attempt ceilings and model request caps. Save preferences.",
  ],
  [
    "Model and run location",
    "providers",
    "Connect a CLI subscription or a paid API key. Finish paused or start batches.",
  ],
];
let setupStep = -1;

function updateDiagnosticsControls() {
  $("#check-setup").disabled = diagnosticsBusy;
  $("#check-setup").textContent = diagnosticsBusy
    ? "Checking setup…"
    : "Check local setup";
  $("#download-setup-report").disabled = diagnosticsBusy || !diagnosticsResult;
}
$("#check-setup").onclick = async () => {
  if (diagnosticsBusy) return;
  diagnosticsBusy = true;
  $("#setup-check-error").hidden = true;
  updateDiagnosticsControls();
  try {
    const result = await api("/api/diagnostics");
    diagnosticsResult = result;
    const list = $("#setup-check-results");
    list.replaceChildren();
    for (const item of result.checks) {
      const row = el("div", undefined, "diagnostic-check");
      row.append(
        el("h3", `${item.ready ? "Ready" : "Needs attention"} · ${item.name}`),
        el("p", item.detail),
      );
      if (item.action) row.append(el("p", item.action, "help"));
      const route = {
        "Model connection": "providers",
        "Applicant setup": "profile",
        "Worker history": "today",
      }[item.name];
      if (!item.ready && route) {
        const button = el(
          "button",
          "Review " + item.name.toLowerCase(),
          "secondary",
        );
        button.onclick = () => show(route);
        row.append(button);
      }
      list.append(row);
    }
    $("#setup-check-status").textContent =
      `${result.demo ? "Sample workspace · " : ""}Checked ${new Date(result.checked_at).toLocaleString()}. This is a snapshot of your saved setup. ${result.note}`;
  } catch (error) {
    $("#setup-check-error").textContent =
      `Setup check could not finish. ${error.message} Try Check local setup again. Any previous report keeps its original check time.`;
    $("#setup-check-error").hidden = false;
  } finally {
    diagnosticsBusy = false;
    updateDiagnosticsControls();
  }
};
$("#download-setup-report").onclick = () => {
  if (!diagnosticsResult || diagnosticsBusy) return;
  const url = URL.createObjectURL(
    new Blob([diagnosticsResult.report], { type: "text/plain;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "application-desk-setup.txt";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

function renderSetup() {
  const checks = state.readiness,
    parent = $("#machine-checks");
  parent.replaceChildren();
  parent.append(
    el("h2", "This machine"),
    el(
      "p",
      `${checks.platform} · ${checks.architecture} · Python ${checks.python}`,
    ),
    el(
      "p",
      `Browser: ${checks.browser_ready ? "available" : "missing — run setup.sh or install Chromium"} · Model: ${checks.provider.message}`,
    ),
  );
  const list = $("#setup-steps");
  list.replaceChildren();
  const completed = [
    state.documents.some((d) => d.kind === "resume" && d.available !== false),
    state.missing_setup.filter((k) => k !== "resume").length === 0,
    state.materials.some((m) => m.confirmed && m.role === "personal"),
    state.materials.some((m) => m.confirmed && m.role === "style"),
    state.settings.onboarding_complete,
    state.settings.onboarding_complete,
    checks.provider.ready,
  ];
  $("#setup-progress-text").textContent =
    `${completed.filter(Boolean).length} of ${setupSteps.length} steps ready`;
  setupSteps.forEach(([title, route, description], index) => {
    const optional = index === 2 || index === 3,
      row = el("div", undefined, "setup-step" + (optional ? " optional" : ""));
    const number = el(
      "span",
      completed[index] ? "✓" : String(index + 1),
      "step-number" + (completed[index] ? " complete" : ""),
    );
    number.setAttribute("aria-hidden", "true");
    const content = el("div"),
      button = el("button", title + (optional ? " · optional" : ""));
    button.type = "button";
    button.onclick = () => goSetup(index);
    if (completed[index]) button.setAttribute("aria-label", title + " · ready");
    content.append(button, el("p", description, "help"));
    row.append(number, content);
    list.append(row);
  });
  const f = $("#provider-form");
  if (!editing("#provider-form"))
    for (const input of f.elements) {
      if (input.name && input.name !== "key")
        if (input.type === "checkbox") input.checked = state.settings[input.name];
        else input.value = state.settings[input.name];
    }
  updateProviderFields();
  $("#provider-state").textContent = checks.provider.message;
  const used = state.summary?.model_requests_today ?? 0,
    cap = state.settings.max_model_requests_per_day,
    usage = `${used} of ${cap} requests used today · ${Math.max(0, cap - used)} remaining${used >= cap ? " · Daily cap reached" : ""}`;
  if ($("#model-request-usage").textContent !== usage)
    $("#model-request-usage").textContent = usage;
  $("#model-request-reset").textContent =
    `Resets at midnight in ${state.settings.timezone}. Per-batch cap: ${state.settings.max_model_requests_per_cycle} requests.`;
  const tokenUsage = $("#model-token-usage"),
    usageRows = state.summary?.model_usage ?? [],
    tokenSignature = JSON.stringify(usageRows);
  if (tokenUsage.dataset.signature !== tokenSignature) {
    tokenUsage.replaceChildren();
    const names = {"claude-cli": "Claude subscription", "codex-cli": "Codex subscription", "anthropic-api": "Anthropic API", "openai-api": "OpenAI API"};
    for (const row of usageRows) {
      const item = document.createElement("li"),
        counts = Object.entries(row.tokens).map(([key, value]) => `${Number(value).toLocaleString()} ${key.replaceAll("_", " ")}`).join(" · ");
      item.textContent = `${names[row.provider] ?? row.provider}${row.model ? ` / ${row.model}` : " / model unavailable"}: ${row.requests} requests · ${row.successful} successful · ${row.failed} failed${row.outcome_unavailable ? ` · ${row.outcome_unavailable} outcomes unavailable` : ""}. ${counts || "Token counts unavailable"}${row.usage_unavailable ? ` · ${row.usage_unavailable} requests with incomplete token counts` : ""}.`;
      tokenUsage.append(item);
    }
    if (!usageRows.length) {
      const item = document.createElement("li");
      item.textContent = "No model requests recorded today.";
      tokenUsage.append(item);
    }
    tokenUsage.dataset.signature = tokenSignature;
  }
  $("#provider-instructions").textContent =
    state.settings.provider === "claude-cli"
      ? "claude auth login"
      : state.settings.provider === "codex-cli"
        ? "codex login"
        : "Use your vendor’s console to create a key and select an accessible model ID.";
  $("#finish-paused").disabled = state.missing_setup.length > 0;
  $("#finish-start").disabled = state.missing_setup.length > 0;
}
function goSetup(index) {
  setupStep = index;
  const [title, route, description] = setupSteps[index];
  $("#guided-setup").hidden = false;
  $("#guided-progress").value = index + 1;
  $("#guided-progress-text").textContent =
    `${index + 1} / ${setupSteps.length}`;
  $("#guided-label").textContent =
    `Step ${index + 1} of ${setupSteps.length} · ${title}. ${description}`;
  $("#setup-back").disabled = index === 0;
  $("#setup-next").hidden = index === setupSteps.length - 1;
  show(route);
  $("#guided-setup").scrollIntoView({ block: "start" });
}
$("#begin-setup").onclick = () => goSetup(0);
$("#setup-back").onclick = () => goSetup(Math.max(0, setupStep - 1));
$("#setup-next").onclick = () => {
  if (
    setupStep === 0 &&
    !state.documents.some((d) => d.kind === "resume" && d.available !== false)
  )
    return note("Import a resume before continuing.", true);
  if (setupStep === 1 && state.missing_setup.length)
    return note(
      "Confirm the required facts before continuing: " +
        state.missing_setup.join(", "),
      true,
    );
  goSetup(Math.min(setupSteps.length - 1, setupStep + 1));
};
$("#setup-exit").onclick = () => {
  $("#guided-setup").hidden = true;
  setupStep = -1;
  show("today");
};
$("#provider-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target;
  const provider = form.elements.provider.value;
  try {
    const model = form.elements.provider_model.value.trim();
    if (provider.endsWith("-api") && !model)
      throw new Error("Enter an exact model ID for API mode.");
    if (model && !/^[A-Za-z0-9._:/-]{1,100}$/.test(model))
      throw new Error(
        "Use a model ID of up to 100 letters, numbers, dots, underscores, colons, slashes, or hyphens.",
      );
    if (provider.endsWith("-api") && form.elements.key.value) {
      await api("/api/provider-key", {
        provider,
        key: form.elements.key.value,
      });
      form.elements.key.value = "";
    }
    await api("/api/settings", {
      provider,
      provider_model: model,
      model_effort: form.elements.model_effort.value,
      model_escalation: form.elements.model_escalation.checked,
      deployment: form.elements.deployment.value,
    });
    saved(form);
    blurForm(form);
    await refresh();
    note("Connection saved. Check login before starting.");
  } catch (error) {
    note(error.message, true);
  }
};
$("#check-provider").onclick = async (event) => {
  event.target.disabled = true;
  try {
    const result = await api("/api/provider-check", {});
    $("#provider-state").textContent = result.provider.message;
    note(result.provider.message, !result.provider.ready);
  } catch (error) {
    note(error.message, true);
  } finally {
    event.target.disabled = false;
  }
};
$("#remove-provider-key").onclick = async () => {
  try {
    await api("/api/remove-provider-key", {});
    await refresh();
    note("Saved API key removed.");
  } catch (error) {
    note(error.message, true);
  }
};
async function finishSetup(start) {
  try {
    const result = await api("/api/complete-setup", { start });
    $("#guided-setup").hidden = true;
    setupStep = -1;
    await refresh();
    show("today");
    note(result.message);
  } catch (error) {
    note(error.message, true);
  }
}
$("#finish-paused").onclick = () => finishSetup(false);
$("#finish-start").onclick = () => finishSetup(true);

$("#context-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api(
      "/api/context-text",
      Object.fromEntries(new FormData(event.target)),
    );
    event.target.reset();
    await refresh();
    note("Approved context saved.");
  } catch (error) {
    note(error.message, true);
  }
};

let contextDraft = null;
function renderContextNeeds() {
  const box = $("#context-needs"),
    needs = state.context_needs || [];
  box.replaceChildren();
  if (!needs.length) {
    box.append(el("p", "No held application is waiting on missing information right now.", "help"));
    return;
  }
  box.append(el("p", "Held applications are still asking about:", "help"));
  const list = el("ul");
  for (const need of needs)
    list.append(el("li", `${shortLabel(need.label)} (${need.jobs} application${need.jobs === 1 ? "" : "s"})`));
  box.append(list);
}
function renderContextReview() {
  const form = $("#context-review-form"),
    draft = contextDraft;
  form.hidden = !draft;
  if (!draft) return;
  const facts = $("#context-review-facts");
  facts.replaceChildren();
  if (draft.facts.length) {
    facts.append(el("p", "Facts to update", "context-review-label"));
    for (const fact of draft.facts) {
      const label = el("label", undefined, "confirmation"),
        box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.dataset.key = fact.key;
      box.dataset.value = fact.value;
      label.append(box, ` ${fact.label}: ${fact.value}` + (fact.current ? ` (currently ${fact.current})` : ""));
      facts.append(label);
    }
  }
  form.elements.notes.value = draft.notes.join("\n");
  const unclear = $("#context-review-unclear");
  unclear.replaceChildren();
  if (draft.unclear.length) {
    unclear.append(el("p", "Claude could not place these. Add detail and organize again:"));
    const list = el("ul");
    for (const item of draft.unclear) list.append(el("li", item));
    unclear.append(list);
  }
  $("#context-review-covers").textContent = draft.covers.length
    ? "This should help with: " + draft.covers.join("; ")
    : draft.facts.length || draft.notes.length
      ? "This does not answer a currently held question, but future applications can use it."
      : "Nothing here could be organized. Try adding more detail.";
}
$("#context-inbox-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target,
    button = form.querySelector("button[type=submit]");
  button.disabled = true;
  button.textContent = "Organizing…";
  try {
    contextDraft = await api("/api/context/organize", { text: form.elements.text.value });
    renderContextReview();
  } catch (error) {
    note(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Organize with Claude";
  }
};
$("#context-review-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target,
    facts = Object.fromEntries(
      Array.from(form.querySelectorAll("#context-review-facts input:checked")).map((box) => [box.dataset.key, box.dataset.value]),
    );
  try {
    const result = await api("/api/context/apply", {
      facts,
      notes: form.elements.notes.value,
      revision: Number($("#basic-context-form").dataset.revision || 0),
      confirmed: form.elements.confirmed.checked,
    });
    contextDraft = null;
    form.reset();
    $("#context-inbox-form").reset();
    renderContextReview();
    await refresh();
    note(`Saved ${result.facts.length} fact${result.facts.length === 1 ? "" : "s"} and ${result.notes} note${result.notes === 1 ? "" : "s"}. Held applications are checked again on the next run.`);
  } catch (error) {
    note(error.message, true);
  }
};
$("#context-review-discard").onclick = () => {
  contextDraft = null;
  $("#context-review-form").reset();
  renderContextReview();
};

$("#basic-context-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    await api("/api/basic-context", {
      text: form.elements.text.value,
      revision: Number(form.dataset.revision || 0),
      confirmed: form.elements.confirmed.checked,
    });
    form.elements.confirmed.checked = false;
    blurForm(form);
    await refresh();
    note("Basic context saved. Application answers can use your approved text.");
  } catch (error) {
    note(error.message, true);
  }
};

function organizePreferences() {
  const grid = $("#settings-form > .form-grid");
  const sections = [
    [
      "Where and what you’re looking for",
      "Choose the roles and places that fit your next step.",
      [
        "roles",
        "seniority",
        "locations",
        "summer_2027_locations",
        "school_locations",
        "min_annual_usd",
        "min_hourly_usd",
        "max_years_required",
        "min_fit_score",
      ],
    ],
    [
      "Companies and boundaries",
      "Keep your search focused and avoid applications you don’t want.",
      [
        "skip_companies",
        "interview_companies",
        "prior_employers",
        "max_per_company",
        "company_cooldown_days",
        "company_aliases",
      ],
    ],
    [
      "Your pace",
      "Decide how often your desk works and how many applications it can submit. Targets are goals; ceilings are firm limits.",
      [
        "schedule_hours",
        "cycle_timeout_seconds",
        "timezone",
        "target_per_cycle",
        "max_per_cycle",
        "target_per_day",
        "max_per_day",
      ],
    ],
    [
      "Model usage limits",
      "Limit the number of requests made to your chosen model. These counts include failed requests and writing reviews.",
      [
        "max_attempts_per_cycle",
        "max_model_requests_per_cycle",
        "max_model_requests_per_day",
        "max_output_tokens",
      ],
    ],
    [
      "Advanced browser settings",
      "Change these only if you need a particular browser or already use signed-in employer portals.",
      ["browser_channel", "signed_in_portals"],
    ],
  ];
  for (const [title, description, keys] of sections) {
    const group = el("fieldset", undefined, "settings-group"),
      fields = el("div", undefined, "form-grid");
    group.append(
      el("legend", title),
      el("p", description, "help settings-description"),
    );
    for (const key of keys) {
      const input = grid.querySelector(`[name="${key}"]`);
      if (input)
        fields.append(
          input.closest("[data-preference-field]") || input.closest("label"),
        );
    }
    group.append(fields);
    grid.before(group);
  }
  grid.remove();
}
organizePreferences();

// Prevent duplicate requests while a form or immediate worker action is pending.
document.addEventListener(
  "submit",
  (event) => {
    const form = event.target;
    if (form.dataset.saving === "true") {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (!form.onsubmit) return;
    const handler = form.onsubmit;
    const origin = document.activeElement;
    const originalInert = form.inert;
    const originalBusy = form.getAttribute("aria-busy");
    const originView = view;
    const controls = [...form.elements].filter(
      (control) => "disabled" in control,
    );
    const previous = controls.map((control) => control.disabled);
    const progress = el("p", "Working…", "help form-request-status");
    progress.setAttribute("role", "status");
    form.onsubmit = null;
    event.preventDefault();
    form.dataset.saving = "true";
    form.setAttribute("aria-busy", "true");
    form.after(progress);
    // Let the handler capture FormData before disabling controls. Inert also
    // prevents background readiness rendering from reopening a field mid-save.
    let request;
    try {
      request = handler.call(form, event);
    } catch (error) {
      request = Promise.reject(error);
    }
    form.inert = true;
    controls.forEach((control) => (control.disabled = true));
    Promise.resolve(request)
      .catch((error) => note(error.message, true))
      .finally(() => {
        controls.forEach(
          (control, index) => (control.disabled = previous[index]),
        );
        form.inert = originalInert;
        if (originalBusy === null) form.removeAttribute("aria-busy");
        else form.setAttribute("aria-busy", originalBusy);
        progress.remove();
        delete form.dataset.saving;
        form.onsubmit = handler;
        if (state) render();
        if (originView === view && document.activeElement === document.body) {
          let target = origin?.isConnected
            ? origin
            : origin?.name
              ? form.elements.namedItem(origin.name)
              : null;
          if (
            !target?.isConnected ||
            target.disabled ||
            !target.getClientRects().length
          )
            target = $("#heading");
          if (target === $("#heading")) target.tabIndex = -1;
          target.focus({ preventScroll: true });
        }
      });
  },
  true,
);
for (const id of [
  "pause",
  "run",
  "discover",
  "complete-setup",
  "finish-paused",
  "finish-start",
]) {
  const button = $("#" + id),
    handler = button.onclick;
  button.onclick = async (event) => {
    if (button.dataset.busy === "true") return;
    button.dataset.busy = "true";
    button.disabled = true;
    try {
      await handler.call(button, event);
    } finally {
      delete button.dataset.busy;
      if (state) render();
    }
  };
}

$("#export-ledger").onclick = async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/export.csv", {
      headers: { "X-Hireme-Token": token },
    });
    if (!response.ok)
      throw new Error(
        "Could not export the ledger. Reconnect to your dashboard and try again.",
      );
    const url = URL.createObjectURL(await response.blob()),
      anchor = el("a");
    anchor.href = url;
    anchor.download = "application-ledger.csv";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    note("Your complete application ledger was exported.");
  } catch (error) {
    note(error.message, true);
  } finally {
    button.disabled = false;
  }
};

// Make an unsaved draft visible without placing personal text in browser storage.
document.addEventListener("input", (event) => {
  const form = event.target.form;
  if (form && form.id) {
    const indicator = document.querySelector(`[data-draft-for="${form.id}"]`);
    if (indicator) indicator.textContent = "Unsaved changes";
  }
});

let opportunityOrigin = null;
function openOpportunity(job) {
  opportunityOrigin = document.activeElement;
  const payload = jobPayload(job),
    dialog = $("#job-dialog");
  dialog.dataset.jobId = job.id;
  opportunityNotes.prepare(job.id);
  postingCheckRequest++;
  $("#check-saved-posting").disabled = false;
  $("#check-saved-posting").textContent = "Check saved posting";
  $("#posting-check-result").hidden = true;
  $("#posting-check-result").replaceChildren();
  dialog.dataset.companySkipped = String(job.company_skipped);
  const unsavedPreferences = dirtyForms.has($("#settings-form"));
  $("#skip-job-company").disabled = state.demo || unsavedPreferences;
  $("#skip-job-company").textContent = job.company_skipped
    ? "Include this company again"
    : "Skip this company";
  $("#skip-company-help").removeAttribute("role");
  $("#skip-company-help").textContent = state.demo
    ? "Company boundaries can be changed in your own workspace."
    : unsavedPreferences
      ? "Save your pending preferences before changing company boundaries."
      : job.company_skipped
        ? "Removes this company and its current aliases from exclusions. The next batch will reevaluate unattempted opportunities; limits and uncertain outcomes still apply."
        : "Stops future applications at this company and its configured aliases. Past attempts and uncertain outcomes stay recorded.";
  $("#job-dialog-title").textContent = job.title;
  $("#job-dialog-company").textContent = job.company;
  $("#job-dialog-location").textContent =
    payload.location || "Not stated in the posting";
  $("#job-dialog-fit").textContent =
    job.score > 0 ? `${job.score} / 100` : "Not evaluated yet";
  $("#job-dialog-discovered").textContent = date(job.first_seen);
  const status = $("#job-dialog-state");
  status.replaceChildren(
    el(
      "span",
      job.status_label || statusLabels[job.status] || job.status,
      "state " + (job.display_status || job.status),
    ),
  );
  const guidance = $("#job-dialog-guidance");
  guidance.replaceChildren();
  guidance.hidden = !job.reason;
  if (job.reason)
    guidance.append(
      el("h3", job.reason_label || job.reason),
      el(
        "p",
        job.next_step || "Review the official posting and recorded details.",
      ),
    );
  const description =
    payload.description ||
    "No posting text has been saved yet. Open the official posting for the full role description.";
  $("#job-dialog-description").textContent =
    description.slice(0, 20000) +
    (description.length > 20000
      ? "\n\nThis excerpt is shortened. Open the official posting for the complete description."
      : "");
  $("#job-dialog-reason").textContent = job.reason || "";
  $("#job-dialog-diagnostics").hidden = !job.reason;
  const official = $("#job-dialog-link");
  official.hidden = state.demo || !/^https:\/\//.test(job.url);
  if (!official.hidden) official.href = job.url;
  else official.removeAttribute("href");
  $("#job-dialog-demo").hidden = !state.demo;
  window.connectedWorkspace?.openJob(job);
  show("opportunities");
  if (matchMedia("(max-width: 900px)").matches) {
    document.body.classList.add("dialog-open");
    dialog.showModal();
  } else {
    if (!dialog.open) dialog.show();
  }
}
$("#check-saved-posting").onclick = async () => {
  const dialog = $("#job-dialog"),
    id = dialog.dataset.jobId,
    request = ++postingCheckRequest;
  const button = $("#check-saved-posting"),
    result = $("#posting-check-result");
  button.disabled = true;
  button.textContent = "Checking saved posting…";
  result.hidden = true;
  try {
    const check = await api("/api/posting-check/" + encodeURIComponent(id));
    if (
      request !== postingCheckRequest ||
      dialog.dataset.jobId !== id ||
      !dialog.open
    )
      return;
    result.replaceChildren(el("strong", check.label), el("p", check.detail));
    if (check.score !== null && check.score !== undefined)
      result.append(
        el("p", `Current saved-posting fit: ${check.score}/100`, "help"),
      );
    result.hidden = false;
  } catch (error) {
    if (
      request !== postingCheckRequest ||
      dialog.dataset.jobId !== id ||
      !dialog.open
    )
      return;
    result.replaceChildren(
      el("strong", "Could not check this posting"),
      el("p", error.message),
    );
    result.hidden = false;
  } finally {
    if (request === postingCheckRequest) {
      button.disabled = false;
      button.textContent = "Check saved posting";
    }
  }
};
$("#close-job-dialog").onclick = () => $("#job-dialog").close();
$("#skip-job-company").onclick = async (event) => {
  event.target.disabled = true;
  try {
    const result = await api(
      $("#job-dialog").dataset.companySkipped === "true"
        ? "/api/company-allow"
        : "/api/company-skip",
      {
        id: $("#job-dialog").dataset.jobId,
      },
    );
    await refresh();
    $("#job-dialog").close();
    $("#heading").tabIndex = -1;
    $("#heading").focus({ preventScroll: true });
    note(result.message);
  } catch (error) {
    $("#skip-company-help").textContent = error.message;
    $("#skip-company-help").setAttribute("role", "alert");
    note(error.message, true);
    event.target.disabled = false;
  }
};
$("#job-dialog").addEventListener("click", (event) => {
  if (event.target !== event.currentTarget) return;
  const bounds = event.currentTarget.getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    event.currentTarget.close();
});

$("#job-dialog").addEventListener("close", () => {
  document.body.classList.toggle("dialog-open", $("#tool-dialog").open);
  if(opportunityOrigin?.isConnected && opportunityOrigin.getClientRects().length) opportunityOrigin.focus({preventScroll:true});
});

let backupCheckBusy = false;
$("#backup-check-file").onchange = () => {
  $("#backup-check-result").hidden = true;
  $("#backup-check-status").textContent = "";
};
$("#backup-check-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state || backupCheckBusy || state.demo) return;
  const file = $("#backup-check-file").files[0];
  const status = $("#backup-check-status");
  const feedback = (text, error = false) => {
    status.textContent = text;
    status.setAttribute("role", error ? "alert" : "status");
  };
  if (!file || !file.size || file.size > 1024 ** 3) {
    feedback("Choose a history backup ZIP up to 1 GiB.", true);
    return;
  }
  backupCheckBusy = true;
  render();
  $("#backup-check-result").hidden = true;
  feedback("Checking your backup with a temporary restore…");
  try {
    const result = await api("/api/backup-check", file, true, {
      "Content-Type": "application/zip",
    });
    const report = $("#backup-check-result");
    report.replaceChildren();
    report.append(el("h3", "Backup checks passed"));
    const owner = [result.owner.full_name, result.owner.email].filter(Boolean);
    report.append(
      el(
        "p",
        owner.length
          ? `Applicant: ${owner.join(" · ")}`
          : "Applicant details need confirmation.",
      ),
    );
    const labels = {
      opportunities: "Saved opportunities",
      applications: "Application records",
      confirmations: "Recorded confirmations",
      outcomes_to_review: "Outcomes to review",
      unanswered_questions: "Unanswered questions",
      employer_accounts: "Employer account records",
      accounts_to_review: "Accounts to review",
      writing_sources: "Writing sources",
      approved_sources: "Approved writing sources",
      pdf_files: "PDF files",
      source_files: "Original source files",
      evidence_images: "Evidence images",
    };
    const list = el("ul");
    for (const [key, label] of Object.entries(labels))
      list.append(el("li", `${label}: ${result.counts[key].toLocaleString()}`));
    report.append(list);
    report.append(
      el(
        "p",
        "Restore into a new private directory with applications paused. Reconnect your model, email and browser sessions. Employer passwords need the separate encrypted transfer.",
        "help",
      ),
    );
    report.append(
      el(
        "p",
        `Checked ${new Date(result.checked_at).toLocaleString()}. The temporary copy has been removed.`,
        "help",
      ),
    );
    report.hidden = false;
    feedback("Backup check complete. Your current workspace is unchanged.");
  } catch (error) {
    feedback(`${error.message} You can choose a backup and check again.`, true);
  } finally {
    backupCheckBusy = false;
    render();
  }
};

$("#download-backup").onclick = async () => {
  if (backupBusy) return;
  backupBusy = true;
  render();
  try {
    const response = await fetch("/api/backup", {
      method: "POST",
      headers: { "X-Hireme-Token": token, "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) {
      let error;
      try {
        error = (await response.json()).error;
      } catch {}
      throw new Error(
        error ||
          "Could not prepare your backup. Try again after the active batch finishes.",
      );
    }
    const url = URL.createObjectURL(await response.blob()),
      anchor = el("a");
    anchor.href = url;
    anchor.download = "application-history.zip";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    note(
      "History backup downloaded. Keep this private archive in a safe location.",
    );
  } catch (error) {
    note(error.message, true);
  } finally {
    backupBusy = false;
    render();
  }
};

$("#ledger-previous").onclick = async () => {
  ledgerOffset = Math.max(0, ledgerOffset - (ledgerState?.limit || 50));
  await loadLedger(false);
  scrollLedgerIntoView();
};
$("#ledger-next").onclick = async () => {
  ledgerOffset += ledgerState?.limit || 50;
  await loadLedger(false);
  scrollLedgerIntoView();
};

function scrollLedgerIntoView() {
  const jobs = $("#jobs");
  jobs.scrollIntoView({
    block: "start",
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
  jobs.focus({ preventScroll: true });
}

$("#recover-worker").onclick = async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/api/recover", {});
    await refresh();
    note(result.message);
  } catch (error) {
    note(error.message, true);
  } finally {
    if (state) render();
  }
};

function renderCycleFunnel() {
  const parent=$("#cycle-funnel"),run=state.cycle_funnel;parent.replaceChildren();
  $("#cycle-funnel-status").textContent=run ? `${run.mode === "prepare" ? "Preparation" : "Live"} · ${run.status} · ${run.run_id.slice(0,8)}${run.reconciled ? ` · ${run.reconciled} outcome${run.reconciled === 1 ? "" : "s"} updated after review` : ""}` : "No recorded cycle yet.";
  if (!run) return;
  for (const [key,label] of [["discovered","New discoveries"],["eligible","Eligible"],["attempted","Browser attempted"],["prepared","Prepared"],["confirmed","Confirmed"],["blocked","Attempt blocked"],["uncertain","Uncertain"]]) {
    const item=el("div");item.append(el("dt",label),el("dd",run[key] == null ? "Not recorded" : String(run[key])));parent.append(item);
  }
}
let coverageBusy=false;
async function loadCoverage() {
  if(coverageBusy)return;coverageBusy=true;$("#coverage-refresh").disabled=true;$("#coverage-results").setAttribute("aria-busy","true");
  try {
    const result=await api("/api/coverage"),platforms=new Map();
    for(const group of result.groups) {
      if(!platforms.has(group.ats))platforms.set(group.ats,{...group,sources:0,available:0,discovered:0,eligible:0,attempted:0,confirmed:0,blocked:0,uncertain:0});
      const platform=platforms.get(group.ats);platform.sources+=Boolean(group.configured);platform.available+=Boolean(group.configured) && group.health === "ok";
      for(const key of ["discovered","eligible","attempted","confirmed","blocked","uncertain"])platform[key]+=group[key];
    }
    const parent=$("#coverage-results");parent.replaceChildren();
    for(const platform of platforms.values()) {
      const section=el("section",undefined,"coverage-platform");section.append(el("h3",platform.ats),el("p",`${platform.application_capability}${platform.account_required ? " · Employer account required" : ""}`),el("p",platform.limitations,"help"),el("p",`${platform.sources} configured sources · ${platform.available} last checked available`,"help"));
      const counts=el("dl",undefined,"cycle-funnel");
      for(const key of ["discovered","eligible","attempted","confirmed","blocked","uncertain"]) {const item=el("div");item.append(el("dt",key.charAt(0).toUpperCase()+key.slice(1)),el("dd",String(platform[key])));counts.append(item);}
      section.append(counts);parent.append(section);
    }
    parent.append(el("p",result.eligible_definition,"help"));$("#coverage-error").hidden=true;
  }catch(error){$("#coverage-error").hidden=false;$("#coverage-error").textContent=`Coverage could not load: ${error.message}`;}
  finally {coverageBusy=false;$("#coverage-refresh").disabled=false;$("#coverage-results").removeAttribute("aria-busy");}
}
$("#coverage-panel").ontoggle=()=>{if($("#coverage-panel").open)loadCoverage();};
$("#coverage-refresh").onclick=loadCoverage;
