import os
import sqlite3
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

DB = "biblioteca.db"
GUTENDEX = "https://gutendex.com/books"

if not TOKEN:
    raise RuntimeError("Defina DISCORD_TOKEN no arquivo .env")


def db():
    return sqlite3.connect(DB)


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS favoritos (
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                PRIMARY KEY (user_id, book_id)
            )
        """)


def clean_text(value, limit=900):
    if not value:
        return ""
    value = " ".join(str(value).split())
    return value[:limit] + ("..." if len(value) > limit else "")


def choose_format(formats, kind):
    candidates = {
        "pdf": ["application/pdf"],
        "epub": ["application/epub+zip", "application/epub"],
    }
    for mime in candidates[kind]:
        if mime in formats:
            return formats[mime]

    for mime, url in formats.items():
        base = mime.split(";")[0].lower()
        if kind == "pdf" and base == "application/pdf":
            return url
        if kind == "epub" and base == "application/epub+zip":
            return url
    return None


def book_embed(book):
    authors = ", ".join(a["name"] for a in book.get("authors", [])) or "Autor desconhecido"
    languages = ", ".join(book.get("languages", [])) or "?"
    description = clean_text(
        (book.get("summaries") or ["Sem descrição disponível."])[0], 700
    )

    embed = discord.Embed(
        title=book.get("title", "Livro"),
        description=description,
        url=f"https://www.gutenberg.org/ebooks/{book['id']}",
    )
    embed.add_field(name="Autor", value=authors, inline=False)
    embed.add_field(name="Idioma", value=languages, inline=True)
    embed.add_field(
        name="Downloads",
        value=str(book.get("download_count", 0)),
        inline=True,
    )
    embed.set_footer(text=f"Project Gutenberg • ID {book['id']}")

    formats = book.get("formats", {})
    pdf = choose_format(formats, "pdf")
    epub = choose_format(formats, "epub")

    return embed, pdf, epub


async def fetch_json(session, url):
    async with session.get(
        url,
        timeout=aiohttp.ClientTimeout(total=15),
        headers={"User-Agent": "DiscordBiblioteca/1.0"},
    ) as response:
        response.raise_for_status()
        return await response.json()


class DownloadView(discord.ui.View):
    def __init__(self, book, pdf, epub):
        super().__init__(timeout=300)
        self.book = book

        if pdf:
            self.add_item(discord.ui.Button(
                label="PDF",
                emoji="📕",
                style=discord.ButtonStyle.link,
                url=pdf
            ))

        if epub:
            self.add_item(discord.ui.Button(
                label="EPUB",
                emoji="📱",
                style=discord.ButtonStyle.link,
                url=epub
            ))

        self.add_item(discord.ui.Button(
            label="Gutenberg",
            emoji="🌐",
            style=discord.ButtonStyle.link,
            url=f"https://www.gutenberg.org/ebooks/{book['id']}"
        ))


class Biblioteca(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def search_books(self, query, page=1):
        url = f"{GUTENDEX}?search={quote(query)}&page={page}"
        async with aiohttp.ClientSession() as session:
            return await fetch_json(session, url)

    @app_commands.command(
        name="buscar",
        description="Procura livros gratuitos no catálogo do Project Gutenberg."
    )
    @app_commands.describe(
        busca="Título ou autor",
        pagina="Página de resultados (1 a 5)"
    )
    async def buscar(
        self,
        interaction: discord.Interaction,
        busca: str,
        pagina: app_commands.Range[int, 1, 5] = 1
    ):
        await interaction.response.defer()

        try:
            data = await self.search_books(busca, pagina)
        except Exception:
            await interaction.followup.send(
                "Não consegui consultar o catálogo agora. Tente novamente em alguns segundos."
            )
            return

        results = data.get("results", [])
        if not results:
            await interaction.followup.send(
                f"Nenhum livro encontrado para **{busca}**."
            )
            return

        embed = discord.Embed(
            title=f"📚 Resultados para: {busca}",
            description="Use /livro id:<ID> para abrir um resultado."
        )

        for book in results[:10]:
            authors = ", ".join(
                a["name"] for a in book.get("authors", [])
            ) or "Autor desconhecido"

            formats = book.get("formats", {})
            pdf = bool(choose_format(formats, "pdf"))
            epub = bool(choose_format(formats, "epub"))

            tipos = []
            if pdf:
                tipos.append("PDF")
            if epub:
                tipos.append("EPUB")
            if not tipos:
                tipos.append("outros formatos")

            embed.add_field(
                name=f"#{book['id']} — {book['title'][:90]}",
                value=f"{authors}\nDisponível: {', '.join(tipos)}",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="livro",
        description="Mostra um livro e seus formatos disponíveis."
    )
    @app_commands.describe(id="ID do Project Gutenberg")
    async def livro(
        self,
        interaction: discord.Interaction,
        id: app_commands.Range[int, 1, 999999999]
    ):
        await interaction.response.defer()

        async with aiohttp.ClientSession() as session:
            try:
                book = await fetch_json(session, f"{GUTENDEX}/{id}")
            except Exception:
                await interaction.followup.send(
                    "Livro não encontrado ou catálogo indisponível."
                )
                return

        embed, pdf, epub = book_embed(book)
        view = DownloadView(book, pdf, epub)

        if not pdf and not epub:
            embed.add_field(
                name="Formatos",
                value="Não há PDF/EPUB neste registro. Use o botão Gutenberg para ver os formatos disponíveis.",
                inline=False
            )

        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="popular",
        description="Mostra livros populares do catálogo gratuito."
    )
    async def popular(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            data = await self.search_books("", 1)
        except Exception:
            await interaction.followup.send("Não consegui consultar o catálogo.")
            return

        embed = discord.Embed(title="🔥 Livros populares")
        for book in data.get("results", [])[:10]:
            authors = ", ".join(a["name"] for a in book.get("authors", []))
            embed.add_field(
                name=f"#{book['id']} — {book['title'][:90]}",
                value=authors or "Autor desconhecido",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="favoritar",
        description="Salva um livro nos seus favoritos."
    )
    @app_commands.describe(id="ID do Project Gutenberg")
    async def favoritar(
        self,
        interaction: discord.Interaction,
        id: app_commands.Range[int, 1, 999999999]
    ):
        async with aiohttp.ClientSession() as session:
            try:
                book = await fetch_json(session, f"{GUTENDEX}/{id}")
            except Exception:
                await interaction.response.send_message(
                    "Livro não encontrado.", ephemeral=True
                )
                return

        with db() as con:
            con.execute("""
                INSERT OR REPLACE INTO favoritos (user_id, book_id, title)
                VALUES (?, ?, ?)
            """, (interaction.user.id, id, book["title"]))

        await interaction.response.send_message(
            f"⭐ **{book['title']}** foi adicionado aos seus favoritos.",
            ephemeral=True
        )

    @app_commands.command(
        name="favoritos",
        description="Mostra seus livros favoritos."
    )
    async def favoritos(self, interaction: discord.Interaction):
        with db() as con:
            rows = con.execute("""
                SELECT book_id, title
                FROM favoritos
                WHERE user_id = ?
                ORDER BY title
            """, (interaction.user.id,)).fetchall()

        if not rows:
            await interaction.response.send_message(
                "Você ainda não tem favoritos.", ephemeral=True
            )
            return

        embed = discord.Embed(title="⭐ Seus favoritos")
        for book_id, title in rows[:25]:
            embed.add_field(
                name=f"#{book_id} — {title}",
                value=f"/livro id:{book_id}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default()
        )

    async def setup_hook(self):
        await self.add_cog(Biblioteca(self))

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print("Comandos sincronizados no servidor.")
        else:
            await self.tree.sync()
            print("Comandos globais sincronizados.")

    async def on_ready(self):
        print(f"Conectado como {self.user}")


init_db()
bot = Bot()
bot.run(TOKEN)
