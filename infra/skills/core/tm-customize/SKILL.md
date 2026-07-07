---
name: tm-customize
description: Use when the user wants to customize team banner, identity (team name/greeting/farewell), or util skills. Triggers on "팀색 입히기", "커스텀", "배너 바꿔", "팀명 바꿔", "인사말 바꿔", "greeting", "farewell", "스킬 추가", "스킬 제거", "스킬 추천", "tm-customize", "customize team style", "customize banner", "change banner", "change team name", "change greeting", "change farewell", "add skill", "remove skill", "recommend skills".
---

# tm-customize — Customize Team Style

Router skill for applying team identity: banner, identity, and utility skills.

## Entry

First ask the user, in the user's language:

> "Banner / identity (team name, greeting, farewell) / skills — which area should we customize?"

Based on the user's choice, read the relevant `references/` document and work according to those instructions.

## References By Area

| Choice | Reference document | Summary |
|------|----------|------|
| Banner | `references/banner.md` | Replace the ASCII art banner printed during tm on |
| Identity | `references/identity.md` | Team name, greeting, and farewell text (`team.config.json`, can be changed anytime) |
| Skills | `references/skills.md` | Recommend, add, remove, and inspect per-member utility skills |

Keep the router lightweight. Delegate judgment and procedure to each references document.
