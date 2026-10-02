"use strict";

// Only the visible XMU result adapter; no publisher DOM or private API parsing.
(() => {
  const CONTEXT_ATTEMPTS = 5;
  const CONTEXT_RETRY_MS = 150;
  const deadline = Date.now() + 1000;
  let attempts = 0;

  function eligibleHandshakeUrl() {
    if (window.top !== window) return null;
    const pageUrl = window.location.href;
    try {
      // Page DOI checks URL shape only; the frozen DOI comes from the worker.
      const rft = new URL(pageUrl).searchParams.get("rft_id");
      const doi = rft?.startsWith("info:doi/") ? rft.slice(9) : null;
      return AcquisitionProtocol.validDoi(doi) && AcquisitionProtocol.resolverContext(pageUrl, doi)
        ? pageUrl : null;
    } catch { return null; }
  }

  function requestContext() {
    if (Date.now() > deadline || !eligibleHandshakeUrl()) return;
    attempts++;
    chrome.runtime.sendMessage({type: "resolver_context"}, context => {
      const pageUrl = eligibleHandshakeUrl();
      if (!pageUrl || Date.now() > deadline) return;
      if (!chrome.runtime.lastError && context?.doi) {
        if (AcquisitionProtocol.resolverContext(pageUrl, context.doi)) observe(context);
        return;
      }
      if (attempts < CONTEXT_ATTEMPTS && Date.now() + CONTEXT_RETRY_MS <= deadline) {
        setTimeout(requestContext, CONTEXT_RETRY_MS);
      }
    });
  }

  requestContext();

  function observe(context) {
    let finished = false, settleUsed = false, settleFrame = null;
    // The SPA may populate providers after document_idle. Observe human waits
    // immediately; take one bounded result snapshot without a polling loop.
    // The strict /redirect sentence must be observed before its auto-navigation.
    const observer = new MutationObserver(() => inspect(false));
    const timer = setTimeout(() => inspect(true), 15000);
    observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true});
    inspect(false);

    function stop() {
      finished = true;
      observer.disconnect();
      clearTimeout(timer);
      if (settleFrame !== null) cancelAnimationFrame(settleFrame);
      settleFrame = null;
    }

    function currentRedirect() {
      if (finished) return false;
      const url = window.location.href;
      if (window.top !== window || !AcquisitionProtocol.resolverContext(url, context.doi)) {
        stop(); return false;
      }
      return new URL(url).pathname === "/redirect";
    }

    function settleOnce() {
      if (settleUsed) return;
      settleUsed = true;
      // One render-settle operation per document, never a retry-until-visible loop.
      settleFrame = requestAnimationFrame(() => {
        settleFrame = null;
        if (!currentRedirect()) return;
        settleFrame = requestAnimationFrame(() => {
          settleFrame = null;
          if (currentRedirect()) inspect(false);
        });
      });
    }

    function report(pageUrl, choices, human_required, unavailable = null) {
      stop();
      chrome.runtime.sendMessage({type: "resolver_observation", pageUrl,
        choices, human_required, unavailable}, () => { void chrome.runtime.lastError; });
    }

    function inspect(finalSnapshot) {
      if (finished) return;
      const pageUrl = window.location.href;
      if (window.top !== window || !AcquisitionProtocol.resolverContext(pageUrl, context.doi)) {
        stop(); return;
      }
      const visible = node => {
        const style = getComputedStyle(node);
        return style.display !== "none" && style.visibility === "visible" && style.opacity !== "0" &&
          node.getClientRects().length > 0 && !node.closest('[hidden], [inert], [aria-hidden="true"]');
      };
      const humanRequired = [...document.querySelectorAll('input[type="password"], iframe[src*="captcha"], #challenge-stage')].some(visible);
      if (humanRequired) { report(pageUrl, [], true); return; }
      const earlyRedirect = !finalSnapshot && new URL(pageUrl).pathname === "/redirect";
      if (!finalSnapshot && !earlyRedirect) return;
      if ([...document.querySelectorAll('[aria-busy="true"]')].some(visible)) {
        if (!finalSnapshot) return;
        report(pageUrl, [], false, "not_ready"); return;
      }
      const category = text => {
        const label = text.trim().replace(/\s+/g, " ").toLowerCase();
        if (label === "full text") return "FullText";
        if (["smartlink", "smartlinks", "smart link", "smart links"].includes(label)) return "SmartLinks";
        return null;
      };
      const choices = [];
      for (const anchor of document.querySelectorAll('a[href]')) {
        if (!visible(anchor)) continue;
        const label = anchor.textContent.trim().replace(/\s+/g, " ");
        if (/search\s*engines|document\s*delivery|\bother\b|research|help|tool|privacy|terms/i.test(label)) continue;
        // The observed FTF redirect has a specific visible provider sentence,
        // rather than a result list. Arbitrary SmartLinks anchors still fail.
        const redirectText = anchor.closest('p') || anchor;
        const visibleLabel = anchor.innerText.trim().replace(/\s+/g, " ");
        const redirectCategory = new URL(pageUrl).pathname === "/redirect" && visible(redirectText) &&
          /^Find this article in full text from EBSCOhost (SmartLinks|Full Text)\.?$/i.test(redirectText.innerText.trim().replace(/\s+/g, " ")) &&
          /^(?:Find this article in full text from )?EBSCOhost (SmartLinks|Full Text)$/i.test(visibleLabel)
          ? (/SmartLinks$/i.test(visibleLabel) ? "SmartLinks" : "FullText") : null;
        if (earlyRedirect && !redirectCategory) continue;
        const group = anchor.closest('section, [role="group"]');
        const row = anchor.closest('li, [role="listitem"], tr, [role="row"]');
        const collection = row?.closest('ul, ol, [role="list"], table, [role="table"], section, [role="group"]');
        // Even an exact label needs a visible semantic result structure. A
        // standalone Full Text/SmartLink anchor is not a resolver choice.
        const structure = collection || group;
        if (!redirectCategory && (!structure || !visible(structure) || (row && !visible(row)))) continue;
        const heading = group?.querySelector('h2, h3, [role="heading"]');
        // A visible exact category, not an href or hidden data attribute, is the
        // eligibility signal. Anchors remain in visible document/provider order.
        const ownCategory = category(label);
        const groupCategory = heading && visible(heading) && heading.closest('section, [role="group"]') === group ? category(heading.textContent) : null;
        if (heading && !groupCategory) continue;
        // Exact anchor labels require a result row; a group without a row must
        // establish its category through its own visible eligible heading.
        if (!row && !groupCategory && !redirectCategory) continue;
        const eligible = redirectCategory || groupCategory || ownCategory;
        if (!eligible || !label || label.length > 80) continue;
        const target = AcquisitionProtocol.safeUrl(anchor.href);
        if (!target || choices.some(c => c.target === target)) continue;
        choices.push({category: eligible, label, target});
      }
      // Excess/ambiguous context cannot be truncated into an arbitrary choice.
      if (choices.length > 6) { report(pageUrl, [], false, "choice_overflow"); return; }
      if (!finalSnapshot && choices.length === 0) {
        // Hidden DOM only schedules a future strict inspection; it grants no choice.
        if (earlyRedirect && [...document.querySelectorAll('a[href]')].some(anchor =>
          /^Find this article in full text from EBSCOhost (SmartLinks|Full Text)\.?$/i
            .test((anchor.closest('p') || anchor).textContent.trim().replace(/\s+/g, " ")))) settleOnce();
        return;
      }
      report(pageUrl, choices, false);
    }
  }
})();
