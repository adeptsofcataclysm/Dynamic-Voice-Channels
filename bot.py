import datetime
import os
import re
from collections import Counter
from contextlib import suppress

import discord
from discord.ext import commands
from discord.ext.commands import CommandError

from cogs.help import HelpCommand
from utils.config import settings
from utils.context import Context
from utils.jsonfile import JSONDict, JSONList

extensions = (
    'cogs.settings',
    'cogs.core',
    'cogs.voice',
)

intents = discord.Intents.none()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.presences = True
intents.guild_messages = True
intents.guild_reactions = True
intents.message_content = True


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            intents=intents,
            command_prefix=lambda b, m: b.prefixes.get(str(m.guild.id), settings.command_prefix),
            help_command=HelpCommand(),
            case_insensitive=True,
            owner_id=settings.owner_id,
            activity=discord.Activity(type=discord.ActivityType.listening, name=settings.command_prefix),
        )
        self.launched_at = None
        self.client_id = settings.bot_client_id

        if not os.path.exists('data'):
            os.mkdir('data')

        self.prefixes = JSONDict('data/prefixes.json')  # Mapping[guild_id, prefix]
        self.bad_words = JSONDict('data/bad_words.json')  # Mapping[guild_id, List[str]]
        self.configs = JSONDict('data/configs.json')  # Mapping[channel_id, config]
        self.channels = JSONList('data/channels.json')  # List[channel_id]
        self.blacklist = JSONList('data/blacklist.json')  # List[user_id|guild_id]

        self.voice_spam_control = commands.CooldownMapping.from_cooldown(2, 10, commands.BucketType.user)
        self.voice_spam_counter = Counter()

        self.text_spam_control = commands.CooldownMapping.from_cooldown(8, 10, commands.BucketType.user)
        self.text_spam_counter = Counter()

    async def setup_hook(self) -> None:
        for extension in extensions:
            # noinspection PyBroadException
            try:
                await self.load_extension(extension)
            except Exception:
                print(f'Failed to load extension {extension}.')

    async def on_ready(self):
        if self.launched_at is None:
            self.launched_at = datetime.datetime.utcnow()
            for guild in self.guilds:
                for channel in guild.voice_channels:
                    await self.on_voice_leave(channel)
            print('Logged in as', self.user)

    async def on_message(self, message):
        if message.guild is None:
            return
        await self.process_commands(message)

    async def on_message_edit(self, before, after):
        if before.content != after.content:
            await self.on_message(after)

    async def process_commands(self, message):
        ctx = await self.get_context(message, cls=Context)
        if ctx.command is None:
            return
        if ctx.author.id in self.blacklist:
            return
        if not ctx.channel.permissions_for(ctx.guild.me).send_messages:
            return
        bucket = self.text_spam_control.get_bucket(message)
        current = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()
        retry_after = bucket.update_rate_limit(current)
        if retry_after:
            self.text_spam_counter[ctx.author.id] += 1
            if self.text_spam_counter[ctx.author.id] >= 5:
                del self.text_spam_counter[ctx.author.id]
                self.blacklist.append(ctx.author.id)
                await self.blacklist.save()
            await ctx.send(f'You are being rate limited. Try again in `{retry_after:.2f}` seconds.')
        else:
            self.text_spam_counter.pop(message.author.id)
            await self.invoke(ctx)

    async def on_voice_state_update(self, member, before, after):
        if before.channel != after.channel:
            if before.channel is not None:
                await self.on_voice_leave(before.channel)
            if after.channel is not None:
                await self.on_voice_join(member, after.channel)

    async def on_voice_join(self, member, channel):
        if member.id in self.blacklist:
            return
        if str(channel.id) not in self.configs:
            return
        perms = member.guild.me.guild_permissions
        if not perms.manage_channels or not perms.move_members:
            return
        fake_message = discord.Object(id=0)
        fake_message.author = member
        bucket = self.voice_spam_control.get_bucket(fake_message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            await self._limit_rate(member, retry_after)
            return

        configuration = self.configs[str(channel.id)]
        name = configuration.get('name', "@user's channel")
        limit = configuration.get('limit', 10)
        bitrate = configuration.get('bitrate', 64000)
        top = configuration.get('top', False)
        try:
            category = member.guild.get_channel(configuration['category'])
        except KeyError:
            category = channel.category

        name = self._make_channel_name(name, member, category)

        if perms.manage_roles:
            overwrites = {member: discord.PermissionOverwrite(manage_channels=True, move_members=True)}
        else:
            overwrites = None

        new_channel = await member.guild.create_voice_channel(
            overwrites=overwrites,
            name=name,
            category=category,
            user_limit=limit,
            bitrate=bitrate,
        )
        if top:
            self.loop.create_task(new_channel.edit(position=0))

        await member.move_to(new_channel)
        self.channels.append(new_channel.id)
        await self.channels.save()

    async def on_voice_leave(self, channel):
        if channel.id in self.channels and len(channel.members) == 0:
            ch = channel.guild.get_channel(channel.id)
            if ch is not None:
                perms = channel.permissions_for(channel.guild.me)
                if perms.manage_channels:
                    await channel.delete()
            self.channels.remove(channel.id)
            await self.channels.save()

    async def on_guild_channel_delete(self, channel):
        if str(channel.id) in self.configs:
            try:
                self.configs.pop(str(channel.id))
            except KeyError:
                return
            await self.configs.save()

    async def on_guild_remove(self, guild):
        try:
            self.prefixes.pop(str(guild.id))
        except KeyError:
            pass
        else:
            await self.prefixes.save()
        try:
            self.bad_words.pop(str(guild.id))
        except KeyError:
            pass
        else:
            await self.bad_words.save()
        channel_dump = False
        config_dump = False
        for channel in guild.voice_channels:
            if channel.id in self.channels:
                self.channels.remove(channel.id)
                channel_dump = True
            if str(channel.id) in self.configs:
                try:
                    self.configs.pop(str(channel.id))
                except KeyError:
                    continue
                config_dump = True
        if channel_dump:
            await self.channels.save()
        if config_dump:
            await self.configs.save()

    async def on_guild_join(self, guild):
        if guild.id in self.blacklist:
            await guild.leave()

    async def on_command_error(self, ctx: Context, exception: CommandError) -> None:
        await ctx.send(str(exception), ephemeral=True)

    async def _limit_rate(self, member, retry_after):
        self.voice_spam_counter[member.id] += 1

        if self.voice_spam_counter[member.id] >= 5:
            del self.text_spam_counter[member.id]
            self.blacklist.append(member.id)
            await self.blacklist.save()
        with suppress(discord.Forbidden):
            await member.send(f'You are being rate limited. Try again in `{retry_after:.2f}` seconds.')

    def _make_channel_name(self, name, member, category):
        if '@user' in name:
            name = name.replace('@user', member.display_name)
        if '@game' in name:
            for activity in member.activities:
                if activity.type == discord.ActivityType.playing and activity.name is not None:
                    name = name.replace('@game', activity.name)
                    break
            else:
                name = name.replace('@game', 'no game')
        if '@position' in name:
            channels = [c for c in category.voice_channels if c.id in self.channels]
            name = name.replace('@position', str(len(channels) + 1))

        words = self.bad_words.get(str(member.guild.id), [])
        for word in words:
            if word.casefold() in name.casefold():
                name = re.sub(word, '*' * len(word), name, flags=re.IGNORECASE)

        if len(name) > 100:
            name = name[:97] + '...'

        return name


if __name__ == '__main__':
    Bot().run(settings.discord_token)
