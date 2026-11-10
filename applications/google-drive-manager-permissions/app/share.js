"use strict";
// Change requests: a confirm-before-changing card. Claude prepares a link, you open it, read what
// would change, and Confirm or Decline. Nothing changes until Confirm, the link carries no secrets,
// and nothing is stored. Two actions, each over any mix of files, folders and shared drives:
//   .../apps/google-drive-manager/?share_to=EMAIL&role=reader|commenter|writer&files=ID,ID&drives=ID[&notify=0]
//   .../apps/google-drive-manager/?remove_from=EMAIL&files=ID,ID&drives=ID
// Loaded after the main script, which supplies run, h, chip, $, plural, googleHref,
// readFilePermissions, removeOne, roleLabel, signInPrompt, Uncertain and REQ.
(function () {
  const params = new URLSearchParams(location.search);
  const sharing = params.has("share_to");
  const removing = params.has("remove_from");
  if (!sharing && !removing) return;

  const card = $("share-request");
  const scanButton = $("scan");
  const scanWasPrimary = scanButton.classList.contains("primary");
  const GRANTABLE = ["reader", "commenter", "writer"];
  const FOLDER = "application/vnd.google-apps.folder";
  const ID = /^[A-Za-z0-9_-]{10,128}$/;
  const MAX_TARGETS = 20;

  // One primary button per view: while a request is open, its Confirm is the action that matters.
  scanButton.classList.remove("primary");
  card.hidden = false;

  function close() {
    history.replaceState(null, "", location.pathname); // a reload must not offer the same change again
    if (scanWasPrimary) scanButton.classList.add("primary");
  }
  function show(...children) { card.replaceChildren(...children); card.hidden = false; }
  function dismissButton(label) {
    const button = h("button", { type: "button", class: "btn" }, label || "Dismiss");
    button.addEventListener("click", () => { card.hidden = true; card.replaceChildren(); });
    return button;
  }
  function split(name) { return (params.get(name) || "").split(",").map((s) => s.trim()).filter(Boolean); }

  const email = (params.get(sharing ? "share_to" : "remove_from") || "").trim();
  const role = (params.get("role") || "").trim();
  const targets = [
    ...[...new Set(split("drives"))].map((id) => ({ id, kind: "drive" })),
    ...[...new Set(split("files"))].map((id) => ({ id, kind: "file" })),
  ];
  const notifyDefault = params.get("notify") !== "0";

  const problems = [];
  if (sharing && removing) problems.push("One link can share or remove, not both.");
  if (!/^[^@\s,;<>"']+@[^@\s,;<>"']+\.[^@\s,;<>"']+$/.test(email)) problems.push("The email address isn't valid.");
  if (sharing && !GRANTABLE.includes(role)) problems.push("The access level must be reader, commenter or writer.");
  if (!targets.length || targets.length > MAX_TARGETS || targets.some((t) => !ID.test(t.id))) problems.push(`The link must name between 1 and ${MAX_TARGETS} Drive files, folders or shared drives.`);
  if (problems.length) {
    show(h("h2", {}, "This link can't be used"), h("p", {}, problems.join(" ")),
      h("p", { class: "meta" }, "Nothing was changed. Ask Claude for a new link."), dismissButton());
    close();
    return;
  }

  show(h("h2", {}, sharing ? "Share request" : "Remove access request"), h("p", { role: "status" }, "Reading them from Drive…"));

  // What Drive says about one target now: its real name and link, and who has access.
  async function info(t) {
    try {
      const perms = readFilePermissions(t.id).catch(() => null);
      if (t.kind === "drive") {
        const meta = await run(REQ.driveInfo, { drive_id: t.id });
        const d = meta.document || {};
        return { ...t, name: d.name || t.id, link: googleHref(`https://drive.google.com/drive/folders/${t.id}`), canChange: !(d.capabilities && d.capabilities.canManageMembers === false), perms: await perms };
      }
      const meta = await run(REQ.fileInfo, { fileId: t.id });
      const d = meta.document || {};
      return {
        ...t, name: d.name || t.id, link: googleHref(d.webViewLink || d.display_url), folder: d.mimeType === FOLDER,
        trashed: d.trashed === true, canChange: !(d.capabilities && d.capabilities.canShare === false), perms: await perms,
      };
    } catch (err) {
      return { ...t, error: err };
    }
  }

  function hint(message) {
    if (/no google account|notification/i.test(message)) return " This address may not have a Google account; leave the notification email on so Google can invite them.";
    if (/policy|not (allowed|permitted)|outside|domain|external/i.test(message)) return " Your Workspace's sharing settings may not allow sharing outside the organization.";
    return "";
  }

  function row(t, note) {
    t.status = h("div", { class: "meta" }, note || "");
    const name = t.error ? h("strong", {}, t.id) : t.link ? h("a", { href: t.link, target: "_blank", rel: "noopener noreferrer" }, `${t.name} ↗`) : h("strong", {}, t.name);
    return h("li", {}, name, " ", t.kind === "drive" ? chip("shared drive", "info") : t.folder ? chip("folder") : null, t.status);
  }

  const orderFor = (list) => [...list.filter((t) => t.kind === "drive"), ...list.filter((t) => t.folder), ...list.filter((t) => t.kind === "file" && !t.folder)];

  async function start() {
    const loaded = await Promise.all(targets.map(info));
    const needSignIn = loaded.find((t) => t.error && t.error.status === 409 && t.error.code === "user_sign_in_required" && t.error.queryId);
    if (needSignIn) {
      show(h("h2", {}, "Request"), h("p", {}, "Sign in to Google Drive first, then open this link again."), dismissButton());
      signInPrompt(needSignIn.error.queryId);
      return;
    }
    return sharing ? shareCard(loaded) : removeCard(loaded);
  }

  /* ---------- Share ---------- */

  function shareCard(list) {
    const mine = (t) => t.perms && t.perms.find((p) => !p.deleted && (p.emailAddress || "").toLowerCase() === email.toLowerCase());
    for (const t of list) {
      t.existing = mine(t);
      t.skip = t.error ? "Couldn't read this one." : t.trashed ? "This file is in the trash." : !t.canChange ? (t.kind === "drive" ? "You can't manage this shared drive's members." : "You can't share this file.")
        : t.existing && t.existing.role === role ? "Already has this access." : null;
    }
    const todo = orderFor(list.filter((t) => !t.skip));
    const notify = h("input", { type: "checkbox", id: "share-notify" });
    notify.checked = notifyDefault;
    const items = list.map((t) => row(t, t.skip
      || (t.existing ? `Currently ${roleLabel(t.existing.role)}; this sets ${roleLabel(role)}.` : t.kind === "drive" ? "Adds them as a member of the whole shared drive." : "")));

    const confirm = h("button", { type: "button", class: "btn primary", disabled: !todo.length }, todo.length ? `Confirm: share ${plural(todo.length, "item")}` : "Nothing to share");
    const decline = h("button", { type: "button", class: "btn" }, "Decline");
    const result = h("p", { role: "status", "aria-live": "polite" });
    const note = h("p", { class: "meta" }, "Nothing is shared until you confirm.");

    decline.addEventListener("click", () => {
      close();
      show(h("h2", {}, "Share request declined"), h("p", {}, `Nothing was shared with ${email}.`), dismissButton());
    });
    confirm.addEventListener("click", async () => {
      confirm.disabled = true;
      decline.disabled = true;
      notify.disabled = true;
      note.hidden = true;
      close();
      let done = 0;
      let failed = 0;
      for (const t of todo) {
        t.status.textContent = "Sharing…";
        try {
          await run(REQ.grant, { file_id: t.id, type: "user", email_address: email, role, send_notification_email: notify.checked });
          t.status.textContent = `Shared: ${email} now has ${roleLabel(role)} access.`;
          done++;
        } catch (err) {
          failed++;
          if (err.status === 409 && err.code === "user_sign_in_required" && err.queryId) {
            t.status.textContent = "Sign in to Google Drive, then ask Claude for a new link.";
            signInPrompt(err.queryId);
            break;
          }
          t.status.textContent = err instanceof Uncertain ? `${err.message} Check the sharing before trying again.` : `Couldn't share. ${err.message}${hint(err.message)}`;
          if (err instanceof Uncertain) break;
        }
      }
      result.textContent = failed ? `${done} shared, ${failed} not. Nothing else was changed.` : `Done. Shared ${plural(done, "item")} with ${email}. Scan Drive to see it in the list.`;
      confirm.replaceWith(dismissButton("Close"));
      decline.remove();
    });

    show(
      h("h2", {}, "Share request"),
      h("p", {}, "Give ", h("strong", {}, email), " ", h("strong", {}, roleLabel(role)), ` access to ${plural(list.length, "item")}?`),
      h("ul", {}, ...items),
      h("label", { class: "check", for: "share-notify" }, notify, "Send Google's notification email to this address"),
      h("div", { class: "scan-bar" }, confirm, decline),
      result,
      note);
  }

  /* ---------- Remove ---------- */

  function removeCard(list) {
    for (const t of list) {
      const p = t.perms && t.perms.find((q) => !q.deleted && (q.emailAddress || "").toLowerCase() === email.toLowerCase());
      t.permission = p;
      t.skip = t.error ? "Couldn't read this one."
        : !t.perms ? "Couldn't read who has access."
        : !p ? "No direct access to remove. They may have it through a group, a parent folder or the shared drive."
        : p.role === "owner" ? "They own this, so it can't be removed."
        : (p.permissionDetails || []).some((d) => d.inherited) ? "Their access comes from a parent folder or shared drive. Remove it there."
        : !t.canChange ? (t.kind === "drive" ? "You can't manage this shared drive's members." : "You can't change this file's sharing.")
        : t.trashed ? "This file is in the trash." : null;
    }
    const todo = orderFor(list.filter((t) => !t.skip));
    const items = list.map((t) => row(t, t.skip || `Currently ${roleLabel(t.permission.role)}.${t.kind === "drive" ? " Removes them from the whole shared drive." : ""}`));

    const confirm = h("button", { type: "button", class: "btn danger", disabled: !todo.length }, todo.length ? `Confirm: remove access from ${plural(todo.length, "item")}` : "Nothing to remove");
    const decline = h("button", { type: "button", class: "btn" }, "Decline, keep their access");
    const result = h("p", { role: "status", "aria-live": "polite" });
    const note = h("p", { class: "meta" }, "This takes effect as soon as you confirm, and Google doesn't notify them. To restore access, ask Claude to share again. Nothing changes until you confirm.");

    decline.addEventListener("click", () => {
      close();
      show(h("h2", {}, "Removal declined"), h("p", {}, `${email}'s access was not changed.`), dismissButton());
    });
    confirm.addEventListener("click", async () => {
      confirm.disabled = true;
      decline.disabled = true;
      note.hidden = true;
      close();
      let removed = 0;
      let kept = 0;
      let failed = 0;
      for (const t of todo) {
        t.status.textContent = "Removing…";
        try {
          const outcome = await removeOne({ file: { id: t.id }, permId: t.permission.id });
          if (outcome === "inherited") { kept++; t.status.textContent = "Their access comes from a parent folder or shared drive, so it was left as is. Remove it there."; }
          else { removed++; t.status.textContent = outcome === "gone" ? "Already gone." : "Removed."; }
        } catch (err) {
          failed++;
          if (err.status === 409 && err.code === "user_sign_in_required" && err.queryId) {
            t.status.textContent = "Sign in to Google Drive, then ask Claude for a new link.";
            signInPrompt(err.queryId);
            break;
          }
          t.status.textContent = err instanceof Uncertain ? `${err.message} Check the sharing before trying again.` : `Couldn't remove. ${err.message}`;
          if (err instanceof Uncertain) break;
        }
      }
      const parts = [`Removed ${email} from ${plural(removed, "item")}.`];
      if (kept) parts.push(`${plural(kept, "item")} left as is.`);
      if (failed) parts.push(`${plural(failed, "removal")} failed.`);
      result.textContent = parts.join(" ");
      confirm.replaceWith(dismissButton("Close"));
      decline.remove();
    });

    show(
      h("h2", {}, "Remove access request"),
      h("p", {}, "Remove ", h("strong", {}, email), `'s access to ${plural(list.length, "item")}?`),
      h("ul", {}, ...items),
      h("div", { class: "scan-bar" }, confirm, decline),
      result,
      note);
  }

  start().catch((err) => {
    show(h("h2", {}, sharing ? "Share request" : "Remove access request"), h("p", { role: "alert" }, `Couldn't read the items. ${err.message}`), h("p", { class: "meta" }, "Nothing was changed."), dismissButton());
    close();
  });
})();
