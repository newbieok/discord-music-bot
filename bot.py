import discord
from discord import Option
import yt_dlp
import asyncio
import random
import os
import tempfile
import re

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

queues = {}      # {guild_id: [(file_path, title, link), ...]}
loop_mode = {}   # {guild_id: "none"/"song"/"queue"}

SHORTS_REGEX = re.compile(r'(https?://)?(www\.)?youtube\.com/shorts/(\w+)')

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

def get_loop_mode(guild_id):
    return loop_mode.get(guild_id, "none")

def set_loop_mode(guild_id, mode):
    loop_mode[guild_id] = mode

# ---------- Fetch + download audio tạm ----------
async def fetch_youtube_tempfile(query):
    # Chuyển Shorts sang link chuẩn
    match = SHORTS_REGEX.match(query)
    if match:
        video_id = match.group(3)
        query = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        'format': 'bestaudio/best',  # tự chọn audio tốt nhất
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'http_headers': {'User-Agent': 'Mozilla/5.0'},
    }

    loop = asyncio.get_event_loop()
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)

    def run():
        ydl_opts['outtmpl'] = temp_file.name
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]

            # Kiểm tra video có audio không
            formats = info.get('formats', [])
            audio_formats = [f for f in formats if f['acodec'] != 'none']
            if not audio_formats:
                raise ValueError("Video này không có audio hợp lệ!")

            # Download audio tốt nhất
            ydl.download([info['webpage_url']])
            return info

    info = await loop.run_in_executor(None, run)
    return temp_file.name, info['title'], info['webpage_url']

# ---------- Play loop ----------
async def play_loop(vc, guild_id, channel):
    queue = get_queue(guild_id)
    while queue:
        file_path, title, link = queue[0]

        done = asyncio.Event()

        def after_playing(error):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
            vc.loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegOpusAudio(file_path)
        vc.play(source, after=after_playing)
        asyncio.create_task(channel.send(f"🎶 Đang phát: [{title}]({link})"))

        await done.wait()

        mode = get_loop_mode(guild_id)
        if mode == "song":
            continue
        elif mode == "queue":
            queue.append(queue.pop(0))
        else:
            queue.pop(0)

# ---------- Slash commands ----------
@bot.slash_command(name="play", description="Phát nhạc")
async def play(ctx, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()

    queue = get_queue(ctx.guild.id)
    try:
        file_path, title, link = await fetch_youtube_tempfile(query)
    except ValueError as e:
        return await ctx.followup.send(f"❌ {e}")
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi tải nhạc: {e}")

    if any(link == t[2] for t in queue):
        if os.path.exists(file_path):
            os.remove(file_path)
        return await ctx.followup.send("⚠️ Bài này đã có trong queue")

    queue.append((file_path, title, link))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue")

    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))

# ---------- Các command khác giữ nguyên ----------
@bot.slash_command(name="pause", description="Tạm dừng bài đang phát")
async def pause(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.respond("⏸️ Đã pause")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="resume", description="Tiếp tục bài đang pause")
async def resume(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.respond("▶️ Đã resume")
    else:
        await ctx.respond("❌ Không có bài đang pause")

@bot.slash_command(name="stop", description="Dừng nhạc và xoá queue")
async def stop(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    for f, _, _ in queue:
        try:
            if os.path.exists(f):
                os.remove(f)
        except:
            pass
    queue.clear()
    if vc:
        vc.stop()
    await ctx.respond("⏹️ Đã dừng tất cả nhạc và xoá queue")

# ... giữ nguyên các command skip, queue, shuffle, loop, join, leave, ping

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

bot.run(os.getenv("TOKEN"))
