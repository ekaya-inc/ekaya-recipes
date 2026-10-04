# AGENTS.md

Rules for coding agents working in this repository. This file is the single source of those rules;
`CLAUDE.md` only points here.

## What this repository is

The public home of Ekaya's Recipes, licensed under Apache 2.0. A Recipe is an end-to-end walkthrough of a
real business use case. It gives the user two things:

- a **video walkthrough** to watch, and
- a **pastable prompt** that sets up Ekaya for that use case.

The Ekaya app lists Recipes on its Projects page. A card's "View recipe" button opens a page in the app
that plays the recipe's video, which is published to the CDN (see Videos). This repository holds each
recipe's source and a `README.md` for anyone who finds it on GitHub.

The repository holds content, the scripts that render the recipes' videos, and any file a recipe gives its
reader (the `create-video` recipe's video generator). It has no build or test suite.

## Audience

The reader of a recipe is a business user setting up Ekaya, not a developer. A recipe must be
non-technical and quick to follow: the video shows what to do, and the prompt does the setup.

When the reader's agent needs exact steps, they go last in the recipe's `README.md`, under "For your
agent", and are written to the agent. The prompt gives the recipe's URL, so the agent reads them there.
Anything that needs the reader's consent (installing software, for example) is also said in the prompt
itself: the prompt is the reader's instruction, and the page is only what the agent reads.

## Layout

```
README.md                     what Recipes are, and the list of them
<slug>/README.md              one recipe: its video walkthrough and its prompt
<slug>/<slug>.mp4              the rendered video (ignored by git, never committed)
<slug>/.assets/               what the recipe's video is made from
<slug>/.assets/scenes.json    narration text, voice, and the shot list: the source of the video
<slug>/.assets/generate.py    renders the video from the assets in this folder
<slug>/.assets/screenshots/   the UI screenshots, in the order the video uses them
<slug>/.assets/audio/         one narration clip per scene, and which text each was made from
<slug>/.assets/poster.jpg     the image the recipe's README shows in place of the video
<category>/<slug>/            a recipe can also sit in a category folder: media/create-video/
media/create-video/generate.py   the video generator that recipe gives its reader
```

- Each recipe is one directory named by its slug: lowercase words joined by hyphens (`getting-started`).
  It sits at the repository root or one level down, in a category folder (`media/create-video`).
- The slug names the recipe's published files and its page in the Ekaya app, without the category, so a
  slug is unique across the repository. A recipe's prompt can hold its GitHub URL. Do not rename or move a
  recipe directory unless all of those change at the same time.
- `media/create-video/generate.py` is downloaded from the `main` branch and run by readers' agents. Keep it
  one file that needs only Python 3.9, Pillow, and ffmpeg, and keep its commands and the fields of
  `scenes.json` working for the scripts readers already have.
- A new recipe gets a row in the list in the root `README.md`. The list has no status column: a recipe is
  listed once it is published.
- A recipe's prompt, if it has one, is in its `README.md` and in the Ekaya app's recipe page, below the video.
  Break its lines at natural points (about 70 characters) so a code block on GitHub does not scroll sideways.
- `.assets/` holds the source of a recipe's video: screenshots, script, and generation parameters. None of
  it is secret, and none of it is for the reader, so it stays out of the recipe's page.

## Videos

- A recipe's video is rendered here (`./generate.py render`) but never committed: `*.mp4` is ignored. It is
  hosted on the CDN at `https://cdn.ekaya.ai/recipes/<slug>.mp4` (720p, for embedding), with
  `<slug>-1080p.mp4` for full resolution and `<slug>.jpg` as the poster. Publishing to the CDN is a
  decision for the maintainer, made from the sibling `ekaya-marketing` repository, not from here.
- GitHub does not play a video hosted elsewhere, so the recipe's `README.md` shows a poster image that
  links to the video's URL.
- Narration is the ElevenLabs voice "Justin Time - Elearning Narration" (`uFIXVu9mmnDZ7dTKCBTX`) on the
  `eleven_multilingual_v2` model, with the settings in `scenes.json`. Write "Ekaya" as spelled in a script:
  no pronunciation rule is applied. Spell out anything the voice would misread in `scenes.json` →
  `pronounce` (for example `MCP` is read as "M C P"), not in the narration text.
- Regenerate a recipe's video with `./generate.py render` in its `.assets/` folder (needs Python with
  Pillow, and ffmpeg). It never calls a voice service: it uses the clips in `audio/`, so a render is
  repeatable and does not change narration nobody edited. `./generate.py render --preview` is a quick
  half-resolution render for checking timing and framing.
- The `create-video` recipe has no `generate.py` in `.assets/`. Its video is made by the generator the
  recipe gives its readers, so run `../generate.py` from `media/create-video/.assets/`: `render` for the
  720p video, `render --height 1080` for full resolution, and `poster`. It has no screenshots: its scenes
  are drawn from the text in `scenes.json`.
- After editing a scene's `narration`, run `./generate.py status`. It lists the scenes whose clip no longer
  matches the text. For each, generate the narration with the voice in `scenes.json`, save it as
  `audio/<scene id>.mp3`, run `./generate.py stamp <scene id>`, then render. ElevenLabs output is not
  repeatable, so regenerate only the scenes that changed.
- Shots in `scenes.json` use `at` (a phrase from the narration) to say when a shot begins, and
  coordinates in CSS pixels of the 1280×720 capture viewport for `focus`, `cursor`, and `frame`. After
  changing a screenshot, check those coordinates against it.
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
