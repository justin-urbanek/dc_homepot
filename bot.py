"""Moderation bot: honeypot channel + quarantine (strip all roles, restore them later)."""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()


def env_id(name: str, required: bool = False) -> int:
    value = os.environ.get(name, "").strip()
    if not value and not required:
        return 0
    if not value.isdigit():
        sys.exit(
            f"{name} must be a numeric Discord ID (e.g. 123456789012345678), got {value!r}. "
            "Enable Developer Mode in Discord and use right-click → Copy ID."
        )
    return int(value)


def env_ids(name: str) -> list[int]:
    ids = []
    for part in os.environ.get(name, "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            sys.exit(f"{name} must be comma-separated numeric Discord IDs, got {part!r}.")
        ids.append(int(part))
    return ids


TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
if not TOKEN:
    sys.exit("DISCORD_TOKEN is missing in .env")
GUILD_ID = env_id("GUILD_ID", required=True)
HONEYPOT_CHANNEL_ID = env_id("HONEYPOT_CHANNEL_ID")
LOG_CHANNEL_ID = env_id("LOG_CHANNEL_ID")
QUARANTINE_ROLE_ID = env_id("QUARANTINE_ROLE_ID")
# Role users get by clicking the "accept rules" button
MEMBER_ROLE_ID = env_id("MEMBER_ROLE_ID")
# quarantine | softban | ban
HONEYPOT_ACTION = os.environ.get("HONEYPOT_ACTION", "quarantine").lower()
# How far back the caught user's messages get deleted
CLEANUP_MINUTES = int(os.environ.get("CLEANUP_MINUTES") or 10)
# Channels where all messages (except pinned ones) get deleted after AUTO_DELETE_HOURS
AUTO_DELETE_CHANNEL_IDS = env_ids("AUTO_DELETE_CHANNEL_IDS")
AUTO_DELETE_HOURS = float(os.environ.get("AUTO_DELETE_HOURS") or 24)

DATA_FILE = Path(__file__).parent / "data" / "quarantine.json"
GUILD = discord.Object(id=GUILD_ID)


class QuarantineError(Exception):
    pass


# --- Storage: which roles did a user have before quarantine? -----------------

def load_store() -> dict:
    try:
        return json.loads(DATA_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_store() -> None:
    DATA_FILE.parent.mkdir(exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(DATA_FILE)


store = load_store()


# --- Bot ---------------------------------------------------------------------

class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # must be enabled in the Developer Portal
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.handling: set[int] = set()

    async def setup_hook(self):
        self.add_view(RulesView())  # keeps buttons from earlier posts working after a restart
        if AUTO_DELETE_CHANNEL_IDS:
            auto_delete.start()
        self.tree.copy_global_to(guild=GUILD)
        try:
            await self.tree.sync(guild=GUILD)
        except discord.Forbidden:
            sys.exit(
                f"No access to server {GUILD_ID}. Either GUILD_ID is wrong or the bot isn't on the server "
                "(invite it with the scopes 'bot' AND 'applications.commands')."
            )


bot = Bot()


def quarantine_role(guild: discord.Guild) -> discord.Role | None:
    return guild.get_role(QUARANTINE_ROLE_ID) if QUARANTINE_ROLE_ID else None


def strippable_roles(member: discord.Member) -> list[discord.Role]:
    """All roles the bot can remove (excluding @everyone, booster/integration roles and the quarantine role)."""
    q_role = quarantine_role(member.guild)
    return [r for r in member.roles if not r.is_default() and not r.managed and r != q_role]


def outranks(actor: discord.Member, role: discord.Role) -> bool:
    return actor.id == actor.guild.owner_id or actor.top_role > role


async def log(guild: discord.Guild, text: str) -> None:
    print(text)
    channel = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if channel:
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            print(f"WARNING: couldn't post in the log channel: {e}")


async def quarantine(member: discord.Member, reason: str, by: discord.abc.User) -> None:
    guild = member.guild
    if member.id == guild.owner_id:
        raise QuarantineError("The server owner can't be quarantined.")
    if member.top_role >= guild.me.top_role:
        raise QuarantineError(
            f"{member.mention} has a role above or equal to the bot's role – move the bot role higher in the role list."
        )

    roles = strippable_roles(member)
    if str(member.id) in store and not roles:
        raise QuarantineError(f"{member.mention} is already quarantined.")

    # Save first, then remove roles – so the roles are never lost.
    store[str(member.id)] = {
        "roles": [r.id for r in roles],
        "reason": reason,
        "by": by.id,
        "at": discord.utils.utcnow().isoformat(),
    }
    save_store()

    keep = [r for r in member.roles if r.managed]
    if q_role := quarantine_role(guild):
        keep.append(q_role)
    try:
        await member.edit(roles=keep, reason=f"Quarantine: {reason}")
    except discord.HTTPException:
        del store[str(member.id)]
        save_store()
        raise


async def release(member: discord.Member, by: discord.abc.User) -> list[int]:
    """Restores the saved roles. Returns the IDs of roles that could no longer be given back."""
    entry = store.get(str(member.id))
    if not entry:
        raise QuarantineError(f"{member.mention} is not quarantined.")

    guild = member.guild
    restore, skipped = [], []
    for role_id in entry["roles"]:
        role = guild.get_role(role_id)
        if role and role < guild.me.top_role:
            restore.append(role)
        else:
            skipped.append(role_id)

    # Delete the entry first so the role guard (on_member_update) doesn't interfere.
    del store[str(member.id)]
    save_store()
    keep = [r for r in member.roles if r.managed]
    try:
        await member.edit(roles=keep + restore, reason=f"Quarantine lifted by {by}")
    except discord.HTTPException:
        store[str(member.id)] = entry
        save_store()
        raise
    return skipped


async def purge_recent(member: discord.Member) -> int:
    """Deletes the user's messages from the last CLEANUP_MINUTES in all text channels."""
    after = discord.utils.utcnow() - timedelta(minutes=CLEANUP_MINUTES)
    deleted = 0
    for channel in member.guild.text_channels:
        perms = channel.permissions_for(member.guild.me)
        if not (perms.read_message_history and perms.manage_messages):
            continue
        try:
            msgs = await channel.purge(
                limit=100, after=after, check=lambda m: m.author.id == member.id, reason="Honeypot cleanup"
            )
            deleted += len(msgs)
        except discord.HTTPException:
            continue
    return deleted


# --- Slash commands ----------------------------------------------------------

@bot.tree.command(name="quarantine", description="Removes all roles from a user until /release is used")
@app_commands.describe(user="Who?", reason="Why?")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def quarantine_cmd(interaction: discord.Interaction, user: discord.Member, reason: str = "no reason given"):
    if not outranks(interaction.user, user.top_role):
        await interaction.response.send_message("You can only quarantine users below your highest role.", ephemeral=True)
        return
    try:
        await quarantine(user, reason, interaction.user)
    except (QuarantineError, discord.HTTPException) as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(f"🔒 {user.mention} is now quarantined.", ephemeral=True)
    await log(interaction.guild, f"🔒 {user.mention} (`{user}`) quarantined by {interaction.user.mention} – {reason}")


@bot.tree.context_menu(name="Quarantine")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def quarantine_menu(interaction: discord.Interaction, user: discord.Member):
    await quarantine_cmd.callback(interaction, user, "via context menu")


@bot.tree.command(name="release", description="Gives a quarantined user their old roles back")
@app_commands.describe(user="Who?")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def release_cmd(interaction: discord.Interaction, user: discord.Member):
    entry = store.get(str(user.id))
    if entry:
        saved = [r for r in map(interaction.guild.get_role, entry["roles"]) if r]
        highest = max(saved, default=None)
        if highest and not outranks(interaction.user, highest):
            await interaction.response.send_message(
                "You can't give back roles that are above your own.", ephemeral=True
            )
            return
    try:
        skipped = await release(user, interaction.user)
    except (QuarantineError, discord.HTTPException) as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    note = f" ({len(skipped)} role(s) no longer exist or are above the bot)" if skipped else ""
    await interaction.response.send_message(f"🔓 {user.mention} has their roles back{note}.", ephemeral=True)
    await log(interaction.guild, f"🔓 {user.mention} (`{user}`) released by {interaction.user.mention}{note}")


@bot.tree.command(name="quarantine-list", description="Shows all quarantined users")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def list_cmd(interaction: discord.Interaction):
    if not store:
        await interaction.response.send_message("Nobody is quarantined.", ephemeral=True)
        return
    lines = [f"<@{uid}> – {e['reason']} (<t:{int(discord.utils.parse_time(e['at']).timestamp())}:R>)"
             for uid, e in store.items()]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


# --- Rules button ------------------------------------------------------------

class RulesView(discord.ui.View):
    def __init__(self, label: str = "Accept rules"):
        super().__init__(timeout=None)
        self.accept.label = label

    @discord.ui.button(label="Accept rules", style=discord.ButtonStyle.success, custom_id="rules:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        role = interaction.guild.get_role(MEMBER_ROLE_ID) if MEMBER_ROLE_ID else None
        if role is None:
            await interaction.response.send_message("The member role isn't set up – please tell an admin.", ephemeral=True)
            return
        if str(member.id) in store:
            await interaction.response.send_message("You are quarantined – please contact a moderator.", ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message("You already have access. 👍", ephemeral=True)
            return
        try:
            await member.add_roles(role, reason="Accepted the rules")
        except discord.HTTPException as e:
            await interaction.response.send_message("Something went wrong – please tell an admin.", ephemeral=True)
            await log(interaction.guild, f"⚠️ Couldn't give {role.mention} to {member.mention}: {e}")
            return
        await interaction.response.send_message(f"Welcome! You now have the {role.mention} role. 🎉", ephemeral=True)
        await log(interaction.guild, f"✅ {member.mention} (`{member}`) accepted the rules → {role.mention}")


@bot.tree.command(name="rules-button", description="Posts the 'accept rules' button in this channel")
@app_commands.describe(text="Message above the button", label="Text on the button")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def rules_button_cmd(
    interaction: discord.Interaction,
    text: app_commands.Range[str, 1, 2000] = "Read the rules above, then click the button to get access to the server.",
    label: app_commands.Range[str, 1, 80] = "Accept rules",
):
    try:
        await interaction.channel.send(text, view=RulesView(label))
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Couldn't post here: {e}", ephemeral=True)
        return
    await interaction.response.send_message("✅ Button posted.", ephemeral=True)


# --- Auto-delete -------------------------------------------------------------

@tasks.loop(minutes=10)
async def auto_delete():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    cutoff = discord.utils.utcnow() - timedelta(hours=AUTO_DELETE_HOURS)
    for channel_id in AUTO_DELETE_CHANNEL_IDS:
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        try:
            deleted = await channel.purge(
                limit=None, before=cutoff, check=lambda m: not m.pinned, reason="Auto-delete"
            )
        except discord.HTTPException as e:
            print(f"WARNING: auto-delete in #{channel.name} failed: {e}")
            continue
        if deleted:
            print(f"Auto-delete: removed {len(deleted)} message(s) from #{channel.name}")


@auto_delete.before_loop
async def before_auto_delete():
    await bot.wait_until_ready()


# --- Events ------------------------------------------------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} – {len(store)} user(s) in quarantine")
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"WARNING: bot is not on server {GUILD_ID}")
        return
    me = guild.me

    def check_channel(label: str, channel_id: int, needed: list[str]) -> None:
        if not channel_id:
            print(f"{label}: not set (disabled)")
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            print(f"WARNING: {label} {channel_id} not found – wrong ID?")
            return
        perms = channel.permissions_for(me)
        missing = [p for p in needed if not getattr(perms, p)]
        if missing:
            print(f"WARNING: {label} #{channel.name}: bot is missing {', '.join(missing)} "
                  "(private channel/category? add the bot role to the channel permissions)")
        else:
            print(f"{label}: #{channel.name} OK")

    check_channel("Honeypot", HONEYPOT_CHANNEL_ID, ["view_channel", "read_message_history", "manage_messages"])
    check_channel("Log channel", LOG_CHANNEL_ID, ["view_channel", "send_messages"])
    for channel_id in AUTO_DELETE_CHANNEL_IDS:
        check_channel(
            f"Auto-delete ({AUTO_DELETE_HOURS:g}h)", channel_id,
            ["view_channel", "read_message_history", "manage_messages"],
        )
    if MEMBER_ROLE_ID:
        member_role = guild.get_role(MEMBER_ROLE_ID)
        if member_role is None:
            print(f"WARNING: MEMBER_ROLE_ID {MEMBER_ROLE_ID} not found – wrong ID?")
        elif member_role >= me.top_role:
            print(f"WARNING: member role {member_role.name} is above the bot – the rules button can't give it")
        else:
            print(f"Member role: {member_role.name} OK")
    else:
        print("Member role: not set (rules button disabled)")
    if QUARANTINE_ROLE_ID and quarantine_role(guild) is None:
        print(f"WARNING: QUARANTINE_ROLE_ID {QUARANTINE_ROLE_ID} not found – wrong ID?")
    if not me.guild_permissions.manage_roles:
        print("WARNING: bot is missing the Manage Roles permission")
    above = [r.name for r in guild.roles if r > me.top_role]
    if above:
        print(f"WARNING: these roles are above the bot, their members can't be quarantined: {', '.join(above)}")


@bot.event
async def on_message(message: discord.Message):
    if message.channel.id != HONEYPOT_CHANNEL_ID or not isinstance(message.author, discord.Member):
        return
    member = message.author
    # Mods/admins may post there (e.g. the warning), and so may the bot itself.
    if member == message.guild.me:
        return
    if member.guild_permissions.manage_messages:
        if member.id == message.guild.owner_id:
            source = "server owner"
        else:
            source = ", ".join(
                r.name for r in member.roles if r.permissions.manage_messages or r.permissions.administrator
            )
        print(f"Honeypot: ignoring {member} (has Manage Messages via: {source})")
        return
    if member.id in bot.handling:
        return  # spam bots often post several messages at once
    bot.handling.add(member.id)
    try:
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        reason = "Honeypot channel"
        if HONEYPOT_ACTION in ("ban", "softban"):
            await member.ban(reason=reason, delete_message_seconds=CLEANUP_MINUTES * 60)
            if HONEYPOT_ACTION == "softban":
                await message.guild.unban(member, reason="Softban (only delete messages + kick)")
            await log(message.guild, f"🍯 {member.mention} (`{member}`) posted in the honeypot → {HONEYPOT_ACTION}")
        else:
            await quarantine(member, reason, bot.user)
            deleted = await purge_recent(member)
            await log(
                message.guild,
                f"🍯 {member.mention} (`{member}`) posted in the honeypot → quarantined, "
                f"{deleted} message(s) from the last {CLEANUP_MINUTES} min deleted. Review, then /release or ban.",
            )
    except QuarantineError:
        pass  # already quarantined
    except discord.HTTPException as e:
        await log(message.guild, f"⚠️ Honeypot: couldn't punish {member.mention}: {e}")
    finally:
        bot.handling.discard(member.id)


@bot.event
async def on_member_join(member: discord.Member):
    # Leaving and rejoining doesn't lift the quarantine.
    if str(member.id) in store and (q_role := quarantine_role(member.guild)):
        await member.add_roles(q_role, reason="Rejoined while quarantined")
        await log(member.guild, f"↩️ {member.mention} rejoined while quarantined.")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Guard: quarantined users don't get roles back (e.g. via onboarding) except through /release."""
    if str(after.id) not in store:
        return
    added = [r for r in strippable_roles(after) if r not in before.roles]
    if not added:
        return
    try:
        await after.remove_roles(*added, reason="User is quarantined – use /release")
    except discord.HTTPException:
        return
    await log(
        after.guild,
        f"🛡️ {after.mention} is quarantined, removed role(s) {', '.join(r.mention for r in added)} again. "
        "Use /release to give roles back.",
    )


bot.run(TOKEN)
