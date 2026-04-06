import discord
from discord.ui import View
from discord import ButtonStyle
import yt_dlp
import asyncio
import tempfile
import os
from datetime import timedelta
import random

TOKEN = os.getenv("TOKEN")
TEMP_DIR = "/tmp/music"

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

queues = {}        # guild_id -> [(file_path,title,link,duration)]
loop_mode = {}     # guild_id -> "none"/"song"/"queue"
update_embeds = {} # guild_id -> embed message

def get_queue(gid): return queues.setdefault(gid, [])
def get_loop_mode(gid): return loop_mode.get(gid, "none")
def set_loop_mode(gid, mode): loop_mode[gid]=mode
def format_duration(sec): return str(timedelta(seconds=int(sec)))

# ---------- fetch + convert to opus ----------
async def fetch_tempfile(query):
    loop = asyncio.get_event_loop()
    temp_file = tempfile.NamedTemporaryFile(suffix=".opus", dir=TEMP_DIR, delete=False)
    cookies_path = "cookies.txt" if os.path.exists("cookies.txt") else None

    def run():
        ydl_opts = {
            'format':'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'outtmpl': temp_file.name + ".%(ext)s",
            'default_search':'ytsearch',
        }
        if cookies_path: ydl_opts['cookiefile'] = cookies_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info: info=info['entries'][0]
            ydl.download([info['webpage_url']])
            # Convert to opus
            file_path = temp_file.name
            os.system(f'ffmpeg -i "{temp_file.name}.{info["ext"]}" -c:a libopus -b:a 128k -y "{file_path}"')
            try: os.remove(f'{temp_file.name}.{info["ext"]}')
            except: pass
            return file_path, info

    file_path, info = await loop.run_in_executor(None, run)
    return file_path, info['title'], info['webpage_url'], info.get('duration',0)

# ---------- Embed + Buttons ----------
def make_embed(title, link, elapsed, duration, mode):
    embed = discord.Embed(title="🎵 Now Playing", description=f"[{title}]({link})", color=0x1DB954)
    loop_emoji = {"none":"❌","song":"🔂","queue":"🔁"}.get(mode,"❌")
    embed.set_footer(text=f"Loop: {loop_emoji}")
    bar_len = 25
    if duration==0:
        embed.add_field(name="⏱", value="LIVE STREAM", inline=True)
        embed.add_field(name="Progress", value="🔴 LIVE", inline=False)
    else:
        if elapsed>duration: elapsed=duration
        filled=int(bar_len*elapsed/max(duration,1))
        bar="▬"*filled+"🔘"+"▬"*(bar_len-filled)
        embed.add_field(name="⏱", value=f"{format_duration(elapsed)} / {format_duration(duration)}", inline=True)
        embed.add_field(name="Progress", value=bar, inline=False)
    return embed

class MusicControlView(View):
    def __init__(self, gid):
        super().__init__(timeout=None)
        self.gid = gid

    @discord.ui.button(label="⏭️ Skip", style=ButtonStyle.green)
    async def skip(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing(): vc.stop()
        await interaction.response.send_message("⏭️ Skip", ephemeral=True)

    @discord.ui.button(label="⏸️ Pause", style=ButtonStyle.blurple)
    async def pause(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing(): vc.pause()
        await interaction.response.send_message("⏸️ Pause", ephemeral=True)

    @discord.ui.button(label="▶️ Resume", style=ButtonStyle.green)
    async def resume(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused(): vc.resume()
        await interaction.response.send_message("▶️ Resume", ephemeral=True)
        # Gợi ý bài tiếp theo
        queue = get_queue(interaction.guild.id)
        if len(queue)>1:
            next_title = queue[1][1]
            await interaction.followup.send(f"🎶 Bài tiếp theo: {next_title}", ephemeral=True)

    @discord.ui.button(label="🔁 Loop", style=ButtonStyle.gray)
    async def loop(self, button, interaction):
        mode = get_loop_mode(self.gid)
        new_mode = {"none":"song","song":"queue","queue":"none"}[mode]
        set_loop_mode(self.gid, new_mode)
        await interaction.response.send_message(f"🔁 Loop mode: {new_mode}", ephemeral=True)

# ---------- Progress updater ----------
async def update_progress(gid,duration,start):
    msg = update_embeds.get(gid)
    if not msg: return
    while True:
        vc = msg.guild.voice_client
        if not vc or not vc.is_playing(): break
        elapsed = int(asyncio.get_event_loop().time()-start)
        embed = make_embed(msg.embeds[0].title, msg.embeds[0].description, elapsed, duration, get_loop_mode(msg.guild.id))
        try: await msg.edit(embed=embed)
        except: break
        await asyncio.sleep(1)

# ---------- Play loop ----------
async def play_loop(vc,gid,channel):
    queue = get_queue(gid)
    while queue:
        file_path, title, link, duration = queue[0]
        done = asyncio.Event()
        def after_play(e):
            try: os.remove(file_path)
            except: pass
            vc.loop.call_soon_threadsafe(done.set)
        vc.play(discord.FFmpegOpusAudio(file_path), after=after_play)
        view = MusicControlView(gid)
        start = asyncio.get_event_loop().time()
        msg = await channel.send(embed=make_embed(title, link, 0, duration, get_loop_mode(gid)), view=view)
        update_embeds[gid] = msg
        task = asyncio.create_task(update_progress(gid, duration, start))
        await done.wait()
        task.cancel()
        mode = get_loop_mode(gid)
        if mode=="song": continue
        elif mode=="queue": queue.append(queue.pop(0))
        else: queue.pop(0)
        update_embeds.pop(gid,None)

# ---------- COMMANDS ----------
@bot.slash_command(name="play", description="Phát nhạc")
async def play(ctx, *, query:str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc: vc = await ctx.author.voice.channel.connect()
    try:
        file_path,title,link,duration = await fetch_tempfile(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi: {e}")
    queue = get_queue(ctx.guild.id)
    queue.append((file_path,title,link,duration))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue | Queue: {len(queue)} bài")
    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))
    elif vc.is_paused():
        vc.resume()

# Các command khác: pause, resume, skip, stop, queue, shuffle, loop, join, leave giữ nguyên logic cũ
# ...

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

bot.run(TOKEN)
