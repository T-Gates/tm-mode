<!--
  tm-mode value — single source.
  The tm-onboard skill reads this file and conveys it according to the person and context
  (new team creation / joining an existing team, role) in the user's language,
  **in human words**. Do not recite it verbatim — deliver the points in the speaker's words,
  and only what fits the situation.
  (The team/founder may freely refine the value "wording." The skill owns only the procedure;
  the content lives here.)
-->

# What tm-mode does

## In one line
tm-mode **turns on "team mode" in the Claude and Codex you already use** — **that one-line install you just finished is all of it.** There is no new tool, no new dashboard, and nothing separate to learn. The agent you already use now **knows the team.**

That moves the team's **recording and reading from people to agents** — even when someone works fast alone, team context automatically flows and accumulates.

## Why — for AI-native teams, this is infrastructure, not an option
When people work with AI agents, individuals get explosively faster. But **that is exactly why the team gets more opaque.**

- The faster individuals move, the faster and more deeply context gets trapped inside personal sessions -> the *"fast individuals, opaque team"* paradox is **most severe in AI-native teams**.
- On top of that, agents need context to work -> every session creates an endless cost of hand-feeding team context to the agent.

tm-mode solves both at once. **For teams that do not use AI, it is "nice to have"; for AI-native teams, it is infrastructure they cannot do without.**

> Why not Slack, Notion, or a wiki? — people write those, and people read those. In tm-mode, **agents** do both sides -> zero extra human labor.

## What it does

### 1. Automatic team context injection
- Before — every time work starts, people ask "What is that person doing right now?" or move ahead without knowing.
- After — when a session opens, *what the team is doing right now* comes up automatically. You start on top of the team context.

### 2. Automatic session-log recording
- Before — people summarize and report "What did you do today?" and align in meetings.
- After — when work ends, it becomes a shared team log as-is. The team knows without anyone separately saying it — zero reporting cost.

### 3. Shared team memory
- Before — the next person struggles through a problem someone else already solved.
- After — know-how, decisions, and hard-won lessons accumulate in the team memory base and become searchable. What was solved once becomes a team asset.

### 4. Team skills ready to use — `/tm`
- tm-mode brings a default set of **skills that use accumulated session logs and memory** — checking team context, searching and adding team memory, customizing the team, and connecting services.
- Once tm-mode is on, **one `/tm`** brings those skills into one place. There is no need to memorize commands; the accumulated team data attaches directly to the work at hand.

### 5. Free customization — skills and team identity
- Before — everyone tweaks their own agent settings separately, and better ways of working stay in individual heads.
- After — when the team defines the skills it will use (work methods and procedures) **in one place, they are automatically distributed to both Claude and Codex**. Team members choose and enable only the skills they want. **The team's way of working is shared and versioned like code** — even joiners inherit the team's way of working on day one.
- **Utility skills are opt-in** — shared team (base) skills are distributed automatically, and other utility skills are **enabled and disabled individually** through `tm-customize`'s `util` (add, remove, list). Zero coercion; each person keeps only what they need, lightly.
- Team **identity is flexible too** — apply the team's color, such as banners and greetings, with `tm-customize`. This is not a fixed mold; shape tm-mode **to fit your team.**

### 6. Team-mode switching — on and off
- tm-mode is not always on. When you **turn it on** with `tm on`, you work on top of team context and automatic recording; when you **turn it off** with `tm off`, you return to personal work.
- It is the switch that explicitly turns on *"I am doing team work from now on."* Team work and personal work do not mix, and only team work remains in team memory. (tm-mode = **Turn your team mode on.**)

## Delivery tone (guide)
- **The listener has just finished installation (`tm-mode init`/`tm-mode join`).** Do not explain the installation *method* — lightly note, in the user's language, that "that one line just now was all of it," then go straight to the value.
- No exaggeration or sales pitch. Plainly explain "what this does **for you**."
- If the listener is **AI-native**, lead with "why — infrastructure you cannot do without" (the paradox + the cost of hand-feeding context).
- For a **newly created team**: say, in the user's language, "It is empty now — but it accumulates **from now on**." / For a **team the person has joined**: say, in the user's language, "You start on top of already accumulated team context."
- Do not recite all six items. Pick two or three that fit the person's situation and make them clear. Do not drag it out.
