# Banner Customization

## What the Banner Is

The ASCII art shown at the top when running `tm on` (turning team mode on). It is the team's first impression, the team sign shown every time a session opens.

- **Source**: `memory/banner.txt`. If this file exists, the engine prints it exactly as-is. Teams set up with `install.py` already have the default (`ansi_shadow`, "TEAMMODE") installed (`install` also installs banner.txt for fresh teams). An environment that only ran plain `teammode.py on` falls back to simple text.
- **Side effect**: If the banner content is changed to be **different** from the default, `personality_customized` becomes `true`, and the "💡 팀색 입히기: tm-customize" suggestion at the end of the `tm on` banner disappears (a signal that the team color has already been applied). This flag is **not** a key stored in `team.config.json`; it is a value the engine computes at runtime. It exists only as a field in `tm context --json` output, and becomes `true` if any one of the banner, greeting, or farewell differs from the default. Because this is a content comparison, not an existence check, an unchanged default banner is false.

## ⚠️ Guard (Mandatory)

`banner.txt` is under `memory/`, so the `kb-write-guard` hook **blocks Edit/Write tools**. Write it only through **Bash** (`cp`, `tee`, etc.).

```bash
# ✅ Correct
cp infra/banners/slant.txt memory/banner.txt
# ❌ Blocked — the guard rejects writing memory/banner.txt with Edit/Write tools
```

## Choose the Method — Always Present Both Paths When Starting Banner Customization

**⚠️ Required agent behavior**: When a banner customization request arrives, do not stop after showing only presets. **Explicitly guide the user through these two paths**:

> **(A) 6 presets** — Choose a font from the ready-made ASCII art for the text "TEAMMODE"  
> **(B) Team-name ASCII** — Generate custom art directly from the actual team name, for example "ACME"

Tell the user **first** that both are possible, then either ask which direction to take or show samples from both and let the user choose. Respond in the user's language.

---

## Method 1 — Choose from 6 Presets

There are 6 options in `infra/banners/`. Each has a different character:

| Font | Character | Feel |
|---|---|---|
| `ansi_shadow` | Heavy blocks + shadow | Weighty, high-impact (current default) |
| `slant` | Italic slashes | Sharp, fast |
| `chunky` | Box/dot style | Retro, arcade |
| `cyberlarge` | Thin and wide | Minimal, technical |
| `larry3d` | 3D depth | Flashy, old-school |
| `speed` | Underlined italic | Fast, simple |

**Recommendation**: If the team name is short and the user wants impact, show `ansi_shadow` (the default) first. If the user wants a sharper feel, show `slant` first. The final choice belongs to the user.

**Procedure** — Do not choose by guessing. **Show the actual output and let the user choose**:

```bash
# 1) Show candidates directly (one or two at a time, or all)
cat infra/banners/slant.txt
# 2) Apply the font selected by the user
cp infra/banners/<폰트명>.txt memory/banner.txt
# 3) Verify the applied result
cat memory/banner.txt
```

**⚠️ Output rule**: Spaces and alignment are meaningful in ASCII art. When showing candidates to the user, **always wrap them in code blocks (``` fences)**. Without fences, the Markdown renderer may break alignment or collapse content. When showing multiple candidates at once, wrap **each candidate in its own separate code block**. Respond in the user's language.

> Note: All presets use the text "TEAM" or "TEAMMODE". If the user wants to change it to the team name, use Method 2.

## Method 2 — Generate Custom ASCII from the Team Name

Use `figlet` or `pyfiglet` (a Python package) to create art directly from the team name. Because generation is possible with pyfiglet **even when figlet is unavailable**, try the options in the order below.

### figlet (system command)

```bash
command -v figlet || echo "figlet 없음"                    # Check availability first
figlet -f slant "ACME" > /tmp/b.txt && cat /tmp/b.txt    # Preview
cp /tmp/b.txt memory/banner.txt                            # Apply if the user likes it
```

- figlet fonts: many options such as `standard`, `big`, `banner`, `block`, and `slant` (list them with `figlist`). Preview and apply with `figlet -f <폰트> "팀명"`.

### pyfiglet (Python package — try first when figlet is unavailable)

If figlet is unavailable, check pyfiglet first. It is often already installed in the system python3 (no separate installation needed):

```bash
python3 -c "import pyfiglet; print(pyfiglet.__version__)"                          # Check availability
python3 -c "import pyfiglet; print(pyfiglet.figlet_format('ACME', font='slant'))"  # Preview
```

Generate and apply with pyfiglet:

```bash
python3 -c "import pyfiglet; print(pyfiglet.figlet_format('ACME', font='ansi_shadow'))" > /tmp/b.txt
cat /tmp/b.txt          # After checking
cp /tmp/b.txt memory/banner.txt   # Apply
```

pyfiglet font names match the presets (`ansi_shadow`, `slant`, `chunky`, `cyberlarge`, `larry3d`, `speed`, etc.).

**Recommendation**: For team-name ASCII, `slant` works well for most team-name lengths. If the team name is 4 characters or fewer, `ansi_shadow` can also work well for a heavier look.

### When neither figlet nor pyfiglet is available

Either ① use the presets from Method 1 or ② guide installation (`sudo apt install figlet` for Debian/Raspbian, or `pip install pyfiglet`). Do not force installation on your own; ask the user.

> **Same output rule**: When showing a generated ASCII preview to the user, **wrap it in a code block** (the same principle as Method 1). Respond in the user's language.

## Common Mistakes

| Mistake | Correct Method |
|---|---|
| Writing `memory/banner.txt` with Edit/Write tools | Bash `cp`/`tee` only (the guard blocks Edit/Write) |
| Applying a candidate arbitrarily without showing it | Show the actual output with `cat`, then let the user choose |
| Pasting ASCII art without a code block | Always wrap it in ``` fences (preserve alignment) |
| Showing only presets and not mentioning the team-name ASCII option | Present both Method 1 and Method 2 first |
| Assuming figlet exists | Check `command -v figlet`; if unavailable, try pyfiglet |
| Treating missing pyfiglet as immediate failure | Check `python3 -c "import pyfiglet"`; if unavailable, guide installation |
| Modifying the original files under `infra/banners/` | Leave originals unchanged; create only `memory/banner.txt` |
