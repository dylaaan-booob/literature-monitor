(() => {
  let settingsDirty = false;
  let lastRunAnnouncementKey = null;

  function setSettingsDirty(value) {
    settingsDirty = value;
    document.documentElement.dataset.settingsDirty = value ? "true" : "false";
  }

  function isSettingsField(target) {
    return target instanceof Element && target.closest("form[data-settings-form]");
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
    if (isSettingsField(event.target)) {
      setSettingsDirty(true);
    }
  });

  document.addEventListener("change", (event) => {
    if (isSettingsField(event.target)) {
      setSettingsDirty(true);
    }
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const addButton = target.closest("[data-add-journal]");
    if (addButton) {
      const editor = addButton.closest("#settings-editor");
      const rows = editor?.querySelector("[data-journal-rows]");
      const template = editor?.querySelector("template[data-journal-template]");
      if (rows && template instanceof HTMLTemplateElement) {
        rows.append(template.content.cloneNode(true));
        setSettingsDirty(true);
      }
      return;
    }

    const removeButton = target.closest("[data-remove-journal]");
    if (removeButton) {
      removeButton.closest(".journal-row")?.remove();
      setSettingsDirty(true);
    }
  });

  document.body.addEventListener("settingsSaved", () => {
    setSettingsDirty(false);
  });

  document.body.addEventListener("htmx:afterSwap", () => {
    syncRunAnnouncement();
  });

  window.addEventListener("beforeunload", (event) => {
    if (!settingsDirty) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  setSettingsDirty(false);
  syncRunAnnouncement();
})();
