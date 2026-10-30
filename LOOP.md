# Crew Loop — Bloom Book

Meridian Zero · private company property.

## Crew
| Role | Who | Job |
|---|---|---|
| Master | Captain Mohamed | Approves. Only he says **merge**. |
| Builder | Claude | Fixes, tests, pushes to the loop branch |
| Advisor | ChatGPT (Codex) | Reviews every round on this pull request |

## One round
1. Claude pushes fixes to the `loop/*` branch and comments `@codex review`.
2. Codex reviews and lists findings with severity **High / Medium / Low**.
3. Claude (scheduled) reads the findings, fixes them, tests on simulated phones, pushes, and asks for the next review.
4. The round result is written to the Crew Loop board and Linear.

## Done when
Codex reports **no High and no Medium findings**. Then the loop stops and asks the Captain to merge.

## Rules (never broken)
- Nothing is merged into `main` without the Captain's word **merge**.
- No payments, no paid services, nothing sent to clients.
- No passwords, keys or client personal data in code, comments or reviews.
- Database changes are written as migration files only; the Captain runs them in Supabase.
- Max 6 rounds, then stop and report to the Captain.

## Review scope for Codex
Whole app, not only the diff: `index.html`, `sw.js`, `config.js`, `manifest.webmanifest`, `supabase/migrations/`.
Check: booking flow (Arabic and English), double submit, Cairo time zone, closing time 20:00, Monday closed,
payment wording, privacy (Law 151/2020), offline cache, phone install (iPhone and Android), security of public inserts.
