---
name: generate-avatars
description: Generate realistic AI headshot avatars for scenario personas
allowed-tools: Bash, Read, Edit, Glob, Grep, Write, AskUserQuestion
---

# Generate Avatars for Scenario Personas

Generate realistic AI-generated headshot avatars for personas in a scenario. Uses thispersondoesnotexist.com for photorealistic faces, with user review for quality and fit.

## Usage

Invoke with a scenario name:
```
/generate-avatars tech-startup
```

If no scenario is specified, ask which scenario to generate avatars for.

## Process

### 1. Read the scenario

Read `scenarios/<name>/scenario.yaml` to get the character list. For each character, note:
- **persona key** (e.g. `director`, `senior`)
- **display_name** (e.g. "Dr. Chen (Research Director)")
- **team_description** (role context)
- **existing avatar** field (skip if already set, unless user wants to regenerate)

Also read each character's `.md` file and look for age/career/experience clues to estimate an appropriate age range for the avatar.

### 2. Create the avatars directory

```bash
mkdir -p scenarios/<name>/avatars
```

### 3. Fetch candidate headshots

Fetch candidates from thispersondoesnotexist.com. The site returns a random 1024x1024 JPEG face on each request — there is NO control over age, gender, or ethnicity, so you must fetch in batches and visually review.

Fetch 8 candidates at a time with a 2-second delay between requests:
```bash
for i in 1 2 3 4 5 6 7 8; do
  curl -s -L -o "scenarios/<name>/avatars/candidate_${i}.jpg" \
    "https://thispersondoesnotexist.com"
  sleep 2
done
```

Then resize them all to 256x256:
```bash
for f in scenarios/<name>/avatars/candidate_*.jpg; do
  magick "$f" -resize 256x256 -quality 85 "$f"
done
```

### 4. Visual review with the user

View each candidate image using the Read tool (it renders images inline). For each candidate, assess:
- **Is it an adult?** (discard children immediately)
- **Clean framing?** (discard if extra people visible in frame)
- **Plausible match?** for the persona's name, role, and estimated age

Present a proposed assignment table to the user:
```
| Persona              | Candidate   | Reasoning                        |
|----------------------|-------------|----------------------------------|
| Dr. Chen (Director)  | candidate_3 | East Asian woman, ~40s, glasses  |
| Raj (Technical)      | candidate_7 | South Asian man, ~30s            |
```

If no suitable candidate exists for a persona, fetch another batch of 8 and repeat. Keep trying until the user approves all assignments.

### 5. Assign and clean up

Copy approved candidates to their final filenames and remove leftovers:
```bash
cp candidate_3.jpg director.jpg
cp candidate_7.jpg technical.jpg
# ... etc
rm candidate_*.jpg
```

### 6. Update scenario.yaml

Add or update the `avatar` field on each character entry. The value should be just the filename (not a path prefix) since the `/avatars/` route handles the directory:
```yaml
characters:
  director:
    display_name: "Dr. Chen (Research Director)"
    avatar: "director.jpg"      # <-- add this line
    character_file: "characters/dr-chen-director.CS.md"
```

### 7. Verify the wiring

Confirm the avatar serving infrastructure is in place:
- `lib/scenario_loader.py` reads the `avatar` field into PERSONAS dict
- `lib/webapp.py` has the `/avatars/<path:filename>` route
- `lib/webapp.py` JS `loadPersonas()` builds PERSONA_AVATARS map
- `lib/webapp.py` JS `appendMessageEl()` renders `<img>` for avatars

If any of these are missing, refer to the chat avatars implementation in commit `d2b742e`.

## Re-rolling individual avatars

If the user wants to replace a specific persona's avatar:
1. Fetch a batch of 8 candidates (as above)
2. View them all with the Read tool
3. Let the user pick one
4. Copy it to `<persona_key>.jpg`, remove candidates
5. No YAML change needed (filename stays the same)

## Important notes

- **No age/gender control**: thispersondoesnotexist.com is fully random. Expect ~30-50% of faces to be unusable (children, artifacts, extra people). Budget for 2-3 batches per persona.
- **Image size**: Always resize to 256x256 — the originals are 1024x1024 (~500KB each) which is excessive for 32px chat avatars.
- **Use `magick` not `convert`**: ImageMagick v7 deprecates the `convert` command.
- **Rate limiting**: Use `sleep 2` between fetches to be respectful to the service.
- **Licensing**: These AI-generated faces are not based on real people. There is no formal public domain declaration, but AI-generated images with no human author are generally considered non-copyrightable.
- **Alternative source**: DiceBear API (`api.dicebear.com/9.x/<style>/svg?seed=<name>`) can generate deterministic illustrated avatars. Use `notionists` style for professional look, `adventurer` for cartoon style. These are CC0/CC-BY licensed.
