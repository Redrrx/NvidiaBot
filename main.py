import json
import discord
from discord.ext import tasks, commands
from tinydb import TinyDB, Query
import feedparser
from requests import get
import datetime
import uuid


try:
    with open('config.json') as config_file:
        config = json.load(config_file)
        token = config.get('token')
        if not token:
            raise ValueError("No token found in config.json. Please ensure the 'token' field is filled.")
except FileNotFoundError:
    raise FileNotFoundError("The config.json file was not found. Please ensure it exists in the same directory as your script.")



intents = discord.Intents.default()
bot = commands.Bot(intents=intents, command_prefix='!')
Filing = Query()
db = TinyDB('news.json')


@bot.event
async def on_guild_join(guild):
    try:
        integrations = await guild.integrations()
        for integration in integrations:
            if isinstance(integration, discord.BotIntegration):
                if integration.application.user.id == bot.user.id:
                    bot_inviter = integration.user
                    await bot_inviter.send(
                        "Thank you for using Nvidia news discord bot!\n"
                        "Please use the `/setchannel` command to set the channels for updates.\n"
                        "For example, use `/setchannel filings #sec-filings` for SEC filings, and\n"
                        "`/setchannel press #press-releases` for press releases.\n"
                        "You can also use the same channel for both updates if you prefer."
                    )
                    break
    except discord.Forbidden:
        print("Missing permissions to fetch integrations.")
    except Exception as e:
        print(f"An error occurred: {e}")


@bot.slash_command(description="Set the channel for posting updates")
@commands.has_permissions(administrator=True)
async def setchannel(ctx, update_type: discord.Option(str, "Choose the update type", choices=["filings", "press"]), channel: discord.TextChannel):
    update_key = f"{update_type}_channel_name"
    db.upsert({'type': update_key, 'channel_name': channel.name}, Query().type == update_key)

    news_instance = getattr(bot, 'news', None)
    if news_instance:
        if update_type == "filings":
            news_instance.check_filings.restart()
        elif update_type == "press":
            news_instance.press_releases.restart()
        await ctx.respond(f"{update_type.capitalize()} updates will be posted in #{channel.name}")
    else:
        await ctx.respond("News module not initialized yet.")


class News:
    def __init__(self, bot, db):
        self.db = db
        self.bot = bot
        self.Posts = Query()
        self.press_releases.start()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.google.com',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'no-cache',
        }

    @tasks.loop(minutes=10)
    async def check_filings(self):
        channel_name_entry = self.db.search(Query().type == 'filings_channel_name')
        if not channel_name_entry:
            print("Filings channel not set.")
            return

        rss_url = "https://investor.nvidia.com/rss/SECFiling.aspx?Exchange=CIK&Symbol=0001045810"
        response = get(rss_url, headers=self.headers)
        rss = feedparser.parse(response.text)
        ninety_days_ago = datetime.datetime.now() - datetime.timedelta(days=30)

        for item in rss.entries:
            sec_link = item.link
            sec_title = item.title
            sec_pub_date_str = item.published
            sec_pub_date = datetime.datetime.strptime(sec_pub_date_str, '%a, %d %b %Y %H:%M:%S %z')
            sec_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, sec_link))

            if sec_pub_date >= ninety_days_ago and not self.db.search(self.Posts.uuid == sec_uuid):
                self.db.insert({
                    'type': 'sec_filing',
                    'title': sec_title,
                    'link': sec_link,
                    'pub_date': sec_pub_date_str,
                    'uuid': sec_uuid,
                    'posted': False
                })
                channel_name = self.db.search(self.Posts.channel_name.exists())
                if channel_name:
                    channel_name = channel_name[0]['channel_name']
                    channel = discord.utils.get(self.bot.guilds[0].channels, name=channel_name)
                else:
                    channel = discord.utils.get(self.bot.guilds[0].channels, name='general')

                if channel:
                    embed = discord.Embed(title=sec_title, url=sec_link, description=f"Published on {sec_pub_date_str}")
                    await channel.send(embed=embed)
                else:
                    print("No suitable channel found.")

    @tasks.loop(minutes=10)
    async def press_releases(self):
        channel_name_entry = self.db.search(Query().type == 'press_channel_name')
        if not channel_name_entry:
            print("Press releases channel not set.")
            return
        rss_url = "https://nvidianews.nvidia.com/cats/press_release.xml"
        response = get(rss_url, headers=self.headers)
        rss = feedparser.parse(response.text)
        for item in reversed(rss.entries):
            link = item.link
            title = item.title
            pub_date_str = item.published
            pub_date = datetime.datetime.strptime(item.published, '%a, %d %b %Y %H:%M:%S %Z')
            press_release_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, link))
            one_month_ago = datetime.datetime.now() - datetime.timedelta(days=30)
            if pub_date >= one_month_ago and not self.db.search(self.Posts.uuid == press_release_uuid):
                self.db.insert({
                    'type': 'press_release',
                    'title': title,
                    'link': link,
                    'pub_date': pub_date_str,
                    'uuid': press_release_uuid,
                    'posted': False
                })
                channel_name = self.db.search(self.Posts.channel_name.exists())
                if channel_name:
                    channel_name = channel_name[0]['channel_name']
                    channel = discord.utils.get(self.bot.guilds[0].channels, name=channel_name)
                else:
                    channel = discord.utils.get(self.bot.guilds[0].channels, name='general')

                if channel:
                    embed = discord.Embed(title=title, url=link, description=f"Published on {pub_date_str}")
                    await channel.send(embed=embed)
                    self.db.update({'posted': True}, self.Posts.uuid == press_release_uuid)
                else:
                    print("No suitable channel found.")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    bot.news = News(bot, db)

bot.run(token)
