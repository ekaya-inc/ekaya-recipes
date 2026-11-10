"use strict";
// Access log tab: who other than you opened which files, read from the Google Workspace audit
// log. Loaded after the main script, which supplies run, h, chip, $, plural, googleHref, readMe,
// signInPrompt, REQ and model.
(function () {
  const EVENTS = {
    view: { label: "Viewed", tone: null },
    download: { label: "Downloaded", tone: "warn" },
    access_item_content: { label: "Read by an app", tone: "info" },
  };
  const LIMIT = 1000;
  const MAX_FILES_SHOWN = 200;
  const openKeys = new Set();
  const stamp = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const when = (value) => { const d = new Date(value); return isNaN(d) ? String(value) : stamp.format(d); };
  let loading = false;
  let data = null;

  /* ---------- Tabs ---------- */

  function selectTab(name) {
    for (const tab of document.querySelectorAll('[role="tab"]')) {
      const on = tab.id === `tab-${name}`;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      $(tab.getAttribute("aria-controls")).hidden = !on;
    }
    // The sign-in prompt belongs to whichever view is showing.
    const prompt = $("signin");
    prompt.hidden = true;
    $(`panel-${name}`).prepend(prompt);
  }
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => selectTab(tab.id.replace("tab-", "")));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      selectTab(next.id.replace("tab-", ""));
    });
  });

  /* ---------- Load ---------- */

  function status(text, isError) {
    const p = $("log-status");
    p.hidden = !text;
    p.textContent = text || "";
    p.className = isError ? "status err" : "status";
  }

  async function load() {
    if (loading) return;
    loading = true;
    $("log-load").disabled = true;
    $("log-results").hidden = true;
    $("signin").hidden = true;
    try {
      const events = [...document.querySelectorAll("#log-events input:checked")].map((i) => i.value);
      if (!events.length) throw new Error("Choose at least one kind of access.");
      const days = Number($("log-range").value);
      status("Checking who is signed in…");
      const me = (model && model.me) || await readMe();
      status("Reading the audit log. This can take up to a minute…");
      const start = new Date(Date.now() - days * 86400000).toISOString().replace(/\.\d+Z$/, "Z");
      const body = await run(REQ.audit, { start_time: start, events: events.join(","), exclude_actor: me.email }, LIMIT);
      const rows = body.rows || [];
      data = group(rows, me, days);
      data.truncated = body.truncated === true || rows.length >= LIMIT;
      status("");
      render();
    } catch (err) {
      if (err.status === 409 && err.code === "user_sign_in_required" && err.queryId) {
        status("");
        signInPrompt(err.queryId, "This connection needs your Google audit-log credentials. Open the link to enter them, then load the access log again.", load);
      } else {
        status(`Couldn't load the access log. ${err.message}`, true);
      }
    } finally {
      loading = false;
      $("log-load").disabled = false;
    }
  }

  function group(rows, me, days) {
    const domain = (me.email.split("@")[1] || "").toLowerCase();
    const people = new Map();
    let skipped = 0;
    for (const r of rows) {
      // Some accesses carry no identity, typically someone opening an "anyone with the link" file
      // while signed out. They are kept as one group; system accounts are left out.
      const unidentified = !r.actor_email && r.actor_kind === "unknown";
      if (!r.actor_email && !unidentified) { skipped++; continue; }
      const key = unidentified ? "(unidentified)" : r.actor_email.toLowerCase();
      let p = people.get(key);
      if (!p) {
        p = { key, email: unidentified ? "Not identified" : r.actor_email, unidentified, external: !unidentified && !!domain && !key.endsWith(`@${domain}`), docs: new Map(), events: 0, last: "" };
        people.set(key, p);
      }
      const id = r.doc_id || r.doc_title;
      let d = p.docs.get(id);
      if (!d) {
        d = { id: r.doc_id, title: r.doc_title || "Untitled", owner: r.owner, visibility: r.visibility, counts: {}, apps: new Set(), last: "" };
        p.docs.set(id, d);
      }
      d.counts[r.event] = (d.counts[r.event] || 0) + 1;
      if (r.app_name) d.apps.add(r.app_name);
      if (r.time > d.last) d.last = r.time;
      if (r.time > p.last) p.last = r.time;
      p.events++;
    }
    return { people, skipped, total: rows.length, days, me };
  }

  /* ---------- Render ---------- */

  function render() {
    $("log-results").hidden = false;
    const q = $("log-q").value.trim().toLowerCase();
    const list = [...data.people.values()].map((p) => {
      const hit = !q || p.email.toLowerCase().includes(q);
      const docs = [...p.docs.values()].filter((d) => hit || (d.title || "").toLowerCase().includes(q) || (d.owner || "").toLowerCase().includes(q));
      return { p, docs };
    }).filter((x) => x.docs.length).sort((a, b) => (a.p.last < b.p.last ? 1 : -1));

    const everyone = [...data.people.values()];
    $("log-facts").replaceChildren(
      h("dt", {}, "Period"), h("dd", {}, `Last ${data.days} days`),
      h("dt", {}, "People other than you"), h("dd", {}, String(everyone.length)),
      h("dt", {}, "Files opened"), h("dd", {}, String(everyone.reduce((n, p) => n + p.docs.size, 0))),
      h("dt", {}, "Events"), h("dd", {}, String(data.total - data.skipped)),
    );
    const caveats = [];
    if (data.truncated) caveats.push(`Showing the newest ${LIMIT} events; there are more. Choose a shorter period to see the rest.`);
    if (data.skipped) caveats.push(`${plural(data.skipped, "event")} from system accounts left out.`);
    if (data.people.has("(unidentified)")) caveats.push("\"Not identified\" groups accesses Google recorded without a person, usually someone opening a file shared with \"anyone with the link\" while signed out.");
    caveats.push("Google's audit log can lag by a few minutes or more and keeps only recent history. \"Read by an app\" means an application opened the file on that person's behalf.");
    $("log-caveats").replaceChildren(...caveats.map((c) => h("li", {}, c)));

    const target = $("log-people");
    if (!list.length) {
      target.replaceChildren(h("li", { class: "empty-note" }, everyone.length
        ? "Nobody matches that search."
        : "No one other than you opened files in this period."));
      return;
    }
    target.replaceChildren(...list.map(({ p, docs }) => personCard(p, docs)));
  }

  function personCard(p, docs) {
    const body = h("div", { class: "files" });
    const details = h("details", {},
      h("summary", {},
        h("span", { class: "who" }, p.email),
        p.external ? chip("outside your domain", "warn") : null,
        p.unidentified ? chip("signed out or not identified", "warn") : null,
        h("span", { class: "mail" }, `last ${when(p.last)}`),
        h("span", { class: "count" }, `${plural(docs.length, "file")} · ${plural(p.events, "event")}`)),
      body);
    const fill = () => { if (!body.childNodes.length) body.append(fileTable(docs)); };
    details.addEventListener("toggle", () => {
      if (details.open) { openKeys.add(p.key); fill(); } else openKeys.delete(p.key);
    });
    if (openKeys.has(p.key)) { details.open = true; fill(); }
    return h("li", { class: "person" }, details);
  }

  function sharing(visibility) {
    if (visibility === "shared_externally") return chip("shared externally", "warn");
    if (visibility === "people_with_link") return chip("anyone with the link", "warn");
    if (visibility === "shared_internally") return h("span", { class: "meta" }, "shared inside your organization");
    if (visibility === "private") return h("span", { class: "meta" }, "private");
    return h("span", { class: "meta" }, visibility || "");
  }

  function fileTable(docs) {
    const sorted = [...docs].sort((a, b) => (a.last < b.last ? 1 : -1));
    const rows = sorted.slice(0, MAX_FILES_SHOWN);
    const wrap = h("div", {}, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Owner"), h("th", {}, "Sharing"), h("th", {}, "What happened"), h("th", {}, "Last"))),
      h("tbody", {}, ...rows.map((d) => {
        const href = d.id ? googleHref(`https://drive.google.com/open?id=${encodeURIComponent(d.id)}`) : null;
        const what = Object.keys(d.counts).flatMap((name) => {
          const info = EVENTS[name] || { label: name, tone: null };
          return [chip(`${info.label}${d.counts[name] > 1 ? ` ×${d.counts[name]}` : ""}`, info.tone), " "];
        });
        const apps = d.apps.size ? h("span", { class: "meta" }, ` via ${[...d.apps].join(", ")}`) : null;
        return h("tr", {},
          h("td", {}, href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, d.title) : d.title),
          h("td", { class: "meta" }, d.owner || ""),
          h("td", {}, sharing(d.visibility)),
          h("td", {}, what, apps),
          h("td", { class: "meta" }, when(d.last)));
      }))));
    if (sorted.length > MAX_FILES_SHOWN) wrap.append(h("p", { class: "meta" }, `Showing the ${MAX_FILES_SHOWN} most recent of ${sorted.length} files.`));
    return wrap;
  }

  $("log-load").addEventListener("click", load);
  $("log-q").addEventListener("input", () => { if (data) render(); });
})();
