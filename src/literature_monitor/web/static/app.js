(() => {
  let settingsDirty = false;
  let lastRunAnnouncementKey = null;
  let workspaceScroll = null;

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
    }
  });

  window.addEventListener("resize", sizeWorkspacePanes);

  window.addEventListener("beforeunload", (event) => {
    if (!settingsDirty) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  setSettingsDirty(false);
  syncRunAnnouncement();
  syncWorkspaceSelection();
  sizeWorkspacePanes();
})();
