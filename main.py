"""
Seymour Discord Bot
====================

Commands
--------
/simport file:<.txt>        Import a Seymour-style export, linked to your Discord account.
/soverride file:<.txt>      Like /simport, but wipes your existing items first.
/search hex:<#RRGGBB> [threshold] [max_abs]
                             Find ΔE matches across everyone's imported items.
/spattern pattern:<...>      Find hexes by substring, wildcard, or grouped-variable pattern.
/sdupe user:<discord username>
                             Find exact-hex dupes between you and another user.
/smystats                   Quick stats on your own imported collection.
/ssummary                   Overall piece count across everyone's databases.
/suploadinfo                 Step-by-step on how to get + upload your export.
/sclear user:<discord username>
                             ADMIN ONLY (gling). Wipes another user's stored items.

Setup
-----
1. pip install -r requirements.txt
2. Copy .env.example to .env and fill in DISCORD_TOKEN (and optional GUILD_ID
   for instant command sync while developing).
3. python bot.py
"""
import os
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import color_utils
import database as db
import pattern_utils

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional, speeds up command sync during dev

# Discord *username* (not nickname/display name) allowed to run /sclear.
# Override via .env if needed: ADMIN_USERNAME=someoneelse
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "gling").lower()

PAGE_SIZE = 15
DEFAULT_DELTA_THRESHOLD = 10.0

# Matches: "<Item Name> | #RRGGBB | ..." — only the first two fields are used.
LINE_RE = re.compile(r"^(.*?)\s*\|\s*#([0-9A-Fa-f]{6})\s*\|")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------------------------------
# Pagination UI
# --------------------------------------------------------------------------
class PaginatorView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.index = 0
        self.author_id = author_id
        self._update_buttons()

    def _update_buttons(self):
        self.first_page.disabled = self.index == 0
        self.prev_page.disabled = self.index == 0
        self.next_page.disabled = self.index >= len(self.pages) - 1
        self.last_page.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran the command can flip pages.", ephemeral=True
            )
            return False
        return True

    async def _go(self, interaction: discord.Interaction, new_index: int):
        self.index = new_index
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, 0)

    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, max(0, self.index - 1))

    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, min(len(self.pages) - 1, self.index + 1))

    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, len(self.pages) - 1)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True  # type: ignore


def paginate_lines(title: str, lines: list[str], color: discord.Color, footer_extra: str = "") -> list[discord.Embed]:
    if not lines:
        return [discord.Embed(title=title, description="No results found.", color=color)]

    pages = []
    chunks = [lines[i:i + PAGE_SIZE] for i in range(0, len(lines), PAGE_SIZE)]
    total_pages = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        embed = discord.Embed(title=title, description="\n".join(chunk), color=color)
        footer = f"Page {i}/{total_pages} • {len(lines)} result(s)"
        if footer_extra:
            footer += f" • {footer_extra}"
        embed.set_footer(text=footer)
        pages.append(embed)
    return pages


async def send_paginated(interaction: discord.Interaction, pages: list[discord.Embed]):
    if len(pages) == 1:
        await interaction.followup.send(embed=pages[0])
        return
    view = PaginatorView(pages, author_id=interaction.user.id)
    await interaction.followup.send(embed=pages[0], view=view)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
@bot.event
async def on_ready():
    db.init_db()
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        # Wipe any stale *global* commands from a previous run without
        # GUILD_ID set, so the picker doesn't show two copies of everything.
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
    else:
        # Wipe any stale *guild-scoped* commands from a previous run that
        # had GUILD_ID set, for every guild this bot is currently in.
        for guild in bot.guilds:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
        await bot.tree.sync()
    print(f"Logged in as {bot.user} | commands synced")


# --------------------------------------------------------------------------
# Shared import helpers
# --------------------------------------------------------------------------
async def parse_import_attachment(file: discord.Attachment) -> tuple[list[tuple[str, str]], Optional[str]]:
    """
    Reads + parses a Seymour-style .txt export attachment.
    Returns (rows, error_message). On success error_message is None.
    """
    if not file.filename.lower().endswith(".txt"):
        return [], "Please upload a `.txt` file."

    try:
        raw = await file.read()
        text = raw.decode("utf-8", errors="ignore")
    except Exception as e:
        return [], f"Couldn't read that file: {e}"

    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        item_name = m.group(1).strip()
        hex_color = m.group(2).upper()
        if item_name:
            rows.append((item_name, hex_color))

    if not rows:
        return [], (
            "No matching lines found. Expected lines like:\n"
            "`Satin Trousers | #623E2B | Top: Exo pure brown (\u0394E: 7.54 | Abs: 26)`"
        )

    return rows, None


# --------------------------------------------------------------------------
# /simport
# --------------------------------------------------------------------------
@bot.tree.command(name="simport", description="Import your Seymour .txt export and link it to your Discord account.")
@app_commands.describe(file="The .txt export file (e.g. 'Item | #HEXCODE | Top: ...' per line)")
async def simport(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(thinking=True)

    rows, error = await parse_import_attachment(file)
    if error:
        await interaction.followup.send(error)
        return

    db.upsert_user(interaction.user.id, interaction.user.name)
    inserted = db.bulk_insert_items(interaction.user.id, rows)
    total = db.item_count_for_user(interaction.user.id)

    embed = discord.Embed(
        title="Import complete",
        description=(
            f"Parsed **{len(rows)}** line(s) from `{file.filename}`.\n"
            f"Added **{inserted}** item(s).\n"
            f"You now have **{total}** item(s) stored."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Linked to Discord user: {interaction.user.name}")
    await interaction.followup.send(embed=embed)


# --------------------------------------------------------------------------
# /soverride
# --------------------------------------------------------------------------
@bot.tree.command(
    name="soverride",
    description="Wipe your existing items and replace them with a fresh .txt export.",
)
@app_commands.describe(file="The .txt export file that will REPLACE your current stored items")
async def soverride(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(thinking=True)

    rows, error = await parse_import_attachment(file)
    if error:
        await interaction.followup.send(error)
        return

    db.upsert_user(interaction.user.id, interaction.user.name)
    old_count = db.item_count_for_user(interaction.user.id)
    db.clear_user_items(interaction.user.id)
    inserted = db.bulk_insert_items(interaction.user.id, rows)
    total = db.item_count_for_user(interaction.user.id)

    embed = discord.Embed(
        title="Override complete",
        description=(
            f"Wiped your previous **{old_count}** item(s).\n"
            f"Parsed **{len(rows)}** line(s) from `{file.filename}`.\n"
            f"You now have **{total}** item(s) stored."
        ),
        color=discord.Color.orange(),
    )
    embed.set_footer(text=f"Linked to Discord user: {interaction.user.name}")
    await interaction.followup.send(embed=embed)


# --------------------------------------------------------------------------
# /search
# --------------------------------------------------------------------------
@bot.tree.command(name="search", description="Find color matches for a hex code across everyone's imported items.")
@app_commands.describe(
    hex="Hex color, e.g. 000000 or #623E2B",
    threshold="Max CIE76 delta E to include (default 10)",
    max_results="Cap on number of results to show (default: all matches)",
)
async def search(
    interaction: discord.Interaction,
    hex: str,
    threshold: Optional[float] = DEFAULT_DELTA_THRESHOLD,
    max_results: Optional[int] = None,
):
    await interaction.response.defer(thinking=True)

    target_hex = color_utils.normalize_hex(hex)
    if not target_hex:
        await interaction.followup.send("That doesn't look like a valid hex code. Try something like `623E2B`.")
        return

    items = db.get_all_items()
    if not items:
        await interaction.followup.send("No items have been imported yet. Run `/simport` first.")
        return

    matches = []
    for item in items:
        try:
            delta_e, abs_d = color_utils.compare_hex(target_hex, item.hex_color)
        except Exception:
            continue
        if delta_e <= threshold:
            matches.append((delta_e, abs_d, item))

    matches.sort(key=lambda t: t[0])
    if max_results:
        matches = matches[:max_results]

    lines = [
        f"**{item.item_name}** `#{item.hex_color}` — ΔE: `{delta_e:.2f}` | Abs: `{abs_d}` — *{item.username}*"
        for delta_e, abs_d, item in matches
    ]

    pages = paginate_lines(
        title=f"Matches for #{target_hex} (ΔE \u2264 {threshold})",
        lines=lines,
        color=discord.Color.blurple(),
    )
    await send_paginated(interaction, pages)


# --------------------------------------------------------------------------
# /spattern
# --------------------------------------------------------------------------
@bot.tree.command(
    name="spattern",
    description="Find hexes by substring (ABC), wildcard (4x5x0x), or grouped pattern (WYWYWY).",
)
@app_commands.describe(
    pattern=(
        "ABC = substring anywhere. 4x5x0x = positional, x = don't care. "
        "WYWYWY = same letter must be the same digit at every position it appears."
    )
)
async def spattern(interaction: discord.Interaction, pattern: str):
    await interaction.response.defer(thinking=True)

    try:
        normalized = pattern_utils.validate_pattern(pattern)
    except pattern_utils.PatternError as e:
        await interaction.followup.send(str(e))
        return

    items = db.get_all_items()
    if not items:
        await interaction.followup.send("No items have been imported yet. Run `/simport` first.")
        return

    found = [item for item in items if pattern_utils.matches(normalized, item.hex_color)]

    lines = [
        f"**{item.item_name}** `#{item.hex_color}` — *{item.username}*"
        for item in found
    ]

    pages = paginate_lines(
        title=f"Pattern matches for `{normalized}`",
        lines=lines,
        color=discord.Color.purple(),
    )
    await send_paginated(interaction, pages)


# --------------------------------------------------------------------------
# /sdupe
# --------------------------------------------------------------------------
async def username_autocomplete(interaction: discord.Interaction, current: str):
    names = db.all_usernames(current)
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


@bot.tree.command(name="sdupe", description="Find exact-hex dupes between you and another imported Discord user.")
@app_commands.describe(user="The other Discord username to compare against")
@app_commands.autocomplete(user=username_autocomplete)
async def sdupe(interaction: discord.Interaction, user: str):
    await interaction.response.defer(thinking=True)

    me = db.find_user_by_username(interaction.user.name)
    if not me:
        await interaction.followup.send("You haven't imported anything yet. Run `/simport` first.")
        return

    other = db.find_user_by_username(user)
    if not other:
        await interaction.followup.send(f"No imported data found for a user named `{user}`.")
        return

    if other[0] == me[0]:
        await interaction.followup.send("You can't compare your collection against itself.")
        return

    my_items = db.get_items_for_user(me[0])
    their_items = db.get_items_for_user(other[0])

    their_by_hex: dict[str, list] = {}
    for it in their_items:
        their_by_hex.setdefault(it.hex_color, []).append(it)

    lines = []
    for mine in my_items:
        for theirs in their_by_hex.get(mine.hex_color, []):
            lines.append(
                f"**{mine.username}** — {mine.item_name} — `#{mine.hex_color}`  ⟷  "
                f"**{theirs.username}** — {theirs.item_name} — `#{theirs.hex_color}`"
            )

    pages = paginate_lines(
        title=f"Dupes: {me[1]} vs {other[1]}",
        lines=lines,
        color=discord.Color.gold(),
    )
    await send_paginated(interaction, pages)


# --------------------------------------------------------------------------
# /smystats
# --------------------------------------------------------------------------
@bot.tree.command(name="smystats", description="See how many items you've imported.")
async def smystats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    count = db.item_count_for_user(interaction.user.id)
    await interaction.followup.send(f"You have **{count}** item(s) stored.", ephemeral=True)


# --------------------------------------------------------------------------
# /ssummary
# --------------------------------------------------------------------------
@bot.tree.command(name="ssummary", description="Overall piece count across everyone's imported databases.")
async def ssummary(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    total = db.total_item_count()
    per_user = db.item_counts_by_user()

    if not per_user:
        await interaction.followup.send("No items have been imported yet. Run `/simport` first.")
        return

    lines = [f"**{username}** — {count} item(s)" for username, count in per_user]

    pages = paginate_lines(
        title=f"Overall summary — {total} item(s) across {len(per_user)} user(s)",
        lines=lines,
        color=discord.Color.dark_teal(),
    )
    await send_paginated(interaction, pages)


# --------------------------------------------------------------------------
# /suploadinfo
# --------------------------------------------------------------------------
@bot.tree.command(name="suploadinfo", description="How to get your Seymour export and upload it to this bot.")
async def suploadinfo(interaction: discord.Interaction):
    embed = discord.Embed(
        title="How to upload your collection",
        description=(
            "**1.** In-game, run `/seymour export database` to generate your export.\n"
            "**2.** Save the output as a plain text file (`.txt`).\n"
            "**3.** In Discord, run `/simport` and attach that `.txt` file "
                    "(use `/soverride` instead if you want it to replace what you already have).\n\n"
            "Each line should look like:\n"
            "`Satin Trousers | #623E2B | Top: Exo pure brown (\u0394E: 7.54 | Abs: 26)`\n\n"
            "Once it's in, try `/search`, `/spattern`, or `/sdupe`."
        ),
        color=discord.Color.teal(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------------
# /sclear (admin only)
# --------------------------------------------------------------------------
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.name.lower() == ADMIN_USERNAME


@bot.tree.command(name="sclear", description="[Admin only] Wipe another user's stored items.")
@app_commands.describe(user="The Discord username whose stored items will be deleted")
@app_commands.autocomplete(user=username_autocomplete)
@app_commands.check(is_admin)
async def sclear(interaction: discord.Interaction, user: str):
    await interaction.response.defer(thinking=True, ephemeral=True)

    target = db.find_user_by_username(user)
    if not target:
        await interaction.followup.send(f"No imported data found for a user named `{user}`.", ephemeral=True)
        return

    discord_id, username = target
    count = db.item_count_for_user(discord_id)
    db.clear_user_items(discord_id)

    await interaction.followup.send(
        f"Cleared **{count}** item(s) belonging to **{username}**.", ephemeral=True
    )


@sclear.error
async def sclear_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        msg = "You don't have permission to use this command."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    raise error


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    bot.run(TOKEN)