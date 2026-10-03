"use strict";

// Only the visible Research record and its user-selected PDF Download action.
(() => {
  if (window.top !== window || !AcquisitionProtocol.ebscoRecordContext(window.location.href)) return;
  const pageUrl = window.location.href;
  const DOWNLOAD = 'button[data-auto="bulk-download-modal-download-button"]';
  const PDF = 'input[data-auto="bulk-download-formats-group-input"][name="fullText"][value="pdf"][type="radio"]';
  const visible = node => {
    if (!node || !node.getClientRects().length || node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    for (let own = node; own; own = own.parentElement) {
      const style = getComputedStyle(own);
      if (style.display === "none" || style.visibility !== "visible" || style.opacity === "0") return false;
    }
    return true;
  };
  const text = node => node.textContent.trim().replace(/\s+/g, " ");
  // Extension-injected descendant links are not the record's DOI text.
  const ownText = node => [...node.childNodes].filter(child => child.nodeType === 3)
    .map(child => child.textContent).join(" ").trim().replace(/\s+/g, " ");
  const one = (root, selector) => {
    const nodes = [...root.querySelectorAll(selector)].filter(visible);
    return nodes.length === 1 ? nodes[0] : null;
  };
  const field = heading => {
    const list = heading?.nextElementSibling;
    if (!list || !list.matches("ul") || !visible(heading) || !visible(list)) return null;
    const values = [...list.children].filter(node => node.matches("li") && visible(node));
    return values.length === 1 ? values[0] : null;
  };

  const METADATA = 'div[data-auto="record-html-metadata"] > article[lang]';
  const ENTRY = 'button[data-auto="card-call-to-action-download-button"][value="download"]';
  function readRecord() {
    const record = one(document, METADATA);
    if (!record) return null;
    const doiHeadings = [...record.querySelectorAll("h3")].filter(node => visible(node) && text(node) === "DOI");
    const doi = doiHeadings.length === 1 ? field(doiHeadings[0]) : null;
    if (!doi || doi.id !== "DOI") return null;
    return {node: record, text: text(record), fields: {doi: ownText(doi).toLowerCase()}};
  }

  chrome.runtime.sendMessage({type: "ebsco_context"}, context => {
    if (chrome.runtime.lastError || !context?.doi || !Number.isSafeInteger(context.navigation_epoch) ||
        context.navigation_epoch < 1) return;
    let captured = null;
    document.addEventListener("click", event => {
      if (!event.isTrusted || !AcquisitionProtocol.sameEbscoRecordPage(window.location.href, pageUrl)) return;
      const entry = event.target.closest(ENTRY);
      if (entry && visible(entry) && !entry.disabled) {
        captured = readRecord();
        return;
      }
      const button = event.target.closest(DOWNLOAD);
      if (!button || button !== one(document, DOWNLOAD) || button.disabled ||
          !["下载", "Download"].includes(text(button))) return;
      // An accessible modal may hide/inert the background record. Keep the
      // earlier visible entry observation only while its exact DOM is unchanged.
      const observed = captured;
      if (!observed || !observed.node.isConnected || text(observed.node) !== observed.text ||
          document.querySelectorAll(METADATA).length !== 1 ||
          document.querySelectorAll(METADATA)[0] !== observed.node || observed.fields.doi !== context.doi) return;
      const pdfs = [...document.querySelectorAll(PDF)].filter(node => node.checked && !node.disabled &&
        !node.closest('[hidden], [inert], [aria-hidden="true"]') &&
        [...node.labels].some(label => visible(label) && /^PDF(?:\b|[（(])/i.test(text(label))));
      if (pdfs.length !== 1) return;
      chrome.runtime.sendMessage({type: "ebsco_pdf_action", task_id: context.task_id, pageUrl,
        navigation_epoch: context.navigation_epoch, action_time: Date.now(), record: observed.fields}, () => { void chrome.runtime.lastError; });
    }, true);
  });
})();
