// Private notes never enter application facts, writing sources, or browser storage.
const opportunityNotes = (() => {
  const entries = new Map();
  let currentId = null;
  const panel = () => document.querySelector("#opportunity-notes");
  const editor = () => document.querySelector("#opportunity-note-editor");
  const dirty = (entry) => entry.loaded && entry.draft !== entry.body;
  const visible = (entry) =>
    currentId === entry.id &&
    panel().open &&
    document.querySelector("#job-dialog").open;

  function summary() {
    const entry = entries.get(currentId);
    panel().querySelector("summary").textContent =
      "Your private notes" + (entry && dirty(entry) ? " · Unsaved draft" : "");
  }
  function trimCache() {
    for (const [id, entry] of entries) {
      if (entries.size <= 100) break;
      if (id !== currentId && !dirty(entry) && !entry.loading && !entry.saving)
        entries.delete(id);
    }
  }
  function render(entry, focus = false) {
    summary();
    if (!visible(entry)) return;
    const parent = editor();
    parent.replaceChildren();
    const feedback = el(
      "p",
      entry.error ||
        entry.message ||
        (entry.updated
          ? `Saved ${date(entry.updated)}.`
          : "No note saved for this opportunity."),
      "help",
    );
    feedback.id = "opportunity-note-status";
    feedback.setAttribute("role", entry.error ? "alert" : "status");
    if (entry.loading) feedback.textContent = "Loading your saved note…";
    if (!entry.loaded) {
      parent.append(feedback);
      if (!entry.loading) {
        const retry = el("button", "Retry loading note", "secondary");
        retry.type = "button";
        retry.onclick = () => load(entry);
        parent.append(retry);
      }
      return;
    }
    const form = el("form", undefined, "opportunity-note-form");
    form.method = "post";
    const label = el("label", "Notes about this opportunity");
    const field = el("textarea");
    field.id = "opportunity-note-body";
    field.name = "body";
    field.rows = 5;
    field.maxLength = 4000;
    field.value = entry.draft;
    field.setAttribute(
      "aria-describedby",
      "opportunity-note-help opportunity-note-length",
    );
    label.append(field);
    const count = el("p", "", "help");
    count.id = "opportunity-note-length";
    const actions = el("div", undefined, "actions");
    const save = el("button", "Save note");
    save.type = "submit";
    const discard = el("button", "", "secondary");
    discard.type = "button";
    function controls() {
      field.disabled = state.demo || entry.loading || entry.saving;
      save.disabled =
        state.demo || entry.loading || entry.saving || !dirty(entry);
      discard.disabled = entry.loading || entry.saving;
      discard.textContent = dirty(entry)
        ? "Discard edits and reload"
        : "Reload saved note";
      count.textContent = `${entry.draft.length.toLocaleString()} / 4,000`;
    }
    field.oninput = () => {
      entry.draft = field.value;
      entry.message = "";
      controls();
      summary();
    };
    discard.onclick = () => load(entry, true);
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (state.demo || entry.saving || entry.loading || !dirty(entry)) return;
      entry.saving = true;
      entry.message = "Saving your note…";
      entry.error = "";
      feedback.setAttribute("role", "status");
      feedback.textContent = "Saving your note…";
      try {
        const result = await api("/api/job-note", {
          job_id: entry.id,
          body: entry.draft,
          revision: entry.revision,
        });
        entry.body = entry.draft = result.note.body;
        entry.revision = result.note.revision;
        entry.updated = result.note.updated;
        entry.message = entry.body
          ? "Private note saved."
          : "Private note cleared.";
        saved(form);
      } catch (error) {
        entry.error = error.message;
      } finally {
        entry.saving = false;
        const restoreFocus =
          document.activeElement === document.body ||
          form.contains(document.activeElement);
        render(entry, restoreFocus);
        trimCache();
      }
    };
    actions.append(save, discard);
    form.append(label, count, actions);
    if (dirty(entry)) dirtyForms.add(form);
    controls();
    parent.append(form, feedback);
    if (state.demo)
      parent.append(
        el("p", "Notes can be edited in your own workspace.", "help"),
      );
    if (focus && !field.disabled) field.focus({ preventScroll: true });
  }
  async function load(entry, discard = false) {
    if (entry.loading || entry.saving) {
      render(entry);
      return;
    }
    entry.loading = true;
    entry.error = "";
    const restoreFocus = editor().contains(document.activeElement);
    render(entry);
    try {
      const note = await api("/api/job-note/" + encodeURIComponent(entry.id));
      if (!dirty(entry) || discard) {
        entry.body = entry.draft = note.body;
        entry.revision = note.revision;
        entry.updated = note.updated;
        entry.message = discard
          ? "Saved note loaded. Unsaved edits discarded."
          : "";
      } else if (note.revision !== entry.revision) {
        entry.error =
          "The saved note changed elsewhere. Your draft is retained. Copy text you want to keep, then discard edits and reload before saving.";
      }
      entry.loaded = true;
    } catch (error) {
      entry.error = error.message;
    } finally {
      entry.loading = false;
      render(entry, restoreFocus);
      trimCache();
    }
  }
  panel().ontoggle = () => {
    if (panel().open && currentId) load(entries.get(currentId));
  };
  return {
    prepare(id) {
      currentId = id;
      if (!entries.has(id))
        entries.set(id, {
          id,
          body: "",
          draft: "",
          revision: 0,
          updated: null,
          loaded: false,
          loading: false,
          saving: false,
          error: "",
          message: "",
        });
      panel().open = false;
      editor().replaceChildren();
      summary();
      trimCache();
    },
    hasDrafts() {
      return [...entries.values()].some(
        (entry) => dirty(entry) || entry.saving,
      );
    },
  };
})();
