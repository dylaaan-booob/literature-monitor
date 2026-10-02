"use strict";

// This extension-owned UI supplies fixed gestures, never URLs or authority.
const status = document.getElementById("status");
let pending = false;

async function action(name, choiceId = null) {
  if (pending) return;
  pending = true;
  try {
    const reply = await chrome.runtime.sendMessage({type: "task_action", action: name, choice_id: choiceId});
    status.textContent = reply?.ok ? (name === "arm" ? "Armed for 10 seconds. Download in this same tab." : "Request sent. Continue in this task tab.") : "Action unavailable. Check the current task in Literature Monitor.";
  } catch {
    status.textContent = "Companion unavailable. Check the current task in Literature Monitor.";
  } finally { pending = false; }
}

for (const name of ["ready", "arm", "download", "check", "fallback", "human"]) {
  document.getElementById(name).addEventListener("click", () => { void action(name); });
}

(async () => {
  try {
    const reply = await chrome.runtime.sendMessage({type: "task_ui"});
    if (!reply?.ok) { status.textContent = "Open the claimed Literature Monitor task tab first."; return; }
    document.getElementById("actions").hidden = false;
    const task = reply.task;
    if (!task) { status.textContent = "Retry connection on this handoff tab if needed."; return; }
    status.textContent = task.ambiguous ? "Browser actions are unavailable. Continue in Literature Monitor." : "Use only this task tab for the selected version.";
    document.getElementById("browser-actions").hidden = task.ambiguous;
    document.getElementById("ready").hidden = true;
    document.getElementById("fallback").hidden = !task.can_fallback;
    document.getElementById("check").hidden = !task.candidate;
    for (const choice of task.choices) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = choice.category + ": " + choice.label;
      button.addEventListener("click", () => { void action("choose", choice.id); });
      document.getElementById("choices").append(button);
    }
  } catch { status.textContent = "Companion unavailable. Reload the extension if needed."; }
})();
