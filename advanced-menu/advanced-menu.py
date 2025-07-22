# Rebuilt full advanced-menu.py with fixes and final improvements

import asyncio
import traceback
import discord
from discord.ext import commands
from discord.ext.commands.view import StringView
from copy import copy

from core import checks
from core.models import DummyMessage, PermissionLevel
from core.utils import normalize_alias

import logging
logger = logging.getLogger(__name__)


class AdvancedMenu(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = self.bot.plugin_db.get_partition(self)
        self.config = {
            "_id": "advanced-menu",
            "enabled": True,
            "timeout": 60,
            "close_on_timeout": True,
            "anonymous_menu": True,
            "dropdown_placeholder": "Select an option to contact the staff team.",
            "embed_text": "Please select an option.",
            "options": {
                "test_report": {
                    "label": "Test Report",
                    "description": "Test test",
                    "emoji": "1️⃣",
                    "type": "command",
                    "callback": "reply Hello, please make your report."
                }
            },
            "submenus": {}
        }

    async def cog_load(self):
        stored = await self.db.find_one({"_id": "advanced-menu"})
        if not stored:
            await self.update_config()
        else:
            self.config.update(stored)

    async def update_config(self):
        await self.db.find_one_and_update(
            {"_id": "advanced-menu"},
            {"$set": self.config},
            upsert=True
        )

    @commands.group(name="menu", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def menu_group(self, ctx):
        await ctx.send("Available subcommands: `add`, `remove`, `list`, `settext`, `enable`, `disable`, `reload`.")

    @menu_group.command()
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def list(self, ctx):
        if not self.config["options"]:
            return await ctx.send("No options configured.")
        msg = "**Current Options:**\n"
        for key, opt in self.config["options"].items():
            msg += f"`{key}` → {opt['label']}\n"
        await ctx.send(msg)

    @menu_group.command()
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def add(self, ctx, key, label, type, callback, emoji="💬", *, description="-"):
        self.config["options"][key] = {
            "label": label,
            "description": description,
            "emoji": emoji,
            "type": type,
            "callback": callback
        }
        await self.update_config()
        await ctx.send(f"Added menu option `{key}`.")

    @menu_group.command()
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def remove(self, ctx, key):
        if key not in self.config["options"]:
            return await ctx.send("That key doesn't exist.")
        del self.config["options"][key]
        await self.update_config()
        await ctx.send(f"Removed menu option `{key}`.")

    @menu_group.command()
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def enable(self, ctx):
        self.config["enabled"] = True
        await self.update_config()
        await ctx.send("Advanced menu has been enabled.")

    @menu_group.command()
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def disable(self, ctx):
        self.config["enabled"] = False
        await self.update_config()
        await ctx.send("Advanced menu has been disabled.")

    @menu_group.command()
    @checks.has_permissions(PermissionLevel.ADMIN)
    async def reload(self, ctx):
        stored = await self.db.find_one({"_id": "advanced-menu"})
        self.config.update(stored)
        await ctx.send("Reloaded advanced menu config from database.")

    @commands.Cog.listener()
    async def on_thread_ready(self, thread, creator, category, initial_message):
        if not self.config.get("enabled"):
            return

        dummy_msg = DummyMessage(initial_message)
        dummy_msg.author = self.bot.modmail_guild.me
        dummy_msg.content = self.config.get("embed_text")

        sent, _ = await thread.reply(dummy_msg, self.config.get("anonymous_menu", True))
        main = next((m for m in sent if hasattr(m.channel, 'recipient') and m.channel.recipient == thread.recipient), None)

        if main:
            await main.edit(view=DropdownView(self.bot, main, thread, self.config, self.config["options"], is_home=True))


# Helpers
class Dropdown(discord.ui.Select):
    def __init__(self, bot, msg, thread, config, data, is_home):
        self.bot = bot
        self.msg = msg
        self.thread = thread
        self.config = config
        self.data = data
        self.is_home = is_home
        options = [
            discord.SelectOption(label=item["label"], description=item["description"], emoji=item["emoji"])
            for item in data.values()
        ]
        if not is_home:
            options.append(discord.SelectOption(label="Main menu", description="Back to root menu", emoji="🏠"))
        super().__init__(placeholder=config["dropdown_placeholder"], min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.view.done()
        selected = self.values[0]

        if selected == "Main menu":
            await self.msg.edit(view=DropdownView(self.bot, self.msg, self.thread, self.config, self.config["options"], is_home=True))
            return

        # Match by label instead of derived key
        option = next((opt for opt in self.data.values() if opt["label"] == selected), None)
        if not option:
            await self.thread.reply(f"Unknown option `{selected}`.")
            return

        if option["type"] == "command":
            dummy = DummyMessage(copy(self.thread._genesis_message))
            dummy.content = self.bot.prefix + option["callback"]
            await invoke_commands(option["callback"], self.bot, self.thread, dummy)
        else:
            submenu = option["callback"]
            submenu_data = self.config["submenus"].get(submenu)
            if submenu_data:
                await self.msg.edit(view=DropdownView(self.bot, self.msg, self.thread, self.config, submenu_data, is_home=False))
            else:
                await self.thread.reply(f"Submenu `{submenu}` not found.")


class DropdownView(discord.ui.View):
    def __init__(self, bot, msg, thread, config, options, is_home):
        super().__init__(timeout=config["timeout"])
        self.bot = bot
        self.msg = msg
        self.thread = thread
        self.config = config
        self.add_item(Dropdown(bot, msg, thread, config, options, is_home))

    async def on_timeout(self):
        await self.msg.edit(view=None)
        if self.config.get("close_on_timeout"):
            dummy = DummyMessage(copy(self.thread._genesis_message))
            dummy.content = self.bot.prefix + "close The menu selection timed out."
            await invoke_commands("close The menu selection timed out.", self.bot, self.thread, dummy)

    async def done(self):
        self.stop()
        await self.msg.edit(view=None)


async def invoke_commands(alias, bot, thread, message):
    ctxs = []
    if alias:
        aliases = normalize_alias(alias)
        for alias in aliases:
            view = StringView(bot.prefix + alias)
            ctx_ = commands.Context(prefix=bot.prefix, view=view, bot=bot, message=message)
            ctx_.thread = thread
            discord.utils.find(view.skip_string, await bot.get_prefix())
            ctx_.invoked_with = view.get_word().lower()
            ctx_.command = bot.all_commands.get(ctx_.invoked_with)
            ctxs.append(ctx_)

    for ctx in ctxs:
        if ctx.command:
            old_checks = copy(ctx.command.checks)
            ctx.command.checks = [checks.has_permissions(PermissionLevel.INVALID)]
            try:
                await bot.invoke(ctx)
            except Exception as e:
                logger.warning("Command failed: %s", e)
                await thread.reply(f"⚠️ Command `{ctx.command}` failed.\n{e}")
            ctx.command.checks = old_checks
        else:
            await thread.reply(f"⚠️ Command not found: `{ctx.invoked_with}`")


async def setup(bot):
    await bot.add_cog(AdvancedMenu(bot))
