@bot.tree.command(name="play", description="เปิดเพลงจาก YouTube หรือ Spotify")
@app_commands.describe(search="ชื่อเพลง, ลิงก์ YouTube หรือ ลิงก์ Spotify")
async def slash_play(interaction: discord.Interaction, search: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("ดีนต้องเข้าห้องเสียงก่อนสั่งเปิดเพลงนะ!", ephemeral=True)
        return

    # ตรวจสอบการเชื่อมต่อห้องเสียง
    if interaction.guild.voice_client is None:
        try:
            await interaction.user.voice.channel.connect()
        except discord.errors.ClientException:
            pass
        except Exception as e:
            await interaction.followup.send(f"ไม่สามารถเข้าห้องเสียงได้: {e}")
            return

    player = get_player(interaction)
    query = search.strip()
    search_queries = []

    if "open.spotify.com" in query:
        if not sp:
            await interaction.followup.send("ยังไม่ได้ตั้งค่า SPOTIPY_CLIENT_ID และ SPOTIPY_CLIENT_SECRET ครับ!")
            return
        
        try:
            search_queries = await asyncio.wait_for(
                bot.loop.run_in_executor(None, get_spotify_tracks, query),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("ดึงข้อมูล Spotify นานเกินไป กรุณาลองใหม่อีกครั้งครับ")
            return
    else:
        search_queries.append(query)

    if not search_queries:
        await interaction.followup.send("ไม่พบรายการเพลงที่ต้องการค้นหาครับ!")
        return

    added_count = 0
    first_title = ""

    for item_query in search_queries:
        try:
            info = await asyncio.wait_for(
                bot.loop.run_in_executor(None, extract_yt_info, item_query),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            continue

        if not info:
            continue

        video_data = None
        if 'entries' in info and info['entries']:
            valid = [e for e in info['entries'] if e]
            if valid:
                video_data = valid[0]
        else:
            video_data = info

        if video_data:
            stream_url = video_data.get('url') or f"https://www.youtube.com/watch?v={video_data.get('id')}"
            track_title = video_data.get('title', 'Unknown Title')
            
            await player.queue.put({'url': stream_url, 'title': track_title})
            added_count += 1
            if not first_title:
                first_title = track_title

    if added_count == 1:
        await interaction.followup.send(f"เพิ่มเพลง **{first_title}** เข้าคิวเรียบร้อยครับ!")
    elif added_count > 1:
        await interaction.followup.send(f"เพิ่มเพลงเข้าคิวทั้งหมด **{added_count}** เพลงเรียบร้อยครับ!")
    else:
        await interaction.followup.send("ไม่สามารถดึงข้อมูลเพลงจาก YouTube ได้ครับ")

    if player.current:
        await player.update_panel()
