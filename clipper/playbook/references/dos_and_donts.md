# Clipper dos and don'ts (human reference; the validators enforce the checkable ones)

## What makes a good anime Short
- **One complete idea.** A setup, a turn, and a payoff that a new viewer can follow in 20-58 seconds.
- **Starts where the idea starts.** Not mid-sentence, not mid-answer. First 2 seconds should show a clear situation or a line that makes people ask "what happens next?"
- **Ends right after the payoff.** The last line, strike, or reaction, then cut. No trailing small talk.
- **Standalone.** If the clip needs three earlier scenes to make sense, mark `standalone: false` and rank it lower, or add a context line.
- **Clean audio and captions.** Names spelled right (glossary), no one-word flash captions, text inside the safe area.

## Moment types
- Plot reveal: new information that changes the situation.
- Memorable line: short, quotable, clear alone.
- Emotional: the lines themselves express fear, grief, anger or relief.
- Comedy: a joke or awkward exchange stated in the lines.
- Confrontation: open disagreement or threat.
- Fight: confirmed by frames and signals, not by loudness alone. Fights are often silent; dialogue alone cannot find them.

## Do
- Use only the supplied lines or frames. Quote lines exactly and cite their IDs.
- Say "unclear" when unsure. Return zero nominations when nothing qualifies.
- Keep reasons short and tied to quoted lines.
- Keep the opening and ending songs, credits and next-episode previews out (HARD and VERIFIED zones). Position or music guesses (SOFT) only warn.
- Prefer a candidate with a visible reason over a candidate with a high score.
- Add your own value layer: a hook line, a context line, or voice. It helps with YouTube's rules on reused content.

## Don't
- Don't invent names, relationships, motives or plot. Don't say who spoke a line (the system has no speaker detection).
- Don't use outside knowledge of the series, even if the model "knows" it.
- Don't output timestamps. Return IDs; Python resolves times.
- Don't select by loudness, motion or cut rate alone.
- Don't reveal the payoff in the hook. Don't use clickbait ("shocking", "you won't believe", "insane").
- Don't fill a quota. 0, 1 or 2 clips are all acceptable.
- Don't call a clip "ready to post", "viral" or "monetizable".

## YouTube notes (policy changes often; check the live pages)
- Shorts can run up to three minutes, but a Short over 60 seconds with an active third-party Content ID claim is blocked worldwide. Default to 20-58 seconds.
- Reused-content rules for monetization are separate from copyright and from permission. Clips with little added value may get less reach and may not be monetizable.
- Export success does not mean you may publish the footage. Label every output "Provisional draft - rights not verified".

## Standard states
- Confirmation: CONFIRM / REJECT / UNCLEAR (vision model).
- Zones: HARD, VERIFIED (excluded) and SOFT (warning).
- Every nomination: type, evidence IDs, quoted lines, reason, standalone flag, zone status.
