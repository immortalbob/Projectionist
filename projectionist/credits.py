"""Scenes during and after the end credits, worked out from Plex's credits markers.

Plex's credits detection marks each stretch of end credits it finds. The last one is flagged
"final" and runs to the end of the file, so any earlier stretch has footage after it: a
mid-credits scene, outtakes, or - when a stretch ends before the file does - a scene after the
credits. The detector also fires now and then on other on-screen text (newspaper headlines, an
epilogue card, a silent film's intertitles), so every scene gets a verdict:

    Likely  the usual shape of a mid- or post-credits scene (or outtakes)
    Maybe   there's footage, but it could be a title card, exit music or text inside the film

What it can't see: a gag so short, or outtakes so tightly woven into the credits, that Plex's
last credits marker simply covers them - and movies Plex hasn't scanned for credits at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Footage between two credits stretches shorter than this is a cut or a title card - the stretches are merged.
MERGE_GAP_MS = 5_000
# More film than this after a 'credits' stretch means it wasn't the end credits - the film carried on.
NOT_CREDITS_GAP_MS = 480_000
# Mid/post-credits scenes are rarely longer than this; a longer stretch is only a Maybe.
LONG_SCENE_MS = 240_000
SHORT_SCENE_MS = 15_000
# Before the late '70s, footage after the end titles is nearly always a card, exit music or text.
MODERN_FROM_YEAR = 1977
# A first 'credits' stretch shorter than SHORT_BURST_MS followed by more than BURST_GAP_MS of footage is often
# on-screen text inside the film (Taxi Driver's newspaper clippings) rather than the start of the credits.
SHORT_BURST_MS = 60_000
BURST_GAP_MS = 120_000
# The final marker reaches the end of the file; allow for rounding.
END_SLACK_MS = 2_000

LIKELY, MAYBE = "Likely", "Maybe"
MID, AFTER = "Mid-credits", "After the credits"
YES, NONE_FOUND = "Yes", "None found"

WHY_SHORT = "very short - could be a title card or a logo rather than a scene"
WHY_OLD = "an older film - footage after the end titles is usually a 'The End' card, exit music or text"
WHY_BURST = ("a short burst of 'credits' with a longer stretch after it - Plex may have taken on-screen text "
             "inside the film for credits")
WHY_LONG = ("a long stretch (over 4 minutes) - a long scene, or the film carrying on after on-screen text "
            "Plex took for credits")
WHY_NO_FLAGS = "this Plex version doesn't say which credits are the last, so this may be the tail of the credits"


@dataclass
class Marker:
    start: int                 # ms
    end: int                   # ms
    final: bool | None = None  # Plex's pv:final flag; None when the marker doesn't say


@dataclass
class CreditsScene:
    kind: str                  # MID or AFTER
    start: int                 # ms: where the footage starts (the end of the credits before it)
    end: int                   # ms: where it stops (the next credits, or the end of the file)
    verdict: str               # LIKELY or MAYBE
    reason: str = ""           # why it's only a Maybe
    credits_before: tuple[int, int] = (0, 0)

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class CreditsInfo:
    credits_start: int | None            # ms: where the end credits begin (None: nothing to go on)
    duration: int | None                 # ms: length of the file the markers belong to
    scenes: list[CreditsScene] = field(default_factory=list)
    ignored: list[tuple[int, int]] = field(default_factory=list)   # 'credits' that were really something else
    stretches: list[tuple[int, int]] = field(default_factory=list)  # every credits stretch, merged, in order

    @property
    def verdict(self) -> str:
        """'Yes', 'Maybe', 'None found' - or '' when there's nothing to go on."""
        if self.credits_start is None:
            return ""
        if any(s.verdict == LIKELY for s in self.scenes):
            return YES
        return MAYBE if self.scenes else NONE_FOUND


def analyse(markers: list[Marker], duration: int | None, year: int | None = None,
            final_flags: bool = True) -> CreditsInfo:
    """Scenes during/after the credits for one movie.

    markers:     Plex's credits markers for the movie (any order)
    duration:    length of the video file in ms (the markers' time line)
    year:        release year - older films get more cautious verdicts
    final_flags: whether this database records Plex's 'final' flag at all (Plex versions from before it
                 existed leave the flag off every marker)
    """
    blocks = sorted((m for m in markers if m.start is not None and m.end is not None and m.end > m.start),
                    key=lambda m: (m.start, m.end))
    if not blocks:
        return CreditsInfo(None, duration)
    last_end = max(b.end for b in blocks)
    if not duration or duration < last_end:
        duration = last_end              # a file shorter than its markers: trust the markers

    # Merge overlapping stretches, and ones split by a moment's gap.
    merged: list[Marker] = []
    for b in blocks:
        if merged and b.start - merged[-1].end < MERGE_GAP_MS:
            last = merged[-1]
            flags = [f for f in (last.final, b.final) if f is not None]
            merged[-1] = Marker(last.start, max(last.end, b.end), any(flags) if flags else None)
        else:
            merged.append(Marker(b.start, b.end, b.final))

    # Footage after each stretch: up to the next stretch, or to the end of the file.
    stretches = []
    for i, b in enumerate(merged):
        last = i + 1 == len(merged)
        nxt = duration if last else merged[i + 1].start
        runs_to_end = duration - b.end <= END_SLACK_MS
        gap = 0 if last and (b.final or runs_to_end) else max(nxt - b.end, 0)
        stretches.append((b, gap))

    info = CreditsInfo(None, duration, stretches=[(b.start, b.end) for b in merged])
    old = year is not None and year < MODERN_FROM_YEAR

    # A stretch followed by many minutes of film wasn't the end credits: drop it and everything before it.
    first = next((i + 1 for i in range(len(stretches) - 1, -1, -1) if stretches[i][1] > NOT_CREDITS_GAP_MS), 0)
    info.ignored = [(b.start, b.end) for b, _ in stretches[:first]]
    kept = stretches[first:]

    # A 4-8 minute stretch of footage splits the run too, but might be a genuinely long scene (Napoleon
    # Dynamite's wedding), so on a modern film it's reported as a Maybe.
    cut = next((i for i in range(len(kept) - 1, -1, -1) if kept[i][1] > LONG_SCENE_MS), None)
    if cut is not None:
        if not old:
            info.scenes.append(_scene(kept, cut, MAYBE, WHY_LONG))
        info.ignored += [(b.start, b.end) for b, _ in kept[:cut + 1]]
        kept = kept[cut + 1:]

    # The credits start where the first stretch we report on starts - including the stretch before a long Maybe,
    # so no reported scene ever comes before the credits start.
    starts = [kept[0][0].start] if kept else []
    starts += [s.credits_before[0] for s in info.scenes]
    info.credits_start = min(starts) if starts else None
    for i, (b, gap) in enumerate(kept):
        if gap <= 0:
            continue
        if gap < SHORT_SCENE_MS:
            verdict, reason = MAYBE, WHY_SHORT
        elif old:
            verdict, reason = MAYBE, WHY_OLD
        elif i == 0 and b.end - b.start < SHORT_BURST_MS and gap > BURST_GAP_MS:
            verdict, reason = MAYBE, WHY_BURST
        else:
            verdict, reason = LIKELY, ""
        info.scenes.append(_scene(kept, i, verdict, reason))
    if not final_flags:
        for s in info.scenes:
            if s.kind == AFTER and s.verdict == LIKELY:
                s.verdict, s.reason = MAYBE, WHY_NO_FLAGS
    info.scenes.sort(key=lambda s: s.start)
    return info


def _scene(stretches, i, verdict, reason) -> CreditsScene:
    b, gap = stretches[i]
    return CreditsScene(kind=AFTER if i + 1 == len(stretches) else MID, start=b.end, end=b.end + gap,
                        verdict=verdict, reason=reason, credits_before=(b.start, b.end))


def clock(ms: int | None) -> str:
    """ms -> 'h:mm:ss'."""
    if ms is None or ms < 0:
        return ""
    s = int(ms) // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def span(ms: int) -> str:
    """ms -> '28 s', '1 min 42 s', '5 min'."""
    s = round(ms / 1000)
    if s < 60:
        return f"{s} s"
    return f"{s // 60} min" + (f" {s % 60} s" if s % 60 else "")


def describe(scene: CreditsScene) -> str:
    """'2:05:12 mid-credits (28 s)', with '(maybe)' on the unsure ones."""
    text = f"{clock(scene.start)} {scene.kind.lower()} ({span(scene.length)})"
    return text + (" (maybe)" if scene.verdict == MAYBE else "")
