# AGENTS.md

Rules for coding agents working in this repository. This file is the single source of those rules;
`CLAUDE.md` only points here.

## What this repository is

The public home of Ekaya's Recipes, licensed under Apache 2.0. A Recipe is an end-to-end walkthrough of a
real business use case. It gives the user two things:

- a **video walkthrough** to watch, and
- a **pastable prompt** that sets up Ekaya for that use case.

The Ekaya app lists Recipes on its Projects page. A card's "View recipe" button opens
`https://github.com/ekaya-inc/ekaya-recipes/tree/main/<slug>`, so the recipe's `README.md`, as GitHub
renders it, is the page the user lands on.

The repository holds content only: there is no code, build, or test suite.

## Audience

The reader of a recipe is a business user setting up Ekaya, not a developer. A recipe must be
non-technical and quick to follow: the video shows what to do, and the prompt does the setup.

## Layout

```
README.md                     what Recipes are, and the list of them
<slug>/README.md              one recipe: its video walkthrough and its prompt
<slug>/.assets/               what the recipe's video is made from
<slug>/.assets/screenshots/   the UI screenshots, in the order the video uses them
```

- Each recipe is one directory at the repository root, named by its slug: lowercase words joined by
  hyphens (`getting-started`).
- The slug is part of a public URL that the Ekaya app links to. Do not rename or move a recipe directory
  unless the app's link changes at the same time.
- A new recipe gets a row in the list in the root `README.md`.
- `.assets/` holds the source of a recipe's video: screenshots, script, and generation parameters. None of
  it is secret, and none of it is for the reader, so it stays out of the recipe's page.

## Videos

- A recipe's video is rendered outside this repository and hosted at
  `https://cdn.ekaya.ai/recipes/<slug>.mp4`. Never commit a rendered video: `*.mp4` is ignored.
- GitHub does not play a video hosted elsewhere, so the recipe's `README.md` shows a poster image that
  links to the video's URL.
- Narration is the ElevenLabs voice "Justin Time - Elearning Narration" (`uFIXVu9mmnDZ7dTKCBTX`) on the
  `eleven_multilingual_v2` model. Write "Ekaya" as spelled in a script: no pronunciation rule is applied.
- Capture screenshots from a 1280×720 browser viewport at device pixel ratio 3, which gives a 3840×2160
  PNG. In Chrome DevTools, add a custom Desktop device of that size to the device toolbar and use "Capture
  screenshot". That size is 16:9, keeps UI text readable in a 1080p video, and stays sharp when the video
  zooms in.
- Name a screenshot `NNN--what-it-shows.png`, numbered in tens in the order the video uses them
  (`000--sign-in.png`, `010--no-projects.png`), so one can be inserted later without renaming the rest.
- Before committing a screenshot, drop its unused alpha channel and recompress it. The pixels do not
  change, and Chrome's file becomes about four times smaller:
  `magick in.png -alpha off -strip -define png:compression-level=9 out.png`
- Commit assets as ordinary files, not with Git LFS. This repository is public, and every clone would draw
  on the organization's LFS allowance.

## Rules

- **Everything here is public.** Never commit credentials, tokens, customer data, or URLs and IDs that
  belong to a private project. This applies to screenshots and videos as much as to text.
- **Describe what Ekaya does today.** Do not invent screens, steps, or product behavior. If you cannot
  verify a step, ask.
- **Do not add other documentation files** (`CONTRIBUTING.md`, `docs/`, and the like) unless asked.
