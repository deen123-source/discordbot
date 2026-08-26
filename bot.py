import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
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
# 1. ตั้งค่า Discord Token & Bot Client
# ==========================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "ใส่_DISCORD_BOT_TOKEN_ของคุณที่นี่")

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("Sync Slash Commands เรียบร้อยแล้ว!")

bot = MyBot()

# ==========================================
# 2. ตัวแปรตั้งค่าระบบ (Global Storage)
# ==========================================
config = {
    "welcome_channel_id": None,
    "goodbye_channel_id": None,
    "welcome_message": "ยินดีต้อนรับ {member} เข้าสู่เซิร์ฟเวอร์!",
    "goodbye_message": "คุณ {member} ได้ออกจากเซิร์ฟเวอร์ไปแล้ว...",
    "welcome_image_url": "",
    "verify_title": "📌 ยืนยันตัวตน",
    "verify_desc": "ยินดีต้อนรับเข้าสู่เซิร์ฟเวอร์! กรุณากดปุ่มด้านล่างเพื่อทำการยืนยันตัวตนและรับยศครับ",
    "verify_role_id": None,
    "verify_log_channel_id": None
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
# 3. ระบบเล่นเพลง และ Control Panel
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
# 4. ระบบยืนยันตัวตนแบบกรอกฟอร์ม (Form Modal)
# ==========================================
class AdvancedVerifyModal(discord.ui.Modal, title="ยืนยันตัวตน"):
    nickname = discord.ui.TextInput(
        label="อยากแรกเลยนะ ชื่อเล่นชื่ออะไรหรออออ",
        placeholder="กรอกชื่อเล่นตรงนี้นะ",
        required=True,
        max_length=50
    )
    age = discord.ui.TextInput(
        label="อายุเท่าไหร่ยยย",
        placeholder="ไม่อยากบอกก็ได้นะ :(",
        required=False,
        max_length=10
    )
    source = discord.ui.TextInput(
        label="ได้ดิสจากไหนหรอจ้ะ",
        placeholder="บอกหน่อยน้า",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        role_id = config["verify_role_id"]
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                await interaction.user.add_roles(role)

        log_ch_id = config["verify_log_channel_id"]
        if log_ch_id:
            log_channel = interaction.guild.get_channel(log_ch_id)
            if log_channel:
                embed = discord.Embed(
                    description=f"{interaction.user.mention} ☑️ **ได้รับยศเรียบร้อยแล้ว**",
                    color=discord.Color.from_rgb(47, 49, 54)
                )
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.add_field(
                    name="ℹ️ อยากแรกเลยนะ ชื่อเล่นชื่ออะไรหรออออ:",
                    value=f"└ {self.nickname.value}",
                    inline=False
                )
                embed.add_field(
                    name="ℹ️ อายุเท่าไหร่ยยย:",
                    value=f"└ {self.age.value if self.age.value else 'ไม่ระบุ'}",
                    inline=False
                )
                embed.add_field(
                    name="ℹ️ ได้ดิสจากไหนหรอจ้ะ:",
                    value=f"└ {self.source.value}",
                    inline=False
                )
                embed.set_footer(text=f"ID: {interaction.user.id}")
                await log_channel.send(content=f"{interaction.user.mention}", embed=embed)

        await interaction.response.send_message("ยืนยันตัวตนเรียบร้อยแล้วครับ!", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ยืนยันตัวตน", style=discord.ButtonStyle.success, emoji="📌", custom_id="verify_button_custom_v2")
    async def verify_button_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdvancedVerifyModal())

# ==========================================
# 5. CONTROL PANEL ระบบตั้งค่าบอทผ่าน UI
# ==========================================
class EditVerifyEmbedModal(discord.ui.Modal, title="แต่งข้อความ Embed ยืนยันตัวตน"):
    title_input = discord.ui.TextInput(label="หัวข้อ (Title)", default=config["verify_title"], required=True)
    desc_input = discord.ui.TextInput(label="รายละเอียด (Description)", default=config["verify_desc"], style=discord.TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        config["verify_title"] = self.title_input.value
        config["verify_desc"] = self.desc_input.value
        await interaction.response.send_message("อัปเดตข้อความ Embed ยืนยันตัวตนสำเร็จ!", ephemeral=True)

class EditWelcomeModal(discord.ui.Modal, title="ตั้งค่าข้อความ คนเข้า/ออก"):
    welcome_msg = discord.ui.TextInput(label="ข้อความคนเข้า (ใช้ {member} แทนชื่อ)", default=config["welcome_message"], style=discord.TextStyle.paragraph)
    goodbye_msg = discord.ui.TextInput(label="ข้อความคนออก (ใช้ {member} แทนชื่อ)", default=config["goodbye_message"], style=discord.TextStyle.paragraph)
    img_url = discord.ui.TextInput(label="URL รูปภาพวอลเปเปอร์", default=config["welcome_image_url"], required=False)

    async def on_submit(self, interaction: discord.Interaction):
        config["welcome_message"] = self.welcome_msg.value
        config["goodbye_message"] = self.goodbye_msg.value
        config["welcome_image_url"] = self.img_url.value
        await interaction.response.send_message("อัปเดตข้อความการแจ้งเตือนคนเข้า-ออกเรียบร้อย!", ephemeral=True)

class MasterControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="เลือกยศที่จะให้เมื่อยืนยันตัวตน...",
        cls=discord.ui.RoleSelect,
        custom_id="select_verify_role"
    )
    async def select_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        config["verify_role_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่ายศยืนยันตัวตนเป็น **{select.values[0].name}** เรียบร้อย!", ephemeral=True)

    @discord.ui.select(
        placeholder="เลือกห้องส่ง Log ยืนยันตัวตน...",
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        custom_id="select_log_channel"
    )
    async def select_log_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        config["verify_log_channel_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่าห้องส่ง Log เป็น {select.values[0].mention} เรียบร้อย!", ephemeral=True)

    @discord.ui.select(
        placeholder="เลือกห้องแจ้งเตือน คนเข้า...",
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        custom_id="select_welcome_channel"
    )
    async def select_welcome_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        config["welcome_channel_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่าห้องแจ้งคนเข้าเป็น {select.values[0].mention} เรียบร้อย!", ephemeral=True)

    @discord.ui.select(
        placeholder="เลือกห้องแจ้งเตือน คนออก...",
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        custom_id="select_goodbye_channel"
    )
    async def select_goodbye_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        config["goodbye_channel_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่าห้องแจ้งคนออกเป็น {select.values[0].mention} เรียบร้อย!", ephemeral=True)

    @discord.ui.button(label="แต่งข้อความปุ่มยืนยัน", style=discord.ButtonStyle.primary, emoji="✏️", row=4)
    async def edit_verify_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditVerifyEmbedModal())

    @discord.ui.button(label="แต่งข้อความต้อนรับ/คนออก", style=discord.ButtonStyle.primary, emoji="🖼️", row=4)
    async def edit_welcome_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditWelcomeModal())

    @discord.ui.button(label="🚀 ส่งปุ่มยืนยันตัวตนลงห้องนี้", style=discord.ButtonStyle.success, emoji="✅", row=4)
    async def spawn_verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title=config["verify_title"],
            description=config["verify_desc"],
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed, view=VerifyView())
        await interaction.response.send_message("ส่งปุ่มยืนยันตัวตนเรียบร้อยแล้ว!", ephemeral=True)

# ==========================================
# 6. Slash Commands
# ==========================================
@bot.tree.command(name="setup_panel", description="เปิดแผงควบคุมตั้งค่าระบบบอท (Control Panel)")
async def slash_setup_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚙️ Master Control Panel - แผงควบคุมบอท",
        description="ใช้เมนูด้านล่างนี้เพื่อตั้งค่าห้อง ยศ และปรับแต่งข้อความต่าง ๆ ได้เลยครับ",
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed, view=MasterControlPanel(), ephemeral=True)

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

# ==========================================
# 7. Event Listeners
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
            avatar_url = member.display_avatar.url
            embed.set_thumbnail(url=avatar_url)
            
            if config["welcome_image_url"]:
                embed.set_image(url=config["welcome_image_url"])
            else:
                embed.set_image(url=avatar_url)

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

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
