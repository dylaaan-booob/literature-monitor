(() => {
  let settingsDirty = false;
  let lastRunAnnouncementKey = null;
  let workspaceScroll = null;
  let settingsJournalScroll = null;
  let settingsPublisherScroll = null;
  let settingsDisclosures = [];

  let draggedJournal = null;
  let selectedMarkdownFile = null;

  // 视图只是同一表单的展示模式；概览始终从当前字段生成，不缓存第二份草稿。
  let settingsView = "overview";
  let settingsBaseline = null;
  let overviewExpandedAfterSwap = null;

  function settingsSignature(editor) {
    if (!editor) return "";
    const value = (row, name) => row.querySelector('[name="' + name + '"]')?.value || "";
    const ordinary = ["name", "keyword_expression", "output_dir", "log_level",
      "from_date", "to_date", "window_days", "institution_name",
      "institution_idp_entity_id", "legacy_journals", "monitor_revision_digest", "journal_revision_digest"]
      .map((name) => value(editor, name));
    const journal = journalRows(editor).map((row) => ({
      order: value(row, "journal_order"), name: value(row, "journal_name"),
      issn: value(row, "journal_issns"), publisher: value(row, "journal_publisher_id"),
      pending: value(row, "journal_pending"), group: value(row, "journal_group"),
    })).sort((a, b) => Number(a.order) - Number(b.order));
    const publisher = publisherRows(editor).map((row) => ({
      order: value(row, "publisher_order"), id: value(row, "publisher_id"),
      name: value(row, "publisher_name"), url: value(row, "publisher_access_url"),
      service: value(row, "publisher_access_service_id"),
    })).sort((a, b) => Number(a.order) - Number(b.order));
    const services = Array.from(editor.querySelectorAll("[data-service-row]")).map((row) => ({
      id: value(row, "service_id"), name: value(row, "service_name"),
      url: value(row, "service_access_url"),
    })).sort((a, b) => a.id.localeCompare(b.id));
    const groups = groupRows(editor).map((row) => row.dataset.groupName);
    const migration = Array.from(editor.querySelectorAll('[name="migration_issn_l"]'))
      .map((input) => input.value);
    return JSON.stringify({ordinary, journal, publisher, services, groups, migration});
  }

  function renderSettingsOverview(editor) {
    const content = editor?.querySelector("[data-overview-content]");
    if (!content) return;
    const expanded = overviewExpandedAfterSwap || new Set(
      Array.from(content.querySelectorAll("details"))
        .filter((details) => details.open).map((details) => details.dataset.overviewKey),
    );
    overviewExpandedAfterSwap = null;
    const journals = journalRows(editor).filter((row) =>
      row.querySelector('[name="journal_issns"]').value.trim()).map((row) => ({
      name: row.querySelector('[name="journal_name"]').value,
      issn: row.querySelector('[name="journal_issns"]').value,
      publisher: row.querySelector('[name="journal_publisher_id"]').value,
      group: journalGroup(row),
    }));
    if (!journals.length) {
      editor.querySelectorAll(".legacy-journal-row").forEach((row) => {
        journals.push({
          name: row.querySelector(".config-metadata strong").textContent,
          issn: row.dataset.legacyIssns,
          publisher: "",
          group: row.dataset.legacyGroup,
          legacy: true,
        });
      });
    }
    const publishers = publisherRows(editor).map((row) => ({
      name: row.querySelector('[name="publisher_name"]').value,
      id: row.querySelector('[name="publisher_id"]').value,
      url: row.querySelector('[name="publisher_access_url"]').value,
      service: publisherService(row),
    }));
    const services = Array.from(editor.querySelectorAll("[data-service-row]")).map((row) => ({
      name: row.querySelector('[name="service_name"]').value,
      id: row.querySelector('[name="service_id"]').value,
      url: row.querySelector('[name="service_access_url"]').value,
    }));
    const groups = groupRows(editor).map((row) => row.dataset.groupName);
    const serviceNames = new Map(services.map((service) => [service.id, service.name]));
    const publisherNames = new Map(publishers.map((publisher) => [publisher.id, publisher]));

    const statistics = document.createElement("dl");
    statistics.className = "overview-statistics";
    const stats = [
      ["Journals", journals.length],
      ["Groups", groups.length],
      ["Ungrouped Journals", journals.filter((journal) => !journal.group).length],
      ["Publishers", publishers.length],
      ["Access Services", services.length],
      ["Unassigned Publishers", publishers.filter((publisher) => !publisher.service).length],
    ];
    stats.forEach(([label, count]) => {
      const item = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const result = document.createElement("dd");
      result.textContent = String(count);
      item.append(term);
      item.append(result);
      statistics.append(item);
    });
    content.replaceChildren();
    content.append(statistics);

    const heading = (text) => {
      const element = document.createElement("h3");
      element.textContent = text;
      content.append(element);
    };
    const detailList = (label, entries) => {
      const details = document.createElement("details");
      details.className = "overview-relationship";
      details.dataset.overviewKey = label;
      details.open = expanded.has(label);
      const summary = document.createElement("summary");
      summary.textContent = label + " · " + entries.length;
      details.append(summary);
      const list = document.createElement("ul");
      if (!entries.length) {
        const empty = document.createElement("li");
        empty.textContent = "None";
        list.append(empty);
      }
      entries.forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = entry;
        list.append(item);
      });
      details.append(list);
      content.append(details);
    };
    heading("Journal Groups");
    for (const group of ["", ...groups]) {
      detailList(group || "Ungrouped", journals.filter((journal) => journal.group === group).map((journal) => {
        const publisher = publisherNames.get(journal.publisher);
        const service = publisher?.service ? (serviceNames.get(publisher.service) || "Unknown Service") : "Unassigned";
        return journal.name + (journal.legacy ? " · Legacy identifiers " : " · ISSN-L ") + journal.issn
          + " · Publisher " + (publisher?.name || journal.publisher || "Unknown")
          + " · Service " + service;
      }));
    }
    heading("Access Mapping");
    for (const service of [{id: "", name: "Unassigned"}, ...services]) {
      const label = service.id ? service.name + " · " + service.id + " · URL: " + (service.url || "None") : "Unassigned";
      detailList(label, publishers.filter((publisher) => publisher.service === service.id)
        .map((publisher) => publisher.name + " · " + publisher.id
          + " · Publisher URL: " + (publisher.url || "None")));
    }
  }

  function setSettingsView(editor, view, invalidate = false) {
    if (!editor?.querySelector("[data-integrated-settings]")) return;
    settingsView = view;
    editor.querySelector("[data-settings-overview]").hidden = view !== "overview";
    editor.querySelectorAll("[data-settings-view]").forEach((panel) => {
      panel.hidden = panel.dataset.settingsView !== view;
    });
    editor.querySelectorAll("[data-settings-view-button]").forEach((button) => {
      const current = button.dataset.settingsViewButton === view;
      button.ariaPressed = String(current);
      button.dataset.active = String(current);
    });
    const status = editor.querySelector("[data-settings-view-status]");
    status.textContent = view === "overview" ? "Read-only Overview"
      : "Organize · " + (view === "groups" ? "Journal Groups" : "Access Mapping");
    if (invalidate) {
      // 视图切换使此前的删除/导入计划不再是可执行授权；草稿节点保持原位。
      editor.querySelectorAll("[data-markdown-preview], [data-settings-deletion-preview], [data-access-upgrade-plan]")
        .forEach((panel) => panel.remove());
      editor.querySelectorAll("[data-delete-confirm], [data-service-delete-preview], [data-journal-cascade-preview]")
        .forEach((panel) => { panel.hidden = true; });
      groupRows(editor).forEach((row) => { row.confirmedMembers = null; });
      editor.querySelectorAll("[data-service-row]").forEach((row) => { row.confirmedSignature = null; });
      pendingJournalRemoval = null;
    }
    if (view === "overview") renderSettingsOverview(editor);
  }

  function groupRows(editor) {
    return Array.from(editor.querySelectorAll("[data-group-rows] [data-group-row]"));
  }

  function journalRows(editor) {
    return Array.from(editor.querySelectorAll("[data-journal-rows] .journal-row"));
  }

  function journalGroup(row) {
    return row.querySelector('[name="journal_group"]').value;
  }

  function groupContainer(editor, name) {
    return Array.from(editor.querySelectorAll("[data-group-container]"))
      .find((container) => container.dataset.groupName === name);
  }

  function syncGroupOptions(editor, renamed = null) {
    const names = groupRows(editor).map((row) => row.dataset.groupName);
    journalRows(editor).forEach((row) => {
      const select = row.querySelector('[name="journal_group"]');
      const old = select.value;
      const selected = old === renamed?.old ? renamed.name : old;
      select.replaceChildren();
      ["", ...names].forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name === "Ungrouped" ? "Ungrouped (named Group)" : name || "Ungrouped";
        select.append(option);
      });
      select.value = selected;
      row.querySelector("[data-journal-group-label]").textContent = "Group: " + (selected || "Ungrouped");
    });
    const batch = editor.querySelector("[data-batch-group]");
    if (batch) {
      const selected = batch.value === renamed?.old ? renamed.name : batch.value;
      batch.replaceChildren();
      ["", ...names].forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name === "Ungrouped" ? "Ungrouped (named Group)" : name || "Ungrouped";
        batch.append(option);
      });
      batch.value = names.includes(selected) ? selected : "";
    }
  }

  function updateJournalGroups(editor) {
    if (!editor?.querySelector("[data-journal-search]")) return;
    const query = editor.querySelector("[data-journal-search]").value.trim().toLocaleLowerCase();
    journalRows(editor).forEach((row) => {
      const text = ["journal_name", "journal_issns", "journal_publisher_id"]
        .map((field) => row.querySelector('[name="' + field + '"]')?.value || "")
        .join(" ").toLocaleLowerCase();
      row.hidden = !text.includes(query);
    });
    editor.querySelectorAll("[data-group-container]").forEach((container) => {
      const members = Array.from(container.querySelectorAll(".journal-row"));
      const visible = members.filter((row) => !row.hidden).length;
      container.querySelector("[data-member-count]").textContent =
        query ? visible + " matching / " + members.length + " total" : members.length + " Journals";
    });
    const selected = journalRows(editor).filter((row) => row.querySelector("[data-select-journal]").checked);
    const hidden = selected.filter((row) => row.hidden).length;
    editor.querySelector("[data-selection-status]").textContent = selected.length + " Journals selected"
      + (hidden ? " (" + hidden + " hidden by search)" : "");
  }

  function moveJournal(editor, journal, name) {
    const destination = groupContainer(editor, name)?.querySelector("[data-group-members]");
    if (!destination) return false;
    const changed = journalGroup(journal) !== name;
    journal.querySelector('[name="journal_group"]').value = name;
    journal.querySelector("[data-journal-group-label]").textContent = "Group: " + (name || "Ungrouped");
    if (journal.parentNode !== destination) destination.append(journal);
    return changed;
  }

  function assignJournals(editor, journals, name) {
    if (!groupContainer(editor, name)) return;
    const changed = journals.some((journal) => journalGroup(journal) !== name);
    journals.forEach((journal) => moveJournal(editor, journal, name));
    updateJournalGroups(editor);
    if (changed) setSettingsDirty(true);
  }

  function initializeJournalGroups(editor) {
    if (!editor?.querySelector("[data-journal-search]")) return;
    // 移动原表单节点而不复制字段，避免草稿值、待解析身份或原始版本丢失。
    journalRows(editor).forEach((row) => moveJournal(editor, row, journalGroup(row)));
    updateJournalGroups(editor);
  }

  function groupError(editor, message) {
    const notice = editor.querySelector("[data-group-error]");
    notice.textContent = message;
    notice.hidden = !message;
  }

  function renameGroup(editor, row) {
    const input = row.querySelector("[data-group-edit]");
    const old = row.dataset.groupName;
    const name = input.value.trim();
    if (!name) {
      groupError(editor, "Group name cannot be empty.");
      input.value = old;
      return;
    }
    if (name === old) return;
    if (groupRows(editor).some((other) => other !== row
        && other.dataset.groupName.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      groupError(editor, "Group already exists. Choose a distinct name.");
      input.value = old;
      return;
    }
    row.dataset.groupName = name;
    row.querySelector('[name="settings_group"]').value = name;
    input.value = name;
    row.querySelector("[data-delete-confirm]").hidden = true;
    syncGroupOptions(editor, {old, name});
    updateJournalGroups(editor);
    groupError(editor, "");
    setSettingsDirty(true);
  }

  function groupAction(button, editor) {
    if (button.hasAttribute("data-create-group")) {
      const input = editor.querySelector("[data-new-group]");
      const name = input.value.trim();
      if (!name || groupRows(editor).some((row) =>
        row.dataset.groupName.toLocaleLowerCase() === name.toLocaleLowerCase())) {
        groupError(editor, name ? "Group already exists." : "Enter a Group name.");
        return;
      }
      const fragment = editor.querySelector("template[data-group-template]").content.cloneNode(true);
      const row = fragment.querySelector("[data-group-row]");
      row.dataset.groupName = name;
      row.querySelector('[name="settings_group"]').value = name;
      row.querySelector("[data-group-edit]").value = name;
      editor.querySelector("[data-group-rows]").append(fragment);
      input.value = "";
      groupError(editor, "");
    } else {
      const row = button.closest("[data-group-row]");
      if (button.hasAttribute("data-rename-group")) {
        renameGroup(editor, row);
        return;
      }
      if (button.hasAttribute("data-delete-group")) {
        const members = Array.from(row.querySelectorAll(".journal-row"));
        if (members.length) {
          const panel = row.querySelector("[data-delete-confirm]");
          panel.querySelector("[data-delete-summary]").textContent =
            "Delete Group " + row.dataset.groupName + "? " + members.length + " Journals will move to Ungrouped:";
          const list = row.querySelector("[data-delete-members]");
          list.replaceChildren();
          members.forEach((journal) => {
            const item = document.createElement("li");
            item.textContent = journal.querySelector('[name="journal_name"]').value + " ("
              + journal.querySelector('[name="journal_issns"]').value + ")";
            list.append(item);
          });
          // 删除预览绑定当时的成员；成员变化后必须重新确认。
          row.confirmedMembers = members;
          panel.hidden = false;
          return;
        }
        row.remove();
      } else {
        const groups = groupRows(editor);
        const index = groups.indexOf(row);
        const up = button.hasAttribute("data-group-up");
        const neighbor = groups[index + (up ? -1 : 1)];
        if (!neighbor) return;
        row.parentNode.insertBefore(up ? row : neighbor, up ? neighbor : row);
      }
    }
    syncGroupOptions(editor);
    updateJournalGroups(editor);
    setSettingsDirty(true);
  }

  let draggedPublisher = null;
  let pendingJournalRemoval = null;

  function publisherRows(editor) {
    return Array.from(editor.querySelectorAll("[data-publisher-row]"));
  }

  function mappingContainer(editor, serviceId) {
    return Array.from(editor.querySelectorAll("[data-mapping-container]"))
      .find((container) => container.dataset.serviceTarget === serviceId);
  }

  function publisherService(row) {
    return row.querySelector('[name="publisher_access_service_id"]').value;
  }

  function movePublisher(editor, row, serviceId) {
    const destination = mappingContainer(editor, serviceId)?.querySelector("[data-publisher-members]");
    if (!destination) return false;
    const changed = publisherService(row) !== serviceId;
    row.querySelector('[name="publisher_access_service_id"]').value = serviceId;
    if (row.parentNode !== destination) destination.append(row);
    return changed;
  }

  function refreshPublisherMapping(editor) {
    if (!editor?.querySelector("[data-access-mapping]")) return;
    editor.querySelectorAll("[data-mapping-container]").forEach((container) => {
      const count = container.querySelectorAll("[data-publisher-row]").length;
      container.querySelector("[data-publisher-count]").textContent = count + " Publishers";
    });
    const selected = publisherRows(editor).filter((row) => row.querySelector("[data-select-publisher]").checked);
    editor.querySelector("[data-publisher-selection]").textContent = selected.length + " Publishers selected";
  }

  function mappingError(editor, message) {
    const notice = editor.querySelector("[data-mapping-error]");
    notice.textContent = message;
    notice.hidden = !message;
  }

  function syncServiceOptions(editor) {
    const services = Array.from(editor.querySelectorAll("[data-service-row]")).map((row) => ({
      id: row.querySelector('[name="service_id"]').value,
      name: row.querySelector('[name="service_name"]').value,
    }));
    const selections = [
      editor.querySelector("[data-batch-service]"),
      ...publisherRows(editor).map((row) => row.querySelector('[name="publisher_access_service_id"]')),
    ];
    selections.forEach((select) => {
      const current = select.value;
      select.replaceChildren();
      [{id: "", name: "Unassigned"}, ...services].forEach((service) => {
        const option = document.createElement("option");
        option.value = service.id;
        option.textContent = service.name;
        select.append(option);
      });
      if (current && !services.some((service) => service.id === current)) {
        // Never erase a dangling FK during redisplay; the application validator must reject it.
        const invalid = document.createElement("option");
        invalid.value = current;
        invalid.textContent = "Invalid Service ID: " + current;
        select.append(invalid);
      }
      select.value = current;
    });
  }

  function initializePublisherMapping(editor) {
    if (!editor?.querySelector("[data-access-mapping]")) return;
    // 原 Publisher 表单控件直接进入 Service 容器，URL 和外键始终仅有一份。
    publisherRows(editor).forEach((row) => {
      const target = mappingContainer(editor, publisherService(row));
      if (target) target.querySelector("[data-publisher-members]").append(row);
    });
    refreshPublisherMapping(editor);
  }

  function validateServiceNames(editor, name, excluded = null) {
    const trimmed = name.trim();
    if (!trimmed) {
      mappingError(editor, "Service name cannot be empty.");
      return false;
    }
    const conflict = Array.from(editor.querySelectorAll("[data-service-row]"))
      .some((row) => row !== excluded
        && row.querySelector('[name="service_name"]').value.trim().toLocaleLowerCase() === trimmed.toLocaleLowerCase());
    if (conflict) mappingError(editor, "Service name already exists.");
    return !conflict;
  }

  function validServiceUrl(editor, value) {
    if (!value.trim()) return true;
    try {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password
        || !url.hostname || ["localhost", "127.0.0.1", "::1"].includes(url.hostname.toLowerCase())) {
        throw new Error("Unsafe URL");
      }
      return true;
    } catch {
      mappingError(editor, "Enter a public HTTP(S) Access URL without credentials. Save applies full URL validation.");
      return false;
    }
  }

  function createService(editor) {
    const input = editor.querySelector("[data-new-service-name]");
    const url = editor.querySelector("[data-new-service-url]");
    if (!validateServiceNames(editor, input.value) || !validServiceUrl(editor, url.value)) return;
    const existing = new Set(Array.from(editor.querySelectorAll('[name="service_id"]')).map((row) => row.value));
    let id;
    do { id = "svc_" + crypto.randomUUID().replaceAll("-", ""); } while (existing.has(id));
    const fragment = editor.querySelector("template[data-service-template]").content.cloneNode(true);
    const row = fragment.querySelector("[data-service-row]");
    row.dataset.serviceTarget = id;
    row.querySelector('[name="service_id"]').value = id;
    row.querySelector("[data-service-id-label]").textContent = id;
    row.querySelector('[name="service_name"]').value = input.value.trim();
    row.querySelector('[name="service_access_url"]').value = url.value.trim();
    editor.querySelector("[data-service-rows]").append(fragment);
    input.value = "";
    url.value = "";
    syncServiceOptions(editor);
    refreshPublisherMapping(editor);
    mappingError(editor, "");
    setSettingsDirty(true);
  }

  function serviceSignature(row) {
    return JSON.stringify([
      row.querySelector('[name="service_id"]').value,
      row.querySelector('[name="service_name"]').value,
      row.querySelector('[name="service_access_url"]').value,
      ...Array.from(row.querySelectorAll("[data-publisher-row]")).map((member) => member.querySelector('[name="publisher_id"]').value),
    ]);
  }

  function serviceDeleteAction(editor, row, confirm) {
    const panel = row.querySelector("[data-service-delete-preview]");
    if (!confirm) {
      const members = Array.from(row.querySelectorAll("[data-publisher-row]"));
      panel.querySelector("[data-service-delete-summary]").textContent =
        "Delete Service " + row.querySelector('[name="service_name"]').value + "? "
        + members.length + " Publishers will become Unassigned:";
      const list = panel.querySelector("[data-service-delete-members]");
      list.replaceChildren();
      members.forEach((member) => {
        const item = document.createElement("li");
        item.textContent = member.querySelector('[name="publisher_name"]').value + " ("
          + member.querySelector('[name="publisher_id"]').value + ")";
        list.append(item);
      });
      row.confirmedSignature = serviceSignature(row);
      panel.hidden = false;
      return;
    }
    if (row.confirmedSignature !== serviceSignature(row)) {
      panel.hidden = true;
      mappingError(editor, "Service or Publisher membership changed. Preview deletion again.");
      return;
    }
    Array.from(row.querySelectorAll("[data-publisher-row]")).forEach((member) => movePublisher(editor, member, ""));
    row.remove();
    syncServiceOptions(editor);
    refreshPublisherMapping(editor);
    setSettingsDirty(true);
  }

  function removeJournalRow(editor, row) {
    row.remove();
    editor.querySelector("[data-journal-count]").textContent = journalRows(editor).length;
    updateJournalGroups(editor);
    setSettingsDirty(true);
  }

  function journalCascade(editor, row) {
    const publisherId = row.querySelector('[name="journal_publisher_id"]').value;
    if (!publisherId || journalRows(editor).some((other) => other !== row
      && other.querySelector('[name="journal_publisher_id"]').value === publisherId)) return null;
    const publisher = publisherRows(editor).find((item) =>
      item.querySelector('[name="publisher_id"]').value === publisherId);
    if (!publisher) return null;
    const url = publisher.querySelector('[name="publisher_access_url"]').value;
    const serviceId = publisherService(publisher);
    // 当前草稿字段可能已经清空；级联仍可能丢失最初磁盘保存的人工 URL / 映射。
    if (!url && !serviceId && !publisher.dataset.savedPublisherUrl && !publisher.dataset.savedServiceId) return null;
    return {
      publisherId,
      name: publisher.querySelector('[name="publisher_name"]').value,
      url,
      serviceId,
      savedUrl: publisher.dataset.savedPublisherUrl || "",
      savedServiceId: publisher.dataset.savedServiceId || "",
      journalName: row.querySelector('[name="journal_name"]').value,
      journalId: row.querySelector('[name="journal_issns"]').value,
      revisions: ["monitor_revision_digest", "journal_revision_digest"]
        .map((name) => editor.querySelector('[name="' + name + '"]').value),
    };
  }

  function previewJournalRemoval(editor, row) {
    const effect = journalCascade(editor, row);
    if (!effect) {
      removeJournalRow(editor, row);
      return;
    }
    const panel = editor.querySelector("[data-journal-cascade-preview]");
    panel.querySelector("[data-journal-cascade-summary]").textContent =
      "Remove Journal " + effect.journalName + " (" + effect.journalId
      + ")? This also removes its last Publisher's manual URL and Access Service mapping after Save:";
    const members = panel.querySelector("[data-journal-cascade-members]");
    members.replaceChildren();
    const item = document.createElement("li");
    item.textContent = effect.name + " (" + effect.publisherId + ") · Saved URL: "
      + (effect.savedUrl || "None") + " · Saved Service ID: " + (effect.savedServiceId || "Unassigned")
      + " · Draft URL: " + (effect.url || "None") + " · Draft Service ID: " + (effect.serviceId || "Unassigned");
    members.append(item);
    pendingJournalRemoval = {row, effect};
    panel.hidden = false;
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
    const editor = document.getElementById("settings-editor");
    if (!value) settingsBaseline = settingsSignature(editor);
    settingsDirty = !!value && settingsSignature(editor) !== settingsBaseline;
    document.documentElement.dataset.settingsDirty = settingsDirty ? "true" : "false";
    if (settingsView === "overview") renderSettingsOverview(editor);
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
    const button = editor.querySelector("[data-import-preview]");
    if (button) button.disabled = !ready;
  }

  async function loadImportFile(input) {
    const editor = input.closest("#settings-editor");
    const error = editor.querySelector("[data-import-file-error]");
    const file = input.files?.[0];
    selectedMarkdownFile = file || null;
    const preview = editor.querySelector("[data-markdown-preview]");
    if (preview) preview.remove();
    if (!file) return;
    setImportReady(editor, false);
    error.hidden = true;
    try {
      if (!/\.md$/i.test(file.name) || file.size > 1024 * 1024) {
        throw new Error("Choose a complete .md file no larger than 1 MiB.");
      }
      // 浏览器端仅作快速预检；服务器仍以原始上传字节和严格 UTF-8 解码为准。
      new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
      if (document.getElementById("settings-editor") !== editor || input.files?.[0] !== file) return;
      setImportReady(editor, true);
    } catch (failure) {
      if (document.getElementById("settings-editor") !== editor || input.files?.[0] !== file) return;
      error.textContent = failure.message || "Invalid UTF-8 Markdown file.";
      error.hidden = false;
    }
  }

  document.body.addEventListener("htmx:confirm", (event) => {
    const button = event.detail.elt;
    if (!(button instanceof Element)
        || !button.matches("[data-import-confirm], [data-import-confirm-discard]")) return;
    event.preventDefault();
    const preview = button.closest("[data-markdown-preview]");
    const file = selectedMarkdownFile;
    (async () => {
      if (!file) throw new Error("Selected Markdown file is no longer available. Preview again.");
      // 确认前重新读取原 File；隐藏字段不能代替用户实际选中的源文件。
      const bytes = await file.arrayBuffer();
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
                                (item) => item.toString(16).padStart(2, "0")).join("");
      if (digest !== preview?.dataset.sourceDigest) {
        throw new Error("Selected Markdown file changed since Preview. Select it again and Preview.");
      }
      event.detail.issueRequest(true);
    })().catch((failure) => {
      const error = button.closest("#settings-editor")?.querySelector("[data-import-file-error]");
      if (error) {
        error.textContent = failure.message;
        error.hidden = false;
      }
    });
  });

  async function exportMarkdown(editor, discard) {
    const form = editor.querySelector("#settings-form");
    const payload = new FormData(form);
    if (discard) payload.set("export_discard", "yes");
    try {
      const response = await fetch("/settings/export", {method: "POST", body: payload});
      if (!response.ok) throw new Error("Export HTTP " + response.status);
      if (response.headers.get("Content-Disposition")?.includes("attachment")) {
        const downloadUrl = URL.createObjectURL(await response.blob());
        const anchor = document.createElement("a");
        anchor.href = downloadUrl;
        anchor.download = "list.md";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(downloadUrl);
        if (discard) {
          // Discard 是明确操作；导出完成后同步刷新 UI 与真实磁盘版本。
          const page = await fetch("/settings");
          if (page.ok) {
            const documentPage = new DOMParser().parseFromString(await page.text(), "text/html");
            const replacement = documentPage.querySelector("#settings-editor");
            if (replacement) {
              editor.replaceWith(replacement);
              window.htmx?.process(replacement);
              initializeJournalGroups(replacement);
              initializePublisherMapping(replacement);
              setSettingsView(replacement, settingsView);
              selectedMarkdownFile = null;
              setSettingsDirty(false);
            }
          }
        }
      } else {
        const markup = await response.text();
        const replacement = new DOMParser().parseFromString(markup, "text/html").querySelector("#settings-editor");
        if (replacement) {
          editor.replaceWith(replacement);
          window.htmx?.process(replacement);
          initializeJournalGroups(replacement);
          initializePublisherMapping(replacement);
          setSettingsView(replacement, settingsView);
          if (!replacement.querySelector("[data-markdown-preview]")) selectedMarkdownFile = null;
          setSettingsDirty(settingsDirty);
        }
      }
    } catch (failure) {
      const notice = editor.querySelector("[data-import-file-error]");
      if (notice) {
        notice.textContent = "Markdown Export failed: " + failure.message;
        notice.hidden = false;
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
    if (event.target instanceof Element && event.target.matches("[data-journal-search]")) {
      updateJournalGroups(event.target.closest("#settings-editor"));
    }
    if (isSettingsField(event.target) && event.target.hasAttribute("name")) {
      syncPublisherLink(event.target);
      setSettingsDirty(true);
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target instanceof Element && event.target.closest("[data-transient-import]")) {
      const editor = event.target.closest("#settings-editor");
      if (event.target.matches("[data-journal-import-file]")) {
        loadImportFile(event.target);
      }
      return;
    }
    if (isSettingsField(event.target)) {
      syncPublisherLink(event.target);
      const editor = event.target.closest("#settings-editor");
      if (event.target.matches("[data-select-journal]")) {
        updateJournalGroups(editor);
      } else if (event.target.matches("[data-select-publisher]")) {
        refreshPublisherMapping(editor);
      } else if (event.target.matches('[name="publisher_access_service_id"]')) {
        const row = event.target.closest("[data-publisher-row]");
        const changed = row.closest("[data-mapping-container]")?.dataset.serviceTarget !== event.target.value;
        movePublisher(editor, row, event.target.value);
        refreshPublisherMapping(editor);
        if (changed) setSettingsDirty(true);
      } else if (event.target.matches('[name="service_name"], [name="service_access_url"]')) {
        const row = event.target.closest("[data-service-row]");
        if (validateServiceNames(editor, row.querySelector('[name="service_name"]').value, row)
            && validServiceUrl(editor, row.querySelector('[name="service_access_url"]').value)) {
          mappingError(editor, "");
          syncServiceOptions(editor);
        }
        setSettingsDirty(true);
      } else if (event.target.matches("[data-group-edit]")) {
        renameGroup(editor, event.target.closest("[data-group-row]"));
      } else if (event.target.matches('[name="journal_group"]')) {
        const row = event.target.closest(".journal-row");
        const changed = row.closest("[data-group-container]")?.dataset.groupName !== event.target.value;
        moveJournal(editor, row, event.target.value);
        updateJournalGroups(editor);
        if (changed) setSettingsDirty(true);
      } else if (event.target.hasAttribute("name")) {
        setSettingsDirty(true);
      }
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
    } else if (target.matches("[data-new-service-name], [data-new-service-url]")) {
      event.preventDefault();
      createService(editor);
    }
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const editor = target.closest("#settings-editor");
    if (editor) {
      const viewButton = target.closest("[data-settings-view-button]");
      if (viewButton) {
        setSettingsView(editor, viewButton.dataset.settingsViewButton, true);
        return;
      }
      if (target.closest("[data-cancel-access-upgrade]")) {
        target.closest("[data-access-upgrade-plan]").remove();
        return;
      }
      if (target.closest("[data-cancel-markdown-preview]")) {
        target.closest("[data-markdown-preview]").remove();
        return;
      }
      if (target.closest("[data-cancel-markdown-guard]")) {
        target.closest("[data-export-guard], [data-reload-guard]").remove();
        return;
      }
      if (target.closest("[data-export-markdown], [data-export-discard]")) {
        exportMarkdown(editor, !!target.closest("[data-export-discard]"));
        return;
      }
      if (target.closest("[data-create-service]")) {
        createService(editor);
        return;
      }
      const serviceButton = target.closest("[data-edit-service], [data-delete-service], [data-confirm-service-delete], [data-cancel-service-delete]");
      if (serviceButton) {
        const row = serviceButton.closest("[data-service-row]");
        if (serviceButton.hasAttribute("data-edit-service")) {
          if (validateServiceNames(editor, row.querySelector('[name="service_name"]').value, row)
              && validServiceUrl(editor, row.querySelector('[name="service_access_url"]').value)) {
            syncServiceOptions(editor);
            mappingError(editor, "");
          }
        } else if (serviceButton.hasAttribute("data-cancel-service-delete")) {
          row.querySelector("[data-service-delete-preview]").hidden = true;
        } else {
          serviceDeleteAction(editor, row, serviceButton.hasAttribute("data-confirm-service-delete"));
        }
        return;
      }
      const mappingButton = target.closest("[data-assign-publishers], [data-clear-publishers]");
      if (mappingButton) {
        if (mappingButton.hasAttribute("data-assign-publishers")) {
          const destination = editor.querySelector("[data-batch-service]").value;
          let changed = false;
          publisherRows(editor).filter((row) => row.querySelector("[data-select-publisher]").checked)
            .forEach((row) => { changed = movePublisher(editor, row, destination) || changed; });
          if (changed) setSettingsDirty(true);
        } else {
          publisherRows(editor).forEach((row) => { row.querySelector("[data-select-publisher]").checked = false; });
        }
        refreshPublisherMapping(editor);
        return;
      }
      const journalRemoval = target.closest("[data-confirm-journal-remove], [data-cancel-journal-remove]");
      if (journalRemoval) {
        const panel = editor.querySelector("[data-journal-cascade-preview]");
        panel.hidden = true;
        if (journalRemoval.hasAttribute("data-confirm-journal-remove") && pendingJournalRemoval) {
          const {row, effect} = pendingJournalRemoval;
          if (row.closest("#settings-editor") === editor
              && JSON.stringify(journalCascade(editor, row)) === JSON.stringify(effect)) {
            removeJournalRow(editor, row);
          } else {
            mappingError(editor, "Publisher cascade changed. Review Journal removal again.");
          }
        }
        pendingJournalRemoval = null;
        return;
      }
      if (target.closest("[data-cancel-settings-preview]")) {
        target.closest("[data-settings-deletion-preview]").remove();
        return;
      }
    }

    const groupButton = target.closest("[data-create-group], [data-rename-group], [data-delete-group], [data-group-up], [data-group-down]");
    if (groupButton) {
      groupAction(groupButton, groupButton.closest("#settings-editor"));
      return;
    }

    const confirmButton = target.closest("[data-confirm-delete], [data-cancel-delete]");
    if (confirmButton) {
      const row = confirmButton.closest("[data-group-row]");
      const panel = row.querySelector("[data-delete-confirm]");
      if (confirmButton.hasAttribute("data-cancel-delete")) {
        panel.hidden = true;
        return;
      }
      const members = Array.from(row.querySelectorAll(".journal-row"));
      if (members.length !== row.confirmedMembers?.length
          || members.some((journal, index) => journal !== row.confirmedMembers[index])) {
        panel.hidden = true;
        groupError(row.closest("#settings-editor"), "Group members changed. Review the deletion again.");
        return;
      }
      const editor = row.closest("#settings-editor");
      assignJournals(editor, members, "");
      row.remove();
      syncGroupOptions(editor);
      updateJournalGroups(editor);
      setSettingsDirty(true);
      return;
    }

    const selectionButton = target.closest("[data-select-visible], [data-clear-selection], [data-assign-selected]");
    if (selectionButton) {
      const editor = selectionButton.closest("#settings-editor");
      if (selectionButton.hasAttribute("data-assign-selected")) {
        const selected = journalRows(editor).filter((row) => row.querySelector("[data-select-journal]").checked);
        assignJournals(editor, selected, editor.querySelector("[data-batch-group]").value);
      } else {
        journalRows(editor).forEach((row) => {
          if (selectionButton.hasAttribute("data-clear-selection") || !row.hidden) {
            row.querySelector("[data-select-journal]").checked =
              selectionButton.hasAttribute("data-select-visible");
          }
        });
        updateJournalGroups(editor);
      }
      return;
    }

    const addButton = target.closest("[data-add-journal]");
    if (addButton) {
      const editor = addButton.closest("#settings-editor");
      const rows = groupContainer(editor, "")?.querySelector("[data-group-members]");
      const template = editor?.querySelector("template[data-journal-template]");
      if (rows && template instanceof HTMLTemplateElement) {
        const fragment = template.content.cloneNode(true);
        const added = fragment.querySelector(".journal-row");
        const nextOrder = Math.max(-1, ...journalRows(editor).map((row) =>
          Number(row.querySelector('[name="journal_order"]').value))) + 1;
        added.querySelector('[name="journal_order"]').value = String(nextOrder);
        rows.append(fragment);
        added?.querySelector('[name="journal_issns"]')?.focus();
        const count = editor.querySelector("[data-journal-count]");
        if (count) count.textContent = journalRows(editor).length;
        syncGroupOptions(editor);
        updateJournalGroups(editor);
        setSettingsDirty(true);
      }
      return;
    }

    const removeButton = target.closest("[data-remove-journal]");
    if (removeButton) {
      const editor = removeButton.closest("#settings-editor");
      previewJournalRemoval(editor, removeButton.closest(".journal-row"));
    }
  });

  document.addEventListener("dragstart", (event) => {
    const publisher = event.target instanceof Element ? event.target.closest("[data-publisher-row]") : null;
    if (publisher?.closest("#settings-editor")) {
      draggedPublisher = publisher;
      event.dataTransfer?.setData("text/plain", publisher.querySelector('[name="publisher_id"]').value);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      return;
    }
    const row = event.target instanceof Element ? event.target.closest(".journal-row") : null;
    if (!row?.closest("#settings-editor")) return;
    draggedJournal = row;
    event.dataTransfer?.setData("text/plain", row.querySelector('[name="journal_issns"]').value);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  });

  document.addEventListener("dragover", (event) => {
    const mappingTarget = event.target instanceof Element ? event.target.closest("[data-mapping-container]") : null;
    if (draggedPublisher && mappingTarget?.closest("#settings-editor")) {
      event.preventDefault();
      mappingTarget.dataset.dropActive = "true";
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      return;
    }
    const target = event.target instanceof Element ? event.target.closest("[data-group-drop]") : null;
    if (!draggedJournal || !target?.closest("#settings-editor")) return;
    event.preventDefault();
    target.dataset.dropActive = "true";
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  });

  document.addEventListener("dragleave", (event) => {
    const mappingTarget = event.target instanceof Element ? event.target.closest("[data-mapping-container]") : null;
    if (mappingTarget) mappingTarget.dataset.dropActive = "false";
    const target = event.target instanceof Element ? event.target.closest("[data-group-drop]") : null;
    if (target) target.dataset.dropActive = "false";
  });

  document.addEventListener("drop", (event) => {
    const mappingTarget = event.target instanceof Element ? event.target.closest("[data-mapping-container]") : null;
    if (draggedPublisher && mappingTarget?.closest("#settings-editor")) {
      event.preventDefault();
      const editor = mappingTarget.closest("#settings-editor");
      if (movePublisher(editor, draggedPublisher, mappingTarget.dataset.serviceTarget)) setSettingsDirty(true);
      refreshPublisherMapping(editor);
      editor.querySelectorAll("[data-mapping-container]").forEach((container) => { container.dataset.dropActive = "false"; });
      draggedPublisher = null;
      return;
    }
    const target = event.target instanceof Element ? event.target.closest("[data-group-drop]") : null;
    if (!draggedJournal || !target?.closest("#settings-editor")) return;
    event.preventDefault();
    const editor = target.closest("#settings-editor");
    assignJournals(editor, [draggedJournal], target.dataset.groupName);
    editor.querySelectorAll("[data-group-drop]").forEach((row) => { row.dataset.dropActive = "false"; });
    draggedJournal = null;
  });

  document.addEventListener("dragend", () => {
    draggedJournal = null;
    draggedPublisher = null;
    document.querySelectorAll("[data-group-drop]").forEach((row) => { row.dataset.dropActive = "false"; });
    document.querySelectorAll("[data-mapping-container]").forEach((row) => { row.dataset.dropActive = "false"; });
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
    if (event.detail.target.id === "danger-zone") {
      if (event.detail.xhr.status === 400 || event.detail.xhr.status === 409) {
        event.detail.shouldSwap = true;
        event.detail.isError = false;
      }
      return;
    }
    if (event.detail.target.id === "settings-editor") {
      const previous = document.getElementById("settings-editor");
      overviewExpandedAfterSwap = new Set(Array.from(previous?.querySelectorAll("[data-overview-content] details") || [])
        .filter((details) => details.open).map((details) => details.dataset.overviewKey));
      const viewport = previous?.querySelector("[data-journal-viewport]");
      settingsJournalScroll = viewport?.scrollTop ?? null;
      settingsDisclosures = Array.from(document.getElementById("settings-editor").querySelectorAll("[data-settings-disclosure]")).filter(panel => panel.open).map(panel => panel.dataset.settingsDisclosure);
      settingsPublisherScroll = document.getElementById("settings-editor")?.querySelector("[data-publisher-viewport]")?.scrollTop ?? null;
      return;
    }
    if (event.detail.target.id !== "workspace-root") {
      return;
    }
    // 无效 expected-status 的 400 HTML 也要展示刷新后的状态；403 仍不 swap。
    if (event.detail.xhr.status === 400 || event.detail.xhr.status === 409) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
    const root = document.getElementById("workspace-root");
    const list = root?.querySelector("#paper-list");
    workspaceScroll = list ? { view: root.dataset.activeView, top: list.scrollTop } : null;
  });

  function showImportError(event) {
    if (!event.detail.elt?.closest("[data-import-form], [data-import-poll]")) return;
    const notice = document.querySelector("[data-import-error]");
    if (!notice) return;
    const code = event.detail.xhr?.status;
    notice.textContent = code
      ? `Import request was rejected (HTTP ${code}). Refresh this page to check the current batch.`
      : "Cannot reach the local application. Refresh this page to check actual import progress.";
    notice.hidden = false;
  }
  document.body.addEventListener("htmx:responseError", showImportError);
  document.body.addEventListener("htmx:sendError", showImportError);

  document.body.addEventListener("htmx:afterSwap", (event) => {
    syncRunAnnouncement();
    syncWorkspaceSelection();
    sizeWorkspacePanes();
    if (event.detail.target.id === "workspace-root") {
      restoreWorkspaceScroll();
    } else if (event.detail.target.id === "settings-editor") {
      draggedJournal = null;
      draggedPublisher = null;
      pendingJournalRemoval = null;
      const editor = document.getElementById("settings-editor");
      initializeJournalGroups(editor);
      initializePublisherMapping(editor);
      setSettingsView(editor, settingsView);
      if (!editor.querySelector("[data-markdown-preview]")) selectedMarkdownFile = null;
      if (settingsDirty) setSettingsDirty(true);
      else setSettingsDirty(false);
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
  window.addEventListener("beforeunload", (event) => {
    if (!settingsDirty) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  initializeJournalGroups(document.getElementById("settings-editor"));
  initializePublisherMapping(document.getElementById("settings-editor"));
  setSettingsView(document.getElementById("settings-editor"), settingsView);
  setSettingsDirty(false);
  revealWorkspaceHealth();
  syncRunAnnouncement();
  syncWorkspaceSelection();
  sizeWorkspacePanes();
})();
