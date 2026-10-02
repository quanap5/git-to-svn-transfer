# README banner GIF

Status: done

## Outcome

A looping, silent GIF at the top of `README.md` that shows what the
git-to-svn-transfer skill does: split Git work into functional groups, check
every cumulative state, hand each group to the user to commit to SVN.

## Constraints

- Built with the `ak-motion-video` skill: HyperFrames composition, 1920x1080,
  30 fps master, style `developer-workflow` (energy 3).
- A GIF has no sound, so the voice, music, beat and mix steps of the pipeline
  are skipped. TIMING is written by hand, as the composition contract allows.
- No captions and no "Zuey" signature: the banner belongs to this repository.
- English on-screen copy, matching the README. Every label traces to
  `plans/reports/facts-261002-0909-readme-banner.md`.
- The GIF must stay small enough for a README (target under 5 MB).

## Non-goals

Voice-over, music, square or vertical editions, a social MP4.

## Steps

1. Scaffold `assets/videos/readme-banner/`, fonts, `data/script.json`. Done.
2. Direction brief from the director, saved as `data/direction.md`.
3. Executor builds `index.html`, runs lint, check, snapshots, a draft render and
   contact sheets.
4. Director's taste review, at most two rounds.
5. Final render, GIF export to `assets/readme-banner.gif`, README updated.

## Acceptance criteria

- `hyperframes lint` and `check` pass; every scene reviewed on a contact sheet.
- Master MP4 is 1920x1080, 30 fps, 15 s; blackdetect finds nothing outside the
  opening and closing fades.
- GIF loops, is under 5 MB, and is the first thing in `README.md`.
