# Biblioteca Discord

Bot de Discord para pesquisar ebooks gratuitos no catálogo do Project Gutenberg através da API Gutendex.

## Comandos

- `/buscar busca` — procura por título ou autor.
- `/livro id` — mostra o livro e os formatos disponíveis.
- `/popular` — mostra livros populares.
- `/favoritar id` — salva um livro nos seus favoritos.
- `/favoritos` — lista seus favoritos.

## Instalação

1. Crie um ambiente virtual: `python -m venv .venv`
2. No Windows, ative com `.venv\\Scripts\\activate`
3. Instale as dependências: `pip install -r requirements.txt`
4. Copie `.env.example` para `.env` e preencha `DISCORD_TOKEN` e `GUILD_ID`.
5. Execute `python bot.py`.

## Discord

Crie uma aplicação no Discord Developer Portal, adicione um Bot e convide-o usando os escopos `bot` e `applications.commands`. O token é secreto e nunca deve ser publicado no GitHub.

## Fonte

Os metadados são consultados pela API Gutendex, usando o catálogo do Project Gutenberg. O bot oferece os formatos que a própria fonte informa.

## Direitos autorais

Use o bot para acessar obras disponibilizadas legalmente, como obras de domínio público ou obras para as quais você tenha autorização.
