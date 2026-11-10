# ISSUE: AGENTS.md has no rules for recipes that ship app or connector code, or for videos of a private product

Target repository: ekaya-recipes. This repository is public: keep private ids, names and data out of this file.

Source: adding two recipes on 2026-11-10 (`applications/google-drive-manager-permissions` and
`applications/google-drive-manager-access-log`). Every recipe so far was a video plus a prompt, with steps for the
reader's agent in the README. These two also give the agent the source of a Hosted App and a Custom connector.
`AGENTS.md` (main `552d721`) did not say how to do that, so each point below was decided ad hoc.

## Observed

1. **No place or convention for shipped source.** The Layout lists `<slug>/README.md`, `.assets/` and category
   folders, and mentions one shipped file (`create-video`'s generator). It says nothing about folders such as
   `app/` and `connector/`, nor about a `requests.json` that defines the Requests the agent must create. The two
   recipes invented them, and a `REPLACE_*` placeholder convention for ids the reader's agent fills in.
2. **No `applications/` category.** The new recipes went in a new category folder. Nothing says which categories
   exist or when to add one.
3. **A shipped copy of a live app drifts.** The recipe's `app/` files are a fork of an app that was already live.
   Within hours, three real defects were found in the live app (a stray `null` printed in a confirmation, a stale
   statement about delay, and leftover text on a finished card) and each had to be fixed in two places by hand.
   Nothing says which copy is the source or how the other is kept current.
4. **Screenshots of a private product.** The rule "Everything here is public" and the screenshot format are clear,
   but not how to show an app whose real data is private. These videos used the real app pointed at a small local
   server that returns invented data, then captured it at the required size. That server and the slide builder
   lived in a scratch directory outside the repo, so the screenshots cannot be regenerated from what is committed.
5. **"Built but unpublished" has no state.** AGENTS.md says a recipe is listed once published and that publishing
   to the CDN is the maintainer's decision. The READMEs already carry the poster and the CDN links (the repo's
   convention), which do not resolve until the files are uploaded. Nothing says when to add the video block or
   whether an unlisted recipe may sit in `main`.
6. **No `plans/` directory existed**, so this file created it with a `.gitkeep`.

## To reproduce

1. Read `AGENTS.md` and try to place a recipe that must give the reader's agent a page or a connector's source.
2. Compare `applications/google-drive-manager-permissions/app/` with the app it was copied from after any fix to
   either.

## Expected

`AGENTS.md` says: where shipped source goes and what it may contain; the placeholder convention; which copy is
authoritative for a shipped app and how a fix reaches the other; how to make screenshots of a private product with
invented data (and where that harness lives if it is kept, for example under `.assets/`); and what state an
unpublished recipe is in.

## Notes

- This is a rules question for the maintainer, not a defect in the recipes' text. The two recipes follow the
  README format of the existing ones.
