import os
import asyncio
import discord
from discord.ext import commands
from openai import OpenAI
import yt_dlp
from aiohttp import web
import imageio_ffmpeg

# ==========================================
# 0. ระบบหลอก Render ให้รัน Web Service ได้ (Keep Alive Port)
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running!")

async def start_dummy_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ==========================================
# 1. ตั้งค่า Key และ Client
# ==========================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "ใส่_OPENROUTER_API_KEY_ของคุณที่นี่")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "ใส่_DISCORD_BOT_TOKEN_ของคุณที่นี่")
MODEL_NAME = "stealth/ox-alpha"

ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# ปรับแก้มาใช้ scsearch (SoundCloud) เพื่อเลี่ยงการติดล็อก IP Bot บน YouTube
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'scsearch',
    'source_address': '0.0.0.0'
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# ==========================================
# 2. ระบบคิวเพลง และ Control Panel (UI Buttons)
# ==========================================
class MusicPlayer:
    def __init__(self, ctx):
        self.bot = ctx.bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.current = None
        self.is_looping = False
        self.panel_message = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            if not self.is_looping or not self.current:
                try:
                    async with asyncio.timeout(300):
                        self.current = await self.queue.get()
                except TimeoutError:
                    return self.destroy(self.guild)

            source = discord.FFmpegPCMAudio(self.current['url'], executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
            self.guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))

            await self.update_panel()
            await self.next.wait()

            source.cleanup()

    async def update_panel(self):
        embed = discord.Embed(title="🎶 Music Control Panel", color=discord.Color.blue())
        embed.add_field(name="เพลงที่กำลังเล่น", value=f"**{self.current['title']}**", inline=False)
        embed.add_field(name="สถานะ Loop", value="🔄 เปิดอยู่" if self.is_looping else "❌ ปิดอยู่", inline=True)
        embed.add_field(name="คิวที่เหลือ", value=f"{self.queue.qsize()} เพลง", inline=True)
        
        view = MusicControlView(self)
        if self.panel_message:
            try:
                await self.panel_message.edit(embed=embed, view=view)
                return
            except Exception:
                pass
        self.panel_message = await self.channel.send(embed=embed, view=view)

    def destroy(self, guild):
        return self.bot.loop.create_task(guild.voice_client.disconnect())

class MusicControlView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("หยุดเพลงชั่วคราวแล้ว!", ephemeral=True)
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("เล่นเพลงต่อ!", ephemeral=True)
        else:
            await interaction.response.send_message("ไม่มีเพลงที่เล่นอยู่", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            self.player.is_looping = False
            vc.stop()
            await interaction.response.send_message("ข้ามเพลงเรียบร้อย!", ephemeral=True)
        else:
            await interaction.response.send_message("ไม่มีเพลงให้ข้าม", ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.success, emoji="🔁")
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.is_looping = not self.player.is_looping
        status = "เปิด" if self.player.is_looping else "ปิด"
        await self.player.update_panel()
        await interaction.response.send_message(f"{status} การเล่นซ้ำ (Loop) แล้ว!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            self.player.queue = asyncio.Queue()
            self.player.is_looping = False
            vc.stop()
            await vc.disconnect()
            await interaction.response.send_message("หยุดเพลงและออกจากห้องเรียบร้อย!", ephemeral=True)

players = {}

def get_player(ctx):
    try:
        player = players[ctx.guild.id]
    except KeyError:
        player = MusicPlayer(ctx)
        players[ctx.guild.id] = player
    return player

# ==========================================
# 3. คำสั่งเปิดเพลง และจัดการคิว
# ==========================================
@bot.command(name="play", help="สั่งเปิดเพลงหรือเพิ่มเข้าคิว")
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        await ctx.send("ดีนต้องเข้าห้องเสียงก่อนสั่งเปิดเพลงนะ!")
        return

    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()

    async with ctx.typing():
        try:
            info = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
            video = info['entries'][0] if 'entries' in info and len(info['entries']) > 0 else info

            player = get_player(ctx)
            await player.queue.put({'url': video['url'], 'title': video['title']})
            await ctx.send(f"เพิ่มเพลง **{video['title']}** เข้าคิวเรียบร้อยครับ!")
        except Exception as e:
            await ctx.send(f"เกิดข้อผิดพลาดในการดึงเพลง: {e}")

@bot.command(name="stop", help="หยุดและออกจากห้อง")
async def stop(ctx):
    if ctx.guild.id in players:
        del players[ctx.guild.id]
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("ออกจากห้องเสียงเรียบร้อยครับ!")

@bot.event
async def on_ready():
    print(f"ล็อกอินเรียบร้อย! บอท {bot.user.name} พร้อมใช้งานแล้ว!")
    await start_dummy_web_server()

# ==========================================
# 4. ระบบตอบแชทด้วย AI (ข้ามคำสั่งที่ขึ้นต้นด้วย !)
# ==========================================
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    clean_content = message.content.strip()
    if clean_content.startswith("!") or clean_content.startswith("<@"):
        content_without_mention = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if content_without_mention.startswith("!"):
            ctx = await bot.get_context(message)
            ctx.message.content = content_without_mention
            await bot.invoke(ctx)
            return

    if not clean_content:
        return

    async with message.channel.typing():
        try:
            response = await bot.loop.run_in_executor(
                None,
                lambda: ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": clean_content}]
                )
            )
            reply_text = response.choices[0].message.content

            if len(reply_text) > 2000:
                for i in range(0, len(reply_text), 1900):
                    await message.channel.send(reply_text[i:i+1900])
            else:
                await message.channel.send(reply_text)
        except Exception as e:
            await message.channel.send(f"เกิดข้อผิดพลาดในการประมวลผล AI: {e}")

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
