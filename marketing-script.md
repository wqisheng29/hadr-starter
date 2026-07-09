# HADR Monitor — Marketing Walkthrough Script

A spoken script for talking a prospect through the marketing landing page
(`landing.html`) — a live demo, a screen-share, a recorded voiceover, or a booth
pitch. It follows the page top to bottom, so you can scroll as you speak.

- **Audience:** disaster-response coordinators, duty officers, and the agencies
  behind them (national disaster agencies, militaries running HADR operations, UN
  OCHA, Red Cross / Red Crescent, humanitarian NGOs).
- **Tone:** calm, credible, operational. This is a life-safety tool — sell trust,
  not hype.
- **Full run time:** ~4–5 minutes. A 45-second elevator version is at the end.
- **Cues:** `[SCROLL TO …]` marks where to move on the page; *italics* are
  delivery notes, not spoken.

---

## 0. Cold open (Hero)

`[SCROLL TO: top — the hero]`

> The hardest problem in the first hours of a disaster isn't effort. It's
> information. It's scattered across a dozen feeds, it arrives faster than anyone
> can read it, and it's thick with duplicates and false alarms — and the events
> that matter most tend to happen overnight, in another time zone, when no one's
> on watch.
>
> HADR Monitor is an unattended agent that fixes exactly that. It watches the
> authoritative feeds around the clock, cuts the noise, and hands your team one
> decision-ready situation report every morning — so scarce responders and
> supplies go where they matter.

*Point at the three source chips.*

> Everything it reports is reconciled from three authoritative sources: USGS for
> fast detection, GDACS for impact triage, and ReliefWeb for humanitarian
> context.

---

## 1. The 20-second version (Executive summary)

`[SCROLL TO: Executive summary panel]`

> If you read nothing else, read this box. What it does: watches three feeds
> non-stop, collapses the same event across them into one record, and filters out
> routine noise. What you get: a self-correcting 08:30 situation report, plus an
> immediate push for severe, confirmed events — and silence when nothing has
> changed. And why teams trust it: a deterministic core, so the same inputs
> always produce the same picture. The numbers are reproducible; only the prose
> is written by a model.

*This is your natural stopping point for a time-boxed pitch. Everything below is
the proof.*

---

## 2. From firehose to a picture you can act on (Features)

`[SCROLL TO: "From a firehose of feeds to a picture you can act on"]`

> Six things it does, and I'll show each one working.

*Walk the cards left to right — one line each, don't linger.*

> - **Detect early, day or night** — a significant event is noticed within
>   minutes, with no human on watch.
> - **Cut through the noise** — minor tremors and routine updates are filtered,
>   and the same quake arriving from three feeds becomes one event, not three.
> - **Assess real impact** — it ranks by who's affected, not by raw magnitude.
> - **Brief decision-makers** — one concise report at 08:30, no dashboard to
>   babysit.
> - **Urgent alerts** — it breaks the silence only when something severe and
>   confirmed appears.
> - **Reproducible and auditable** — every reconciliation is testable and
>   defensible when the stakes are high.

*Then the second row — these three are the differentiators.*

> And three that set it apart: it links **humanitarian context to the right
> event** — ReliefWeb records tied back to the exact quake by GLIDE number or a
> recorded match, never a duplicate. It **reads like an analyst wrote it** — a
> language model writes the plain-English assessment, but only after the facts
> are locked. And it **runs unattended, on a schedule** — a persistent ledger
> between ticks, each urgent alert delivered exactly once, even across restarts.

---

## 3. Never mistake an early read for a settled one (Lifecycle)

`[SCROLL TO: "Never mistake an early read for a settled one"]`

> The first minutes of a quake are the fastest information you'll get — and the
> least certain. Magnitude and location land before any impact signal does.
>
> So every event is labelled **provisional** on first detection, and firms up to
> **confirmed** the moment the data settles — a reviewed USGS status, a PAGER
> alert, or a GDACS ShakeMap.

*Point at the mock: provisional row → confirmed row.*

> Your team sees, at a glance, exactly how much to trust each line. Material
> events lead; routine ones fold below the fold. And once something is confirmed,
> it never quietly slips back — no second-guessing.

---

## 4. Who it's for (Audience)

`[SCROLL TO: "For the people who decide where help goes"]`

> This is built for the people who have to decide where help goes before the
> picture is complete — national disaster agencies, militaries running HADR
> operations, UN OCHA, the Red Cross and Red Crescent, and humanitarian NGOs.
>
> The decision it speeds up is the expensive one: where to send scarce
> responders, supplies, and attention — and how fast. Instead of a morning
> scramble across a dozen feeds, coordinators open one brief, see what changed
> overnight, and commit resources on facts.

---

## 5. The feature no one else has (Self-correcting brief)

`[SCROLL TO: "It tells you what changed — including what it got wrong"]`

*This is the emotional center of the pitch. Slow down.*

> Here's the part no other tool does. Disaster feeds aren't bulletins — they're
> living databases. A magnitude gets revised down a day later. An alert colour is
> downgraded. An event is deleted outright. Almost every tool reports once and
> never looks back — so it never tells you that something you acted on yesterday
> is no longer true.
>
> HADR Monitor's briefing leads with **what changed since the last brief** —
> including honest corrections of anything it previously told you. It's
> self-correcting, it builds trust precisely *because* it owns its revisions, and
> it's change-first, so you read the delta, not re-read the whole world every
> morning.

`[SCROLL TO: "Six kinds of change — never conflated"]`

> And it's precise about *how* something changed — because each kind demands a
> different response. It never conflates these six:

*Read the taxonomy as three contrasting pairs — the contrast is the point.*

> - **New** versus **Upgraded** — brand-new event, versus one whose impact tier
>   rose.
> - **Downgraded** versus **Retracted** — reassessed as smaller but still real,
>   versus positively withdrawn by the source. A colour drop, even to green, is a
>   downgrade — *not* a retraction.
> - **Aged out** versus a **Correction** — fell out of the feed window, which is
>   *not* an all-clear; versus a revised fact, stated as "we said X, it's now Y,
>   because the source revised it."
>
> That distinction is the difference between standing down a response and standing
> one up. Getting it wrong costs lives — so we never blur it.

---

## 6. How it works (Architecture)

`[SCROLL TO: "Four moving parts. Zero babysitting."]`

> Under the hood it's four steps. **Watch** — it polls the three feeds about every
> 30 minutes, within each source's rate budget. **Reconcile** — a deterministic
> core matches events across all three and keeps one canonical record.
> **Assess** — confidence firms from provisional to confirmed, and severe,
> confirmed events trigger an immediate push. **Brief** — at 08:30 it diffs
> against yesterday and a model writes the self-correcting report, then goes quiet.

`[SCROLL TO: "Deterministic where it counts. Judgement where it helps."]`

> This is the line that matters for trust. The hot path — matching events,
> tracking every change, deciding what breaks the silence — is plain, testable
> code: same inputs, same picture, same alert, every time. The language model is
> trusted only *above* that line, to weigh impact and write readable prose. So the
> numbers stay reproducible and the writing stays human — and neither job
> contaminates the other.

---

## 7. Where it's headed (Roadmap)

`[SCROLL TO: the roadmap timeline]`

> What's shipped today is the full earthquake pipeline, live: all three feeds
> reconciled, the provisional-to-confirmed lifecycle, the six-state change
> taxonomy, urgent push delivery, and a model-written 08:30 brief — running
> unattended.
>
> Next is multi-hazard coverage — cyclones, floods, volcanic activity — on the
> same pipeline that already generalises to them. Then population-aware impact:
> PAGER loss bins and exposure counts on every confirmed event. And ahead, the
> full crisis arc — preparedness, response, and recovery.

---

## 8. What it deliberately does *not* do (Boundaries)

`[SCROLL TO: "What it deliberately does not do"]`

*Objection pre-empt. Owning the limits is what makes the briefings credible.*

> Good situational awareness knows its own limits, and being explicit about them
> is what keeps the briefings trustworthy. It doesn't replace official warnings —
> a feed flag is not a NOAA tsunami warning. It doesn't cover every hazard yet —
> earthquakes are end-to-end today; every brief names its own blind spots. It
> doesn't republish others' reporting — link, short excerpt, attribution only. It
> doesn't cry wolf, it doesn't decide for you, and it never guesses silently —
> when a signal is provisional or missing, it says so.

---

## 9. Close (Final CTA)

`[SCROLL TO: "Start every morning with the whole picture"]`

> So here's the offer: point it at your region, set your briefing time, and let it
> run. When disaster strikes, your team acts on facts — not a scramble across a
> dozen feeds. The minutes you save at the start of a response are the ones that
> save the most lives.
>
> Want to see a live briefing? *[click "See a live briefing" / open the sample
> dashboard]* — this is exactly what lands in your team's hands at 08:30.

---

## Elevator version (~45 seconds)

> In the first hours of a disaster, the bottleneck isn't effort — it's
> information: scattered across feeds, faster than anyone can read, full of
> duplicates and false alarms. HADR Monitor is an unattended agent that reconciles
> USGS, GDACS and ReliefWeb into one decision-ready picture and briefs your team
> every morning at 08:30 — leading with what changed since yesterday, including
> anything it previously got wrong. It pushes immediately for severe, confirmed
> events, and stays silent when nothing's changed. The facts come from a
> deterministic core — same inputs, same answer, every time — and a model writes
> only the prose. So your team starts every morning with the whole picture, and
> acts on facts.

---

## Delivery notes

- **Lead with the pain, not the product.** The first 15 seconds are about *their*
  3 a.m. problem, not our architecture.
- **The two power moments** are Section 5 (it tells you what it got wrong) and the
  "deterministic where it counts" line in Section 6. If you're short on time, cut
  everything else before you cut those.
- **Don't over-claim.** Everything in this script maps to shipped behaviour
  (earthquakes, three feeds, provisional→confirmed, the six-state diff, urgent
  push, the 08:30 brief). Multi-hazard and population-aware enrichment are
  explicitly framed as roadmap — keep them there.
- **Numbers to have ready:** three feeds, 24/7, ~30-minute refresh loop, one 08:30
  brief. They're on the stats strip if you need to point.
