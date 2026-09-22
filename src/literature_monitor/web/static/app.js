(() => {
  let settingsDirty = false;

  function setSettingsDirty(value) {
    settingsDirty = value;
    document.documentElement.dataset.settingsDirty = value ? "true" : "false";
  }

  function isSettingsField(target) {
    return target instanceof Element && target.closest("form[data-settings-form]");
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

  window.addEventListener("beforeunload", (event) => {
    if (!settingsDirty) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  setSettingsDirty(false);
})();
