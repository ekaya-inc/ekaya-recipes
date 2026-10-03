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

Recipes replace an earlier set of tutorials that were too technical for users to spend the time on. The
reader of a recipe is a business user setting up Ekaya, not a developer. A recipe must be quick to follow:
the video shows what to do, and the prompt does the setup.

## Layout

```
README.md            what Recipes are, and the list of them
<slug>/README.md     one recipe: its video walkthrough and its prompt
```

- Each recipe is one directory at the repository root, named by its slug: lowercase words joined by
  hyphens (`getting-started`).
- The slug is part of a public URL that the Ekaya app links to. Do not rename or move a recipe directory
  unless the app's link changes at the same time.
- A new recipe gets a row in the list in the root `README.md`.

## Rules

- **Everything here is public.** Never commit credentials, tokens, customer data, or URLs and IDs that
  belong to a private project. This applies to screenshots and videos as much as to text.
- **Describe what Ekaya does today.** Do not invent screens, steps, or product behavior. If you cannot
  verify a step, ask.
- **Do not add other documentation files** (`CONTRIBUTING.md`, `docs/`, and the like) unless asked.
