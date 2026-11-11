"use strict";
// Resume and letter style: bounded layout settings, a rendered PDF preview and
// the reasons behind each selected resume line. Nothing here supplies content.
(() => {
  const get = (id) => document.getElementById(id);
  const form = get("resume-style-form"), status = get("resume-style-status");
  const orderList = get("resume-section-order"), panel = get("resume-style-preview-panel");
  const sectionNames = { education: "Education", experience: "Experience", projects: "Projects", skills: "Skills" };
  const numeric = ["body_size", "margin", "line_spacing", "max_bullets"];
  let snapshot = null, catalog = null, saved = null, order = [], busy = false, dirty = false, loaded = false;
  let referenceHash = null, previewUrl = null;

  function announce(text, error = false) {
    status.textContent = text;
    status.setAttribute("role", error ? "alert" : "status");
    status.classList.toggle("error", error);
  }
  function controls() {
    const locked = busy || !catalog || Boolean(snapshot?.demo);
    for (const control of form.elements) control.disabled = locked;
    get("resume-style-reference").disabled = locked;
    for (const button of orderList.querySelectorAll("button")) button.disabled = locked || button.dataset.edge === "true";
    // The reference template needs measurements from an uploaded reference.
    form.elements.template.querySelector('[value="reference"]').disabled = !referenceHash;
    const rewrite = form.elements.mode.value === "rewrite";
    get("resume-style-mode-help").textContent = rewrite
      ? catalog?.rewriting_authorized
        ? "Each rephrased line is checked against your confirmed line: no new numbers, tools, employers or results. A line that fails either check keeps your exact wording. This uses two model requests per resume."
        : "Rephrasing needs source-grounded tailored writing, which is off in Search & automation. Until you enable it, resumes use your confirmed lines exactly."
      : "Each line appears exactly as you confirmed it. No model request is made.";
  }
  function renderOrder() {
    orderList.replaceChildren();
    order.forEach((key, index) => {
      const row = el("li"), actions = el("span", undefined, "actions");
      row.append(el("span", `${index + 1}. ${sectionNames[key]}`));
      for (const [label, step] of [["Move up", -1], ["Move down", 1]]) {
        const button = el("button", label, "secondary");
        button.type = "button";
        button.setAttribute("aria-label", `${label}: ${sectionNames[key]}`);
        button.dataset.edge = String(index + step < 0 || index + step >= order.length);
        button.onclick = () => {
          [order[index], order[index + step]] = [order[index + step], order[index]];
          dirty = true;
          renderOrder();
          // Keep keyboard focus on the section that moved.
          orderList.children[index + step]?.querySelectorAll("button")[step < 0 ? 0 : 1]?.focus();
        };
        actions.append(button);
      }
      row.append(actions);
      orderList.append(row);
    });
    controls();
  }
  function fill(style) {
    for (const key of ["template", "mode", "font", "date_layout", "heading_case", "accent"]) form.elements[key].value = style[key];
    for (const key of numeric) form.elements[key].value = style[key];
    form.elements.max_pages.value = String(style.max_pages);
    form.elements.rule.checked = style.rule;
    referenceHash = style.reference_hash || referenceHash;
    order = [...style.section_order];
    renderOrder();
  }
  function read() {
    const style = { section_order: [...order], rule: form.elements.rule.checked, max_pages: Number(form.elements.max_pages.value) };
    for (const key of ["template", "mode", "font", "date_layout", "heading_case", "accent"]) style[key] = form.elements[key].value;
    for (const key of numeric) style[key] = Number(form.elements[key].value);
    if (style.template === "reference") style.reference_hash = referenceHash;
    return style;
  }
  async function load() {
    if (busy) return;
    busy = true;
    controls();
    try {
      catalog = await api("/api/resume-style");
      saved = catalog.style;
      // The first load always fills the form: an edit made before the saved
      // style arrived has nothing to build on.
      if (!loaded || !dirty) {
        fill(saved);
        dirty = false;
      }
      loaded = true;
      announce(`Saved style: ${form.elements.template.selectedOptions[0].textContent}.`);
    } catch (error) {
      announce(error.message, true);
    } finally {
      busy = false;
      controls();
    }
  }
  form.oninput = () => {
    dirty = true;
    controls();
  };
  form.elements.template.onchange = () => {
    const name = form.elements.template.value;
    if (name === "reference" || !catalog) return;
    // Choosing a template loads its defaults; the wording choice is kept.
    fill({ ...catalog.templates[name], template: name, mode: form.elements.mode.value });
    dirty = true;
    announce("Template defaults loaded. Preview, adjust, then save.");
  };
  get("resume-style-reset").onclick = () => {
    if (!catalog) return;
    const name = form.elements.template.value === "reference" ? "ats" : form.elements.template.value;
    fill({ ...catalog.templates[name], template: name, mode: "select" });
    dirty = true;
    announce("Template defaults loaded. Save to use them.");
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (busy || snapshot?.demo) return;
    busy = true;
    controls();
    try {
      const result = await api("/api/resume-style", { style: read() });
      saved = result.style;
      dirty = false;
      fill(saved);
      announce("Style saved. New and re-prepared applications build a resume revision in this style; earlier revisions are unchanged.");
      note("Resume style saved.");
    } catch (error) {
      announce(error.message, true);
    } finally {
      busy = false;
      controls();
    }
  };
  get("resume-style-preview").onclick = async () => {
    if (busy) return;
    busy = true;
    controls();
    announce("Rendering the preview…");
    try {
      const result = await api("/api/resume-preview", { style: read() });
      const bytes = Uint8Array.from(atob(result.pdf), (char) => char.charCodeAt(0));
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      get("resume-style-preview-frame").src = previewUrl;
      get("resume-style-preview-download").href = previewUrl;
      get("resume-style-preview-note").textContent =
        (result.sample
          ? "Sample text only: add confirmed employment or projects to preview your own content. "
          : "Your confirmed content, without tailoring to a posting. ") +
        `${result.pages} page${result.pages === 1 ? "" : "s"}.` +
        (dirty ? " This style is not saved yet." : "");
      panel.hidden = false;
      announce("Preview ready below.");
    } catch (error) {
      announce(error.message, true);
    } finally {
      busy = false;
      controls();
    }
  };
  get("resume-style-reference").onchange = async (event) => {
    const input = event.target, file = input.files[0];
    if (!file || busy) return;
    if (file.size > 20 * 1024 * 1024) {
      input.value = "";
      return announce("Choose a PDF up to 20 MiB.", true);
    }
    busy = true;
    controls();
    announce("Measuring the reference layout…");
    try {
      const result = await api("/api/resume-style-reference", file, true);
      referenceHash = result.style.reference_hash;
      fill({ ...result.style, mode: form.elements.mode.value });
      dirty = true;
      const notes = get("resume-style-reference-notes");
      notes.replaceChildren(...result.notes.map((text) => el("li", text)));
      notes.hidden = false;
      announce("Reference layout measured. Preview it, adjust anything, then save. None of the reference text was kept.");
    } catch (error) {
      announce(error.message, true);
    } finally {
      busy = false;
      input.value = "";
      controls();
    }
  };

  // Why each line is on a saved resume revision.
  async function explain(artifact, parent) {
    const existing = parent.querySelector(".resume-explanation");
    if (existing) return existing.remove();
    const box = el("div", undefined, "resume-explanation");
    box.append(el("p", "Loading the selection record…", "help"));
    parent.append(box);
    try {
      const record = await api(`/api/resume-explain?id=${encodeURIComponent(artifact.id)}`);
      box.replaceChildren();
      const templates = { ats: "ATS single column", modern: "Modern", compact: "Compact technical", reference: "Reference layout" };
      const methods = {
        grounded_rewrite: "Selected lines, some rephrased and checked against your confirmed wording",
        confirmed_history_selection: "Your confirmed lines, exactly as written",
      };
      box.append(el("p", `${methods[record.method] || "Selection recorded before explanations were saved"} · ${templates[record.style?.template] || "Earlier fixed layout"}`, "help"));
      if (!record.selection.length) box.append(el("p", "This earlier revision has no saved selection record.", "help"));
      const rewritten = new Map((record.rewriting?.lines || []).map((line) => [line.original, line]));
      for (const entry of record.selection) {
        box.append(el("h4", entry.heading));
        const list = el("ul");
        for (const line of entry.lines) {
          const item = el("li", undefined, line.selected ? "" : "left-out");
          const change = rewritten.get(line.text);
          item.append(el("span", `${line.selected ? "Included" : "Left out"}: ${line.text}`));
          item.append(el("br"), el("span", line.reason, "help"));
          if (change) item.append(el("br"), el("span", change.used ? `Rephrased as: ${change.proposed}` : `Rephrasing not used. ${change.reason}`, "help"));
          list.append(item);
        }
        box.append(list);
      }
      if (record.rewriting?.detail) box.append(el("p", record.rewriting.detail, "help"));
    } catch (error) {
      box.replaceChildren(el("p", error.message, "help"));
    }
  }

  window.resumeStyle = {
    explain,
    render(value) {
      snapshot = value;
      if (!loaded && !busy) load();
      else controls();
    },
  };
  // Locked until the saved style has loaded.
  controls();
  if (typeof state !== "undefined" && state) window.resumeStyle.render(state);
})();

// Writing voice: measured description plus the applicant's saved corrections.
(() => {
  const get = (id) => document.getElementById(id);
  const form = get("voice-form"), status = get("voice-status");
  let snapshot = null, revision = 0, busy = false, dirty = false, loaded = false;
  function announce(text, error = false) {
    status.textContent = text;
    status.setAttribute("role", error ? "alert" : "status");
    status.classList.toggle("error", error);
  }
  function controls() {
    for (const control of form.elements) control.disabled = busy || !loaded || Boolean(snapshot?.demo);
  }
  function show(profile) {
    get("voice-summary").replaceChildren(...profile.summary.map((line) => el("li", line)));
    get("voice-sources").textContent = profile.sample_count
      ? `Based on ${profile.sample_count} approved sample${profile.sample_count === 1 ? "" : "s"} (${profile.word_count} words).`
      : "No approved writing samples yet.";
    if (dirty) return;
    revision = profile.preferences_revision;
    const saved = profile.preferences;
    form.elements.avoid_phrases.value = saved.avoid_phrases.join("\n");
    form.elements.exclamations.value = saved.exclamations;
    form.elements.contractions.value = saved.contractions;
    form.elements.max_sentence_words.value = saved.max_sentence_words;
    form.elements.notes.value = saved.notes;
  }
  async function load() {
    if (busy) return;
    busy = true;
    controls();
    try {
      show(await api("/api/voice"));
      loaded = true;
    } catch (error) {
      announce(error.message, true);
    } finally {
      busy = false;
      controls();
    }
  }
  form.oninput = () => {
    dirty = true;
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (busy || snapshot?.demo) return;
    busy = true;
    controls();
    try {
      const result = await api("/api/voice-preferences", {
        revision,
        preferences: {
          avoid_phrases: form.elements.avoid_phrases.value.split("\n").map((line) => line.trim()).filter(Boolean),
          exclamations: form.elements.exclamations.value,
          contractions: form.elements.contractions.value,
          max_sentence_words: Number(form.elements.max_sentence_words.value),
          notes: form.elements.notes.value,
        },
      });
      dirty = false;
      show(result.profile);
      announce(
        result.drafts_removed
          ? `Preferences saved. ${result.drafts_removed} prepared application${result.drafts_removed === 1 ? "" : "s"} will be written again with them.`
          : "Preferences saved. New drafts follow them.",
      );
    } catch (error) {
      announce(error.message, true);
    } finally {
      busy = false;
      controls();
    }
  };
  let materialSignature = null;
  window.writingVoice = {
    render(value) {
      snapshot = value;
      // Reviewing or adding a sample changes the measured description.
      const signature = JSON.stringify((value.materials || []).map((item) => [item.id, item.revision, item.confirmed, item.role]));
      if ((!loaded || signature !== materialSignature) && !busy) {
        materialSignature = signature;
        load();
      } else controls();
    },
  };
  controls();
  if (typeof state !== "undefined" && state) window.writingVoice.render(state);
})();
