"use strict";

// Deterministic transport helpers. Runtime URLs stay in memory/session only.
const AcquisitionProtocol = (() => {
  function validDoi(value) {
    return typeof value === "string" && value.length <= 200 && value === value.trim().toLowerCase() &&
      /^10\.[0-9]{4,9}\/[^\s]+$/.test(value);
  }

  function safeUrl(value) {
    if (typeof value !== "string" || value.length > 512 || /[\s\\\x00-\x1f\x7f]/.test(value)) return null;
    try {
      const url = new URL(value);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.hash ||
          url.hostname === "localhost" || url.hostname.endsWith(".localhost") ||
          /^[0-9.]+$/.test(url.hostname) || url.hostname.includes(":")) return null;
      return url.href;
    } catch { return null; }
  }

  function doiUrl(doi) {
    return "https://doi.org/" + doi.split("/").map(part => encodeURIComponent(part)
      .replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase())).join("/");
  }

  function downloadTransport(item) {
    const url = safeUrl(item.url), finalUrl = safeUrl(item.finalUrl);
    if (url && finalUrl) return {kind: "http", url, finalUrl};
    // Blob tokens are download transport only, never navigation authority.
    if (typeof item.url !== "string" || item.url !== item.finalUrl || item.url.length > 512 ||
        !item.url.startsWith("blob:https://") || /[\s\\\x00-\x1f\x7f]/.test(item.url)) return null;
    try {
      const blob = new URL(item.url);
      const embedded = new URL(blob.pathname);
      if (blob.protocol !== "blob:" || blob.search || blob.hash ||
          embedded.protocol !== "https:" || !safeUrl(embedded.href) ||
          embedded.search || embedded.hash || blob.origin !== embedded.origin ||
          !/^\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(embedded.pathname)) return null;
      return {kind: "blob", origin: embedded.origin, url: null, finalUrl: null};
    } catch { return null; }
  }

  function plan(value) {
    if (!value || Object.keys(value).sort().join(",") !== "direct_url,doi,task_id" ||
        !BrowserHandoffProtocol.validTaskId(value.task_id) || !validDoi(value.doi)) return null;
    if (value.direct_url !== doiUrl(value.doi) || !safeUrl(value.direct_url)) return null;
    return structuredClone(value);
  }

  function resolverUrl(doi) {
    const url = new URL("https://resolver.ebsco.com/c/45yels/result");
    url.search = new URLSearchParams({rft_id: "info:doi/" + doi, "x-opid": "45yels",
      customer: "s1215021", group: "main", profile: "ftf"}).toString();
    return url.href;
  }

  function resolverContext(value, doi) {
    try {
      const url = new URL(value);
      const expected = new URL(resolverUrl(doi));
      return safeUrl(value) !== null && url.origin === expected.origin &&
        [expected.pathname, "/redirect"].includes(url.pathname) &&
        [...url.searchParams.keys()].length === 5 &&
        [...expected.searchParams].every(([key, v]) => url.searchParams.getAll(key).length === 1 && url.searchParams.get(key) === v);
    } catch { return false; }
  }

  function sameResolverObservationContext(senderUrl, pageUrl, doi) {
    if (!resolverContext(senderUrl, doi) || !resolverContext(pageUrl, doi)) return false;
    const sourcePath = new URL(senderUrl).pathname, currentPath = new URL(pageUrl).pathname;
    // A surviving result content-script context may observe /redirect. The
    // reverse transition and all other paths remain outside this relation.
    return sourcePath === currentPath ||
      (sourcePath === new URL(resolverUrl(doi)).pathname && currentPath === "/redirect");
  }

  function ebscoRecordContext(value) {
    const safe = safeUrl(value);
    if (!safe) return false;
    const url = new URL(safe);
    return url.origin === "https://research.ebsco.com" &&
      /^\/c\/[a-zA-Z0-9_-]+\/search\/details\/[a-zA-Z0-9_-]+$/.test(url.pathname);
  }

  function sameEbscoRecordPage(a, b) {
    if (![a, b].every(value => ebscoRecordContext(value) && !value.includes("#"))) return false;
    const urls = [new URL(a), new URL(b)];
    // Reject dot-segment normalization: compare the supplied paths, not a
    // different record reached by URL parsing. Query state has no authority.
    if (![a, b].every((value, i) =>
      value.match(/^https:\/\/[^/?#]+([^?#]*)/i)?.[1] === urls[i].pathname)) return false;
    return urls[0].origin === urls[1].origin && urls[0].pathname === urls[1].pathname;
  }

  function sanitizedIdentity(value) {
    const safe = safeUrl(value);
    return safe ? {scheme: new URL(safe).protocol, host: new URL(safe).hostname} : null;
  }

  function boundedPayload(value) {
    return new TextEncoder().encode(JSON.stringify(value)).length <= 4096;
  }

  function command(value, taskId) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !boundedPayload(value) ||
        value.task_id !== taskId || !BrowserHandoffProtocol.validTaskId(taskId)) return null;
    const fields = Object.keys(value).sort().join(",");
    if (value.type === "START") {
      const validated = plan(value.plan);
      return fields === "plan,task_id,type" && validated?.task_id === taskId ? {...value, plan: validated} : null;
    }
    if (value.type === "CHOOSE") {
      return fields === "choice_id,task_id,type" && Number.isSafeInteger(value.choice_id) &&
        value.choice_id >= 0 && value.choice_id < 6 ? structuredClone(value) : null;
    }
    return ["PUBLISHER_EXHAUSTED", "DOWNLOAD_CURRENT"].includes(value.type) &&
      fields === "task_id,type" ? structuredClone(value) : null;
  }

  return Object.freeze({validDoi, safeUrl, downloadTransport, doiUrl, plan, resolverUrl, resolverContext,
    sameResolverObservationContext, ebscoRecordContext, sameEbscoRecordPage,
    sanitizedIdentity, boundedPayload, command});
})();
