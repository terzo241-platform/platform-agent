# 6-Month Platform Architect Playbook at Ford

**Starting Oct 12, 2026 | Chennai, India | US Remote Manager (Detroit/Dearborn)**

> Practical, field-tested strategy for a Platform Architect joining Ford's India tech center.
> Grounded in research from platformengineering.org, Martin Fowler, Team Topologies, Humanitec (249 platform reviews), Will Larson, Charity Majors, and Marty Cagan.

---

## The Reality You're Walking Into

- Ford's India tech center (Chennai) is the **retained GBS operation** post-manufacturing exit — ~2,000-4,000 people doing enterprise tech, data analytics, transformation
- Ford is deep into **Google Cloud** (6-year partnership since 2021) — GCP is the preferred cloud
- The org is split into **Ford Model E** (EVs), **Ford Blue** (ICE), **Ford Pro** (fleet/commercial) — each has its own tech priorities
- Your manager is in **Detroit/Dearborn** — IST to EST gap is +9:30/+10:30 hours
- **Golden overlap: 7:00-10:30 PM IST** (9:30 AM-1:00 PM Detroit)

---

## Phase 1: Weeks 1-3 — Listen & Map

> Don't propose anything yet.

### DO

- **Listening tour with every team lead** — schedule 1:1s with 15-20 people. Ask three questions:
  - "What takes you the longest in your dev lifecycle today?"
  - "What did you try to fix that didn't work?"
  - "If you could change one thing about how we build/deploy, what would it be?"
- **Deploy something yourself** — don't just read docs. Actually push a change through the existing pipeline end-to-end. Feel the pain firsthand
- **Map the current tech landscape** — what CI/CD tools exist (Tekton? Jenkins? GHA?), what's the Git platform (GHEC EMU?), how many teams, what's their maturity level
- **Take 100 recent deployments** and map the full lifecycle — find where time actually goes
- **Schedule intros with US peers** — not just your manager. Engineering directors, product leads, SRE leads. Do this in the first 2 weeks while "I'm new" is a valid reason to ask for anyone's time
- **1:1 with your manager twice a week** for the first month — 30 mins, IST evening slot

### DON'T

- Never say **"At my last company, we..."** — the most infuriating refrain for existing teams (Will Larson)
- Don't propose any tool changes, architecture changes, or new platforms yet
- Don't express too many lightly-held opinions — creates organizational thrash
- Don't skip local team introductions to focus on US stakeholders

---

## Phase 2: Weeks 4-6 — Identify Bottlenecks & Quick Win

### DO

- **Identify 2-3 recurring pain points** from your listening tour — rank by frequency x time cost
- **Pick ONE quick win** that's visible and deliverable in 2 weeks. Examples:
  - Fix a broken CI pipeline that annoys 5 teams
  - Create a golden path template that eliminates 2 hours of boilerplate setup
  - Automate a manual deployment step everyone hates
- **Share your observations in writing** — a 2-page "What I've Learned" doc sent to your manager and key stakeholders. Observations, not recommendations yet
- **Start the weekly 3-bullet email** to your manager + skip-level:
  - What shipped this week
  - What's blocked
  - What's next
- **Record 5-min Loom/video demos** of anything you build — US teams consume video faster than docs

### DON'T

- Don't try to consolidate tools yet — "tool consolidation wars" are high friction, low value in the first 6 months
- Don't build dashboards on broken foundations — "Put a pane of glass on a pile of sh*t and all devs see is a pile of sh*t" (Humanitec, from 249 platform reviews)
- Don't copy hyperscaler patterns — Netflix/Google practices don't transfer to automotive

---

## Phase 3: Weeks 7-12 — First Golden Path (Day 90 Deliverable)

### DO

- **Deliver ONE working golden path by day 90** — a complete "new service" flow: repo creation -> CI/CD -> security scanning -> deployment
- **Find a lighthouse team** — one team willing to adopt your golden path first. Overinvest in making their experience dramatically better. They become your evangelists
- **Build golden paths, not cages** — let teams opt out, but they bear the maintenance cost of going custom. Platform must be "compelling to use, not standing on a mandate" (Martin Fowler)
- **Quantify the before/after** — "Tasks requiring cross-team coordination were 10-12x slower" (Fowler). Measure your org's version of this. DORA metrics from day 1
- **Present at a team all-hands** within your first month — volunteer, don't wait to be asked. Show the quick win, show the golden path vision, keep it to 10 minutes
- **Establish your async rhythm** — deep work 9 AM-4 PM IST (your India hours), US sync 7-10:30 PM IST (decisions only, not status updates)

### DON'T

- Don't do a big-bang rollout — "Transformations almost never fail because of wrong technology and almost always because of culture" (Humanitec)
- Don't optimize onboarding — it's 1% of the lifecycle. Optimize the deploy-test-release loop instead
- Don't let the platform team become a helpdesk — you're a product team assigned problems to solve, not a feature team taking orders (Marty Cagan)

---

## Phase 4: Months 4-5 — Scale & Stakeholder Alignment

### DO

- **Expand from 1 lighthouse team to 3-5** — each adoption teaches you what's missing
- **Run a toolchain hackathon** — let teams influence the platform roadmap. This kills "not invented here" syndrome (Disney's approach)
- **Build your stakeholder map:**
  - Who controls budget? (likely US-based director/VP)
  - Who are the skeptics? (win them with data, not arguments)
  - Who are your allies? (other architects, SRE leads, DevOps folks)
- **Start monthly platform metrics reports** — adoption rate, deployment frequency, lead time, failure rate. US leadership trusts numbers over narratives
- **Build 2-3 US allies** who loop you into informal decisions — explicitly tell them: "If something affects platform architecture, please ping me before it's decided"

### DON'T

- Don't let your local team bypass you to go directly to US stakeholders — that fragments your authority. You're the bridge, not a passthrough
- Don't over-promise US timelines to your local team — be transparent: "US is still deciding, I'll update by Thursday"

---

## Phase 5: Month 6 — Strategic Position

### DO

- **Write a 6-month retrospective** — what you found, what you built, what changed, what's the 12-month vision. Share it widely
- **Propose your 12-month platform roadmap** — grounded in the data you've collected, backed by the wins you've delivered
- **Position the platform team as a product team** — with a backlog, user research (developer interviews), and measurable outcomes
- **Push for a recurring platform review** with US leadership — monthly or quarterly, where you present metrics and roadmap

### DON'T

- Don't wait 6 months to share your vision — by month 4, you should be seeding ideas. Month 6 is when you formalize them

---

## Daily Operating Rhythm (Chennai)

| Time (IST) | Activity |
|------------|----------|
| 9:00-9:30 AM | Scan Slack/email, triage overnight US messages |
| 9:30 AM-12:30 PM | Deep work — architecture, coding, design docs |
| 12:30-1:30 PM | Lunch + local team sync |
| 1:30-4:00 PM | India-side meetings, pair work, mentoring |
| 4:00-6:30 PM | Break / personal time |
| 7:00-10:30 PM | US overlap — sync calls, decisions, 1:1s |

**Rule: No recurring calls before 7 PM IST or after 10:30 PM IST.** Accommodate exceptions for the first 60 days to build trust, then set this boundary.

---

## The 5 Rules That Actually Matter on the Ground

1. **Propose before being asked** — send a 1-page RFC BEFORE the US team discusses it. If you only respond to tickets, you'll be positioned as an executor regardless of your title

2. **Speak in the first 5 minutes** of any meeting or you'll be forgotten. Prepare 1-2 points beforehand. Use "I want to add context from the India side" as an entry phrase

3. **Deliver something visible every 2 weeks** — a demo, a metric, a tool, a doc. Silence from India = assumption that nothing is happening

4. **Ask your manager for a weekly "what's being discussed informally" debrief** — hallway decisions in Dearborn are real and damaging when you miss them

5. **Your POC is your secret weapon** — you've already built a working platform agent (19 tools, 317 tests, scaffold -> CI -> infra in one command). Don't lead with it on day 1, but by week 6-8, demo it as "something I prototyped" — it instantly proves you're not theoretical

---

## India-US Remote Working: Critical Tactics

### Building Trust Remotely

- **Deliver one visible quick win in week 2-3** — something the US team notices
- **Be the person who follows up** — if something was discussed, send the summary, track the action item
- **Show your reasoning, not just conclusions** — US teams trust people who explain their thinking process
- **1:1s with US peers (not just your manager)** — schedule 30-min introductions with every stakeholder in your first 2 weeks

### Avoiding the "Execution Arm" Trap

This is the #1 risk for India-based architects.

**Countermove:** Propose solutions before being asked. Send a 1-page RFC/design doc BEFORE the US team discusses it. Frame it as "here's what I recommend and why" not "what should I do?"

### Handling Hallway Decisions You Miss

- Ask your manager for a weekly "what's being discussed informally" debrief
- Build 2-3 US allies who will loop you in via Slack when decisions are brewing
- Explicitly say in your first week: "If something affects platform architecture, please ping me before it's decided"

### Making Work Visible to US Leadership

- **Weekly 3-bullet update email** to your manager + skip-level
- **Record 5-min Loom demos** of architectural decisions or POCs
- **Metrics dashboard** from day 1 — DORA metrics, adoption numbers, anything quantifiable
- **Present at team all-hands** within your first month — volunteer for it

### Meeting Culture

- Speak in the **first 5 minutes** or you'll be forgotten
- Prepare 1-2 points beforehand
- Use "I want to add context from the India side" as a legitimate entry phrase
- Don't wait to be called on

---

## Key References

| Source | Key Insight |
|--------|-------------|
| Humanitec (249 platform reviews) | "Transformations fail because of culture, not technology" |
| Martin Fowler | "Build golden paths, not cages — compelling to use, not mandated" |
| Will Larson | "Limit to ONE organizational change in first 90 days" |
| Charity Majors | "Real influence comes from subject-matter expertise, not title" |
| Marty Cagan | "Platform team must be an empowered product team, not a helpdesk" |
| Team Topologies | "Thinnest Viable Platform — just enough capability, no bloat" |
| platformengineering.org | "Don't invent paths — observe how they emerge and productize them" |

---

> The biggest risk isn't technical; it's being perceived as "the India execution guy" instead of the architect driving strategy. Every action above is designed to prevent that.
