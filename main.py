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

@bot.command(name='say')
async def say(ctx, *, message):
    await ctx.send(message)

@bot.command(name='calc', aliases=['soma'])
async def calc(ctx, a: float, operador: str, b: float):
    operador = operador.lower()
    if operador in ['+', 'add', 'soma']:
        resultado = a + b
        simbolo = '+'
    elif operador in ['-', 'sub', 'menos']:
        resultado = a - b
        simbolo = '-'
    elif operador in ['*', 'x', 'mul', 'vezes']:
        resultado = a * b
        simbolo = '×'
    elif operador in ['/', 'div', 'divide']:
        if b == 0:
            await ctx.send('Não é possível dividir por zero. 😕')
            return
        resultado = a / b
        simbolo = '÷'
    else:
        await ctx.send('Use um operador válido: +, -, *, /')
        return

    await ctx.send(f'O resultado de {a} {simbolo} {b} é {resultado}')

if __name__ == '__main__':
# Discord token removed; configure via DISCORD_TOKEN environment variable
    bot.run(TOKEN)
