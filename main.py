import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user}')

@bot.command(name='ping')
async def ping(ctx):
    await ctx.send('Pong! 🏓')

@bot.command(name='hello')
async def hello(ctx):
    await ctx.send(f'Olá, {ctx.author.mention}! Tudo bem?')

@bot.command(name='soma')
async def soma(ctx, a: int, b: int):
    resultado = a + b
    await ctx.send(f'O resultado de {a} + {b} é {resultado}')

if __name__ == '__main__':
# Discord token removed; configure via DISCORD_TOKEN environment variable
    bot.run(TOKEN)
