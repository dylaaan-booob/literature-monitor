(() => {
  let settingsDirty = false;
  let lastRunAnnouncementKey = null;
  let workspaceScroll = null;
  let settingsJournalScroll = null;
  let settingsPublisherScroll = null;
  let settingsDisclosures = [];
  let pendingZoteroReturn = null;
  let zoteroReturnRequestInFlight = false;

  function groupRows(editor) {
    return Array.from(editor.querySelectorAll("[data-group-rows] [data-group-row]"));
  }

  function journalRows(editor) {
    return Array.from(editor.querySelectorAll("[data-journal-rows] .journal-row"));
  }

  function journalGroup(row) {
    return row.querySelector('[name="journal_group"]').value;
  }

  function syncGroupOptions(editor) {
    const names = groupRows(editor).map((row) => row.dataset.groupName);
    journalRows(editor).forEach((row) => {
      const select = row.querySelector('[name="journal_group"]');
      const selected = select.value;
      select.replaceChildren();
      ["", ...names].forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name || "Ungrouped";
        select.append(option);
      });
      select.value = selected;
    });
  }

  function alignJournalGroupOrder(editor) {
    // Only organization actions call this. Move the first occurrence, or a
    // conflicting prefix, without regrouping all Journals into blocks (§34.2).
    const container = editor.querySelector("[data-journal-rows]");
    let previous = null;
    let beforePrevious = null;
    groupRows(editor).forEach((group) => {
      const rows = journalRows(editor);
      const members = rows.filter((row) => journalGroup(row) === group.dataset.groupName);
      if (!members.length) return;
      const first = members[0];
      if (previous && rows.indexOf(first) < rows.indexOf(previous)) {
        if (!beforePrevious || rows.indexOf(first) > rows.indexOf(beforePrevious)) {
          container.insertBefore(previous, first);
        } else {
          let anchor = previous;
          members.filter((row) => rows.indexOf(row) < rows.indexOf(previous)).forEach((row) => {
            container.insertBefore(row, anchor.nextSibling);
            anchor = row;
          });
        }
      }
      beforePrevious = previous;
      previous = first;
    });
  }

  function renameGroup(editor, row) {
    const input = row.querySelector("[data-group-edit]");
    const old = row.dataset.groupName;
    const name = input.value.trim();
    if (!name) {
      input.value = old;
      return;
    }
    if (name === old) return;
    const existing = groupRows(editor).find((other) => other !== row && other.dataset.groupName === name);
    journalRows(editor).forEach((journal) => {
      const select = journal.querySelector('[name="journal_group"]');
      if (select.value === old) {
        // Rename the selected option before rebuilding choices, so value survives.
        const option = Array.from(select.options).find((item) => item.value === old);
        option.value = name;
      }
    });
    if (existing) {
      row.remove();
    } else {
      row.dataset.groupName = name;
      row.querySelector('[name="settings_group"]').value = name;
      input.value = name;
    }
    syncGroupOptions(editor);
    alignJournalGroupOrder(editor);
    setSettingsDirty(true);
  }

  function groupAction(button, editor) {
    if (button.hasAttribute("data-create-group")) {
      const input = editor.querySelector("[data-new-group]");
      const name = input.value.trim();
      if (!name || groupRows(editor).some((row) => row.dataset.groupName === name)) return;
      const template = editor.querySelector("template[data-group-template]");
      const fragment = template.content.cloneNode(true);
      const row = fragment.querySelector("[data-group-row]");
      row.dataset.groupName = name;
      row.querySelector('[name="settings_group"]').value = name;
      row.querySelector("[data-group-edit]").value = name;
      editor.querySelector("[data-group-rows]").append(fragment);
      input.value = "";
    } else {
      const row = button.closest("[data-group-row]");
      if (button.hasAttribute("data-rename-group")) {
        renameGroup(editor, row);
        return;
      }
      if (button.hasAttribute("data-delete-group")) {
        journalRows(editor).forEach((journal) => {
          const select = journal.querySelector('[name="journal_group"]');
          if (select.value === row.dataset.groupName) select.value = "";
        });
        row.remove();
      } else {
        const groups = groupRows(editor);
        const index = groups.indexOf(row);
        const up = button.hasAttribute("data-group-up");
        const neighbor = groups[index + (up ? -1 : 1)];
        if (!neighbor) return;
        row.parentNode.insertBefore(up ? row : neighbor, up ? neighbor : row);
        alignJournalGroupOrder(editor);
      }
    }
    syncGroupOptions(editor);
    setSettingsDirty(true);
  }

  function syncWorkspaceSelection() {
    const root = document.getElementById("workspace-root");
    const detail = root?.querySelector("#paper-detail");
    if (!root || !detail) {
      return;
    }
    // 只采用 server 在当前 view 验证后的 selection，不猜测点击目标。
    const selected = detail.dataset.activeView === root.dataset.activeView
      ? detail.dataset.selectedPaperId : "";
    root.dataset.selectedPaperId = selected || "";
    root.querySelectorAll("#paper-list a[data-paper-id]").forEach((link) => {
      if (selected && link.dataset.paperId === selected) {
        link.setAttribute("aria-current", "true");
      } else {
        link.removeAttribute("aria-current");
      }
    });
  }

  function sizeWorkspacePanes() {
    const grid = document.querySelector("#workspace-root .workspace-grid");
    if (!grid) {
      return;
    }
    if (window.innerWidth <= 760) {
      grid.style.removeProperty("--workspace-pane-height");
      return;
    }
    const shell = grid.closest(".page-shell");
    const bottomPadding = shell ? parseFloat(getComputedStyle(shell).paddingBottom) : 0;
    // 页面滚过 grid 顶部时，可用高度仍不能超过 viewport。
    const top = Math.max(0, grid.getBoundingClientRect().top);
    const available = Math.max(0, window.innerHeight - top - bottomPadding);
    grid.style.setProperty("--workspace-pane-height", `${available}px`);
  }

  function restoreWorkspaceScroll() {
    const root = document.getElementById("workspace-root");
    const list = root?.querySelector("#paper-list");
    if (list && workspaceScroll?.view === root.dataset.activeView) {
      list.scrollTop = workspaceScroll.top;
      if (root.dataset.selectionStepped === "true") {
        // 邻项只在 list 内最小移动到可见位置，不滚动整页或重置 review 区域。
        const selected = list.querySelector('[aria-current="true"]');
        if (selected) {
          const pane = list.getBoundingClientRect();
          const item = selected.getBoundingClientRect();
          if (item.top < pane.top) {
            list.scrollTop += item.top - pane.top;
          } else if (item.bottom > pane.bottom) {
            list.scrollTop += item.bottom - pane.bottom;
          }
        }
      }
    }
    workspaceScroll = null;
  }

  function setSettingsDirty(value) {
    settingsDirty = value;
    document.documentElement.dataset.settingsDirty = value ? "true" : "false";
  }

  function isSettingsField(target) {
    return target instanceof Element && target.closest("form[data-settings-form]")
      && !target.closest("[data-transient-import]");
  }

  function syncPublisherLink(target) {
    if (!target.matches('[name="publisher_access_url"]')) return;
    const link = target.closest("[data-publisher-row]")?.querySelector("[data-publisher-open]");
    // New links require the shared server URL validator; never keep a stale href.
    if (link) link.hidden = target.value !== link.dataset.accessUrl;
  }

  function setImportReady(editor, ready) {
    editor.querySelectorAll("[data-import-preview], [data-import-apply]").forEach((button) => {
      button.disabled = !ready;
    });
  }

  async function loadImportFile(input) {
    const editor = input.closest("#settings-editor");
    const textarea = editor.querySelector("[data-journal-import-text]");
    const error = editor.querySelector("[data-import-file-error]");
    const file = input.files?.[0];
    if (!file) return;
    editor.querySelector("[data-import-plan]")?.remove();
    textarea.value = "";
    textarea.disabled = true;
    editor.dataset.importFileLoading = "true";
    error.hidden = true;
    setImportReady(editor, false);
    try {
      if (!/\.(csv|tsv|md)$/i.test(file.name) && !file.type.startsWith("text/")) {
        throw new Error("Choose a UTF-8 CSV, TSV, or Markdown text file.");
      }
      const bytes = await file.arrayBuffer();
      const contents = new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(bytes);
      if (document.getElementById("settings-editor") !== editor || input.files?.[0] !== file) return;
      textarea.value = contents;
      setImportReady(editor, true);
    } catch {
      if (document.getElementById("settings-editor") !== editor || input.files?.[0] !== file) return;
      error.textContent = "Could not read this file as UTF-8 text. Choose a CSV, TSV, or Markdown text file, or paste the table below.";
      error.hidden = false;
    } finally {
      if (input.files?.[0] === file) {
        textarea.disabled = false;
        editor.dataset.importFileLoading = "false";
      }
    }
  }

  function revealWorkspaceHealth() {
    if (window.location.hash === "#workspace-health") {
      const advanced = document.getElementById("advanced-diagnostics");
      if (advanced instanceof HTMLDetailsElement) {
        advanced.open = true;
      }
    }
  }

  function rememberOpenDoi(link) {
    const actions = link.closest(".decision-actions");
    const form = actions?.querySelector("[data-check-zotero-form]");
    if (!form) return;
    pendingZoteroReturn = {form, leftPage: false, attempted: false};
  }

  function noteOpenDoiPageLeft() {
    if (pendingZoteroReturn) pendingZoteroReturn.leftPage = true;
  }

  function reconcileAfterDoiReturn() {
    const pending = pendingZoteroReturn;
    if (!pending || !pending.leftPage || pending.attempted || zoteroReturnRequestInFlight) return;
    if (document.visibilityState === "hidden") return;

    pending.attempted = true;
    zoteroReturnRequestInFlight = true;
    const values = Object.fromEntries(new FormData(pending.form).entries());
    Promise.resolve(htmx.ajax("POST", pending.form.action, {
      source: pending.form,
      target: "#workspace-root",
      swap: "outerHTML",
      values,
    })).finally(() => {
      zoteroReturnRequestInFlight = false;
    });
  }

  function syncRunAnnouncement() {
    const panel = document.getElementById("run-panel");
    const announcer = document.getElementById("run-live-announcer");
    if (!(panel instanceof HTMLElement) || !(announcer instanceof HTMLElement)) {
      return;
    }

    const key = panel.dataset.runAnnouncementKey;
    const message = panel.dataset.runAnnouncement;
    if (!key || !message || key === lastRunAnnouncementKey) {
      return;
    }

    // 只缓存 presentation identity；runtime state 始终来自 server snapshot。
    lastRunAnnouncementKey = key;
    announcer.textContent = message;
  }

  document.addEventListener("input", (event) => {
    if (event.target instanceof Element && event.target.matches("[data-journal-import-text]")) {
      const editor = event.target.closest("#settings-editor");
      editor.querySelector("[data-import-plan]")?.remove();
      editor.querySelector("[data-import-file-error]").hidden = true;
      if (editor.dataset.importFileLoading !== "true") setImportReady(editor, true);
    }
    if (isSettingsField(event.target)) {
      syncPublisherLink(event.target);
      setSettingsDirty(true);
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target instanceof Element && event.target.closest("[data-transient-import]")) {
      const editor = event.target.closest("#settings-editor");
      if (event.target.matches("[data-journal-import-file]")) {
        loadImportFile(event.target);
      } else if (event.target.matches('[name="journal_import_mode"]')) {
        editor.querySelector("[data-import-plan]")?.remove();
      }
      return;
    }
    if (isSettingsField(event.target)) {
      syncPublisherLink(event.target);
      const editor = event.target.closest("#settings-editor");
      if (event.target.matches("[data-group-edit]")) {
        renameGroup(editor, event.target.closest("[data-group-row]"));
      } else if (event.target.matches('[name="journal_group"]')) {
        alignJournalGroupOrder(editor);
      }
      setSettingsDirty(true);
    }
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (event.key !== "Enter" || !isSettingsField(target)) return;
    const editor = target.closest("#settings-editor");
    if (target.matches("[data-group-edit]")) {
      event.preventDefault();
      renameGroup(editor, target.closest("[data-group-row]"));
    } else if (target.matches("[data-new-group]")) {
      event.preventDefault();
      groupAction(editor.querySelector("[data-create-group]"), editor);
    }
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const openDoiLink = target.closest("[data-open-doi]");
    if (openDoiLink) {
      rememberOpenDoi(openDoiLink);
      return;
    }

    const groupButton = target.closest("[data-create-group], [data-rename-group], [data-delete-group], [data-group-up], [data-group-down]");
    if (groupButton) {
      groupAction(groupButton, groupButton.closest("#settings-editor"));
      return;
    }

    const addButton = target.closest("[data-add-journal]");
    if (addButton) {
      const editor = addButton.closest("#settings-editor");
      const rows = editor?.querySelector("[data-journal-rows]");
      const template = editor?.querySelector("template[data-journal-template]");
      if (rows && template instanceof HTMLTemplateElement) {
        rows.append(template.content.cloneNode(true));
        const added = rows.lastElementChild;
        added?.querySelector('[name="journal_issns"]')?.focus();
        const count = editor.querySelector("[data-journal-count]");
        if (count) count.textContent = journalRows(editor).length;
        syncGroupOptions(editor);
        setSettingsDirty(true);
      }
      return;
    }

    const removeButton = target.closest("[data-remove-journal]");
    if (removeButton) {
      const editor = removeButton.closest("#settings-editor");
      removeButton.closest(".journal-row")?.remove();
      const count = editor.querySelector("[data-journal-count]");
      if (count) count.textContent = journalRows(editor).length;
      alignJournalGroupOrder(editor);
      setSettingsDirty(true);
    }
  });

  document.body.addEventListener("settingsSaved", () => {
    setSettingsDirty(false);
  });

  document.body.addEventListener("settingsDraftChanged", () => {
    setSettingsDirty(true);
  });

  document.body.addEventListener("htmx:configRequest", (event) => {
    if (event.detail.path.split("?")[0] !== "/fragments/workspace") {
      return;
    }
    const root = document.getElementById("workspace-root");
    if (root?.dataset.selectedPaperId) {
      event.detail.parameters.paper = root.dataset.selectedPaperId;
    }
  });

  document.body.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target.id === "settings-editor") {
      const viewport = document.getElementById("settings-editor")?.querySelector("[data-journal-viewport]");
      settingsJournalScroll = viewport?.scrollTop ?? null;
      settingsDisclosures = Array.from(document.getElementById("settings-editor").querySelectorAll("[data-settings-disclosure]")).filter(panel => panel.open).map(panel => panel.dataset.settingsDisclosure);
      settingsPublisherScroll = document.getElementById("settings-editor")?.querySelector("[data-publisher-viewport]")?.scrollTop ?? null;
      return;
    }
    if (event.detail.target.id !== "workspace-root") {
      return;
    }
    // 无效 expected-status 的 400 HTML 也要展示刷新后的状态；403 仍不 swap。
    if (event.detail.xhr.status === 400) {
      event.detail.shouldSwap = true;
    }
    const root = document.getElementById("workspace-root");
    const list = root?.querySelector("#paper-list");
    workspaceScroll = list ? { view: root.dataset.activeView, top: list.scrollTop } : null;
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    syncRunAnnouncement();
    syncWorkspaceSelection();
    sizeWorkspacePanes();
    if (event.detail.target.id === "workspace-root") {
      restoreWorkspaceScroll();
    } else if (event.detail.target.id === "settings-editor") {
      const viewport = document.getElementById("settings-editor")?.querySelector("[data-journal-viewport]");
      if (viewport && settingsJournalScroll !== null) viewport.scrollTop = settingsJournalScroll;
      const publishers = document.getElementById("settings-editor")?.querySelector("[data-publisher-viewport]");
      if (publishers && settingsPublisherScroll !== null) publishers.scrollTop = settingsPublisherScroll;
      document.getElementById("settings-editor").querySelectorAll("[data-settings-disclosure]").forEach(panel => {
        if (settingsDisclosures.includes(panel.dataset.settingsDisclosure)) panel.open = true;
      });
      settingsDisclosures = [];
      settingsJournalScroll = null;
      settingsPublisherScroll = null;
    }
  });

  window.addEventListener("resize", sizeWorkspacePanes);
  window.addEventListener("hashchange", revealWorkspaceHealth);
  window.addEventListener("blur", noteOpenDoiPageLeft);
  window.addEventListener("focus", reconcileAfterDoiReturn);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      noteOpenDoiPageLeft();
    } else {
      reconcileAfterDoiReturn();
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!settingsDirty) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  setSettingsDirty(false);
  revealWorkspaceHealth();
  syncRunAnnouncement();
  syncWorkspaceSelection();
  sizeWorkspacePanes();
})();
