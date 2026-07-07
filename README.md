# HADR Monitor

A monitoring agent for humanitarian assistance and disaster response (HADR).

## What is HADR?

**Humanitarian Assistance and Disaster Response (HADR)** is the coordinated
effort to save lives, reduce suffering, and protect livelihoods in the wake of
natural or human-made disasters — earthquakes, cyclones, floods, volcanic
eruptions, droughts, wildfires, and the like. It spans the full arc of a crisis:

- **Preparedness** — watching for hazards before they escalate and pre-positioning people, supplies, and plans.
- **Response** — the first hours and days after an event, when responders need to know *what happened, where, how bad, and who is affected* to direct rescue, medical aid, shelter, food, and water.
- **Recovery** — the longer effort to restore services and rebuild communities.

HADR work is carried out by national disaster agencies, militaries, UN bodies
(such as UN OCHA), the Red Cross / Red Crescent movement, and countless NGOs —
who all depend on fast, accurate, shared situational awareness to act together.

## What this application is for

The single hardest problem in the first hours of a disaster is **information**:
it is scattered across many feeds, arrives faster than any human can read it, is
full of duplicates and false alarms, and is easy to miss overnight or across time
zones. This agent exists to turn that firehose into a decision-ready picture.

In a HADR scenario it can be used to:

- **Detect early** — continuously watch authoritative disaster feeds (GDACS, USGS, ReliefWeb) so a significant event is noticed within minutes, day or night, without a human on watch.
- **Cut the noise** — filter out minor tremors, routine updates, and false alarms, and de-duplicate the same physical event arriving from several feeds under different identifiers.
- **Assess impact** — for each event that survives the filter, characterise *what happened, where, how severe, and who is affected*.
- **Brief decision-makers** — publish a concise morning situation report to `dashboard.html` at 08:30 Singapore time, so responders and coordinators start the day with a shared, current picture.
- **Run unattended, quietly** — operate on a schedule around the clock and stay silent when nothing has changed, so attention is spent only when it matters.

The result is faster, better-informed decisions on where to send scarce
responders, supplies, and attention — the core of effective disaster response.

## The end state

By Wednesday afternoon this repository contains an agent that:

- watches live disaster feeds — GDACS, USGS and ReliefWeb (see `feeds/`)
- filters out the noise and assesses what remains: what happened, where, how bad, who is affected
- publishes a morning situation report to `dashboard.html` at 08:30 Singapore time
- runs on a schedule, unattended, and stays quiet when nothing has changed

How it does any of that is not specified anywhere in this repository. That is the course.

## The three days

1. **Plan** — interrogate the feeds, write the PRD, cut it into vertical slices
2. **Autonomy** — build the first slice, write a skill, wire up the 08:30 routine, launch the overnight loop
3. **Trust** — review code you didn't write, harden the pipeline, demo

## Artefacts expected by the end

`prd.html` · `system-view.html` · `implementation-notes.md` · `dashboard.html` · `goal.md` · at least one skill

## Day 1 setup

1. Sign in to Claude Code with your Team seat
2. Create your own repository from this template, then clone it
3. Run `/install-github-app` so @claude reviews your pull requests from Day 2
4. Install OpenCode and sign in with your Go key

Fill in `CLAUDE.md` before your first prompt.
