import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI
import yt_dlp
from aiohttp import web
import static_ffmpeg

static_ffmpeg.add_paths()

# ==========================================
# 0. Keep-Alive Web Server สำหรับ Render
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

# ใส่ Guild ID ของเซิร์ฟเวอร์ดีนตรงนี้ (เปลี่ยนตัวเลขเป็น ID เซิร์ฟเวอร์จริงของคุณ)
# วิธีเอา ID: เปิด Developer Mode ใน Discord > คลิกขวาชื่อเซิร์ฟเวอร์ > Copy Server ID
GUILD_ID = 123456789012345678 
MY_GUILD = discord.Object(id=GUILD_ID)

ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Sync Slash Commands เข้า Guild เพื่อให้คำสั่งขึ้นใช้ได้ทันทีไม่ต้องรอ 1 ชม.
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)
        print("Sync Slash Commands เข้า Guild เรียบร้อยแล้ว!")

bot = MyBot()

# ==========================================
# 2. ตัวแปรตั้งค่าระบบต่างๆ (In-Memory Config)
# ==========================================
config = {
    "ai_enabled": True,
    "ai_channel_id": None,        # กำหนด ID ห้องที่อนุญาตให้ AI ตอบ (None คือตอบได้ทุกห้อง)
    "welcome_channel_id": None,   # ห้องแจ้งคนเข้า
    "goodbye_channel_id": None,   # ห้องแจ้งคนออก
    "welcome_message": "ยินดีต้อนรับคุณ {member} เข้าสู่เซิร์ฟเวอร์!",
    "goodbye_message": "คุณ {member} ได้ออกจากเซิร์ฟเวอร์ไปแล้ว...",
    "welcome_image_url": "",
    "verify_question": "กรุณาพิมพ์คำว่า 'agree' เพื่อยืนยันตัวตน:",
    "verify_answer": "agree",
    "verify_role_id": None
}

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
# 3. ระบบคิวเพลง และ Control Panel (แก้บัคส่งซ้ำ 2 รอบ)
# ==========================================
class MusicPlayer:
    def __init__(self, interaction):
        self.bot = interaction.client
        self.guild = interaction.guild
        self.channel = interaction.channel

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.current = None
        self.is_looping = False
        self.panel_message = None
        self._updating = False

        self.bot.loop.create_task(self.player_loop())

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

            source = discord.FFmpegPCMAudio(
                self.current['url'],
                executable='ffmpeg',
                **FFMPEG_OPTIONS
            )

            self.guild.voice_client.play(source, after=lambda e: self.bot.loop.call_soon_threadsafe(self.next.set))

            await self.update_panel()
            await self.next.wait()

            source.cleanup()

    async def update_panel(self):
        if not self.current or self._updating:
            return
        
        self._updating = True
        try:
            embed = discord.Embed(title="🎶 Music Control Panel", color=discord.Color.blue())
            embed.add_field(name="เพลงที่กำลังเล่น", value=f"**{self.current['title']}**", inline=False)
            embed.add_field(name="สถานะ Loop", value="🔄 เปิดอยู่" if self.is_looping else "❌ ปิดอยู่", inline=True)
            embed.add_field(name="คิวที่เหลือ", value=f"{self.queue.qsize()} เพลง", inline=True)
            
            view = MusicControlView(self)

            if self.panel_message:
                try:
                    await self.panel_message.edit(embed=embed, view=view)
                except discord.NotFound:
                    self.panel_message = await self.channel.send(embed=embed, view=view)
            else:
                self.panel_message = await self.channel.send(embed=embed, view=view)
        finally:
            self._updating = False

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

def get_player(interaction):
    try:
        player = players[interaction.guild.id]
    except KeyError:
        player = MusicPlayer(interaction)
        players[interaction.guild.id] = player
    return player

# ==========================================
# 4. ระบบยืนยันตัวตน (Verify Modal & Button)
# ==========================================
class VerifyModal(discord.ui.Modal, title="ยืนยันตัวตน"):
    answer_input = discord.ui.TextInput(
        label="คำตอบสำหรับยืนยันตัวตน",
        placeholder="กรอกคำตอบที่นี่...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_answer = self.answer_input.value.strip()
        expected = config["verify_answer"].strip()

        if user_answer.lower() == expected.lower():
            role_id = config["verify_role_id"]
            if role_id:
                role = interaction.guild.get_role(role_id)
                if role:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message("ยืนยันตัวตนสำเร็จ! มอบยศเรียบร้อยแล้วครับ 🎉", ephemeral=True)
                    return
            await interaction.response.send_message("ยืนยันตัวตนถูกต้องเรียบร้อยครับ!", ephemeral=True)
        else:
            await interaction.response.send_message("คำตอบไม่ถูกต้อง กรุณาลองใหม่อีกครั้งครับ!", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="คลิกเพื่อยืนยันตัวตน", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_button")
    async def verify_button_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = VerifyModal()
        modal.answer_input.label = config["verify_question"][:45]
        await interaction.response.send_modal(modal)

# ==========================================
# 5. Slash Commands (สั่งงานผ่าน / )
# ==========================================
@bot.tree.command(name="play", description="เปิดเพลงหรือเพิ่มเข้าคิว")
@app_commands.describe(search="ชื่อเพลง หรือ Link จาก YouTube / SoundCloud")
async def slash_play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.response.send_message("ดีนต้องเข้าห้องเสียงก่อนสั่งเปิดเพลงนะ!", ephemeral=True)
        return

    await interaction.response.defer()

    if interaction.guild.voice_client is None:
        await interaction.user.voice.channel.connect()

    try:
        info = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        video = info['entries'][0] if 'entries' in info and len(info['entries']) > 0 else info

        player = get_player(interaction)
        await player.queue.put({'url': video['url'], 'title': video['title']})
        await interaction.followup.send(f"เพิ่มเพลง **{video['title']}** เข้าคิวเรียบร้อยครับ!")

        if player.current:
            await player.update_panel()

    except Exception as e:
        await interaction.followup.send(f"เกิดข้อผิดพลาดในการดึงเพลง: {e}")

@bot.tree.command(name="stop", description="หยุดเล่นเพลงและให้อออกจากห้องเสียง")
async def slash_stop(interaction: discord.Interaction):
    if interaction.guild.id in players:
        del players[interaction.guild.id]
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("หยุดเพลงและออกจากห้องเสียงเรียบร้อยครับ!")
    else:
        await interaction.response.send_message("บอทไม่ได้อยู่ในห้องเสียงครับ", ephemeral=True)

@bot.tree.command(name="ai_toggle", description="เปิด หรือ ปิด ระบบ AI ตอบออโต้")
@app_commands.describe(status="เลือก True เพื่อเปิด หรือ False เพื่อปิด")
async def slash_ai_toggle(interaction: discord.Interaction, status: bool):
    config["ai_enabled"] = status
    msg = "เปิด" if status else "ปิด"
    await interaction.response.send_message(f"ทำการ {msg} ระบบตอบออโต้ AI เรียบร้อยแล้วครับ!")

@bot.tree.command(name="set_ai_channel", description="กำหนดห้องสำหรับการตอบออโต้ของ AI (ถ้าไม่เลือกจะตอบทุกห้อง)")
@app_commands.describe(channel="เลือกห้องที่ต้องการให้ AI ตอบ")
async def slash_set_ai_channel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if channel:
        config["ai_channel_id"] = channel.id
        await interaction.response.send_message(f"ตั้งค่าให้ AI ตอบเฉพาะในห้อง {channel.mention} เรียบร้อย!")
    else:
        config["ai_channel_id"] = None
        await interaction.response.send_message("ยกเลิกการจำกัดห้อง! AI จะตอบในทุกๆ ห้องแชทแล้วครับ")

@bot.tree.command(name="setup_welcome", description="ตั้งค่าระบบต้อนรับคนเข้าและคนออกจากดิส")
@app_commands.describe(
    welcome_channel="ห้องสำหรับแจ้งคนเข้า",
    goodbye_channel="ห้องสำหรับแจ้งคนออก",
    welcome_msg="ข้อความต้อนรับ (ใช้ {member} แทนการแท็กชื่อคนเข้า)",
    goodbye_msg="ข้อความคนออก (ใช้ {member} แทนชื่อคนออก)",
    image_url="URL รูปภาพวอลเปเปอร์หลัก (ถ้าไม่ใส่จะใช้รูปโปรไฟล์สมาชิก)"
)
async def slash_setup_welcome(
    interaction: discord.Interaction,
    welcome_channel: discord.TextChannel = None,
    goodbye_channel: discord.TextChannel = None,
    welcome_msg: str = None,
    goodbye_msg: str = None,
    image_url: str = None
):
    if welcome_channel:
        config["welcome_channel_id"] = welcome_channel.id
    if goodbye_channel:
        config["goodbye_channel_id"] = goodbye_channel.id
    if welcome_msg:
        config["welcome_message"] = welcome_msg
    if goodbye_msg:
        config["goodbye_message"] = goodbye_msg
    if image_url is not None:
        config["welcome_image_url"] = image_url

    await interaction.response.send_message("อัปเดตระบบแจ้งเตือนคนเข้า-ออกจากดิสเรียบร้อยครับ!", ephemeral=True)

@bot.tree.command(name="setup_verify", description="สร้างปุ่มและตั้งค่าระบบยืนยันตัวตน")
@app_commands.describe(
    question="คำถามยืนยันตัวตน",
    answer="คำตอบที่ถูกต้อง",
    role="ยศที่จะให้หลังจากยืนยันสำเร็จ"
)
async def slash_setup_verify(
    interaction: discord.Interaction,
    question: str,
    answer: str,
    role: discord.Role
):
    config["verify_question"] = question
    config["verify_answer"] = answer
    config["verify_role_id"] = role.id

    embed = discord.Embed(
        title="🔒 ระบบยืนยันตัวตน (Verification)",
        description=f"กรุณากดปุ่มด้านล่างเพื่อทำการตอบคำถามและรับยศ **{role.name}**",
        color=discord.Color.green()
    )
    view = VerifyView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("สร้างปุ่มยืนยันตัวตนเรียบร้อยครับ!", ephemeral=True)

# ==========================================
# 6. Event Listeners (คนเข้า/ออก และ AI แชท)
# ==========================================
@bot.event
async def on_member_join(member):
    ch_id = config["welcome_channel_id"]
    if ch_id:
        channel = member.guild.get_channel(ch_id)
        if channel:
            text = config["welcome_message"].format(member=member.mention)
            embed = discord.Embed(
                title="👋 ยินดีต้อนรับสมาชิกใหม่!", 
                description=text, 
                color=discord.Color.green()
            )
            
            # ดึงรูปโปรไฟล์ (Avatar) ของคนที่เข้ามาใหม่มาแปะใน Embed
            avatar_url = member.display_avatar.url
            embed.set_thumbnail(url=avatar_url)
            
            if config["welcome_image_url"]:
                embed.set_image(url=config["welcome_image_url"])
            else:
                embed.set_image(url=avatar_url)

            # พิมพ์แท็กเรียกชื่อผู้ใช้ใหม่พร้อมส่ง Embed
            await channel.send(content=f"ยินดีต้อนรับ {member.mention} !", embed=embed)

@bot.event
async def on_member_remove(member):
    ch_id = config["goodbye_channel_id"]
    if ch_id:
        channel = member.guild.get_channel(ch_id)
        if channel:
            text = config["goodbye_message"].format(member=member.display_name)
            embed = discord.Embed(
                title="😢 สมาชิกออกจากเซิร์ฟเวอร์", 
                description=text, 
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f"ล็อกอินเรียบร้อย! บอท {bot.user.name} พร้อมใช้งานแล้ว!")
    bot.add_view(VerifyView())
    await start_dummy_web_server()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    clean_content = message.content.strip()

    # เช็คเงื่อนไขระบบตอบออโต้ AI
    if not config["ai_enabled"]:
        return

    if config["ai_channel_id"] and message.channel.id != config["ai_channel_id"]:
        return

    if clean_content.startswith("/") or clean_content.startswith("!"):
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
