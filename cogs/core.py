import datetime

import discord
import psutil
from discord.ext import commands

from utils.converters import StrRange


async def fetch_owner(bot):
    if bot.owner_id:
        owner = bot.get_user(bot.owner_id)
        if owner is not None:
            return owner
    app = await bot.application_info()
    bot.owner_id = app.owner.id
    return app.owner


class Core(commands.Cog):
    @commands.command(aliases=['join'])
    async def invite(self, ctx):
        """Gives you the bot invite link.
        If you don't want the bot to create its own role, or you want to set the permissions yourself,
        use the invite without permissions. But don't forget that it won't work without these permissions.
        """
        perms = discord.Permissions.none()
        perms.read_messages = True
        perms.read_message_history = True
        perms.send_messages = True
        perms.embed_links = True
        perms.add_reactions = True
        perms.external_emojis = True
        perms.move_members = True
        perms.manage_channels = True
        perms.manage_messages = True
        perms.manage_roles = True

        best_perms = discord.utils.oauth_url(ctx.bot.client_id, permissions=perms)
        current_perms = ctx.channel.permissions_for(ctx.guild.me)
        if current_perms.embed_links:
            no_perms = discord.utils.oauth_url(ctx.bot.client_id, permissions=discord.Permissions.none())
            await ctx.send(embed=discord.Embed(
                title=':envelope: Invite links',
                description=f'[Invite (recommended)]({best_perms})\n[Invite (no permissions)]({no_perms})',
                color=ctx.guild.me.color,
            ))
        else:
            await ctx.send(best_perms)

    @commands.command(aliases=['about', 'stats'])
    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.bot_has_permissions(embed_links=True)
    async def info(self, ctx):
        """Shows you information about the bot."""
        embed = discord.Embed(color=ctx.guild.me.color)
        embed.set_thumbnail(url=ctx.bot.user.avatar_url)
        owner = await fetch_owner(ctx.bot)
        embed.set_author(name=f'Owner: {owner}', icon_url=owner.avatar_url)
        proc = psutil.Process()
        with proc.oneshot():
            uptime = datetime.datetime.utcnow() - ctx.bot.launched_at
            mem_total = psutil.virtual_memory().total / (1024 ** 2)
            mem_of_total = proc.memory_percent()
            mem_usage = mem_total * (mem_of_total / 100)
            cpu_usage = proc.cpu_percent() / psutil.cpu_count()
        embed.add_field(name='Servers', value=str(len(ctx.bot.guilds)))
        embed.add_field(name='Channels', value=str(len(ctx.bot.channels)))
        embed.add_field(name='Latency', value=f'{round(ctx.bot.latency * 1000, 2)} ms')
        embed.add_field(name='Uptime', value=str(uptime))
        embed.add_field(name='CPU usage', value=f'{round(cpu_usage)}%')
        embed.add_field(name='Memory usage', value=f'{int(mem_usage)} / {int(mem_total)} MiB ({round(mem_of_total)}%)')
        await ctx.send(embed=embed)

    @commands.command(aliases=['suggest'])
    @commands.cooldown(1, 120, commands.BucketType.member)
    async def support(self, ctx, *, message: StrRange[0, 2000]):
        """Sends a message to the bot owner. Do not abuse."""
        owner = await fetch_owner(ctx.bot)
        if owner:
            embed = discord.Embed(description=message)
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
            await owner.send(embed=embed)
            await ctx.safe_send('Message has been sent. Thank you!', discord.Color.green())
        else:
            await ctx.safe_send('The owner could not be determined.', discord.Color.red())


async def setup(bot):
    await bot.add_cog(Core())
