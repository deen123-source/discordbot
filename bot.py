import os
import discord
from discord.ext import commands
from openai import OpenAI

OPENROUTER_API_KEY = "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"
DISCORD_BOT_TOKEN = "MTU0MTQ1Mzg5NzE2NjYxODgwNg.GRqDSv.SLWPHLCNOFPUVFQnFiqj7DyGdu3VFlCC-SFWWk"
MODEL_NAME = "stealth/ox-alpha"

ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"ล็อกอินเรียบร้อย! เชื่อมต่อบอท {bot.user.name} ด้วยโมเดล {MODEL_NAME}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        clean_content = message.content.replace(f"<@{bot.user.id}>", "").strip()

        if not clean_content:
            await message.channel.send("มีอะไรให้ Ox Alpha ช่วยวิเคราะห์ไหมครับ ดีน?")
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
                await message.channel.send(f"เกิดข้อผิดพลาดในการประมวลผล: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)