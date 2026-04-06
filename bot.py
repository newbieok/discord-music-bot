import discord
from discord import Option
import yt_dlp
import asyncio
import random

# ---------- Bot setup ----------
intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

# ---------- Global state ----------
queues = {}      # {guild_id: [(url, title, link, thumbnail), ...]}
playing = {}     # {guild_id: bool}
loop_mode = {}   # {guild_id: "none"/"song"/"queue"}

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

def get_loop_mode(guild_id):
    return loop_mode.get(guild_id, "none")

def set_loop_mode(guild_id, mode):
    loop_mode[guild_id] = mode

# ---------- YouTube fetch ----------
async def fetch_youtube(query):
    ydl_opts = {'format': 'bestaudio', 'noplaylist': True}
    loop = asyncio.get_event_loop()
    func = lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(f"ytsearch:{query}", download=False)['entries'][0]
    info = await loop.run_in_executor(None, func)
    url = info['url']
    title = info['title']
    link = info['webpage_url']
    video_id = info.get('id')
    thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else None
    return url, title, link, thumbnail

# ---------- Play loop ----------
async def play_loop(vc, guild_id, channel):
    ffmpeg_path = r"D:\ffmpeg\bin\ffmpeg.exe"

    try:
        while get_queue(guild_id):
            queue = get_queue(guild_id)
            url, title, link, thumbnail = queue[0]

            if not vc or not vc.is_connected():
                vc = await channel.connect()

            # Tạo AudioSource đúng cách
            source = discord.FFmpegOpusAudio(
                url,
                executable=ffmpeg_path,
                before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                options='-vn'
            )

            done = asyncio.Event()
            def after_playing(error):
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(done.set)

            vc.play(source, after=after_playing)

            # Embed nhạc
            embed = discord.Embed(
                title="🎶 Đang phát nhạc",
                description=f"[{title}]({link})",
                color=0x1DB954
            )
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
            embed.set_footer(text=f"Queue: {len(queue)} bài | Loop mode: {get_loop_mode(guild_id)}")
            await channel.send(embed=embed)

            await done.wait()

            mode = get_loop_mode(guild_id)
            if mode == "song":
                continue
            elif mode == "queue":
                queue.append(queue.pop(0))
            else:
                queue.pop(0)
    finally:
        playing[guild_id] = False

# ---------- Slash commands ----------
@bot.slash_command(name="join", description="Bot vào voice channel")
async def join(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)

    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc:
        await vc.move_to(ctx.author.voice.channel)
    else:
        vc = await ctx.author.voice.channel.connect()

    await ctx.followup.send(f"✅ Bot đã vào {ctx.author.voice.channel.name}")

@bot.slash_command(name="play", description="Phát nhạc YouTube")
async def play(ctx, query: Option(str, "Tên bài hoặc link YouTube")):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)

    await ctx.defer()
    channel = ctx.author.voice.channel
    vc = ctx.guild.voice_client
    if not vc:
        vc = await channel.connect()
    
    queue = get_queue(ctx.guild.id)
    try:
        url, title, link, thumbnail = await fetch_youtube(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi khi lấy nhạc: {e}")

    queue.append((url, title, link, thumbnail))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue | Tổng queue: {len(queue)} bài")

    # Nếu bot không đang chơi bài nào → start loop
    if not playing.get(ctx.guild.id, False) or (vc and not vc.is_playing() and not vc.is_paused()):
        playing[ctx.guild.id] = True
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))
    elif vc and vc.is_paused():
        vc.resume()  # resume nếu đang pause

@bot.slash_command(name="leave", description="Rời voice")
async def leave(ctx):
    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
        await ctx.followup.send("👋 Đã rời voice")
    else:
        await ctx.followup.send("❌ Bot chưa vào voice")

@bot.slash_command(name="skip", description="Bỏ qua bài")
async def skip(ctx):
    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.followup.send("⏭️ Đã skip")
    else:
        await ctx.followup.send("❌ Không có bài đang phát")

@bot.slash_command(name="pause", description="Tạm dừng bài đang phát")
async def pause(ctx):
    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.followup.send("⏸️ Đã pause")
    else:
        await ctx.followup.send("❌ Không có bài đang phát")

@bot.slash_command(name="resume", description="Tiếp tục bài đang pause")
async def resume(ctx):
    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.followup.send("▶️ Đã resume")
    else:
        await ctx.followup.send("❌ Không có bài đang pause")

@bot.slash_command(name="stop", description="Dừng nhạc và xoá queue")
async def stop(ctx):
    await ctx.defer()
    queue = get_queue(ctx.guild.id)
    queue.clear()
    vc = ctx.guild.voice_client
    if vc:
        vc.stop()
    await ctx.followup.send("⏹️ Đã dừng tất cả nhạc và xoá queue")

@bot.slash_command(name="queue", description="Xem danh sách nhạc")
async def queue_cmd(ctx):
    await ctx.defer()
    queue = get_queue(ctx.guild.id)
    if not queue:
        await ctx.followup.send("📭 Queue trống")
    else:
        msg = "\n".join([f"{i+1}. [{t[1]}]({t[2]})" for i, t in enumerate(queue)])
        await ctx.followup.send(f"📜 Queue:\n{msg}")

@bot.slash_command(name="loop", description="Loop bài hiện tại hoặc queue")
async def loop_cmd(ctx, mode: Option(str, "none / song / queue")):
    await ctx.defer()
    mode = mode.lower()
    if mode not in ["none", "song", "queue"]:
        return await ctx.followup.send("❌ Chọn: none / song / queue")
    set_loop_mode(ctx.guild.id, mode)
    await ctx.followup.send(f"🔁 Loop mode set: {mode}")

@bot.slash_command(name="shuffle", description="Xáo trộn queue")
async def shuffle_cmd(ctx):
    await ctx.defer()
    queue = get_queue(ctx.guild.id)
    if not queue:
        await ctx.followup.send("❌ Queue trống")
    else:
        random.shuffle(queue)
        await ctx.followup.send("🔀 Queue đã được shuffle")

@bot.slash_command(name="ping", description="Test bot")
async def ping(ctx):
    await ctx.respond("🏓 Pong")

# ---------- Bot events ----------
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    print("🎵 Slash commands hoạt động bình thường")

# ---------- Run bot ----------
bot.run(os.getenv("TOKEN"))
