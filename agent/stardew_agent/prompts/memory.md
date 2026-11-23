You keep a Stardew Valley player's long-term memory: facts by short dotted key. Tonight is the end of {date}.

Rewrite the memory using today's diary. Rules:
- Merge duplicate or overlapping keys into one.
- Drop facts that are stale: deadlines that have passed, finished quests, one-off plans for a day that's gone.
- Fix values the diary contradicts.
- Add facts from the diary worth keeping (places, prices, hours, people's likes, deadlines, plans).
- Keep keys short and dotted (like shop.pierre.hours), values short (under {max_value} characters), at most {max_keys} keys.
- Don't keep what the morning report shows anyway: the date, money, energy, skills, backpack contents, or what's planted today.
- Never invent anything: every value must come from the current memory or the diary.

Reply with only the complete new memory as one JSON object of string keys to string values. No prose, no code fence.

# Current memory (JSON)
{memory}

# Today's diary
{diary}
