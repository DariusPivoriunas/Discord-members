#!/usr/bin/env python3
"""
discord_relationship_map.py

Builds a member "relationship" map for one Discord guild by scanning readable
channel history and scoring replies, mentions and reactions between members.

Discord does NOT expose friend lists via the API, so this measures actual
interaction instead, which is a decent proxy for who is close to whom.

Install:
    pip install "discord.py>=2.3"

Setup:
    1. https://discord.com/developers/applications -> New Application -> Bot
    2. Under Bot -> Privileged Gateway Intents, enable:
         - SERVER MEMBERS INTENT
         - MESSAGE CONTENT INTENT
    3. OAuth2 -> URL Generator -> scope "bot", permissions:
         View Channels, Read Message History
       Invite the bot to the server (you need Manage Server there).
    4. export DISCORD_TOKEN="..."   and   export DISCORD_GUILD_ID="..."
       (guild ID: right-click server -> Copy Server ID, with Developer Mode on)

Run:
    python discord_relationship_map.py
"""

import os
from collections import Counter, defaultdict

import discord

# ---------------------------------------------------------------- config ----

TOKEN = os.environ.get("DISCORD_TOKEN", "PASTE_BOT_TOKEN_HERE")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))

MESSAGE_LIMIT = 5000        # per channel. None = full history (slow!)
INCLUDE_THREADS = True
INCLUDE_REACTIONS = True    # costs one extra API call per reaction emoji
IGNORE_BOTS = True

TOP_N = 10                  # direct links listed per member
SECOND_DEGREE_N = 5         # "friends of friends" listed per member
MIN_WEIGHT = 2              # hide noise-level links

W_REPLY = 3
W_MENTION = 2
W_REACTION = 1

OUT_TXT = "relationship_map.txt"
OUT_CSV = "edges.csv"       # source,target,weight -> import into Gephi if you like

# ----------------------------------------------------------------- state ----

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

weights = defaultdict(Counter)   # weights[a][b] = weights[b][a] = score
names = {}
msg_count = Counter()


def note(user):
    """Register a user, return its id, or None if it should be skipped."""
    if user is None:
        return None
    if IGNORE_BOTS and getattr(user, "bot", False):
        return None
    names[user.id] = getattr(user, "display_name", None) or user.name
    return user.id


def link(a, b, w):
    if a is None or b is None or a == b:
        return
    weights[a][b] += w
    weights[b][a] += w


def second_degree(uid, limit):
    """People uid doesn't interact with, but uid's contacts do."""
    direct = {u for u, w in weights[uid].items() if w >= MIN_WEIGHT}
    scores, via = Counter(), defaultdict(set)
    for f in direct:
        for g, w in weights[f].items():
            if g == uid or g in direct or w < MIN_WEIGHT:
                continue
            scores[g] += w
            via[g].add(f)
    return [(g, s, sorted(names.get(f, str(f)) for f in via[g]))
            for g, s in scores.most_common(limit)]


# ----------------------------------------------------------------- scan -----

async def scan_channel(ch):
    print(f"  #{ch.name}", flush=True)
    try:
        # Newest-first with a limit, then process oldest-first so that a
        # reply's target is already in author_of when we reach the reply.
        history = [m async for m in ch.history(limit=MESSAGE_LIMIT)]
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"    skipped ({e.__class__.__name__})")
        return

    author_of = {}
    for msg in reversed(history):
        a = note(msg.author)
        author_of[msg.id] = a
        if a is None:
            continue
        msg_count[a] += 1

        ref = msg.reference
        if ref is not None and ref.message_id:
            target = author_of.get(ref.message_id)
            if target is None and isinstance(ref.resolved, discord.Message):
                target = note(ref.resolved.author)
            link(a, target, W_REPLY)

        for u in msg.mentions:
            link(a, note(u), W_MENTION)

    if INCLUDE_REACTIONS:
        for msg in history:
            a = author_of.get(msg.id)
            if a is None or not msg.reactions:
                continue
            for r in msg.reactions:
                try:
                    async for u in r.users():
                        link(note(u), a, W_REACTION)
                except discord.HTTPException:
                    pass


@client.event
async def on_ready():
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        print(f"Bot is not in guild {GUILD_ID} (or GUILD_ID is wrong).")
        await client.close()
        return

    print(f"Scanning {guild.name} ...", flush=True)
    targets = [c for c in guild.text_channels
               if c.permissions_for(guild.me).read_message_history]
    if INCLUDE_THREADS:
        for c in list(targets):
            targets.extend(c.threads)

    for ch in targets:
        await scan_channel(ch)

    write_report(guild)
    await client.close()


# --------------------------------------------------------------- output -----

def write_report(guild):
    ranked = sorted(weights, key=lambda u: sum(weights[u].values()), reverse=True)

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(f"Interaction map for {guild.name}\n")
        f.write(f"{len(ranked)} members with at least one link\n")
        f.write("weights: reply=%d mention=%d reaction=%d\n\n"
                % (W_REPLY, W_MENTION, W_REACTION))

        for uid in ranked:
            links = [(b, w) for b, w in weights[uid].most_common() if w >= MIN_WEIGHT]
            if not links:
                continue
            f.write("=== %s  (%d msgs, %d links) ===\n"
                    % (names.get(uid, uid), msg_count[uid], len(links)))
            for b, w in links[:TOP_N]:
                f.write("    %-28s %5d\n" % (names.get(b, b), w))

            fof = second_degree(uid, SECOND_DEGREE_N)
            if fof:
                f.write("  -- friends of friends --\n")
                for g, s, via in fof:
                    f.write("    %-28s %5d   via %s\n"
                            % (names.get(g, g), s, ", ".join(via)))
            f.write("\n")

    seen = set()
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        f.write("source,target,weight\n")
        for a in weights:
            for b, w in weights[a].items():
                key = tuple(sorted((a, b)))
                if key in seen or w < MIN_WEIGHT:
                    continue
                seen.add(key)
                f.write('"%s","%s",%d\n'
                        % (names.get(a, a).replace('"', "'"),
                           names.get(b, b).replace('"', "'"), w))

    print(f"\nWrote {OUT_TXT} and {OUT_CSV}")


if __name__ == "__main__":
    client.run(TOKEN)
