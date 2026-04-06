import discord
from discord.ui import View, Button
import yt_dlp
import asyncio
import tempfile
import os
from datetime import timedelta

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

# ---------- GLOBAL STATE ----------
queues = {}        # {guild_id: [(file_path, title, link, duration)]}
loop_mode = {}     # {guild_id: "none"/"song"/"queue"}
update_embeds = {} # {guild_id: message embed}

def get_queue(gid): return queues.setdefault(gid, [])
def get_loop_mode(gid): return loop_mode.get(gid, "none")
def set_loop_mode(gid, mode): loop_mode[gid] = mode
def format_duration(sec): return str(timedelta(seconds=int(sec)))

# ---------- FETCH AUDIO ----------
async def fetch_tempfile(query):
    ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True}
    loop = asyncio.get_event_loop()
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    def run():
        ydl_opts['outtmpl'] = temp_file.name
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            if 'entries' in info: info = info['entries'][0]
            ydl.download([info['webpage_url']])
            return info
    info = await loop.run_in_executor(None, run)
    return temp_file.name, info['title'], info['webpage_url'], info.get('duration', 0)

# ---------- EMBED & BUTTONS ----------
def make_embed(title, link, elapsed, duration, mode):
    embed = discord.Embed(title="🎵 Now Playing", description=f"[{title}]({link})", color=0x1DB954)
    loop_emoji = {"none":"❌","song":"🔂","queue":"🔁"}.get(mode,"❌")
    embed.set_footer(text=f"Loop: {loop_emoji}")
    bar_len = 25
    if duration == 0:
        embed.add_field(name="⏱", value="LIVE STREAM", inline=True)
        embed.add_field(name="Progress", value="🔴 LIVE", inline=False)
    else:
        if elapsed>duration: elapsed=duration
        filled = int(bar_len*elapsed/max(duration,1))
        bar = "▬"*filled + "🔘" + "▬"*(bar_len-filled)
        embed.add_field(name="⏱", value=f"{format_duration(elapsed)} / {format_duration(duration)}", inline=True)
        embed.add_field(name="Progress", value=bar, inline=False)
    return embed

class MusicControlView(View):
    def __init__(self, gid):
        super().__init__(timeout=None)
        self.gid = gid

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.green)
    async def skip(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing(): vc.stop()
        await interaction.response.send_message("⏭️ Skip", ephemeral=True)

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.blurple)
    async def pause(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing(): vc.pause()
        await interaction.response.send_message("⏸️ Pause", ephemeral=True)

    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.green)
    async def resume(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused(): vc.resume()
        await interaction.response.send_message("▶️ Resume", ephemeral=True)

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.gray)
    async def loop(self, button, interaction):
        mode = get_loop_mode(self.gid)
        new_mode = {"none":"song","song":"queue","queue":"none"}[mode]
        set_loop_mode(self.gid, new_mode)
        await interaction.response.send_message(f"🔁 Loop mode: {new_mode}", ephemeral=True)

# ---------- PROGRESS UPDATER ----------
async def update_progress(gid, duration, start):
    msg = update_embeds.get(gid)
    if not msg: return
    while True:
        vc = msg.guild.voice_client
        if not vc or not vc.is_playing(): break
        elapsed = int(asyncio.get_event_loop().time() - start)
        mode = get_loop_mode(msg.guild.id)
        embed = make_embed(msg.embeds[0].description[1:], msg.embeds[0].description, elapsed, duration, mode)
        try: await msg.edit(embed=embed)
        except: break
        await asyncio.sleep(1)

# ---------- PLAY LOOP ----------
async def play_loop(vc, gid, channel):
    queue = get_queue(gid)
    while queue:
        file_path, title, link, duration = queue[0]
        done = asyncio.Event()
        def after_play(error):
            try: os.remove(file_path)
            except: pass
            vc.loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegOpusAudio(file_path)
        vc.play(source, after=after_play)

        view = MusicControlView(gid)
        start = asyncio.get_event_loop().time()
        msg = await channel.send(embed=make_embed(title, link, 0, duration, get_loop_mode(gid)), view=view)
        update_embeds[gid] = msg
        progress_task = asyncio.create_task(update_progress(gid, duration, start))

        await done.wait()
        progress_task.cancel()

        mode = get_loop_mode(gid)
        if mode == "song": continue
        elif mode == "queue": queue.append(queue.pop(0))
        else: queue.pop(0)
        update_embeds.pop(gid, None)

# ---------- /PLAY COMMAND ----------
@bot.slash_command(name="play", description="Phát nhạc YouTube")
async def play(ctx, *, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Vào voice trước", ephemeral=True)

    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc: vc = await ctx.author.voice.channel.connect()

    try:
        file_path, title, link, duration = await fetch_tempfile(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi: {e}")

    queue = get_queue(ctx.guild.id)
    queue.append((file_path, title, link, duration))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue | Queue: {len(queue)} bài")

    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))
    elif vc.is_paused():
        vc.resume()

# ---------- /JOIN ----------
@bot.slash_command(name="join", description="Vào voice")
async def join(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Vào voice trước", ephemeral=True)
    vc = ctx.guild.voice_client
    if vc: await vc.move_to(ctx.author.voice.channel)
    else: vc = await ctx.author.voice.channel.connect()
    await ctx.respond(f"✅ Bot vào {ctx.author.voice.channel.name}")

# ---------- BOT READY ----------
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

bot.run(os.getenv("TOKEN"))
