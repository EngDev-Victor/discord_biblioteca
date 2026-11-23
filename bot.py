import asyncio
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
GUTENDEX_URL = "https://gutendex.com/books"
HTTP_TIMEOUT = 30
MAX_RESULTS = 10

livros_cache = {}


def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS favoritos (
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                PRIMARY KEY (user_id, book_id)
            )
        """)
        conn.commit()


def add_favorite(user_id, book_id, title):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO favoritos (user_id, book_id, title) VALUES (?, ?, ?)",
            (user_id, book_id, title),
        )
        conn.commit()


def remove_favorite(user_id, book_id):
    with sqlite3.connect(DB) as conn:
        cursor = conn.execute(
            "DELETE FROM favoritos WHERE user_id = ? AND book_id = ?",
            (user_id, book_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_favorites(user_id):
    with sqlite3.connect(DB) as conn:
        return conn.execute(
            """
            SELECT book_id, title
            FROM favoritos
            WHERE user_id = ?
            ORDER BY title COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()


def clean_text(value, limit=1000):
    if value is None:
        return "Não informado."
    text = str(value).strip()
    if not text:
        return "Não informado."
    return text if len(text) <= limit else text[:limit - 3] + "..."


def get_authors(book):
    authors = book.get("authors") or []
    names = [
        clean_text(author.get("name"), 100)
        for author in authors
        if author.get("name")
    ]
    return ", ".join(names) if names else "Autor desconhecido"


def create_book_embed(book):
    title = clean_text(book.get("title"), 256)
    book_id = book.get("id", "N/A")
    summaries = book.get("summaries") or []
    summary = summaries[0] if summaries else "Sem descrição disponível."
    languages = book.get("languages") or []
    downloads = book.get("download_count", 0)

    embed = discord.Embed(
        title=f"📖 {title}",
        description=clean_text(summary, 1000),
        color=discord.Color.blue(),
    )

    embed.add_field(name="Autor(es)", value=get_authors(book), inline=False)
    embed.add_field(
        name="Idioma(s)",
        value=", ".join(languages) if languages else "Não informado",
        inline=True,
    )
    embed.add_field(
        name="Downloads",
        value=f"{downloads:,}",
        inline=True,
    )
    embed.add_field(
        name="ID Gutendex",
        value=str(book_id),
        inline=True,
    )

    formats = book.get("formats") or {}
    links = []

    if formats.get("application/pdf"):
        links.append(f"[PDF]({formats['application/pdf']})")
    if formats.get("application/epub+zip"):
        links.append(f"[EPUB]({formats['application/epub+zip']})")
    if formats.get("text/html"):
        links.append(f"[HTML]({formats['text/html']})")
    if formats.get("text/plain"):
        links.append(f"[TXT]({formats['text/plain']})")

    if links:
        embed.add_field(
            name="🔗 Formatos",
            value=" • ".join(links),
            inline=False,
        )

    cover = formats.get("image/jpeg")
    if cover:
        embed.set_thumbnail(url=cover)

    embed.set_footer(text="Fonte: Gutendex / Project Gutenberg")
    return embed


async def fetch_json(session, url, retries=3):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)

            print(f"[Gutendex] Tentativa {attempt}/{retries}: {url}")

            async with session.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": "DiscordBiblioteca/2.0",
                    "Accept": "application/json",
                },
            ) as response:

                print(f"[Gutendex] HTTP {response.status}")

                if response.status == 429:
                    last_error = "limite de requisições"
                    await asyncio.sleep(2 * attempt)
                    continue

                if 500 <= response.status <= 599:
                    last_error = f"servidor HTTP {response.status}"
                    await asyncio.sleep(attempt)
                    continue

                if response.status != 200:
                    return None, f"A API retornou HTTP {response.status}."

                try:
                    data = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    return None, "A API respondeu com dados inválidos."

                return data, None

        except asyncio.TimeoutError:
            last_error = "timeout"
            print("[Gutendex] Timeout.")

        except aiohttp.ClientConnectorError as error:
            last_error = "erro de conexão"
            print(f"[Gutendex] Conexão: {error!r}")

        except aiohttp.ClientError as error:
            last_error = "erro HTTP"
            print(f"[Gutendex] HTTP: {error!r}")

        except Exception as error:
            print(f"[Gutendex] Erro inesperado: {error!r}")
            return None, "Ocorreu um erro inesperado ao consultar o catálogo."

        if attempt < retries:
            await asyncio.sleep(attempt)

    if last_error == "timeout":
        return None, "O Gutendex demorou demais para responder."
    if last_error == "erro de conexão":
        return None, "Não foi possível conectar ao Gutendex."

    return None, "O Gutendex está temporariamente indisponível."


async def search_books(session, query):
    encoded = quote(query.strip())
    data, error = await fetch_json(
        session,
        f"{GUTENDEX_URL}?search={encoded}&page=1",
    )

    if error:
        return None, error

    if not isinstance(data, dict):
        return None, "Resposta inválida da API."

    results = data.get("results")
    if not isinstance(results, list):
        return None, "A API retornou resultados inválidos."

    for book in results:
        book_id = book.get("id")
        if book_id:
            livros_cache[book_id] = book

    return data, None


async def get_book(session, book_id):
    if book_id in livros_cache:
        return livros_cache[book_id], None

    data, error = await fetch_json(
        session,
        f"{GUTENDEX_URL}/{book_id}",
    )

    if error:
        return None, error

    if not isinstance(data, dict) or not data.get("id"):
        return None, "Livro não encontrado."

    livros_cache[book_id] = data
    return data, None


async def get_popular_books(session):
    data, error = await fetch_json(
        session,
        f"{GUTENDEX_URL}?sort=popular&page=1",
    )

    if error:
        return None, error

    if not isinstance(data, dict):
        return None, "Resposta inválida da API."

    results = data.get("results")
    if not isinstance(results, list):
        return None, "A API retornou resultados inválidos."

    for book in results:
        book_id = book.get("id")
        if book_id:
            livros_cache[book_id] = book

    return data, None


class BookView(discord.ui.View):
    def __init__(self, book, user_id):
        super().__init__(timeout=180)
        self.book = book
        self.book_id = book.get("id")
        self.user_id = user_id

    @discord.ui.button(
        label="Favoritar",
        emoji="⭐",
        style=discord.ButtonStyle.primary,
    )
    async def favorite(self, interaction, button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Esses botões pertencem a quem executou o comando.",
                ephemeral=True,
            )
            return

        try:
            add_favorite(
                interaction.user.id,
                self.book_id,
                clean_text(self.book.get("title"), 200),
            )
            await interaction.response.send_message(
                "⭐ Livro adicionado aos seus favoritos.",
                ephemeral=True,
            )
        except sqlite3.Error as error:
            print(f"[SQLite] {error!r}")
            await interaction.response.send_message(
                "Não foi possível salvar o favorito.",
                ephemeral=True,
            )


class Biblioteca(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="buscar",
        description="Busca livros no catálogo do Gutendex.",
    )
    @app_commands.describe(
        consulta="Título, autor ou palavra-chave.",
    )
    async def buscar(self, interaction, consulta: str):
        await interaction.response.defer()
        consulta = consulta.strip()

        if not consulta:
            await interaction.followup.send("Digite algo para pesquisar.")
            return

        if len(consulta) > 100:
            await interaction.followup.send(
                "A pesquisa pode ter no máximo 100 caracteres."
            )
            return

        data, error = await search_books(
            self.bot.http_session,
            consulta,
        )

        if error:
            await interaction.followup.send(f"❌ {error}")
            return

        results = data.get("results", [])

        if not results:
            await interaction.followup.send(
                f"Não encontrei livros para {consulta}."
            )
            return

        lines = []

        for book in results[:MAX_RESULTS]:
            lines.append(
                f"**{clean_text(book.get('title'), 150)}**\n"
                f"Autor: {get_authors(book)}\n"
                f"ID: {book.get('id', '?')}"
            )

        embed = discord.Embed(
            title=f"🔎 Resultados: {consulta}",
            description="\n\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(
            text="Use /livro id:... para abrir a ficha."
        )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="livro",
        description="Mostra os detalhes de um livro.",
    )
    @app_commands.describe(id="ID do livro no Gutendex.")
    async def livro(
        self,
        interaction,
        id: app_commands.Range[int, 1, 999999999],
    ):
        await interaction.response.defer()

        book, error = await get_book(
            self.bot.http_session,
            id,
        )

        if error:
            await interaction.followup.send(f"❌ {error}")
            return

        await interaction.followup.send(
            embed=create_book_embed(book),
            view=BookView(book, interaction.user.id),
        )

    @app_commands.command(
        name="favoritar",
        description="Adiciona um livro aos seus favoritos.",
    )
    @app_commands.describe(id="ID do livro no Gutendex.")
    async def favoritar(
        self,
        interaction,
        id: app_commands.Range[int, 1, 999999999],
    ):
        await interaction.response.defer(ephemeral=True)

        book, error = await get_book(
            self.bot.http_session,
            id,
        )

        if error:
            await interaction.followup.send(f"❌ {error}")
            return

        try:
            add_favorite(
                interaction.user.id,
                id,
                clean_text(book.get("title"), 200),
            )
            await interaction.followup.send(
                "⭐ Livro adicionado aos favoritos."
            )
        except sqlite3.Error as error:
            print(f"[SQLite] {error!r}")
            await interaction.followup.send(
                "❌ Não foi possível salvar o favorito."
            )

    @app_commands.command(
        name="desfavoritar",
        description="Remove um livro dos seus favoritos.",
    )
    @app_commands.describe(id="ID do livro.")
    async def desfavoritar(
        self,
        interaction,
        id: app_commands.Range[int, 1, 999999999],
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            removed = remove_favorite(
                interaction.user.id,
                id,
            )
        except sqlite3.Error as error:
            print(f"[SQLite] {error!r}")
            await interaction.followup.send(
                "❌ Não foi possível alterar seus favoritos."
            )
            return

        if not removed:
            await interaction.followup.send(
                "Esse livro não está nos seus favoritos."
            )
            return

        await interaction.followup.send(
            "🗑️ Livro removido dos favoritos."
        )

    @app_commands.command(
        name="favoritos",
        description="Mostra seus livros favoritos.",
    )
    async def favoritos(self, interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            favorites = get_favorites(interaction.user.id)
        except sqlite3.Error as error:
            print(f"[SQLite] {error!r}")
            await interaction.followup.send(
                "❌ Não foi possível consultar seus favoritos."
            )
            return

        if not favorites:
            await interaction.followup.send(
                "Você ainda não possui favoritos. Use /buscar para encontrar um livro."
            )
            return

        lines = [
            f"⭐ **{clean_text(title, 150)}** — ID {book_id}"
            for book_id, title in favorites[:25]
        ]

        embed = discord.Embed(
            title="⭐ Seus favoritos",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )

        if len(favorites) > 25:
            embed.set_footer(
                text=f"Mostrando 25 de {len(favorites)} favoritos."
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="popular",
        description="Mostra livros populares do catálogo.",
    )
    async def popular(self, interaction):
        await interaction.response.defer()

        data, error = await get_popular_books(
            self.bot.http_session
        )

        if error:
            await interaction.followup.send(f"❌ {error}")
            return

        results = data.get("results", [])

        if not results:
            await interaction.followup.send(
                "Não encontrei livros populares."
            )
            return

        lines = []

        for position, book in enumerate(
            results[:MAX_RESULTS],
            start=1,
        ):
            title = clean_text(book.get("title"), 150)
            book_id = book.get("id", "?")
            downloads = book.get("download_count", 0)

            lines.append(
                f"**{position}. {title}**\n"
                f"ID: {book_id} • Downloads: {downloads:,}"
            )

        embed = discord.Embed(
            title="🔥 Livros populares",
            description="\n\n".join(lines),
            color=discord.Color.purple(),
        )
        embed.set_footer(
            text="Use /livro id:... para abrir um livro."
        )

        await interaction.followup.send(embed=embed)


class BibliotecaBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default(),
        )
        self.http_session = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

        await self.add_cog(Biblioteca(self))

        if GUILD_ID:
            try:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(
                    f"[Discord] {len(synced)} comandos sincronizados."
                )
            except ValueError:
                print("[ERRO] GUILD_ID inválido no .env.")
        else:
            synced = await self.tree.sync()
            print(
                f"[Discord] {len(synced)} comandos globais sincronizados."
            )

    async def close(self):
        if self.http_session is not None:
            await self.http_session.close()
            self.http_session = None
        await super().close()

    async def on_ready(self):
        print("=" * 50)
        print(f"Conectado como {self.user}")
        print(f"Livros no cache: {len(livros_cache)}")
        print("=" * 50)


def main():
    if not TOKEN:
        print("[ERRO] DISCORD_TOKEN não encontrado no .env.")
        return

    init_db()
    bot = BibliotecaBot()

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("[ERRO] Token do Discord inválido.")
    except KeyboardInterrupt:
        print("\nBot encerrado pelo usuário.")
    except Exception as error:
        print(f"[ERRO FATAL] {error!r}")


if __name__ == "__main__":
    main()
